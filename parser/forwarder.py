from pyrogram import Client
from shared.models import ParseConfig, ParseMode, Message
import logging
import os
import uuid
from typing import Dict, Optional, Callable, List, Any, Tuple
import asyncio
import json
from pyrogram import filters
from pyrogram.types import Message as PyrogramMessage
from pyrogram.types import InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio, InputMediaAnimation
import re
import traceback
from datetime import datetime

# Импорты для python-telegram-bot
try:
    from telegram import Bot as TgBot, InputPaidMediaPhoto, InputPaidMediaVideo
    from telegram.constants import ParseMode as TgParseMode
    import telegram
    TG_BOT_AVAILABLE = True
except ImportError:
    TG_BOT_AVAILABLE = False
    logging.warning("python-telegram-bot не установлен. Платные посты будут недоступны.")

logger = logging.getLogger(__name__)

class TelegramForwarder:
    """Класс для пересылки сообщений без скачивания"""
    
    def __init__(self, db_instance, userbot=None, bot_token=None):
        logger.info(f"[FORWARDER] 🔍 Инициализация TelegramForwarder")
        
        if userbot:
            self.userbot = userbot
        else:
            session_name = os.path.join(os.path.dirname(__file__), "sessions", "userbot")
            self.userbot = Client(
                name=session_name,
                api_id=os.getenv("API_ID"),
                api_hash=os.getenv("API_HASH")
            )
        self.db = db_instance
        self._forwarding_tasks: Dict[int, asyncio.Task] = {}
        self._monitoring_tasks: Dict[Tuple[int, str], asyncio.Task] = {}  # (channel_id, target_channel_id) -> task
        self._media_group_buffers = {}
        self._media_group_timeouts = {}
        self._channel_cache = {}  # Кэш информации о канале
        self._processed_groups = set()  # Для отслеживания уже обработанных медиагрупп
        self._active_handlers = {}  # Для отслеживания активных обработчиков сообщений
        self._forwarding_active = {}  # Для отслеживания активных пересылок по каналам
        # --- python-telegram-bot ---
        self.bot_token = bot_token or os.getenv("BOT_TOKEN")
        self.tg_bot = None
        
        logger.info(f"[FORWARDER] 🔍 Инициализация python-telegram-bot:")
        logger.info(f"[FORWARDER] 🔍 TG_BOT_AVAILABLE: {TG_BOT_AVAILABLE}")
        logger.info(f"[FORWARDER] 🔍 bot_token: {self.bot_token[:10] + '...' if self.bot_token else 'None'}")
        
        if self.bot_token and TG_BOT_AVAILABLE:
            try:
                self.tg_bot = TgBot(token=self.bot_token)
                logger.info("[FORWARDER] ✅ python-telegram-bot инициализирован для платных постов")
            except Exception as e:
                logger.error(f"[FORWARDER] ❌ Ошибка инициализации python-telegram-bot: {e}")
                self.tg_bot = None
        elif not TG_BOT_AVAILABLE:
            logger.warning("[FORWARDER] python-telegram-bot недоступен. Платные посты отключены.")
        elif not self.bot_token:
            logger.warning("[FORWARDER] BOT_TOKEN не найден. Платные посты отключены.")
        
        logger.info(f"[FORWARDER] 🔍 tg_bot инициализирован: {self.tg_bot is not None}")
        
        self._forwarding_settings = {}  # channel_id -> config
        self._counters = {}  # channel_id -> dict с counters
        self._media_group_timeouts = {}  # channel_id -> group_id -> task
        self.media_groups = {}  # group_id -> list of messages
        self.media_group_timeouts = {}  # group_id -> asyncio.Task
        self._is_bot_admin_cache = {}  # channel_id -> bool, всегда инициализирован
        
        # Добавляем систему управления задачами парсинг+пересылки
        self._parse_forward_tasks = {}  # task_id -> task_info
        self._task_counter = 0  # Счетчик для генерации уникальных task_id
        self._monitoring_targets: Dict[Tuple[int, str], str] = {}  # (channel_id, target_channel_id) -> target_channel
        self._handlers = {}  # (source_channel, target_channel) -> handler
    
    async def start(self):
        """Запуск форвардера (только если используется отдельный userbot)"""
        if not hasattr(self.userbot, 'is_connected') or not self.userbot.is_connected:
            session_path = os.path.join(os.path.dirname(__file__), "sessions", "userbot")
            api_id = os.getenv("API_ID")
            api_hash = os.getenv("API_HASH")
            logger.info(f"[FORWARDER] Session file: {session_path}")
            logger.info(f"[FORWARDER] API_ID: {api_id}")
            logger.info(f"[FORWARDER] API_HASH: {api_hash[:4]}***{api_hash[-4:] if api_hash else ''}")
            await self.userbot.start()
            try:
                me = await self.userbot.get_me()
                logger.info(f"[FORWARDER] Logged in as: {me.first_name} {me.last_name or ''} (@{me.username or 'no_username'})")
            except Exception as e:
                logger.error(f"[FORWARDER] Error getting user info: {e}")
            logger.info("Forwarder started successfully")
    
    async def stop(self):
        """Остановка форвардера"""
        for channel_id in list(self._monitoring_tasks.keys()):
            await self.stop_forwarding(channel_id)
        
        try:
            # Проверяем, что клиент еще не остановлен
            if hasattr(self.userbot, 'is_connected') and self.userbot.is_connected:
                await self.userbot.stop()
        except asyncio.CancelledError:
            logger.info("Forwarder stop cancelled (asyncio.CancelledError)")
        except ConnectionError as e:
            if "already terminated" in str(e):
                logger.info("Forwarder client already terminated")
            else:
                logger.error(f"Connection error during forwarder stop: {e}")
        except Exception as e:
            logger.error(f"Error during forwarder stop: {e}")
        logger.info("Forwarder stopped successfully")
    
    async def start_forwarding(self, source_channel: str, target_channel: str, config: dict, callback: Optional[Callable] = None):
        """Запуск пересылки сообщений из одного канала в другой (множественные мониторинги поддерживаются)"""
        try:
            if str(source_channel).startswith("-100"):
                channel = await self.userbot.get_chat(int(source_channel))
            else:
                channel = await self.userbot.get_chat(source_channel)
            channel_id = channel.id
            key = (channel_id, str(target_channel))
            # --- Остановить все мониторинги, у которых target_channel не совпадает с новым ---
            for (src_id, tgt_id) in list(self._monitoring_tasks.keys()):
                if tgt_id != str(target_channel):
                    await self.stop_forwarding(src_id, tgt_id)
            # Если уже есть monitoring для этой пары, не создаём новый
            if key in self._monitoring_tasks:
                logger.info(f"[FORWARDER] Monitoring для {channel_id} -> {target_channel} уже существует, не создаём новый")
                return
            channel_name = channel.username or str(channel_id)
            channel_title = getattr(channel, "title", None)
            logger.info(f"[FORWARDER] 📺 Получен объект канала: {channel_title} (@{channel_name}, ID: {channel_id})")
            logger.info(f"[FORWARDER] 🔄 ЗАПУСК МОНИТОРИНГА (НЕ ПАРСИНГА!)")
            logger.info(f"[FORWARDER] Источник: {source_channel} -> Цель: {target_channel}")
            logger.info(f"[FORWARDER] Конфигурация: {config}")
            if not hasattr(self.userbot, 'is_connected') or not self.userbot.is_connected:
                logger.info(f"[FORWARDER] Userbot не запущен, запускаем...")
                await self.userbot.start()
                logger.info(f"[FORWARDER] Userbot успешно запущен")
            self._channel_cache = {
                'id': channel_id,
                'name': channel_name,
                'title': channel_title
            }
            self._media_group_buffers[channel_id] = {}
            self._media_group_timeouts[channel_id] = {}
            self._forwarding_settings[channel_id] = config.copy()
            self._forwarding_active[channel_id] = True
            if channel_id not in self._counters:
                self._counters[channel_id] = {
                    'hashtag_paid_counter': 0,
                    'select_paid_counter': 0,
                    'media_group_paid_counter': 0,
                    'media_group_hashtag_paid_counter': 0
                }
            self._monitoring_targets[key] = target_channel
            self._monitoring_tasks[key] = asyncio.create_task(self._monitoring_loop())
            # --- Обновить handler для source_channel ---
            self._update_source_handler(channel_id)
            
            # Настройки пересылки
            hide_sender = config.get("hide_sender", True)
            add_footer = config.get("footer_text", "")
            max_posts = config.get("max_posts", 0)
            forward_mode = config.get("forward_mode", "copy")  # copy или forward
            parse_mode = config.get("parse_mode", "all")  # all или hashtags
            hashtag_filter = config.get("hashtag_filter", "")
            text_mode = config.get("text_mode", "hashtags_only")  # hashtags_only, as_is, no_text
            delay_seconds = config.get("delay_seconds", 0)
            paid_content_mode = config.get("paid_content_mode", "off")
            paid_content_hashtag = config.get("paid_content_hashtag")
            paid_content_chance = config.get("paid_content_chance")
            paid_content_stars = config.get("paid_content_stars", 0)
            try:
                paid_content_stars = int(paid_content_stars)
            except Exception as e:
                logger.error(f"[FORWARDER] paid_content_stars не int: {paid_content_stars}, ошибка: {e}")
                paid_content_stars = 0
            logger.info(f"[FORWARDER] ⚙️ paid_content_stars из config: {paid_content_stars} (тип: {type(paid_content_stars)})")
            logger.info(f"[FORWARDER] ⚙️ Весь config: {config}")
            
            logger.info(f"[FORWARDER] ⚙️ Настройки: режим={parse_mode}, хэштег='{hashtag_filter}', лимит={max_posts}, задержка={delay_seconds}с, платные={paid_content_stars}⭐")
            
            if not target_channel:
                raise Exception("Не указан целевой канал для пересылки")
            
            # Инициализируем буферы для медиагрупп (уже сброшены выше)
            # self._media_group_buffers[channel_id] = {}
            # self._media_group_timeouts[channel_id] = {}
            
            # Отмечаем пересылку как активную
            self._forwarding_active[channel_id] = True
            
            # --- counters ---
            self._counters[channel_id] = self._counters.get(channel_id, {
                'hashtag_paid_counter': 0,
                'select_paid_counter': 0,
                'media_group_paid_counter': 0,
                'media_group_hashtag_paid_counter': 0
            })
            if channel_id not in self._media_group_buffers:
                self._media_group_buffers[channel_id] = {}
            if channel_id not in self._media_group_timeouts:
                self._media_group_timeouts[channel_id] = {}
            processed_groups = set()
            media_groups = self._media_group_buffers[channel_id]
            
            logger.info(f"[FORWARDER] 🔄 Мониторинг запущен для канала {channel_name} -> {target_channel}")
            
            forwarded_count = 0
            
            # --- Счётчики для режимов select/hashtag_select ---
            select_paid_counter = 0
            hashtag_paid_counter = 0
            media_group_paid_counter = 0
            media_group_hashtag_paid_counter = 0
            
            # Слушаем только исходный канал, НЕ целевой
            @self.userbot.on_message(filters.chat(channel_id))
            async def handle_new_message(client, message):
                logger.info(f"[FORWARDER][HANDLER] Вызван handler для channel_id={channel_id}, message_id={getattr(message, 'id', None)}")
                nonlocal forwarded_count, select_paid_counter, hashtag_paid_counter
                skip_message = False
                try:
                    # Проверяем, активна ли пересылка для этого канала
                    if not self._forwarding_active.get(channel_id, False):
                        logger.info(f"[FORWARDER][HANDLER] Пересылка для канала {channel_id} остановлена, пропускаем сообщение {getattr(message, 'id', None)}")
                        return
                    logger.info(f"[FORWARDER][HANDLER] Получено сообщение {getattr(message, 'id', None)} из исходного канала {channel_id} -> пересылаем в {target_channel}")
                    # Проверяем лимит
                    if max_posts and max_posts > 0 and forwarded_count >= max_posts:
                        logger.info(f"[FORWARDER] Достигнут лимит пересылок ({max_posts}), останавливаю мониторинг {channel_id}")
                        await self.stop_forwarding(channel_id)
                        return
                    # --- Медиагруппы ---
                    if getattr(message, 'media_group_id', None):
                        group_id = str(message.media_group_id)
                        if group_id not in self.media_groups:
                            self.media_groups[group_id] = []
                        self.media_groups[group_id].append(message)
                        logger.info(f"[DEBUG] Добавлено сообщение {message.id} в медиагруппу {group_id}, теперь файлов: {len(self.media_groups[group_id])}")
                        if group_id not in self.media_group_timeouts:
                            async def send_group_later(forwarded_count):
                                await asyncio.sleep(2.5)
                                group_messages = self.media_groups.get(group_id, [])
                                logger.info(f"[DEBUG] Перед отправкой медиагруппы {group_id}: {len(group_messages)} файлов")
                                # --- Определяем платность медиагруппы ---
                                group_is_paid = False
                                # --- Фильтрация по хэштегу ---
                                if parse_mode == "hashtags" and hashtag_filter and hashtag_filter.strip():
                                    if not any(hashtag_filter.lower() in ((m.text or m.caption or '').lower()) for m in group_messages):
                                        logger.info(f"[FORWARDER] Медиагруппа {group_id} не содержит хэштег '{hashtag_filter}', пропускаем всю группу")
                                        self.media_groups.pop(group_id, None)
                                        self.media_group_timeouts.pop(group_id, None)
                                        return forwarded_count
                                paid_content_mode = config.get('paid_content_mode', 'off')
                                paid_content_hashtag = config.get('paid_content_hashtag')
                                paid_content_every = config.get('paid_content_every', 1)
                                paid_content_chance = config.get('paid_content_chance')
                                counters = self._counters[channel_id]
                                if paid_content_mode == "off" or not paid_content_mode:
                                    group_is_paid = False
                                elif paid_content_mode == "hashtag":
                                    for m in group_messages:
                                        t = (m.text or m.caption or "").lower()
                                        if paid_content_hashtag and paid_content_hashtag.lower() in t:
                                            group_is_paid = True
                                            break
                                elif paid_content_mode == "random":
                                    import random
                                    if paid_content_chance and random.randint(1, 10) <= int(paid_content_chance):
                                        group_is_paid = True
                                elif paid_content_mode == "hashtag_random":
                                    import random
                                    for m in group_messages:
                                        t = (m.text or m.caption or "").lower()
                                        if paid_content_hashtag and paid_content_hashtag.lower() in t:
                                            if paid_content_chance and random.randint(1, 10) <= int(paid_content_chance):
                                                group_is_paid = True
                                                break
                                elif paid_content_mode == "select":
                                    counters['media_group_paid_counter'] += 1
                                    every = config.get('paid_content_every', 1)
                                    try:
                                        every = int(every)
                                    except Exception:
                                        every = 1
                                    if every > 0 and (counters['media_group_paid_counter'] % every == 0):
                                        group_is_paid = True
                                elif paid_content_mode == "hashtag_select":
                                    group_hashtag = False
                                    for m in group_messages:
                                        t = (m.text or m.caption or "").lower()
                                        if paid_content_hashtag and paid_content_hashtag.lower() in t:
                                            group_hashtag = True
                                            break
                                    if group_hashtag:
                                        counters['media_group_hashtag_paid_counter'] += 1
                                        every = config.get('paid_content_every', 1)
                                        try:
                                            every = int(every)
                                        except Exception:
                                            every = 1
                                        if every > 0 and (counters['media_group_hashtag_paid_counter'] % every == 0):
                                            group_is_paid = True
                                else:
                                    group_is_paid = False
                                # --- Отправка медиагруппы ---
                                if group_messages:
                                    try:
                                        logger.info(f"[FORWARDER][DEBUG] Вызов forward_media_group для {group_id} с {len(group_messages)} файлами")
                                        await self.forward_media_group(
                                            channel_id,
                                            group_id,
                                            target_channel,
                                            text_mode,
                                            add_footer,
                                            forward_mode,
                                            hide_sender,
                                            max_posts,
                                            callback,
                                            paid_content_stars if group_is_paid else 0,
                                            group_messages  # <-- передаем явно
                                        )
                                        logger.info(f"[FORWARDER][DEBUG] Возврат из forward_media_group для {group_id}")
                                        
                                        # Увеличиваем счетчик для медиагрупп
                                        forwarded_count += 1
                                        logger.info(f"[FORWARDER] Медиагруппа {group_id} переслана, счетчик: {forwarded_count}/{max_posts}")
                                        
                                        # Проверяем лимит после медиагруппы
                                        if max_posts and max_posts > 0 and forwarded_count >= max_posts:
                                            logger.info(f"[FORWARDER] Достигнут лимит пересылок ({max_posts}) после медиагруппы {group_id}, останавливаю мониторинг")
                                            await self.stop_forwarding(channel_id)
                                            return forwarded_count
                                    except Exception as e:
                                        logger.error(f"[FORWARDER][ERROR] Ошибка при вызове forward_media_group для {group_id}: {e}")
                                        logger.error(f"[FORWARDER][ERROR] Полная ошибка: {traceback.format_exc()}")
                                else:
                                    logger.info(f"[FORWARDER][ERROR] Медиагруппа {group_id} пуста, пропускаем!")
                                    # Очищаем буфер и таймер
                                self.media_groups.pop(group_id, None)
                                self.media_group_timeouts.pop(group_id, None)
                            self.media_group_timeouts[group_id] = asyncio.create_task(send_group_later(forwarded_count))
                        return  # <--- добавлено, чтобы не обрабатывать как одиночное сообщение
                    # Задержка если указана (для одиночных)
                    if delay_seconds > 0 and not getattr(message, 'media_group_id', None):
                        await asyncio.sleep(delay_seconds)
                    # Одиночное сообщение
                    if not skip_message:
                        # --- Одиночные сообщения ---
                        counters = self._counters[channel_id]
                        is_paid = False
                        text = (message.text or message.caption or "").lower()
                        paid_content_mode = config.get('paid_content_mode', 'off')
                        paid_content_hashtag = (config.get('paid_content_hashtag') or '').lower()
                        paid_content_every = config.get('paid_content_every', 1)
                        paid_content_chance = config.get('paid_content_chance')
                        if parse_mode == "hashtags" and hashtag_filter and hashtag_filter.strip():
                            if hashtag_filter.lower() not in text:
                                logger.info(f"[FORWARDER] Сообщение {message.id} не содержит хэштег '{hashtag_filter}', пропускаем")
                                return
                        if paid_content_mode == "off" or not paid_content_mode:
                            is_paid = False
                        elif paid_content_mode == "hashtag":
                            if paid_content_hashtag and paid_content_hashtag in text:
                                is_paid = True
                        elif paid_content_mode == "random":
                            import random
                            if paid_content_chance and random.randint(1, 10) <= int(paid_content_chance):
                                is_paid = True
                        elif paid_content_mode == "hashtag_random":
                            import random
                            if paid_content_hashtag and paid_content_hashtag in text:
                                if paid_content_chance and random.randint(1, 10) <= int(paid_content_chance):
                                    is_paid = True
                        elif paid_content_mode == "select":
                            select_paid_counter += 1
                            every = config.get('paid_content_every', 1)
                            try:
                                every = int(every)
                            except Exception:
                                every = 1
                            if every > 0 and (select_paid_counter % every == 0):
                                is_paid = True
                        elif paid_content_mode == "hashtag_select":
                            if paid_content_hashtag and paid_content_hashtag in text:
                                hashtag_paid_counter += 1
                                logger.info(f"[FORWARDER][PAID] Сообщение {message.id} с хэштегом '{paid_content_hashtag}': #{hashtag_paid_counter} по счёту, every={paid_content_every}")
                                every = config.get('paid_content_every', 1)
                                try:
                                    every = int(every)
                                except Exception:
                                    every = 1
                                if every > 0 and (hashtag_paid_counter % every == 0):
                                    is_paid = True
                                    logger.info(f"[FORWARDER][PAID] Сообщение {message.id} становится платным! (#{hashtag_paid_counter} из {every})")
                                else:
                                    logger.info(f"[FORWARDER][PAID] Сообщение {message.id} не становится платным (#{hashtag_paid_counter} из {every})")
                        else:
                            is_paid = False
                        logger.info(f"[FORWARDER] 🔍 Вызываем _forward_single_message с paid_content_stars={paid_content_stars if is_paid else 0} (тип: {type(paid_content_stars)})")
                        logger.info(f"[FORWARDER][HANDLER] Перед вызовом _forward_single_message для message_id={getattr(message, 'id', None)}")
                        await self._forward_single_message(
                            message,
                            target_channel,
                            hide_sender,
                            add_footer,
                            forward_mode,
                            text_mode,
                            paid_content_stars if is_paid else 0
                        )
                        logger.info(f"[FORWARDER][HANDLER] Успешно переслано сообщение {getattr(message, 'id', None)} в {target_channel}")
                        if delay_seconds and delay_seconds > 0:
                            await asyncio.sleep(delay_seconds)
                        forwarded_count += 1
                        if callback:
                            await callback(message)
                        last_message_id = message.id
                except Exception as e:
                    logger.error(f"[FORWARDER] Ошибка при обработке сообщения {message.id}: {e}")
                    logger.error(f"[FORWARDER] Полная ошибка: {traceback.format_exc()}")
            
            # Сохраняем ссылку на обработчик для возможности его удаления
            self._active_handlers[channel_id] = handle_new_message
            
            # Сохраняем цель мониторинга
            key = (channel_id, str(target_channel))
            self._monitoring_targets[key] = target_channel
            
            # Запускаем мониторинг
            self._monitoring_tasks[key] = asyncio.create_task(self._monitoring_loop())
            logger.info(f"[FORWARDER] Запущен мониторинг канала {channel_name} -> {target_channel}")
            
            # После создания handler:
            self._handlers[key] = handle_new_message
            
        except Exception as e:
            logger.error(f"[FORWARDER] Ошибка при запуске пересылки: {e}")
            raise
    
    async def _timeout_forward_media_group(self, channel_id, group_id, target_channel, text_mode, add_footer, forward_mode, hide_sender, max_posts, callback, paid_content_stars):
        """Таймаут для пересылки медиагруппы"""
        try:
            logger.info(f"[FORWARDER] 🔍 _timeout_forward_media_group: group_id={group_id}, paid_content_stars={paid_content_stars}")
            await asyncio.sleep(5)  # Ждем 5 секунд для сбора всех файлов группы
            
            # Проверяем, активна ли пересылка для этого канала
            if not self._forwarding_active.get(channel_id, False):
                logger.info(f"[FORWARDER] Пересылка для канала {channel_id} остановлена, отменяем обработку медиагруппы {group_id}")
                return
            
            logger.info(f"[FORWARDER] 🔍 Вызываем forward_media_group для группы {group_id} с paid_content_stars={paid_content_stars}")
            await self.forward_media_group(channel_id, group_id, target_channel, text_mode, add_footer, forward_mode, hide_sender, max_posts, callback, paid_content_stars)
        except asyncio.CancelledError:
            logger.info(f"[FORWARDER] Таймаут медиагруппы {group_id} отменен")
        except Exception as e:
            logger.error(f"[FORWARDER] Ошибка в таймауте медиагруппы {group_id}: {e}")
            logger.error(f"[FORWARDER] Полная ошибка: {traceback.format_exc()}")
    
    async def _send_paid_media(self, chat_id, media_type, file_id, caption, stars, is_bot_admin, temp_file_path=None):
        """Отправка платного контента через python-telegram-bot, с поддержкой отправки файла если бот не админ"""
        logger.info(f"[FORWARDER] 🎯 _send_paid_media: chat_id={chat_id}, media_type={media_type}, stars={stars} (тип: {type(stars)}), is_bot_admin={is_bot_admin}")
        if not self.tg_bot:
            logger.error("[FORWARDER] python-telegram-bot не инициализирован!")
            return False
        try:
            # Проверяем доступность чата для python-telegram-bot
            try:
                await self.tg_bot.get_chat(chat_id)
                logger.info(f"[FORWARDER] ✅ Чат {chat_id} доступен для python-telegram-bot")
            except Exception as e:
                if "Chat not found" in str(e) or "chat not found" in str(e):
                    logger.warning(f"[FORWARDER] python-telegram-bot не может найти чат {chat_id}, используем Pyrogram")
                    return False
                else:
                    raise e
            if is_bot_admin:
                # Можно использовать file_id
                if media_type == 'photo':
                    media = [InputPaidMediaPhoto(media=file_id)]
                elif media_type == 'video':
                    media = [InputPaidMediaVideo(media=file_id)]
                else:
                    logger.warning(f"[FORWARDER] Тип {media_type} не поддерживается для платного контента")
                    return False
            else:
                # Нужно отправлять файл
                if not temp_file_path or not os.path.exists(temp_file_path):
                    logger.error(f"[FORWARDER] temp_file_path не найден для отправки файла: {temp_file_path}")
                    return False
                if media_type == 'photo':
                    media = [InputPaidMediaPhoto(media=open(temp_file_path, 'rb'))]
                elif media_type == 'video':
                    media = [InputPaidMediaVideo(media=open(temp_file_path, 'rb'))]
                else:
                    logger.warning(f"[FORWARDER] Тип {media_type} не поддерживается для платного контента (файл)")
                    return False
            logger.info(f"[FORWARDER] 🚀 Отправляем платный контент: {media_type} с {stars} звездами (is_bot_admin={is_bot_admin})")
            # Проверяем наличие HTML-разметки в caption
            contains_html = "<a href=" in caption or "<b>" in caption or "<i>" in caption or "<code>" in caption
            
            result = await self.tg_bot.send_paid_media(
                chat_id=chat_id,
                star_count=stars,
                media=media,
                caption=caption,
                parse_mode=TgParseMode.HTML if contains_html else None
            )
            logger.info(f"[FORWARDER] ✅ Платный пост отправлен через python-telegram-bot: {media_type} с {stars} звездами")
            if not is_bot_admin and temp_file_path:
                try:
                    os.remove(temp_file_path)
                    logger.info(f"[FORWARDER] Временный файл удалён: {temp_file_path}")
                except Exception as e:
                    logger.warning(f"[FORWARDER] Не удалось удалить временный файл {temp_file_path}: {e}")
            return result
        except Exception as e:
            logger.error(f"[FORWARDER] ❌ Ошибка отправки платного поста: {e}")
            logger.error(f"[FORWARDER] Полная ошибка: {traceback.format_exc()}")
            if not is_bot_admin and temp_file_path:
                try:
                    os.remove(temp_file_path)
                except Exception:
                    pass
            return False

    async def _forward_single_message(self, message, target_channel, hide_sender, add_footer, forward_mode, text_mode="hashtags_only", paid_content_stars=0):
        try:
            logger.info(f"[FORWARDER][DEBUG] Используемая приписка (add_footer): {add_footer!r}")
            logger.info(f"[FORWARDER] 🔍 _forward_single_message: paid_content_stars={paid_content_stars} (тип: {type(paid_content_stars)})")
            logger.info(f"[FORWARDER] 🔍 tg_bot доступен: {self.tg_bot is not None}")
            logger.info(f"[FORWARDER] 🔍 Условие для платного контента: paid_content_stars > 0 = {paid_content_stars > 0}")
            original_text = message.text or message.caption or ""
            processed_text = self._process_message_text(original_text, text_mode)
            
            # Получаем настройки гиперссылки из конфигурации канала
            channel_id = message.chat.id if hasattr(message, 'chat') and hasattr(message.chat, 'id') else None
            config = self._forwarding_settings.get(channel_id, {})
            footer_link = config.get("footer_link")
            footer_link_text = config.get("footer_link_text")
            footer_full_link = config.get("footer_full_link", False)
            
            # Форматируем приписку с гиперссылкой, если настроена
            if add_footer:
                formatted_footer = self._format_footer_with_link(add_footer, footer_link, footer_link_text, footer_full_link)
                if processed_text:
                    processed_text += f"\n\n{formatted_footer}"
                else:
                    processed_text = formatted_footer
                    
                # Устанавливаем флаг, содержит ли текст HTML-разметку
                contains_html = "<a href=" in processed_text or "<b>" in processed_text or "<i>" in processed_text
            should_send_paid = paid_content_stars > 0 and self.tg_bot is not None
            channel_id = message.chat.id if hasattr(message, 'chat') and hasattr(message.chat, 'id') else None
            
            # Реальная проверка админа бота
            is_bot_admin = False
            if channel_id:
                if channel_id not in self._is_bot_admin_cache:
                    self._is_bot_admin_cache[channel_id] = await self._is_bot_admin(channel_id)
                is_bot_admin = self._is_bot_admin_cache[channel_id]
            
            logger.info(f"[FORWARDER] 🎯 Должен ли отправлять платный контент: {should_send_paid}, is_bot_admin={is_bot_admin}")
            if should_send_paid:
                logger.info(f"[FORWARDER] 🎯 Отправляем платный контент: {paid_content_stars} звезд")
                if message.media:
                    media_type = message.media.value
                    if media_type in ['photo', 'video']:
                        file_id = getattr(message, media_type).file_id
                        temp_file_path = None
                        if not is_bot_admin:
                            # Скачиваем файл во временную папку
                            import tempfile
                            ext = '.jpg' if media_type == 'photo' else '.mp4'
                            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                                temp_file_path = tmp.name
                            temp_file_path = await self.userbot.download_media(message, file_name=temp_file_path)
                        result = await self._send_paid_media(target_channel, media_type, file_id, processed_text, paid_content_stars, is_bot_admin, temp_file_path)
                        if result:
                            logger.info(f"[FORWARDER] ✅ Переслано платное {media_type} сообщение {message.id} с {paid_content_stars} звездами")
                            return True
                        else:
                            logger.info(f"[FORWARDER][FALLBACK] Не удалось отправить платный пост через python-telegram-bot, отправляем как обычный через Pyrogram")
                            paid_content_stars = 0
                    else:
                        logger.warning(f"[FORWARDER] Тип медиа {media_type} не поддерживается для платного контента, пропускаем!")
                        return False
                else:
                    logger.warning("[FORWARDER] Платный контент поддерживается только для медиа-сообщений (фото/видео), пропускаем!")
                    return False
            else:
                logger.info(f"[FORWARDER] 🔄 Отправляем обычный контент (paid_content_stars={paid_content_stars}, tg_bot={self.tg_bot is not None})")
            send_params = {
                'chat_id': target_channel
            }
            # hide_sender только для forward_messages, не для send_message!
            # Для send_message не добавляем hide_sender
            if message.media:
                media_type = message.media.value
                entities = getattr(message, 'entities', None)
                caption_entities = getattr(message, 'caption_entities', None)
                logger.info(f"[FORWARDER][DEBUG] entities: {entities} (type: {type(entities)}), len: {len(entities) if entities else 0}")
                logger.info(f"[FORWARDER][DEBUG] caption_entities: {caption_entities} (type: {type(caption_entities)}), len: {len(caption_entities) if caption_entities else 0}")
                logger.info(f"[FORWARDER][DEBUG] processed_text: {processed_text}")
                logger.info(f"[FORWARDER][DEBUG] original_text: {original_text}")
                # Исправлено: parse_mode только если реально есть форматирование, и только "HTML" (заглавными)
                parse_mode = 'HTML' if self._should_use_parse_mode(caption_entities) else None
                logger.info(f"[FORWARDER][DEBUG] Итоговый parse_mode для медиа: {parse_mode}")
                if media_type == 'photo':
                    logger.info(f"[FORWARDER][DEBUG] send_photo params: photo={message.photo.file_id}, caption={processed_text}, chat_id={target_channel}, parse_mode={parse_mode}")
                    await self.userbot.send_photo(photo=message.photo.file_id, caption=processed_text, chat_id=target_channel)
                elif media_type == 'video':
                    logger.info(f"[FORWARDER][DEBUG] send_video params: video={message.video.file_id}, caption={processed_text}, chat_id={target_channel}, parse_mode={parse_mode}")
                    await self.userbot.send_video(video=message.video.file_id, caption=processed_text, chat_id=target_channel)
                elif media_type == 'document':
                    logger.info(f"[FORWARDER][DEBUG] send_document params: document={message.document.file_id}, caption={processed_text}, chat_id={target_channel}, parse_mode={parse_mode}")
                    await self.userbot.send_document(document=message.document.file_id, caption=processed_text, chat_id=target_channel)
                elif media_type == 'audio':
                    logger.info(f"[FORWARDER][DEBUG] send_audio params: audio={message.audio.file_id}, caption={processed_text}, chat_id={target_channel}, parse_mode={parse_mode}")
                    await self.userbot.send_audio(audio=message.audio.file_id, caption=processed_text, chat_id=target_channel)
                elif media_type == 'voice':
                    logger.info(f"[FORWARDER][DEBUG] send_voice params: voice={message.voice.file_id}, caption={processed_text}, chat_id={target_channel}, parse_mode={parse_mode}")
                    await self.userbot.send_voice(voice=message.voice.file_id, caption=processed_text, chat_id=target_channel)
                elif media_type == 'video_note':
                    logger.info(f"[FORWARDER][DEBUG] send_video_note params: video_note={message.video_note.file_id}, chat_id={target_channel}")
                    await self.userbot.send_video_note(video_note=message.video_note.file_id, chat_id=target_channel)
                elif media_type == 'animation':
                    logger.info(f"[FORWARDER][DEBUG] send_animation params: animation={message.animation.file_id}, caption={processed_text}, chat_id={target_channel}, parse_mode={parse_mode}")
                    await self.userbot.send_animation(animation=message.animation.file_id, caption=processed_text, chat_id=target_channel)
                elif media_type == 'sticker':
                    logger.info(f"[FORWARDER][DEBUG] send_sticker params: sticker={message.sticker.file_id}, chat_id={target_channel}")
                    await self.userbot.send_sticker(sticker=message.sticker.file_id, chat_id=target_channel)
                else:
                    logger.warning(f"[FORWARDER] Неподдерживаемый тип медиа: {media_type}, пропускаем!")
                    return False  # <-- теперь не отправляем как текст
            else:
                # Для send_message НЕ передавать hide_sender!
                # parse_mode только если реально есть entities
                entities = getattr(message, 'entities', None)
                logger.info(f"[FORWARDER][DEBUG] entities: {entities} (type: {type(entities)}), len: {len(entities) if entities else 0}")
                logger.info(f"[FORWARDER][DEBUG] processed_text: {processed_text}")
                logger.info(f"[FORWARDER][DEBUG] original_text: {original_text}")
                # Исправлено: parse_mode только если реально есть entities
                if entities and len(entities) > 0 and self._should_use_parse_mode(entities):
                    send_params['parse_mode'] = 'HTML'
                logger.info(f"[FORWARDER][DEBUG] Итоговый send_params для send_message: {send_params}")
                await self.userbot.send_message(text=processed_text or original_text, chat_id=target_channel)
            logger.info(f"[FORWARDER] ✅ Переслано одиночное сообщение {message.id}")
            return True
        except Exception as e:
            logger.error(f"[FORWARDER] ❌ Ошибка пересылки одиночного сообщения {message.id}: {e}")
            return False
    
    def _process_message_text(self, text: str, text_mode: str) -> str:
        """Обработка текста сообщения в зависимости от режима"""
        if not text:
            return ""
        
        if text_mode == "hashtags_only":
            # Извлекаем только хэштеги
            import re
            hashtags = re.findall(r'#\w+', text)
            return ' '.join(hashtags)
        elif text_mode == "no_text":
            # Не отправляем текст
            return ""
        elif text_mode == "as_is":
            # Оставляем текст как есть
            return text
        else:
            # По умолчанию - только хэштеги
            import re
            hashtags = re.findall(r'#\w+', text)
            return ' '.join(hashtags)
    
    def _format_footer_with_link(self, footer_text: str, footer_link: str = None, footer_link_text: str = None, footer_full_link: bool = False) -> str:
        """
        Форматирует текст приписки с гиперссылкой
        
        Args:
            footer_text (str): Текст приписки
            footer_link (str, optional): URL для гиперссылки
            footer_link_text (str, optional): Текст, который нужно сделать гиперссылкой
            footer_full_link (bool, optional): Сделать всю приписку гиперссылкой
            
        Returns:
            str: Отформатированная приписка с HTML-разметкой для ссылок
        """
        if not footer_text:
            return ""
        
        if not footer_link:
            return footer_text
        
        if footer_full_link:
            # Вся приписка - гиперссылка
            return f'<a href="{footer_link}">{footer_text}</a>'
        
        if footer_link_text and footer_link_text in footer_text:
            # Часть приписки - гиперссылка
            html_link = f'<a href="{footer_link}">{footer_link_text}</a>'
            return footer_text.replace(footer_link_text, html_link)
        
        # По умолчанию добавляем ссылку в конец
        link_text = footer_link_text or "ссылка"
        return f'{footer_text} <a href="{footer_link}">{link_text}</a>'
    
    async def _save_to_posts_json(self, messages, caption, channel_id):
        """Сохранение информации в posts.json"""
        try:
            posts_json_path = os.path.join(os.path.dirname(__file__), "posts_data", "posts.json")
            posts_data = []
            
            if os.path.exists(posts_json_path):
                try:
                    with open(posts_json_path, "r", encoding="utf-8") as f:
                        posts_data = json.load(f)
                except Exception as e:
                    logger.warning(f"[FORWARDER] posts.json повреждён или пустой: {e}, создаю новый.")
                    posts_data = []
            
            posts_by_id = {p["message_id"]: p for p in posts_data if "message_id" in p}
            
            for m in messages:
                post = posts_by_id.get(m.id)
                if post:
                    post["media_files"] = [r"D:\PycharmProjects\telegram-parse-bot\media\static\img\default.png"]
                    post["text"] = caption or ""
                    posts_by_id[m.id] = post
                    
                    m_dict = m.to_dict() if hasattr(m, 'to_dict') else m.__dict__.copy()
                    m_dict["local_file_path"] = r"D:\PycharmProjects\telegram-parse-bot\media\static\img\default.png"
                    m_dict["text"] = caption or ""
                    await self.db.mark_message_as_parsed(m_dict, channel_id)
            
            with open(posts_json_path, "w", encoding="utf-8") as f:
                json.dump(list(posts_by_id.values()), f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            logger.error(f"[FORWARDER] Ошибка при сохранении в posts.json: {e}")
            logger.error(f"[FORWARDER] Полная ошибка: {traceback.format_exc()}")
    
    async def _monitoring_loop(self):
        """Цикл мониторинга"""
        while True:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[FORWARDER] Ошибка в цикле мониторинга: {e}")
                logger.error(f"[FORWARDER] Полная ошибка: {traceback.format_exc()}")
    
    async def stop_forwarding(self, channel_id: int, target_channel_id: str = None):
        """
        Остановить пересылку/мониторинг для канала и цели (если указана).
        Если target_channel_id не указан — останавливаем все мониторинги для source-канала.
        """
        try:
            if target_channel_id is not None:
                key = (channel_id, str(target_channel_id))
                if key in self._monitoring_tasks:
                    self._monitoring_tasks[key].cancel()
                    del self._monitoring_tasks[key]
                if key in self._monitoring_targets:
                    del self._monitoring_targets[key]
                # --- Обновить handler для source_channel ---
                self._update_source_handler(channel_id)
                logger.info(f"[FORWARDER] Остановлен мониторинг для пары {channel_id} -> {target_channel_id}")
            else:
                to_remove = [k for k in self._monitoring_tasks if k[0] == channel_id]
                for key in to_remove:
                    self._monitoring_tasks[key].cancel()
                    del self._monitoring_tasks[key]
                    if key in self._monitoring_targets:
                        del self._monitoring_targets[key]
                # --- Обновить handler для source_channel ---
                self._update_source_handler(channel_id)
                logger.info(f"[FORWARDER] Остановлены все мониторинги для канала {channel_id}")
        except Exception as e:
            logger.error(f"[FORWARDER][stop_forwarding] Ошибка: {e}")
    
    async def clear_cache(self, channel_id: int = None):
        """Очистка кэша для канала (вызывается после очистки истории)"""
        if channel_id is not None:
            self._media_group_buffers[channel_id] = {}
            self._media_group_timeouts[channel_id] = {}
            self._forwarding_settings[channel_id] = {}
            self._forwarding_active[channel_id] = False
            if channel_id in self._counters:
                self._counters[channel_id] = {
                    'hashtag_paid_counter': 0,
                    'select_paid_counter': 0,
                    'media_group_paid_counter': 0,
                    'media_group_hashtag_paid_counter': 0
                }
            logger.info(f"[FORWARDER] Очищен кэш для канала {channel_id}")
        else:
            self._media_group_buffers = {}
            self._media_group_timeouts = {}
            self._forwarding_settings = {}
            self._forwarding_active = {}
            self._counters = {}
            logger.info(f"[FORWARDER] Очищен весь кэш")
    
    async def get_forwarding_status(self, channel_id: int) -> Dict[str, Any]:
        """Получение статуса пересылки"""
        try:
            # Преобразуем username в id, если нужно
            if isinstance(channel_id, str) and not channel_id.startswith("-100") and not channel_id.isdigit():
                try:
                    chat = await self.userbot.get_chat(channel_id)
                    channel_id = chat.id
                except Exception as e:
                    logger.error(f"[FORWARDER] Не удалось получить id для канала {channel_id}: {e}")
                    return {
                        "error": f"Не удалось получить id для канала {channel_id}: {e}",
                        "is_active": False,
                        "forwarded_count": 0,
                        "today_forwarded": 0,
                        "hashtag_matches": 0,
                        "errors_count": 0,
                        "last_activity": "N/A",
                        "forward_channel_title": ""
                    }
            elif isinstance(channel_id, str) and channel_id.isdigit():
                channel_id = int(channel_id)
            is_monitoring = channel_id in self._monitoring_tasks
            task_active = False
            forwarding_active = self._forwarding_active.get(channel_id, False)
            if is_monitoring:
                task = self._monitoring_tasks[channel_id]
                task_active = not task.done()
            is_active = forwarding_active or (is_monitoring and task_active)
            total_forwarded = 0
            today_forwarded = 0
            hashtag_matches = 0
            errors_count = 0
            last_activity = "N/A"
            try:
                async with self.db.conn.execute(
                    "SELECT COUNT(*) FROM parsed_messages WHERE channel_id = ? AND forwarded_to IS NOT NULL",
                    (channel_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    total_forwarded = row[0] if row else 0
                from datetime import datetime, date
                today = date.today().strftime('%Y-%m-%d')
                async with self.db.conn.execute(
                    "SELECT COUNT(*) FROM parsed_messages WHERE channel_id = ? AND forwarded_to IS NOT NULL AND DATE(parsed_at) = ?",
                    (channel_id, today)
                ) as cursor:
                    row = await cursor.fetchone()
                    today_forwarded = row[0] if row else 0
                async with self.db.conn.execute(
                    "SELECT parsed_at FROM parsed_messages WHERE channel_id = ? AND forwarded_to IS NOT NULL ORDER BY parsed_at DESC LIMIT 1",
                    (channel_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        last_activity = row[0]
            except Exception as e:
                logger.error(f"[FORWARDER] Ошибка при получении статистики из БД: {e}")
            # --- Гарантируем наличие forward_channel_title ---
            forward_channel_title = None
            try:
                if self._channel_cache and self._channel_cache.get("title"):
                    forward_channel_title = self._channel_cache["title"]
                else:
                    try:
                        chat = await self.userbot.get_chat(channel_id)
                        forward_channel_title = getattr(chat, "title", None)
                    except Exception as chat_error:
                        if "Peer id invalid" in str(chat_error) or "ID not found" in str(chat_error):
                            logger.warning(f"[FORWARDER] Канал {channel_id} недоступен при получении статуса: {chat_error}")
                            forward_channel_title = f"Канал {channel_id} (недоступен)"
                        else:
                            logger.warning(f"[FORWARDER] Ошибка получения информации о канале {channel_id}: {chat_error}")
                            forward_channel_title = ""
            except Exception:
                forward_channel_title = ""
            return {
                "is_active": is_active,
                "forwarded_count": total_forwarded,
                "today_forwarded": today_forwarded,
                "hashtag_matches": hashtag_matches,
                "errors_count": errors_count,
                "last_activity": last_activity,
                "is_monitoring": is_monitoring,
                "task_active": task_active,
                "forwarding_active": forwarding_active,
                "channel_id": channel_id,
                "media_groups_buffered": len(self._media_group_buffers.get(channel_id, {})),
                "forward_channel_title": forward_channel_title or "",
                "target_channel": self._monitoring_targets.get(channel_id)  # добавлено
            }
            
        except Exception as e:
            logger.error(f"[FORWARDER] Ошибка при получении статуса: {e}")
            return {
                "error": str(e),
                "is_active": False,
                "forwarded_count": 0,
                "today_forwarded": 0,
                "hashtag_matches": 0,
                "errors_count": 0,
                "last_activity": "N/A",
                "target_channel": self._monitoring_targets.get(channel_id)  # добавлено
            }

    async def forward_media_group(self, channel_id, group_id, target_channel, text_mode, add_footer, forward_mode, hide_sender, max_posts, callback=None, paid_content_stars=0, group_messages=None, config=None):
        logger.info(f"[FORWARDER] 🔍 forward_media_group: paid_content_stars={paid_content_stars} (тип: {type(paid_content_stars)})")
        logger.info(f"[FORWARDER][DEBUG] Используемая приписка для медиагруппы (add_footer): {add_footer!r}")
        if group_messages is not None:
            group_msgs = group_messages
        else:
            group_msgs = self._media_group_buffers.get(channel_id, {}).get(str(group_id), [])
        # Реальная проверка админа бота
        is_bot_admin = False
        if channel_id:
            if channel_id not in self._is_bot_admin_cache:
                self._is_bot_admin_cache[channel_id] = await self._is_bot_admin(channel_id)
            is_bot_admin = self._is_bot_admin_cache[channel_id]
        # --- Фильтрация по хэштегу только для режима парсинг+пересылка ---
        if config and config.get('parse_mode') == 'hashtags' and config.get('hashtag_filter'):
            if not self.group_has_hashtag(group_msgs, config['hashtag_filter']):
                logger.info(f"[FORWARDER] В медиагруппе {group_id} нет сообщений с хэштегом '{config['hashtag_filter']}', пропускаем группу")
                return 0
        logger.info(f"[FORWARDER][DEBUG] Перед пересылкой медиагруппы {group_id}: {len(group_msgs)} файлов")
        if not group_msgs or len(group_msgs) < 2:
            logger.warning(f"[FORWARDER][SKIP] Медиагруппа {group_id} содержит менее 2 файлов после фильтрации, пропускаем!")
            return 0
        group_msgs = sorted(group_msgs, key=lambda m: m.date)
        group_caption = None
        for m in group_msgs:
            if getattr(m, 'caption', None):
                group_caption = m.caption
                break
        if text_mode == "no_text":
            group_caption = ""
        elif text_mode == "hashtags_only":
            import re
            hashtags = re.findall(r'#\w+', group_caption or "")
            group_caption = " ".join(hashtags)
        # Получаем настройки гиперссылки из конфигурации
        footer_link = config.get("footer_link") if config else None
        footer_link_text = config.get("footer_link_text") if config else None
        footer_full_link = config.get("footer_full_link", False) if config else False
        
        # Форматируем приписку с гиперссылкой, если настроена
        if add_footer:
            formatted_footer = self._format_footer_with_link(add_footer, footer_link, footer_link_text, footer_full_link)
            if group_caption:
                group_caption = f"{group_caption}\n\n{formatted_footer}"
            else:
                group_caption = formatted_footer
        group_message_ids = []
        try:
            if forward_mode == "forward":
                for m in group_msgs:
                    await self.userbot.forward_messages(
                        chat_id=target_channel,
                        from_chat_id=channel_id,
                        message_ids=m.id,
                        hide_sender=hide_sender
                    )
                    group_message_ids.append(m.id)
            else:
                # Проверяем, нужно ли отправлять как платный контент
                should_send_paid = paid_content_stars > 0 and self.tg_bot is not None
                logger.info(f"[FORWARDER] 🎯 Должен ли отправлять платный контент для медиагруппы: {should_send_paid}, is_bot_admin={is_bot_admin}")
                
                if should_send_paid:
                    logger.info(f"[FORWARDER] 🎯 Отправляем платную медиагруппу: {paid_content_stars} звезд")
                    # Проверяем доступность чата для python-telegram-bot
                    try:
                        await self.tg_bot.get_chat(target_channel)
                    except Exception as e:
                        if "Chat not found" in str(e) or "chat not found" in str(e):
                            logger.warning(f"[FORWARDER] python-telegram-bot не может найти чат {target_channel}, используем Pyrogram")
                            # Fallback на Pyrogram
                            paid_content_stars = 0
                        else:
                            raise e
                    
                    if paid_content_stars > 0:
                        # Отправляем через python-telegram-bot как платный контент
                        media_list = []
                        temp_files = []
                        for m in group_msgs:
                            if m.photo:
                                media_type = 'photo'
                                file_id = m.photo.file_id
                                temp_file_path = None
                                if not is_bot_admin:
                                    import tempfile
                                    ext = '.jpg'
                                    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                                        temp_file_path = tmp.name
                                    temp_file_path = await self.userbot.download_media(m, file_name=temp_file_path)
                                    if not temp_file_path or not os.path.exists(temp_file_path):
                                        logger.error(f"[FORWARDER] Не удалось скачать файл для платного поста: message_id={getattr(m, 'id', None)}, media_type={media_type}")
                                        continue
                                    temp_files.append(temp_file_path)
                                media_list.append((media_type, file_id, temp_file_path))
                            elif m.video:
                                media_type = 'video'
                                file_id = m.video.file_id
                                temp_file_path = None
                                if not is_bot_admin:
                                    import tempfile
                                    ext = '.mp4'
                                    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                                        temp_file_path = tmp.name
                                    temp_file_path = await self.userbot.download_media(m, file_name=temp_file_path)
                                    if not temp_file_path or not os.path.exists(temp_file_path):
                                        logger.error(f"[FORWARDER] Не удалось скачать файл для платного поста: message_id={getattr(m, 'id', None)}, media_type={media_type}")
                                        continue
                                    temp_files.append(temp_file_path)
                                media_list.append((media_type, file_id, temp_file_path))
                            elif getattr(m, 'document', None) and getattr(m.document, 'mime_type', None):
                                if m.document.mime_type.startswith('image/'):
                                    media_type = 'photo'
                                    file_id = m.document.file_id
                                    temp_file_path = None
                                    if not is_bot_admin:
                                        import tempfile
                                        ext = '.jpg'
                                        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                                            temp_file_path = tmp.name
                                        temp_file_path = await self.userbot.download_media(m, file_name=temp_file_path)
                                        if not temp_file_path or not os.path.exists(temp_file_path):
                                            logger.error(f"[FORWARDER] Не удалось скачать файл для платного поста: message_id={getattr(m, 'id', None)}, media_type={media_type}")
                                            continue
                                        temp_files.append(temp_file_path)
                                    media_list.append((media_type, file_id, temp_file_path))
                                elif m.document.mime_type.startswith('video/'):
                                    media_type = 'video'
                                    file_id = m.document.file_id
                                    temp_file_path = None
                                    if not is_bot_admin:
                                        import tempfile
                                        ext = '.mp4'
                                        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                                            temp_file_path = tmp.name
                                        temp_file_path = await self.userbot.download_media(m, file_name=temp_file_path)
                                        if not temp_file_path or not os.path.exists(temp_file_path):
                                            logger.error(f"[FORWARDER] Не удалось скачать файл для платного поста: message_id={getattr(m, 'id', None)}, media_type={media_type}")
                                            continue
                                        temp_files.append(temp_file_path)
                                    media_list.append((media_type, file_id, temp_file_path))
                                else:
                                    logger.warning(f"[FORWARDER] Документ {getattr(m, 'id', None)} с mime-type {m.document.mime_type} не поддерживается для платного контента")
                                    continue
                            else:
                                logger.warning(f"[FORWARDER] Сообщение {getattr(m, 'id', None)} не поддерживается для платного контента: media={getattr(m, 'media', None)}, type={type(m)}")
                                continue
                        # Добавлено: если нет поддерживаемых файлов, не отправлять платную медиагруппу
                        if not media_list:
                            logger.warning(f"[FORWARDER] В медиагруппе {group_id} нет поддерживаемых файлов для платного контента (photo/video), пропускаем платную отправку.")
                            return 0
                        if media_list:
                            try:
                                # Формируем объекты для отправки
                                if is_bot_admin:
                                    tg_media = [InputPaidMediaPhoto(media=fid) if mt == 'photo' else InputPaidMediaVideo(media=fid) for mt, fid, _ in media_list]
                                else:
                                    tg_media = [InputPaidMediaPhoto(media=open(tf, 'rb')) if mt == 'photo' else InputPaidMediaVideo(media=open(tf, 'rb')) for mt, fid, tf in media_list]
                                # Проверяем наличие HTML-разметки в caption
                                contains_html = "<a href=" in group_caption or "<b>" in group_caption or "<i>" in group_caption or "<code>" in group_caption
                                
                                result = await self.tg_bot.send_paid_media(
                                    chat_id=target_channel,
                                    star_count=paid_content_stars,
                                    media=tg_media,
                                    caption=group_caption,
                                    parse_mode=TgParseMode.HTML if contains_html else None
                                )
                                logger.info(f"[FORWARDER] ✅ Платная медиагруппа {group_id} отправлена через python-telegram-bot с {paid_content_stars} звездами")
                                if not is_bot_admin:
                                    for tf in temp_files:
                                        try:
                                            os.remove(tf)
                                            logger.info(f"[FORWARDER] Временный файл удалён: {tf}")
                                        except Exception as e:
                                            logger.warning(f"[FORWARDER] Не удалось удалить временный файл {tf}: {e}")
                            except Exception as e:
                                if "Invalid paid media file specified" in str(e):
                                    logger.warning(f"[FORWARDER][FALLBACK] Ошибка 'Invalid paid media file specified' при отправке платной медиагруппы {group_id}, отправляем как обычную через Pyrogram")
                                    paid_content_stars = 0
                                elif "Chat not found" in str(e) or "chat not found" in str(e):
                                    logger.warning(f"[FORWARDER] Ошибка отправки платного поста через python-telegram-bot: {e}, используем Pyrogram")
                                    group_message_ids = []
                                    paid_content_stars = 0
                                else:
                                    if not is_bot_admin:
                                        for tf in temp_files:
                                            try:
                                                os.remove(tf)
                                            except Exception:
                                                pass
                                    raise e
                        # Если платный контент не удался или был сброшен, отправляем через Pyrogram
                        if paid_content_stars == 0:
                            logger.info(f"[FORWARDER] 🔄 Отправляем медиагруппу через Pyrogram как обычный контент")
                            # Отправляем через Pyrogram как обычный контент
                            media_objs = []
                            for i, m in enumerate(group_msgs):
                                caption = group_caption if i == 0 and group_caption else None
                                caption_entities = getattr(m, 'caption_entities', None)
                                if m.photo:
                                    media_obj = InputMediaPhoto(
                                        media=m.photo.file_id,
                                        caption=caption
                                    )
                                elif m.video:
                                    media_obj = InputMediaVideo(
                                        media=m.video.file_id,
                                        caption=caption
                                    )
                                elif m.document:
                                    media_obj = InputMediaDocument(
                                        media=m.document.file_id,
                                        caption=caption
                                    )
                                elif m.audio:
                                    media_obj = InputMediaAudio(
                                        media=m.audio.file_id,
                                        caption=caption
                                    )
                                elif m.animation:
                                    media_obj = InputMediaAnimation(
                                        media=m.animation.file_id,
                                        caption=caption
                                    )
                                else:
                                    continue
                                media_objs.append(media_obj)
                                group_message_ids.append(m.id)
                            
                            if media_objs:
                                logger.info(f"[FORWARDER][DEBUG] media_objs для отправки: {[type(obj).__name__ + ':' + getattr(obj, 'media', 'NO_MEDIA') for obj in media_objs]}")
                                await self.userbot.send_media_group(
                                    chat_id=target_channel,
                                    media=media_objs
                                )
                            else:
                                logger.warning(f"[FORWARDER][SKIP] media_objs пустой, медиагруппа {group_id} не будет отправлена!")
                else:
                    logger.info(f"[FORWARDER] 🔄 Отправляем медиагруппу через Pyrogram как обычный контент (paid_content_stars={paid_content_stars}, tg_bot={self.tg_bot is not None})")
                    # Отправляем через Pyrogram как обычный контент
                    media_objs = []
                    for i, m in enumerate(group_msgs):
                        caption = group_caption if i == 0 and group_caption else None
                        caption_entities = getattr(m, 'caption_entities', None)
                        if m.photo:
                            media_obj = InputMediaPhoto(
                                media=m.photo.file_id,
                                caption=caption
                            )
                        elif m.video:
                            media_obj = InputMediaVideo(
                                media=m.video.file_id,
                                caption=caption
                            )
                        elif m.document:
                            media_obj = InputMediaDocument(
                                media=m.document.file_id,
                                caption=caption
                            )
                        elif m.audio:
                            media_obj = InputMediaAudio(
                                media=m.audio.file_id,
                                caption=caption
                            )
                        elif m.animation:
                            media_obj = InputMediaAnimation(
                                media=m.animation.file_id,
                                caption=caption
                            )
                        else:
                            continue
                        media_objs.append(media_obj)
                        group_message_ids.append(m.id)
                    
                    if media_objs:
                        logger.info(f"[FORWARDER][DEBUG] media_objs для отправки: {[type(obj).__name__ + ':' + getattr(obj, 'media', 'NO_MEDIA') for obj in media_objs]}")
                        await self.userbot.send_media_group(
                            chat_id=target_channel,
                            media=media_objs
                        )
                    else:
                        logger.warning(f"[FORWARDER][SKIP] media_objs пустой, медиагруппа {group_id} не будет отправлена!")
            
            paid_text = f" с платным контентом: {paid_content_stars} звездочек" if paid_content_stars > 0 else ""
            logger.info(f"[FORWARDER] Медиагруппа {group_id} с {len(group_msgs)} файлами переслана в {target_channel}{paid_text}")
            await self.db.save_media_group(channel_id, group_id, group_message_ids)
            for msg_id in group_message_ids:
                await self.db.mark_message_as_forwarded(channel_id, msg_id, target_channel)
            self._media_group_buffers[channel_id].pop(group_id, None)
            await self._save_to_posts_json(group_msgs, group_caption, channel_id)
            if callback:
                callback(1)
            return 1
        except Exception as e:
            if "FLOOD_WAIT" in str(e):
                import re
                wait_time = int(re.search(r'(\d+)', str(e)).group(1))
                logger.warning(f"[FORWARDER] FloodWait при пересылке медиагруппы {group_id}: ожидаем {wait_time} секунд")
                await asyncio.sleep(wait_time)
                return await self.forward_media_group(channel_id, group_id, target_channel, text_mode, add_footer, forward_mode, hide_sender, max_posts, callback, paid_content_stars, group_msgs, config)
            else:
                logger.error(f"[FORWARDER] Ошибка при пересылке медиагруппы {group_id}: {e}")
                logger.error(f"[FORWARDER] Полная ошибка: {traceback.format_exc()}")
                return 0

    async def start_forwarding_parsing(self, source_channel: str, target_channel: str, config: dict, callback: Optional[Callable] = None):
        """Запуск парсинга + пересылки (background task)"""
        task_id = self.create_parse_forward_task(source_channel, target_channel, config)
        task_info = self._parse_forward_tasks[task_id]
        
        # Создаем background task
        async def run_parse_forward():
            try:
                logger.info(f"[FORWARDER] 🚀 ЗАПУСК ПАРСИНГА + ПЕРЕСЫЛКИ (НЕ МОНИТОРИНГА!)")
                logger.info(f"[FORWARDER] Источник: {source_channel} -> Цель: {target_channel}")
                logger.info(f"[FORWARDER] Конфигурация: {config}")
                logger.info(f"[FORWARDER] 🔍 ПЛАТНЫЕ ЗВЕЗДЫ: {config.get('paid_content_stars', 0)} (тип: {type(config.get('paid_content_stars', 0))})")
                logger.info(f"[FORWARDER] 🔍 Все ключи конфигурации: {list(config.keys())}")
                
                # Получаем информацию о канале
                if str(source_channel).startswith("-100"):
                    channel = await self.userbot.get_chat(int(source_channel))
                else:
                    channel = await self.userbot.get_chat(source_channel)
                channel_id = channel.id
                channel_name = channel.username or str(channel_id)
                channel_title = getattr(channel, "title", None)
                
                logger.info(f"[FORWARDER] 📺 Канал: {channel_title} (@{channel_name}, ID: {channel_id})")
                
                # Отмечаем пересылку как активную
                self._forwarding_active[channel_id] = True
                
                # Инициализируем буферы для медиагрупп
                if channel_id not in self._media_group_buffers:
                    self._media_group_buffers[channel_id] = {}
                if channel_id not in self._media_group_timeouts:
                    self._media_group_timeouts[channel_id] = {}
                
                # Настройки пересылки
                hide_sender = config.get("hide_sender", True)
                add_footer = config.get("footer_text", "")
                max_posts = config.get("max_posts", 0)
                forward_mode = config.get("forward_mode", "copy")  # copy или forward
                parse_mode = config.get("parse_mode", "all")  # all или hashtags
                hashtag_filter = config.get("hashtag_filter", "")
                text_mode = config.get("text_mode", "hashtags_only")  # hashtags_only, as_is, no_text
                delay_seconds = config.get("delay_seconds", 0)
                paid_content_mode = config.get("paid_content_mode", "off")
                paid_content_hashtag = config.get("paid_content_hashtag")
                paid_content_chance = config.get("paid_content_chance")
                paid_content_stars = config.get("paid_content_stars", 0)
                
                # --- Новые настройки режимов парсинга ---
                parse_direction = config.get("parse_direction", "forward")  # "forward" или "backward"
                media_filter = config.get("media_filter", "all")  # "all" или "media_only"
                range_mode = config.get("range_mode", "all")  # "all" или "range"
                range_start_id = config.get("range_start_id")
                range_end_id = config.get("range_end_id")
                
                logger.info(f"[FORWARDER] ⚙️ Настройки: режим={parse_mode}, хэштег='{hashtag_filter}', лимит={max_posts}, задержка={delay_seconds}с, платные={paid_content_stars}⭐")
                logger.info(f"[FORWARDER] 🔍 Новые режимы: направление={parse_direction}, фильтр медиа={media_filter}, диапазон={range_mode}")
                if range_mode == "range":
                    logger.info(f"[FORWARDER] 🔍 Диапазон: с {range_start_id} по {range_end_id}")
                logger.info(f"[FORWARDER] 🔍 ПЛАТНЫЕ ЗВЕЗДЫ В НАСТРОЙКАХ: {paid_content_stars} (тип: {type(paid_content_stars)})")
                
                if not target_channel:
                    raise Exception("Не указан целевой канал для пересылки")
                
                logger.info(f"[FORWARDER] 🔍 Начинаем получение истории сообщений из канала {channel_name}...")
                
                forwarded_count = 0
                last_message_id = None
                
                # Сначала собираем все сообщения и группируем медиагруппы
                all_messages = []
                media_groups = {}
                try:
                    async for message in self.userbot.get_chat_history(channel_id, limit=1000):
                        try:
                            all_messages.append(message)
                            # Группируем сообщения по media_group_id
                            if getattr(message, 'media_group_id', None):
                                group_id = message.media_group_id
                                if group_id not in media_groups:
                                    media_groups[group_id] = []
                                media_groups[group_id].append(message)
                        except (ValueError, KeyError) as e:
                            if ("Peer id invalid" in str(e)) or ("ID not found" in str(e)):
                                logger.warning(f"[FORWARDER][SKIP] Сообщение пропущено из-за ошибки peer: {e}")
                                continue
                            else:
                                raise
                    logger.info(f"[FORWARDER] ✅ Собрано {len(all_messages)} сообщений, найдено {len(media_groups)} медиагрупп")
                    # Явно заполняем буфер медиагрупп ДО пересылки
                    if channel_id not in self._media_group_buffers:
                        self._media_group_buffers[channel_id] = {}
                    temp_media_groups = {str(group_id): msgs for group_id, msgs in media_groups.items()}
                    self._media_group_buffers[channel_id] = temp_media_groups
                    for group_id, msgs in temp_media_groups.items():
                        logger.info(f"[FORWARDER][DEBUG] Буфер для медиагруппы {group_id}: {len(msgs)} файлов")
                    logger.info(f"[FORWARDER][DEBUG] Буфер медиагрупп заполнен: {len(self._media_group_buffers[channel_id])} групп для канала {channel_id}")
                except Exception as e:
                    logger.error(f"[FORWARDER] Ошибка при сборе истории: {e}")
                    task_info["status"] = "error"
                    task_info["error"] = str(e)
                    task_info["completed_at"] = datetime.now().isoformat()
                    return
                
                # --- Новый буфер для медиагрупп (как в мониторинге) ---
                self.media_groups = {}
                for message in all_messages:
                    if getattr(message, 'media_group_id', None):
                        group_id = message.media_group_id
                        if group_id not in self.media_groups:
                            self.media_groups[group_id] = []
                        self.media_groups[group_id].append(message)
                # --- ВАЖНО: Заполняем буфер медиагрупп ДО пересылки ---
                self._media_group_buffers[channel_id] = {str(gid): msgs for gid, msgs in self.media_groups.items()}
                logger.info(f"[FORWARDER] Буфер медиагрупп заполнен: {len(self._media_group_buffers[channel_id])} групп для канала {channel_id}")
                # --- Применяем новые фильтры и сортировку ---
                
                # 1. Фильтрация по диапазону ID
                if range_mode == "range" and range_start_id and range_end_id:
                    all_messages = [msg for msg in all_messages if range_start_id <= msg.id <= range_end_id]
                    logger.info(f"[FORWARDER] 🔍 После фильтрации по диапазону: {len(all_messages)} сообщений")
                
                # 2. Фильтрация по медиа
                if media_filter == "media_only":
                    all_messages = [msg for msg in all_messages if msg.media is not None]
                    logger.info(f"[FORWARDER] 🔍 После фильтрации по медиа: {len(all_messages)} сообщений")
                
                # 3. Сортировка по направлению
                if parse_direction == "backward":
                    # От новых к старым (по умолчанию)
                    all_messages.sort(key=lambda x: x.date, reverse=True)
                    logger.info(f"[FORWARDER] 🔍 Сортировка: от новых к старым")
                elif parse_direction == "forward":
                    # От старых к новым
                    all_messages.sort(key=lambda x: x.date)
                    logger.info(f"[FORWARDER] 🔍 Сортировка: от старых к новым")
                
                logger.info(f"[FORWARDER] 🚀 Начинаем пересылку сообщений (направление: {parse_direction}, фильтр: {media_filter}, диапазон: {range_mode})...")
                print(f'=== [DEBUG] Начинаем цикл по сообщениям (направление: {parse_direction}, фильтр: {media_filter}) ===')
                # --- ОБРАБАТЫВАЕМ ВСЕ СООБЩЕНИЯ В ХРОНОЛОГИЧЕСКОМ ПОРЯДКЕ ---
                processed_groups = set()
                hashtag_paid_counter = 0
                select_paid_counter = 0
                media_group_paid_counter = 0
                self._parsing_group_hashtag_paid_counter = 0
                
                # Обрабатываем все сообщения в хронологическом порядке
                for message in all_messages:
                    try:
                        # Проверяем лимит
                        if max_posts and max_posts > 0 and forwarded_count >= max_posts:
                            logger.info(f"[FORWARDER] Достигнут лимит пересылок ({max_posts}), останавливаем парсинг")
                            break
                        
                        # Проверяем, не пересылали ли уже это сообщение
                        try:
                            is_forwarded = await self.db.is_message_forwarded(message.chat.id, message.id, target_channel)
                        except (ValueError, KeyError) as e:
                            if ("Peer id invalid" in str(e)) or ("ID not found" in str(e)):
                                logger.warning(f"[FORWARDER][SKIP] Сообщение {getattr(message, 'id', None)} пропущено из-за ошибки peer: {e}")
                                continue
                            else:
                                raise
                        if is_forwarded:
                            logger.info(f"[FORWARDER] Сообщение {message.id} уже переслано, пропускаем")
                            continue
                        
                        # --- ДОБАВЛЕНО: фильтр одиночных сообщений по media_only ---
                        if media_filter == "media_only" and not getattr(message, 'media', None):
                            logger.info(f"[FORWARDER] Сообщение {getattr(message, 'id', None)} без медиа, пропускаем (media_only, мониторинг)")
                            return
                        
                        # --- ОБРАБОТКА МЕДИАГРУПП ---
                        if getattr(message, 'media_group_id', None):
                            group_id = message.media_group_id
                            
                            # Если медиагруппа уже обработана, пропускаем
                            if group_id in processed_groups:
                                continue
                            
                            # Получаем все сообщения медиагруппы
                            group_msgs = media_groups.get(group_id, [])
                            if not group_msgs:
                                logger.warning(f"[FORWARDER] Медиагруппа {group_id} не найдена в буфере, пропускаем")
                                continue
                            
                            print(f'=== [DEBUG] Обработка медиагруппы {group_id}, файлов: {len(group_msgs)} ===')
                            
                            # --- Фильтрация по хэштегу ---
                            if parse_mode == "hashtags" and hashtag_filter and hashtag_filter.strip():
                                if not self.group_has_hashtag(group_msgs, hashtag_filter):
                                    logger.info(f"[FORWARDER] Медиагруппа {group_id} не содержит хэштег '{hashtag_filter}', пропускаем всю группу")
                                    processed_groups.add(group_id)
                                    continue
                            
                            # --- Определяем платность медиагруппы ---
                            group_is_paid = False
                            if paid_content_mode == "off" or not paid_content_mode:
                                group_is_paid = False
                            elif paid_content_mode == "hashtag":
                                for m in group_msgs:
                                    t = (m.text or m.caption or "").lower()
                                    if paid_content_hashtag and paid_content_hashtag.lower() in t:
                                        group_is_paid = True
                                        break
                            elif paid_content_mode == "random":
                                import random
                                if paid_content_chance and random.randint(1, 10) <= int(paid_content_chance):
                                    group_is_paid = True
                            elif paid_content_mode == "hashtag_random":
                                import random
                                for m in group_msgs:
                                    t = (m.text or m.caption or "").lower()
                                    if paid_content_hashtag and paid_content_hashtag.lower() in t:
                                        if paid_content_chance and random.randint(1, 10) <= int(paid_content_chance):
                                            group_is_paid = True
                                            break
                            elif paid_content_mode == "select":
                                media_group_paid_counter += 1
                                every = config.get('paid_content_every', 1)
                                try:
                                    every = int(every)
                                except Exception:
                                    every = 1
                                if every > 0 and (media_group_paid_counter % every == 0):
                                    group_is_paid = True
                            elif paid_content_mode == "hashtag_select":
                                group_hashtag = False
                                for m in group_msgs:
                                    t = (m.text or m.caption or "").lower()
                                    if paid_content_hashtag and paid_content_hashtag.lower() in t:
                                        group_hashtag = True
                                        break
                                if group_hashtag:
                                    self._parsing_group_hashtag_paid_counter += 1
                                    every = config.get('paid_content_every', 1)
                                    try:
                                        every = int(every)
                                    except Exception:
                                        every = 1
                                    if every > 0 and (self._parsing_group_hashtag_paid_counter % every == 0):
                                        group_is_paid = True
                            else:
                                group_is_paid = False
                            
                            logger.info(f"[FORWARDER] Обрабатываем медиагруппу {group_id} с {len(group_msgs)} файлами, платная: {group_is_paid}")
                            
                            # Пересылаем медиагруппу
                            try:
                                await self.forward_media_group(
                                    channel_id,
                                    group_id,
                                    target_channel,
                                    text_mode,
                                    add_footer,
                                    forward_mode,
                                    hide_sender,
                                    max_posts,
                                    callback,
                                    paid_content_stars if group_is_paid else 0,
                                    group_msgs,
                                    config
                                )
                                forwarded_count += 1
                                logger.info(f"[FORWARDER] Медиагруппа {group_id} с {len(group_msgs)} файлами переслана в {target_channel}")
                            except Exception as e:
                                logger.error(f"[FORWARDER] Ошибка при пересылке медиагруппы {group_id}: {e}")
                                logger.error(f"[FORWARDER] Полная ошибка: {traceback.format_exc()}")
                            
                            processed_groups.add(group_id)
                            
                        else:
                            # --- ОБРАБОТКА ОДИНОЧНЫХ СООБЩЕНИЙ ---
                            # --- Фильтрация по хэштегу ---
                            if parse_mode == "hashtags" and hashtag_filter and hashtag_filter.strip():
                                text = (message.text or message.caption or "").lower()
                                if hashtag_filter.lower() not in text:
                                    logger.info(f"[FORWARDER] Сообщение {message.id} не содержит хэштег '{hashtag_filter}', пропускаем")
                                    continue
                            
                            # --- Определяем платность одиночного сообщения ---
                            is_paid = False
                            text = (message.text or message.caption or "").lower()
                            if paid_content_mode == "off" or not paid_content_mode:
                                is_paid = False
                            elif paid_content_mode == "hashtag":
                                if paid_content_hashtag and paid_content_hashtag.lower() in text:
                                    is_paid = True
                            elif paid_content_mode == "random":
                                import random
                                if paid_content_chance and random.randint(1, 10) <= int(paid_content_chance):
                                    is_paid = True
                            elif paid_content_mode == "hashtag_random":
                                import random
                                if paid_content_hashtag and paid_content_hashtag.lower() in text:
                                    if paid_content_chance and random.randint(1, 10) <= int(paid_content_chance):
                                        is_paid = True
                            elif paid_content_mode == "select":
                                select_paid_counter += 1
                                every = config.get('paid_content_every', 1)
                                try:
                                    every = int(every)
                                except Exception:
                                    every = 1
                                if every > 0 and (select_paid_counter % every == 0):
                                    is_paid = True
                            elif paid_content_mode == "hashtag_select":
                                if paid_content_hashtag and paid_content_hashtag.lower() in text:
                                    hashtag_paid_counter += 1
                                    every = config.get('paid_content_every', 1)
                                    try:
                                        every = int(every)
                                    except Exception:
                                        every = 1
                                    if every > 0 and (hashtag_paid_counter % every == 0):
                                        is_paid = True
                            else:
                                is_paid = False
                            
                            # Пересылаем одиночное сообщение
                            try:
                                await self._forward_single_message(
                                    message,
                                    target_channel,
                                    hide_sender,
                                    add_footer,
                                    forward_mode,
                                    text_mode,
                                    paid_content_stars if is_paid else 0
                                )
                                forwarded_count += 1
                                logger.info(f"[FORWARDER] Одиночное сообщение {message.id} переслано в {target_channel}")
                            except Exception as e:
                                logger.error(f"[FORWARDER] Ошибка при пересылке одиночного сообщения {message.id}: {e}")
                                logger.error(f"[FORWARDER] Полная ошибка: {traceback.format_exc()}")
                            
                            if delay_seconds and delay_seconds > 0:
                                await asyncio.sleep(delay_seconds)
                        
                        last_message_id = message.id
                        
                    except Exception as e:
                        logger.error(f"[FORWARDER] Ошибка при обработке сообщения {message.id}: {e}")
                        logger.error(f"[FORWARDER] Полная ошибка: {traceback.format_exc()}")
                
                # Завершаем задачу
                task_info["status"] = "completed"
                task_info["completed_at"] = datetime.now().isoformat()
                logger.info(f"[FORWARDER] ✅ Парсинг+пересылка завершены. Переслано {forwarded_count} сообщений.")
                
            except Exception as e:
                logger.error(f"[FORWARDER] Критическая ошибка в парсинг+пересылке: {e}")
                logger.error(f"[FORWARDER] Полная ошибка: {traceback.format_exc()}")
                task_info["status"] = "error"
                task_info["error"] = str(e)
                task_info["completed_at"] = datetime.now().isoformat()
        
        # Создаем и запускаем background task
        task = asyncio.create_task(run_parse_forward())
        task_info["task"] = task
        
        return task_id

    async def get_forwarding_config(self, user_id: int, source_channel_id: int) -> dict:
        """Получение конфигурации пересылки из базы данных"""
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
            from models import ForwardingConfig
            
            engine = create_engine('sqlite:///parser.db')
            SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
            db = SessionLocal()
            
            try:
                config = db.query(ForwardingConfig).filter_by(
                    user_id=user_id,
                    source_channel_id=source_channel_id
                ).first()
                
                if config:
                    return {
                        'hide_sender': config.hide_sender,
                        'footer_text': config.footer_text,
                        'max_posts': config.max_posts,
                        'forward_mode': 'copy',  # По умолчанию
                        'parse_mode': config.parse_mode,
                        'hashtag_filter': config.hashtag_filter,
                        'text_mode': config.text_mode,
                        'delay_seconds': config.delay_seconds,
                        'paid_content_stars': config.paid_content_stars
                    }
                else:
                    # Возвращаем конфигурацию по умолчанию
                    return {
                        'hide_sender': True,
                        'footer_text': '',
                        'max_posts': 0,
                        'forward_mode': 'copy',
                        'parse_mode': 'all',
                        'hashtag_filter': '',
                        'text_mode': 'hashtags_only',
                        'delay_seconds': 0,
                        'paid_content_stars': 0
                    }
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"[FORWARDER] Ошибка получения конфигурации: {e}")
            # Возвращаем конфигурацию по умолчанию
            return {
                'hide_sender': True,
                'footer_text': '',
                'max_posts': 0,
                'forward_mode': 'copy',
                'parse_mode': 'all',
                'hashtag_filter': '',
                'text_mode': 'hashtags_only',
                'delay_seconds': 0,
                'paid_content_stars': 0
            }

    def _should_use_parse_mode(self, entities):
        """Возвращает True, если среди entities есть форматирующие сущности, иначе False"""
        if not entities or len(entities) == 0:
            return False
        allowed_types = {
            'MessageEntityType.BOLD', 'MessageEntityType.ITALIC', 'MessageEntityType.URL', 'MessageEntityType.TEXT_LINK',
            'MessageEntityType.PRE', 'MessageEntityType.CODE', 'MessageEntityType.UNDERLINE', 'MessageEntityType.STRIKETHROUGH', 'MessageEntityType.SPOILER',
            'bold', 'italic', 'url', 'text_link', 'pre', 'code', 'underline', 'strikethrough', 'spoiler'
        }
        for e in entities:
            t = getattr(e, 'type', None)
            if t and str(t) in allowed_types:
                return True
        return False

    def group_has_hashtag(self, group_messages, hashtag):
        """
        Проверяет, есть ли в медиагруппе хотя бы одно сообщение с нужным хэштегом (в caption, text, entities, caption_entities).
        Если есть — возвращает True (группа подлежит пересылке целиком).
        """
        hashtag_lower = hashtag.lower()
        for m in group_messages:
            t = (getattr(m, 'caption', None) or getattr(m, 'text', None) or "").lower()
            if hashtag_lower in t:
                return True
            # Проверяем entities и caption_entities
            for ent in list(getattr(m, 'entities', []) or []) + list(getattr(m, 'caption_entities', []) or []):
                if getattr(ent, 'type', None) in ('hashtag', 'MessageEntityType.HASHTAG'):
                    value = None
                    if hasattr(m, 'text') and m.text and ent.offset + ent.length <= len(m.text):
                        value = m.text[ent.offset:ent.offset+ent.length]
                    elif hasattr(m, 'caption') and m.caption and ent.offset + ent.length <= len(m.caption):
                        value = m.caption[ent.offset:ent.offset+ent.length]
                    if value and hashtag_lower in value.lower():
                        return True
        return False

    async def _is_bot_admin(self, channel_id):
        """Проверяет, является ли бот админом в канале через Bot API"""
        if not self.tg_bot:
            return False
        try:
            admins = await self.tg_bot.get_chat_administrators(channel_id)
            me = await self.tg_bot.get_me()
            for admin in admins:
                if admin.user.id == me.id:
                    return True
            return False
        except Exception as e:
            logger.warning(f"[FORWARDER] Не удалось проверить админство бота в канале {channel_id}: {e}")
            return False

    def get_all_monitoring_status(self):
        """Возвращает список всех активных мониторингов с полным config каждого."""
        result = []
        for (channel_id, target_channel_id), task in self._monitoring_tasks.items():
            active = self._forwarding_active.get(channel_id, False)
            config = self._forwarding_settings.get(channel_id, {})
            target_channel = self._monitoring_targets.get((channel_id, target_channel_id))
            result.append({
                "channel_id": channel_id,
                "active": active,
                "config": config,
                "task_running": task is not None and not task.done(),
                "target_channel": target_channel_id
            })
        return result

    def _generate_task_id(self) -> str:
        """Генерирует уникальный task_id для задачи парсинг+пересылки."""
        self._task_counter += 1
        return f"parse_forward_{self._task_counter}_{int(asyncio.get_event_loop().time())}"

    def create_parse_forward_task(self, source_channel: str, target_channel: str, config: dict) -> str:
        """Создает новую задачу парсинг+пересылки и возвращает task_id."""
        task_id = self._generate_task_id()
        task_info = {
            "task_id": task_id,
            "source_channel": source_channel,
            "target_channel": target_channel,
            "config": config,
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "error": None,
            "task": None  # Будет установлен при запуске
        }
        self._parse_forward_tasks[task_id] = task_info
        return task_id

    def get_parse_forward_task_status(self, task_id: str) -> dict:
        """Возвращает статус задачи парсинг+пересылки по task_id."""
        task_info = self._parse_forward_tasks.get(task_id)
        if not task_info:
            return {"error": "Task not found"}
        
        # Проверяем, завершился ли task
        if task_info["task"] and task_info["task"].done():
            if task_info["status"] == "running":
                task_info["status"] = "completed"
                task_info["completed_at"] = datetime.now().isoformat()
        
        return {
            "task_id": task_id,
            "source_channel": task_info["source_channel"],
            "target_channel": task_info["target_channel"],
            "status": task_info["status"],
            "started_at": task_info["started_at"],
            "completed_at": task_info["completed_at"],
            "error": task_info["error"]
        }

    def stop_parse_forward_task(self, task_id: str) -> bool:
        """Останавливает задачу парсинг+пересылки по task_id."""
        task_info = self._parse_forward_tasks.get(task_id)
        if not task_info:
            return False
        
        if task_info["status"] == "running" and task_info["task"]:
            task_info["task"].cancel()
            task_info["status"] = "stopped"
            task_info["completed_at"] = datetime.now().isoformat()
            return True
        return False

    def get_all_parse_forward_tasks(self) -> list:
        """Возвращает список всех задач парсинг+пересылки."""
        result = []
        for task_id, task_info in self._parse_forward_tasks.items():
            # Проверяем, завершился ли task
            if task_info["task"] and task_info["task"].done():
                if task_info["status"] == "running":
                    task_info["status"] = "completed"
                    task_info["completed_at"] = datetime.now().isoformat()
            
            result.append({
                "task_id": task_id,
                "source_channel": task_info["source_channel"],
                "target_channel": task_info["target_channel"],
                "status": task_info["status"],
                "started_at": task_info["started_at"],
                "completed_at": task_info["completed_at"],
                "error": task_info["error"]
            })
        return result

    def _update_source_handler(self, channel_id):
        # Удалить старый handler, если есть
        if channel_id in self._handlers:
            self.userbot.remove_handler(self._handlers[channel_id])
            del self._handlers[channel_id]
        # Найти все target_channel для этого source_channel
        targets = [tgt_id for (src_id, tgt_id) in self._monitoring_tasks.keys() if src_id == channel_id]
        if not targets:
            return  # Нет активных мониторингов — handler не нужен
        @self.userbot.on_message(filters.chat(channel_id))
        async def handle_new_message(client, message):
            logger.info(f"[FORWARDER][HANDLER] Вызван handler для channel_id={channel_id}, message_id={getattr(message, 'id', None)}")
            # --- Медиагруппы ---
            if getattr(message, 'media_group_id', None):
                group_id = str(message.media_group_id)
                if group_id not in self.media_groups:
                    self.media_groups[group_id] = []
                self.media_groups[group_id].append(message)
                logger.info(f"[DEBUG] Добавлено сообщение {message.id} в медиагруппу {group_id}, теперь файлов: {len(self.media_groups[group_id])}")
                if group_id not in self.media_group_timeouts:
                    async def send_group_later():
                        await asyncio.sleep(2.5)
                        group_messages = self.media_groups.get(group_id, [])
                        logger.info(f"[DEBUG] Перед отправкой медиагруппы {group_id}: {len(group_messages)} файлов")
                        # --- Определяем платность медиагруппы ОДИН РАЗ ---
                        config = self._forwarding_settings.get(channel_id, {})
                        paid_content_mode = config.get('paid_content_mode', 'off')
                        paid_content_every = config.get('paid_content_every', 1)
                        paid_content_stars = config.get('paid_content_stars', 0)
                        group_is_paid = False
                        if paid_content_mode == "select":
                            counters = self._counters[channel_id]
                            counters['media_group_paid_counter'] += 1
                            every = paid_content_every
                            try:
                                every = int(every)
                            except Exception:
                                every = 1
                            if every > 0 and (counters['media_group_paid_counter'] % every == 0):
                                group_is_paid = True
                        # --- Отправка медиагруппы во все target_channel ---
                        for (src_id2, tgt_id2), task2 in self._monitoring_tasks.items():
                            if src_id2 == channel_id:
                                try:
                                    await self.forward_media_group(
                                        channel_id,
                                        group_id,
                                        tgt_id2,
                                        config.get('text_mode', 'hashtags_only'),
                                        config.get('footer_text', ''),
                                        config.get('forward_mode', 'copy'),
                                        config.get('hide_sender', True),
                                        config.get('max_posts', 0),
                                        None,
                                        paid_content_stars if group_is_paid else 0,
                                        group_messages
                                    )
                                    logger.info(f"[FORWARDER][HANDLER] Медиагруппа {group_id} успешно переслана в {tgt_id2}")
                                except Exception as e:
                                    logger.error(f"[FORWARDER][HANDLER] Ошибка при пересылке медиагруппы {group_id} в {tgt_id2}: {e}")
                        self.media_groups.pop(group_id, None)
                        self.media_group_timeouts.pop(group_id, None)
                    self.media_group_timeouts[group_id] = asyncio.create_task(send_group_later())
                return  # Не обрабатывать как одиночное сообщение
            # --- Одиночные сообщения ---
            for (src_id, tgt_id), task in self._monitoring_tasks.items():
                if src_id == channel_id:
                    config = self._forwarding_settings.get(channel_id, {})
                    hide_sender = config.get("hide_sender", True)
                    add_footer = config.get("footer_text", "")
                    max_posts = config.get("max_posts", 0)
                    forward_mode = config.get("forward_mode", "copy")
                    parse_mode = config.get("parse_mode", "all")
                    hashtag_filter = config.get("hashtag_filter", "")
                    text_mode = config.get("text_mode", "hashtags_only")
                    delay_seconds = config.get("delay_seconds", 0)
                    paid_content_stars = config.get("paid_content_stars", 0)
                    # --- Одиночные paid_content_mode == 'select' ---
                    is_paid = False
                    if config.get('paid_content_mode') == 'select':
                        counters = self._counters[channel_id]
                        if 'single_paid_counter' not in counters:
                            counters['single_paid_counter'] = 0
                        counters['single_paid_counter'] += 1
                        every = config.get('paid_content_every', 1)
                        try:
                            every = int(every)
                        except Exception:
                            every = 1
                        if every > 0 and (counters['single_paid_counter'] % every == 0):
                            is_paid = True
                    try:
                        await self._forward_single_message(
                            message,
                            tgt_id,
                            hide_sender,
                            add_footer,
                            forward_mode,
                            text_mode,
                            paid_content_stars if is_paid else 0
                        )
                        logger.info(f"[FORWARDER][HANDLER] Успешно переслано сообщение {getattr(message, 'id', None)} в {tgt_id}")
                    except Exception as e:
                        logger.error(f"[FORWARDER][HANDLER] Ошибка при пересылке в {tgt_id}: {e}")
        self._handlers[channel_id] = handle_new_message

