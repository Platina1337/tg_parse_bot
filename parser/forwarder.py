from pyrogram import Client
from pyrogram.errors import FloodWait
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
from parser.session_manager import SessionManager
from parser.config import config
import random
from pyrogram.raw.functions.messages import GetMessagesViews
from parser.watermark_processor import watermark_processor

# Импорты для python-telegram-bot
try:
    from telegram import Bot as TgBot, InputPaidMediaPhoto, InputPaidMediaVideo, InputFile
    from telegram.constants import ParseMode as TgParseMode
    from telegram.error import TelegramError, TimedOut, NetworkError, BadRequest
    import telegram
    TG_BOT_AVAILABLE = True
except ImportError:
    TG_BOT_AVAILABLE = False
    logging.warning("python-telegram-bot не установлен. Платные посты будут недоступны.")

logger = logging.getLogger(__name__)

async def ensure_peer_resolved(userbot, bot, channel_id, username=None):
    """
    Убедиться, что peer разрешён в userbot сессии.
    Если peer не разрешён по ID, попытаться разрешить через username.
    
    Args:
        userbot: Pyrogram userbot клиент
        bot: Pyrogram bot клиент (для получения username через Bot API)
        channel_id: ID канала
        username: Username канала (если известен)
    
    Returns:
        chat: Объект чата или None если не удалось разрешить
    """
    try:
        # 1. Пробуем получить чат по ID
        chat = await userbot.get_chat(int(channel_id))
        return chat
    except ValueError as e:
        if "Peer id invalid" not in str(e):
            raise
        
        # 2. Если peer не разрешён, пытаемся получить username
        if not username:
            try:
                # Пробуем получить username через Bot API
                chat_info = await bot.get_chat(int(channel_id))
                username = chat_info.username
            except Exception as e2:
                logger.warning(f"Не удалось получить username для {channel_id}: {e2}")
                return None
        
        if username:
            try:
                # 3. Пробуем разрешить peer через username
                await userbot.get_chat(username)
                # Теперь повторяем попытку по ID
                chat = await userbot.get_chat(int(channel_id))
                logger.info(f"Peer {channel_id} успешно разрешён через username @{username}")
                return chat
            except Exception as e3:
                logger.error(f"Не удалось разрешить peer через username @{username}: {e3}")
                return None
        else:
            logger.error(f"Не удалось найти username для channel_id {channel_id}")
            return None

class TelegramForwarder:
    """Класс для пересылки сообщений без скачивания"""
    
    def __init__(self, db_instance, userbot=None, bot_token=None, session_manager=None, reaction_manager=None):
        logger.info(f"[FORWARDER] 🔍 Инициализация TelegramForwarder")
        self.session_manager = session_manager
        self.reaction_manager = reaction_manager
        self._userbot = userbot
        self.watermark_processor = watermark_processor
        if userbot:
            logger.info(f"[FORWARDER] userbot передан явно: {self._userbot}")
        elif self.session_manager:
            self._userbot = None  # Will be initialized lazily
            logger.info(f"[FORWARDER] session_manager передан, userbot будет инициализирован позже")
        else:
            session_name = os.path.join(config.SESSIONS_DIR, "userbot")
            logger.info(f"[FORWARDER] userbot будет создан: {session_name}")
            self._userbot = Client(
                name=session_name,
                api_id=os.getenv("API_ID"),
                api_hash=os.getenv("API_HASH")
            )
        if self._userbot is None and not self.session_manager:
            logger.error(f"[FORWARDER] ВНИМАНИЕ: self._userbot остался None после конструктора!")
        elif self._userbot is None and self.session_manager:
            logger.info(f"[FORWARDER] self._userbot будет инициализирован через session_manager при необходимости")
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
    
    def _is_post_paid(self, config, is_media_group, messages, counters):
        paid_content_mode = config.get("paid_content_mode", "off")
        paid_content_hashtag = config.get("paid_content_hashtag")
        paid_content_chance = config.get("paid_content_chance", 100)

        is_paid = False

        if paid_content_mode == "hashtag" and paid_content_hashtag:
            text_to_check = ""
            if is_media_group:
                for msg in messages:
                    text_to_check += (getattr(msg, 'caption', "") or "") + " "
            else:
                msg = messages[0]
                text_to_check = (getattr(msg, 'text', "") or "") + " " + (getattr(msg, 'caption', "") or "")

            if paid_content_hashtag in text_to_check:
                is_paid = True

        elif paid_content_mode == "random":
            if random.random() < (paid_content_chance / 100):
                is_paid = True

        return is_paid, counters

    async def get_userbot(self, task: str = "parsing"):
        if self.session_manager:
            sessions = await self.session_manager.get_sessions_for_task(task)
            if sessions:
                client = await self.session_manager.get_client(sessions[0].alias)
                if client:
                    logger.info(f"[FORWARDER][get_userbot] Использую сессию {sessions[0].alias} для задачи {task}, session_file: {getattr(client, 'name', None)}")
                    return client
            
            # Fallback: если нет сессий для конкретной задачи, используем любую доступную
            logger.warning(f"[FORWARDER][get_userbot] Нет сессий для задачи '{task}', пробуем найти любую доступную сессию")
            all_sessions = await self.session_manager.get_all_sessions()
            if all_sessions:
                for session in all_sessions:
                    if session.is_active:
                        client = await self.session_manager.get_client(session.alias)
                        if client:
                            logger.info(f"[FORWARDER][get_userbot] Использую fallback сессию {session.alias} для задачи {task}, session_file: {getattr(client, 'name', None)}")
                            return client
            
            logger.error(f"[FORWARDER][get_userbot] Не удалось получить userbot для задачи {task} - нет доступных сессий")
            return None
        logger.error(f"[FORWARDER][get_userbot] Не удалось получить userbot для задачи {task} - нет session_manager")
        return None

    async def get_userbot_with_fallback(self, task: str = "parsing", current_session_alias: str = None):
        """
        Получить userbot с поддержкой переключения на другую сессию при FloodWait.

        Args:
            task: Задача для которой нужен userbot
            current_session_alias: Текущая сессия (для переключения)

        Returns:
            tuple: (userbot_client, session_alias) или (None, None)
        """
        if not self.session_manager:
            logger.error(f"[FORWARDER][get_userbot_with_fallback] Нет session_manager")
            return None, None

        # Получаем следующую доступную сессию
        client = await self.session_manager.get_next_parsing_session(current_session_alias)

        if client:
            session_alias = getattr(client, 'name', 'unknown')
            if hasattr(client, 'name') and client.name:
                # Извлекаем alias из пути сессии
                session_alias = os.path.basename(client.name).replace('.session', '')

            logger.info(f"[FORWARDER][get_userbot_with_fallback] Получен userbot для задачи {task}, сессия: {session_alias}")
            return client, session_alias

        logger.error(f"[FORWARDER][get_userbot_with_fallback] Не удалось получить userbot для задачи {task}")
        return None, None

    async def start(self):
        logger.info(f"[FORWARDER] Вход в start(). self._userbot: {self._userbot}")
        self._userbot = await self.get_userbot(task="forwarding")
        if not hasattr(self._userbot, 'is_connected') or not self._userbot.is_connected:
            session_file = os.path.join(config.SESSIONS_DIR, "userbot.session")
            logger.info(f"[FORWARDER] Проверка наличия session-файла: {session_file}")
            if not os.path.exists(session_file):
                logger.info(f"[FORWARDER] Session-файл не найден, будет создан новый при авторизации.")
            await self._userbot.start()
            try:
                me = await self._userbot.get_me()
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
            if hasattr(self._userbot, 'is_connected') and self._userbot.is_connected:
                await self._userbot.stop()
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
            logger.info(f"[FORWARDER] Начинаем start_forwarding для {source_channel} -> {target_channel}")
            
            # Проверяем доступные сессии
            if self.session_manager:
                all_sessions = await self.session_manager.get_all_sessions()
                logger.info(f"[FORWARDER] Всего доступных сессий: {len(all_sessions)}")
                for session in all_sessions:
                    logger.info(f"[FORWARDER] Сессия: {session.alias}, активна: {session.is_active}")
                
                monitoring_sessions = await self.session_manager.get_sessions_for_task("monitoring")
                logger.info(f"[FORWARDER] Сессий для мониторинга: {len(monitoring_sessions)}")
                for session in monitoring_sessions:
                    logger.info(f"[FORWARDER] Сессия мониторинга: {session.alias}")
            
            userbot = await self.get_userbot(task="monitoring")
            # Проверяем, что userbot получен успешно
            if userbot is None:
                logger.error(f"[FORWARDER] Не удалось получить userbot для мониторинга")
                raise Exception("Не удалось получить userbot для мониторинга")
                
            # Устанавливаем self._userbot для использования в _update_source_handler
            self._userbot = userbot
            sessions = await self.session_manager.get_sessions_for_task("monitoring") if self.session_manager else []
            
            # Получаем alias для логирования
            if sessions:
                alias = sessions[0].alias
            else:
                # Fallback: пытаемся получить alias из userbot или используем имя файла
                if hasattr(userbot, 'name') and userbot.name:
                    alias = os.path.basename(userbot.name)
                else:
                    alias = 'unknown'
                    
            logger.info(f"[FORWARDER][MONITORING] Используется сессионный файл: {getattr(userbot, 'name', None)}, alias: {alias}, is_connected: {getattr(userbot, 'is_connected', None)}")
            if not hasattr(userbot, 'is_connected') or not userbot.is_connected:
                logger.info(f"[FORWARDER] Userbot не запущен, запускаем...")
                await userbot.start()
                logger.info(f"[FORWARDER] Userbot успешно запущен")
            if str(source_channel).startswith("-100"):
                # Получаем username из конфигурации или состояния
                username = config.get('source_channel_username') if config else None
                
                # Пытаемся разрешить peer с помощью ensure_peer_resolved
                channel = await ensure_peer_resolved(userbot, self.tg_bot, int(source_channel), username)
                if channel is None:
                    raise Exception(f"Не удалось разрешить peer для канала {source_channel}")
            else:
                channel = await userbot.get_chat(source_channel)
            
            # Также проверяем целевой канал
            target_username = config.get('target_channel_username') if config else None
            if str(target_channel).startswith("-100") and target_username:
                try:
                    # Пытаемся разрешить целевой канал через username
                    await ensure_peer_resolved(userbot, self.tg_bot, int(target_channel), target_username)
                except Exception as e:
                    logger.warning(f"Не удалось разрешить целевой канал {target_channel} через username {target_username}: {e}")
            channel_id = channel.id
            key = (channel_id, str(target_channel))
            # --- Для поддержки нескольких каналов НЕ останавливаем другие мониторинги для того же source_channel ---
            # (логика остановки других target_channels убрана)
            # Если уже есть monitoring для этой пары, обновляем настройки
            if key in self._monitoring_tasks:
                logger.info(f"[FORWARDER] Monitoring для {channel_id} -> {target_channel} уже существует, обновляем настройки")
                # Обновляем настройки для существующего мониторинга
                self._forwarding_settings[channel_id] = config.copy()
                logger.info(f"[FORWARDER] Настройки мониторинга обновлены: {config}")
                return
            channel_name = channel.username or str(channel_id)
            channel_title = getattr(channel, "title", None)
            logger.info(f"[FORWARDER] 📺 Получен объект канала: {channel_title} (@{channel_name}, ID: {channel_id})")
            logger.info(f"[FORWARDER] 🔄 ЗАПУСК МОНИТОРИНГА (НЕ ПАРСИНГА!)")
            logger.info(f"[FORWARDER] Источник: {source_channel} -> Цель: {target_channel}")
            logger.info(f"[FORWARDER] Конфигурация: {config}")
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
                    'forwarded_count': 0,
                    'hashtag_paid_counter': 0,
                    'select_paid_counter': 0,
                    'media_group_paid_counter': 0,
                    'media_group_hashtag_paid_counter': 0
                }
            self._monitoring_targets[key] = target_channel
            self._monitoring_tasks[key] = asyncio.create_task(self._monitoring_loop())
            self._update_source_handler(channel_id)
            # Настройки пересылки
            hide_sender = config.get("hide_sender", True)
            add_footer = config.get("footer_text", "")
            max_posts = config.get("max_posts", 0)
            forward_mode = config.get("forward_mode", "copy")
            parse_mode = config.get("parse_mode", "all")
            hashtag_filter = config.get("hashtag_filter", "")
            text_mode = config.get("text_mode", "hashtags_only")
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
            self._forwarding_active[channel_id] = True
            if channel_id not in self._media_group_buffers:
                self._media_group_buffers[channel_id] = {}
            if channel_id not in self._media_group_timeouts:
                self._media_group_timeouts[channel_id] = {}
            processed_groups = set()
            media_groups = self._media_group_buffers[channel_id]
            logger.info(f"[FORWARDER] 🔄 Мониторинг запущен для канала {channel_name} -> {target_channel}")

            # Используем общий счетчик для всех target_channels одного source_channel
            counters = self._counters[channel_id]
            forwarded_count = counters.get('forwarded_count', 0)
            select_paid_counter = counters.get('select_paid_counter', 0)
            hashtag_paid_counter = counters.get('hashtag_paid_counter', 0)
            media_group_paid_counter = counters.get('media_group_paid_counter', 0)
            media_group_hashtag_paid_counter = counters.get('media_group_hashtag_paid_counter', 0)
            @userbot.on_message(filters.chat(channel_id))
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
                                            paid_content_stars if group_is_paid else 0,
                                            config,
                                            group_messages=group_messages,
                                            callback=callback,
                                            max_posts=max_posts
                                        )
                                        logger.info(f"[FORWARDER][DEBUG] Возврат из forward_media_group для {group_id}")
                                        
                                        # Увеличиваем счетчик для медиагрупп
                                        forwarded_count += 1
                                        # Обновляем общий счетчик
                                        self._counters[channel_id]['forwarded_count'] = forwarded_count
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

                        # Получаем результат пересылки
                        forward_result = await self._forward_single_message(
                            message,
                            target_channel,
                            hide_sender,
                            add_footer,
                            forward_mode,
                            config,
                            text_mode,
                            paid_content_stars if is_paid else 0
                        )

                        if forward_result:
                            logger.info(f"[FORWARDER][HANDLER] Успешно переслано сообщение {getattr(message, 'id', None)} в {target_channel}")
                            
                            # --- ДОБАВЛЕНО: Автоматическое проставление реакций ---
                            reactions_enabled = config.get('reactions_enabled', False)
                            reaction_emojis = config.get('reaction_emojis', [])

                            logger.info(f"[FORWARDER][REACTIONS] Проверка реакций: enabled={reactions_enabled}, emojis={reaction_emojis}, manager={self.reaction_manager is not None}")

                            if reactions_enabled and reaction_emojis and self.reaction_manager:
                                try:
                                    # Получаем ID пересланного сообщения
                                    # forward_result может содержать объект Message или True.
                                    # Если это объект Message, у него будет message_id
                                    forwarded_message_id = None
                                    if hasattr(forward_result, 'id'):
                                        forwarded_message_id = forward_result.id
                                    
                                    if forwarded_message_id:
                                        logger.info(f"[FORWARDER][REACTIONS] Умное проставление реакций на сообщение {forwarded_message_id} в канале {target_channel}")
                                        # Используем умное проставление реакций (автоматически выбирает разные эмодзи для дублирующихся аккаунтов)
                                        await self.reaction_manager.add_reactions_smart(
                                            chat_id=target_channel,
                                            message_id=forwarded_message_id,
                                            available_reactions=reaction_emojis
                                            # session_names не указываем, чтобы использовать все доступные
                                        )
                                    else:
                                        logger.warning(f"[FORWARDER][REACTIONS] Не удалось получить ID пересланного сообщения, реакция не будет поставлена.")
                                        
                                except Exception as reaction_error:
                                    logger.error(f"[FORWARDER][REACTIONS] Ошибка при проставлении реакции: {reaction_error}")
                            # --- КОНЕЦ БЛОКА РЕАКЦИЙ ---
                            
                            if delay_seconds and delay_seconds > 0:
                                await asyncio.sleep(delay_seconds)
                            forwarded_count += 1
                            # Обновляем общий счетчик
                            self._counters[channel_id]['forwarded_count'] = forwarded_count
                            if callback:
                                await callback(message)
                            last_message_id = message.id
                        else:
                            logger.warning(f"[FORWARDER][HANDLER] Не удалось пересласть сообщение {getattr(message, 'id', None)} - пропускаем")
                            # Для FloodWait не делаем задержку, продолжаем сразу
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
    
    async def _timeout_forward_media_group(self, channel_id, group_id, target_channel, text_mode, add_footer, forward_mode, hide_sender, max_posts, callback, paid_content_stars, config=None):
        """Таймаут для пересылки медиагруппы"""
        try:
            logger.info(f"[FORWARDER] 🔍 _timeout_forward_media_group: group_id={group_id}, paid_content_stars={paid_content_stars}")
            await asyncio.sleep(5)  # Ждем 5 секунд для сбора всех файлов группы
            
            # Проверяем, активна ли пересылка для этого канала
            if not self._forwarding_active.get(channel_id, False):
                logger.info(f"[FORWARDER] Пересылка для канала {channel_id} остановлена, отменяем обработку медиагруппы {group_id}")
                return
            
            logger.info(f"[FORWARDER] 🔍 Вызываем forward_media_group для группы {group_id} с paid_content_stars={paid_content_stars}")

            # Получаем результат пересылки медиагруппы
            media_group_result = await self.forward_media_group(channel_id, group_id, target_channel, text_mode, add_footer, forward_mode, hide_sender, paid_content_stars, config, group_messages=None, callback=callback, max_posts=max_posts)

            if media_group_result and media_group_result > 0:
                logger.info(f"[FORWARDER][HANDLER] Успешно переслана медиагруппа {group_id}")
                # Получаем delay_seconds из config
                delay_seconds = config.get('delay_seconds', 0) if config else 0
                if delay_seconds and delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                forwarded_count += media_group_result  # forward_media_group возвращает количество пересланных сообщений
                if callback:
                    # Для медиагруппы вызываем callback с количеством файлов
                    for _ in range(media_group_result):
                        await callback(None)
            else:
                logger.warning(f"[FORWARDER][HANDLER] Не удалось пересласть медиагруппу {group_id} - пропускаем")
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
                # Преобразуем chat_id в строку для Bot API
                chat_id_str = str(chat_id)
                await self.tg_bot.get_chat(chat_id_str)
                logger.info(f"[FORWARDER] ✅ Чат {chat_id} доступен для python-telegram-bot")
            except Exception as e:
                if "Chat not found" in str(e) or "chat not found" in str(e):
                    logger.warning(f"[FORWARDER] python-telegram-bot не может найти чат {chat_id}, используем Pyrogram")
                    return False
                else:
                    raise e
            # Для платного контента всегда нужно скачивать файл
            if not temp_file_path or not os.path.exists(temp_file_path):
                logger.error(f"[FORWARDER] temp_file_path не найден для отправки платного контента: {temp_file_path}")
                return False
            
            if media_type == 'photo':
                media = [InputPaidMediaPhoto(media=open(temp_file_path, 'rb'))]
            elif media_type == 'video':
                media = [InputPaidMediaVideo(media=open(temp_file_path, 'rb'))]
            else:
                logger.warning(f"[FORWARDER] Тип {media_type} не поддерживается для платного контента")
                return False
            logger.info(f"[FORWARDER] 🚀 Отправляем платный контент: {media_type} с {stars} звездами (is_bot_admin={is_bot_admin})")
            # Проверяем наличие HTML-разметки в caption
            contains_html = "<a href=" in caption or "<b>" in caption or "<i>" in caption or "<code>" in caption
            
            result = await self.tg_bot.send_paid_media(
                chat_id=str(chat_id),  # Преобразуем в строку для Bot API
                star_count=stars,
                media=media,
                caption=caption,
                parse_mode="html" if contains_html else None
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

    async def _forward_single_message(self, message, target_channel, hide_sender, add_footer, forward_mode, config: dict, text_mode="hashtags_only", paid_content_stars=0):
        """Пересылка одиночного сообщения"""
        logger.info(f"[FORWARDER][DEBUG] Используемая приписка (add_footer): {add_footer!r}")
        logger.info(f"[FORWARDER] 🔍 _forward_single_message: paid_content_stars={paid_content_stars} (тип: {type(paid_content_stars)})")
        sent_message = None  # Инициализируем переменную в начале метода

        # --- ДОБАВЛЕНО: Проверка и инициализация userbot ---
        if not self._userbot or not getattr(self._userbot, 'is_connected', False):
            logger.warning("[FORWARDER] userbot не инициализирован или не подключён, пробую получить и запустить...")
            self._userbot = await self.get_userbot(task="parsing")
            if self._userbot and not getattr(self._userbot, 'is_connected', False):
                await self._userbot.start()
        
        if not self._userbot or not getattr(self._userbot, 'is_connected', False):
            logger.error("[FORWARDER] Не удалось инициализировать userbot для пересылки сообщения!")
            return False
        # --- конец добавленного блока ---
        
        logger.info(f"[FORWARDER] 🔍 tg_bot доступен: {self.tg_bot is not None}")
        logger.info(f"[FORWARDER] 🔍 Условие для платного контента: paid_content_stars > 0 = {paid_content_stars > 0}")
        
        # Проверяем админство бота в канале
        is_bot_admin = await self._check_bot_admin_status(target_channel)
        logger.info(f"[FORWARDER] 🎯 Должен ли отправлять платный контент: {paid_content_stars > 0}, is_bot_admin={is_bot_admin}")
        
        # Обработка текста сообщения
        original_text = message.text or message.caption or ""
        processed_text = self._process_message_text(original_text, text_mode)
        
        # Добавляем приписку
        if add_footer:
            processed_text = f"{processed_text}\n{add_footer}".strip()
        
        try:
            # ПЛАТНЫЙ КОНТЕНТ: Если paid_content_stars > 0, пытаемся отправить платный пост
            if paid_content_stars > 0:
                if message.media and self.tg_bot:
                    media_type = message.media.value
                    logger.info(f"[FORWARDER] 🔍 Проверяем платность: media_type={media_type}, stars={paid_content_stars}")
                    if media_type in ['photo', 'video']:
                        logger.info("[FORWARDER] 🌟 Отправляем платный контент через python-telegram-bot")
                        
                        # Скачиваем файл для платного контента
                        temp_file_path = None
                        try:
                            if media_type == 'photo':
                                temp_file_path = await self._userbot.download_media(message.photo.file_id)
                            elif media_type == 'video':
                                temp_file_path = await self._userbot.download_media(message.video.file_id)
                            logger.info(f"[FORWARDER] 📥 Файл скачан для платного контента: {temp_file_path}")
                        except Exception as e:
                            logger.error(f"[FORWARDER] ❌ Ошибка скачивания файла для платного контента: {e}")
                            temp_file_path = None
                        
                        success = await self._send_paid_media(
                            chat_id=target_channel,
                            media_type=media_type,
                            file_id=message.photo.file_id if media_type == 'photo' else message.video.file_id,
                            caption=processed_text,
                            stars=paid_content_stars,
                            is_bot_admin=is_bot_admin,
                            temp_file_path=temp_file_path
                        )
                        if success:
                            logger.info(f"[FORWARDER] ✅ Платный контент отправлен успешно")
                            return True
                        else:
                            logger.warning(f"[FORWARDER] ⚠️ Не удалось отправить платный контент, отправляем как обычный")
                            # Fallback to normal content
                            paid_content_stars = 0
                    else:
                        logger.warning(f"[FORWARDER] Платный контент поддерживается только для медиа-сообщений (фото/видео), текущий тип: {media_type}, отправляем как обычный!")
                        paid_content_stars = 0
                else:
                    has_media = bool(message.media)
                    has_tg_bot = bool(self.tg_bot)
                    logger.warning(f"[FORWARDER] Платный контент требует tg_bot и медиа, есть медиа: {has_media}, есть tg_bot: {has_tg_bot}, отправляем как обычный!")
                    paid_content_stars = 0

            # ОБЫЧНЫЙ КОНТЕНТ
            logger.info(f"[FORWARDER] 🔄 Отправляем обычный контент (paid_content_stars={paid_content_stars}, tg_bot={self.tg_bot is not None})")
            
            # Пытаемся сначала через tg_bot (если доступен), затем fallback на userbot
            success_via_tg_bot = False
            
            if self.tg_bot and is_bot_admin:
                try:
                    logger.info(f"[FORWARDER] Пытаемся отправить через tg_bot (бот админ в канале)")
                    if message.media:
                        media_type = message.media.value
                        if media_type == 'photo':
                            await self.tg_bot.send_photo(
                                chat_id=target_channel,
                                photo=message.photo.file_id,
                                caption=processed_text,
                                parse_mode='HTML' if processed_text else None
                            )
                        elif media_type == 'video':
                            await self.tg_bot.send_video(
                                chat_id=target_channel,
                                video=message.video.file_id,
                                caption=processed_text,
                                parse_mode='HTML' if processed_text else None
                            )
                        elif media_type == 'document':
                            await self.tg_bot.send_document(
                                chat_id=target_channel,
                                document=message.document.file_id,
                                caption=processed_text,
                                parse_mode='HTML' if processed_text else None
                            )
                        else:
                            logger.warning(f"[FORWARDER] Неподдерживаемый тип медиа для tg_bot: {media_type}, используем userbot")
                            raise Exception("Unsupported media type for tg_bot")
                    else:
                        await self.tg_bot.send_message(
                            chat_id=target_channel,
                            text=processed_text or original_text,
                            parse_mode='HTML' if processed_text else None
                        )
                    success_via_tg_bot = True
                    logger.info(f"[FORWARDER] ✅ Сообщение {message.id} отправлено через tg_bot")
                except Exception as tg_bot_error:
                    logger.warning(f"[FORWARDER] ⚠️ Ошибка отправки через tg_bot: {tg_bot_error}, используем userbot")
                    success_via_tg_bot = False
            
            # Fallback на userbot, если tg_bot не сработал
            if not success_via_tg_bot:
                logger.info(f"[FORWARDER] Отправляем через userbot")
                if message.media:
                    media_type = message.media.value
                    entities = getattr(message, 'entities', None)
                    caption_entities = getattr(message, 'caption_entities', None)
                    logger.info(f"[FORWARDER][DEBUG] entities: {entities} (type: {type(entities)}), len: {len(entities) if entities else 0}")
                    logger.info(f"[FORWARDER][DEBUG] caption_entities: {caption_entities} (type: {type(caption_entities)}), len: {len(caption_entities) if caption_entities else 0}")
                    logger.info(f"[FORWARDER][DEBUG] processed_text: {processed_text}")
                    logger.info(f"[FORWARDER][DEBUG] original_text: {original_text}")
                    
                    # Исправлено: parse_mode только если реально есть форматирование
                    parse_mode = "html" if (entities and len(entities) > 0) or (caption_entities and len(caption_entities) > 0) else None
                    logger.info(f"[FORWARDER][DEBUG] Итоговый parse_mode для медиа: {parse_mode}")
                    
                    sent_message = None
                    if media_type == 'photo':
                        # Проверяем, нужно ли применить watermark
                        watermarked_path = None
                        if self._should_apply_watermark(message, config):
                            logger.info(f"[FORWARDER] Применяем watermark к фото")
                            watermarked_path = await self._apply_watermark_to_photo(message, config)
                        
                        if watermarked_path:
                            # Отправляем обработанное фото
                            logger.info(f"[FORWARDER][DEBUG] send_photo (с watermark) params: photo={watermarked_path}, caption={processed_text}, chat_id={target_channel}, parse_mode={parse_mode}")
                            sent_message = await self._userbot.send_photo(photo=watermarked_path, caption=processed_text, chat_id=target_channel)
                            # Удаляем временный файл
                            self.watermark_processor.cleanup_temp_files(watermarked_path)
                        else:
                            # Отправляем оригинальное фото
                            logger.info(f"[FORWARDER][DEBUG] send_photo params: photo={message.photo.file_id}, caption={processed_text}, chat_id={target_channel}, parse_mode={parse_mode}")
                            sent_message = await self._userbot.send_photo(photo=message.photo.file_id, caption=processed_text, chat_id=target_channel)
                    elif media_type == 'video':
                        logger.info(f"[FORWARDER][DEBUG] send_video params: video={message.video.file_id}, caption={processed_text}, chat_id={target_channel}, parse_mode={parse_mode}")
                        sent_message = await self._userbot.send_video(video=message.video.file_id, caption=processed_text, chat_id=target_channel)
                    elif media_type == 'document':
                        logger.info(f"[FORWARDER][DEBUG] send_document params: document={message.document.file_id}, caption={processed_text}, chat_id={target_channel}, parse_mode={parse_mode}")
                        sent_message = await self._userbot.send_document(document=message.document.file_id, caption=processed_text, chat_id=target_channel)
                    elif media_type == 'audio':
                        logger.info(f"[FORWARDER][DEBUG] send_audio params: audio={message.audio.file_id}, caption={processed_text}, chat_id={target_channel}, parse_mode={parse_mode}")
                        sent_message = await self._userbot.send_audio(audio=message.audio.file_id, caption=processed_text, chat_id=target_channel)
                    elif media_type == 'voice':
                        logger.info(f"[FORWARDER][DEBUG] send_voice params: voice={message.voice.file_id}, caption={processed_text}, chat_id={target_channel}, parse_mode={parse_mode}")
                        sent_message = await self._userbot.send_voice(voice=message.voice.file_id, caption=processed_text, chat_id=target_channel)
                    elif media_type == 'video_note':
                        logger.info(f"[FORWARDER][DEBUG] send_video_note params: video_note={message.video_note.file_id}, chat_id={target_channel}")
                        sent_message = await self._userbot.send_video_note(video_note=message.video_note.file_id, chat_id=target_channel)
                    elif media_type == 'animation':
                        logger.info(f"[FORWARDER][DEBUG] send_animation params: animation={message.animation.file_id}, caption={processed_text}, chat_id={target_channel}, parse_mode={parse_mode}")
                        sent_message = await self._userbot.send_animation(animation=message.animation.file_id, caption=processed_text, chat_id=target_channel)
                    elif media_type == 'sticker':
                        logger.info(f"[FORWARDER][DEBUG] send_sticker params: sticker={message.sticker.file_id}, chat_id={target_channel}")
                        sent_message = await self._userbot.send_sticker(sticker=message.sticker.file_id, chat_id=target_channel)
                    elif media_type == 'poll':
                        logger.warning(f"[FORWARDER] Неподдерживаемый тип медиа: {media_type}, пропускаем!")
                        return False
                    else:
                        logger.warning(f"[FORWARDER] Неподдерживаемый тип медиа: {media_type}, пропускаем!")
                        return False
                else:
                    # Для send_message НЕ передавать hide_sender!
                    # parse_mode только если реально есть entities
                    entities = getattr(message, 'entities', None)
                    logger.info(f"[FORWARDER][DEBUG] entities: {entities} (type: {type(entities)}), len: {len(entities) if entities else 0}")
                    logger.info(f"[FORWARDER][DEBUG] processed_text: {processed_text}")
                    logger.info(f"[FORWARDER][DEBUG] original_text: {original_text}")
                    
                    # Исправлено: parse_mode только если реально есть entities
                    parse_mode = "html" if entities and len(entities) > 0 and self._should_use_parse_mode(entities) else None
                    logger.info(f"[FORWARDER][DEBUG] Итоговый parse_mode для send_message: {parse_mode}")
                    sent_message = await self._userbot.send_message(text=processed_text or original_text, chat_id=target_channel, parse_mode=parse_mode)
            
            logger.info(f"[FORWARDER] ✅ Переслано одиночное сообщение {message.id}")
            return sent_message
            
        except Exception as e:
            if "FLOOD_WAIT" in str(e):
                wait_time = int(re.search(r'(\d+)', str(e)).group(1))
                logger.warning(f"[FORWARDER] FloodWait при пересылке одиночного сообщения {message.id}: пытаемся переключить сессию (ожидание {wait_time} секунд)")

                # Пытаемся переключиться на другую сессию парсинга
                try:
                    new_userbot, new_session_alias = await self.get_userbot_with_fallback(task="parsing", current_session_alias=getattr(self._userbot, 'name', None))
                    if new_userbot and new_userbot != self._userbot:
                        logger.info(f"[FORWARDER] Переключаемся на сессию {new_session_alias} для повтора отправки сообщения {message.id}")
                        self._userbot = new_userbot
                        if not hasattr(self._userbot, 'is_connected') or not self._userbot.is_connected:
                            await self._userbot.start()

                        # Повторяем отправку с новой сессией
                        await asyncio.sleep(min(wait_time, 10))  # Ждем не больше 10 секунд
                        return await self._forward_single_message(message, target_channel, hide_sender, add_footer, forward_mode, config, text_mode, paid_content_stars)
                    else:
                        logger.warning(f"[FORWARDER] Нет доступных сессий для переключения, пропускаем сообщение {message.id}")
                        return False
                except Exception as switch_error:
                    logger.error(f"[FORWARDER] Ошибка при переключении сессии: {switch_error}")
                    return False
            elif "CHAT_FORWARDS_RESTRICTED" in str(e):
                logger.error(f"[FORWARDER] ❌ Канал {target_channel} запрещает пересылку контента: {e}")
                logger.info(f"[FORWARDER] 💡 Попробуйте использовать режим 'copy' вместо 'forward' или убедитесь, что бот имеет права администратора в целевом канале")
            else:
                logger.error(f"[FORWARDER] ❌ Ошибка пересылки одиночного сообщения {message.id}: {e}")
            return False
    
    def _process_message_text(self, text: str, text_mode: str) -> str:
        """Обработка текста сообщения в зависимости от режима"""
        if not text:
            return ""
        
        if text_mode == "hashtags_only":
            # Извлекаем только хэштеги
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
        try:
            # ДОБАВЛЕНО: Проверка и запуск userbot в начале мониторинга
            if not hasattr(self._userbot, 'is_connected') or not self._userbot.is_connected:
                logger.info(f"[FORWARDER][MONITORING_LOOP] Userbot не запущен, запускаем...")
                await self._userbot.start()
                logger.info(f"[FORWARDER][MONITORING_LOOP] Userbot успешно запущен")
        except Exception as e:
            logger.error(f"[FORWARDER][MONITORING_LOOP] Ошибка при запуске userbot: {e}")
            
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
                # --- Обновить handler для source_channel ---
                self._update_source_handler(channel_id)
                logger.info(f"[FORWARDER] Остановлен мониторинг для пары {channel_id} -> {target_channel_id}")
            else:
                to_remove = [k for k in self._monitoring_tasks if k[0] == channel_id]
                for key in to_remove:
                    self._monitoring_tasks[key].cancel()
                    del self._monitoring_tasks[key]
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
                    'forwarded_count': 0,
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
                    chat = await self._userbot.get_chat(channel_id)
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
                        chat = await self._userbot.get_chat(channel_id)
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
                "forward_channel_title": forward_channel_title or ""
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
                "last_activity": "N/A"
            }

    async def forward_media_group(self, channel_id, group_id, target_channel, text_mode, add_footer, forward_mode, hide_sender, paid_content_stars, config, group_messages=None, callback=None, max_posts=None):
        if not self._userbot or not getattr(self._userbot, 'is_connected', False):
            logger.warning("[FORWARDER] userbot не инициализирован, попытка запуска...")
            self._userbot, _ = await self.get_userbot_with_fallback(task="parsing")
            if not self._userbot or not getattr(self._userbot, 'is_connected', False):
                logger.error("[FORWARDER] Не удалось запустить userbot для медиагруппы!")
                return 0

        group_msgs = group_messages if group_messages is not None else self.media_groups.get(group_id, [])
        
        # Фильтрация и сортировка
        original_count = len(group_msgs)
        group_msgs = [msg for msg in group_msgs if (hasattr(msg, 'date') and msg.date is not None) or (hasattr(msg, 'edit_date') and msg.edit_date is not None)]
        if not group_msgs:
            logger.warning(f"[FORWARDER] Медиагруппа {group_id} не содержит валидных сообщений после фильтрации (было {original_count}).")
            return 0
        group_msgs.sort(key=lambda m: m.date if hasattr(m, 'date') and m.date is not None else m.edit_date)
        
        # Получаем оригинальный текст группы
        group_caption = ""
        for m in group_msgs:
            if getattr(m, 'caption', None):
                group_caption = m.caption
                break

        # Обработка текста группы в соответствии с text_mode
        logger.info(f"[FORWARDER] 📝 Оригинальный текст медиагруппы: {group_caption!r}")
        group_caption = self._process_message_text(group_caption, text_mode)
        logger.info(f"[FORWARDER] 📝 Обработанный текст медиагруппы (text_mode={text_mode}): {group_caption!r}")

        # Добавляем приписку
        if add_footer:
            group_caption = f"{group_caption}\n{add_footer}".strip()

        sent_messages = []
        
        # --- ЛОГИКА ПЛАТНОГО КОНТЕНТА ДЛЯ МЕДИАГРУПП ---
        if paid_content_stars > 0 and self.tg_bot:
            is_bot_admin = await self._check_bot_admin_status(target_channel)
            if is_bot_admin:
                logger.info(f"[FORWARDER] 🌟 Попытка отправить медиагруппу {group_id} как платный контент ({paid_content_stars} звезд) в {target_channel}")
                sent_messages = await self._send_paid_media_group(
                    chat_id=target_channel,
                    media_group=group_msgs,
                    caption=group_caption,
                    stars=paid_content_stars
                )
                if sent_messages:
                     logger.info(f"[FORWARDER] ✅ Платная медиагруппа {group_id} успешно отправлена.")
                else:
                    logger.warning(f"[FORWARDER] ⚠️ Не удалось отправить медиагруппу {group_id} как платный контент. Попытка отправки как обычный пост...")
            else:
                 logger.warning(f"[FORWARDER] Бот не является админом в {target_channel}, не могу отправить платный контент.")

        # --- ОБЫЧНАЯ ПЕРЕСЫЛКА (ЕСЛИ НЕ ПЛАТНЫЙ КОНТЕНТ) ---
        if not sent_messages:
            try:
                media_objs = []
                watermarked_files = []  # Список для отслеживания временных файлов
                
                for i, m in enumerate(group_msgs):
                    caption_to_send = group_caption if i == 0 else None
                    
                    if m.photo:
                        # Проверяем, нужно ли применить watermark
                        watermarked_path = None
                        if self._should_apply_watermark(m, config):
                            logger.info(f"[FORWARDER] Применяем watermark к фото {i+1} в медиагруппе {group_id}")
                            watermarked_path = await self._apply_watermark_to_photo(m, config)
                            if watermarked_path:
                                watermarked_files.append(watermarked_path)
                        
                        if watermarked_path:
                            media_objs.append(InputMediaPhoto(watermarked_path, caption=caption_to_send))
                        else:
                            media_objs.append(InputMediaPhoto(m.photo.file_id, caption=caption_to_send))
                    elif m.video:
                        media_objs.append(InputMediaVideo(m.video.file_id, caption=caption_to_send))

                if media_objs:
                    sent_messages = await self._userbot.send_media_group(chat_id=target_channel, media=media_objs)
                    
                    # Удаляем временные файлы после отправки
                    if watermarked_files:
                        self.watermark_processor.cleanup_temp_files(*watermarked_files)
            
            except Exception as e:
                logger.error(f"[FORWARDER] ❌ Ошибка пересылки медиагруппы {group_id} в канал {target_channel}: {e}")
                return 0

        # --- ОБРАБОТКА РЕЗУЛЬТАТА ---
        if sent_messages:
            if not isinstance(sent_messages, list):
                sent_messages = [sent_messages]

            logger.info(f"[FORWARDER] ✅ Медиагруппа {group_id} ({len(group_msgs)} файлов) переслана в {target_channel}.")
            reactions_enabled = config.get('reactions_enabled', False)
            reaction_emojis = config.get('reaction_emojis', [])
            
            if reactions_enabled and reaction_emojis and self.reaction_manager:
                try:
                    if sent_messages and hasattr(sent_messages[0], 'id'):
                        first_message_id = sent_messages[0].id
                        logger.info(f"[REACTIONS] Умное проставление реакций на медиагруппу: канал {target_channel}, сообщение {first_message_id}")
                        await self.reaction_manager.add_reactions_smart(
                            chat_id=target_channel,
                            message_id=first_message_id,
                            available_reactions=reaction_emojis
                        )
                    else:
                        logger.warning(f"[REACTIONS] Не удалось получить ID сообщения для реакции на медиагруппу в канале {target_channel}.")
                except Exception as e:
                    logger.error(f"[REACTIONS] Ошибка реакции на медиагруппу: {e}")
            return 1
        
        return 0

    async def start_forwarding_parsing(self, source_channel: str, target_channels: List[str], config: dict, callback: Optional[Callable] = None):
        """Запуск парсинга + пересылки (background task) с поддержкой переключения сессий при FloodWait"""
        task_id = self.create_parse_forward_task(source_channel, target_channels, config)
        task_info = self._parse_forward_tasks[task_id]
        
        # Создаем background task с поддержкой переключения сессий
        async def run_parse_forward():
            current_session_alias = None
            userbot = None
            forwarded_count = 0

            try:
                # Получаем первую сессию для парсинга
                userbot, current_session_alias = await self.get_userbot_with_fallback(task="parsing")
                if not userbot:
                    raise Exception("Не удалось получить userbot для парсинга")

                # Запускаем сессию если нужно
                if not hasattr(userbot, 'is_connected') or not userbot.is_connected:
                    await userbot.start()

                logger.info(f"[FORWARDER] 🚀 ЗАПУСК ПАРСИНГА + ПЕРЕСЫЛКИ (НЕ МОНИТОРИНГА!)")
                logger.info(f"[FORWARDER] Источник: {source_channel} -> Цели: {target_channels}")
                logger.info(f"[FORWARDER] Конфигурация: {config}")
                logger.info(f"[FORWARDER] 🔍 ПЛАТНЫЕ ЗВЕЗДЫ: {config.get('paid_content_stars', 0)} (тип: {type(config.get('paid_content_stars', 0))})")
                logger.info(f"[FORWARDER] 🔍 Все ключи конфигурации: {list(config.keys())}")
                logger.info(f"[FORWARDER] 🔍 Используется сессия: {current_session_alias}")

                # Функция для получения информации о канале с обработкой FloodWait
                async def get_channel_info_with_retry():
                    nonlocal userbot, current_session_alias
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            if str(source_channel).startswith("-100"):
                                channel = await userbot.get_chat(int(source_channel))
                            else:
                                channel = await userbot.get_chat(source_channel)
                            return channel
                        except FloodWait as e:
                            logger.warning(f"[FORWARDER] FloodWait при получении информации о канале (попытка {attempt + 1}/{max_retries}): {e}")
                            if attempt < max_retries - 1:
                                # Переключаемся на следующую сессию
                                new_userbot, new_alias = await self.get_userbot_with_fallback(task="parsing", current_session_alias=current_session_alias)
                                if new_userbot:
                                    userbot = new_userbot
                                    current_session_alias = new_alias
                                    if not hasattr(userbot, 'is_connected') or not userbot.is_connected:
                                        await userbot.start()
                                    await asyncio.sleep(e.value)  # Ждем время FloodWait
                                else:
                                    raise Exception("Нет доступных сессий для переключения")
                            else:
                                raise e
                        except Exception as e:
                            if attempt == max_retries - 1:
                                raise e
                            logger.warning(f"[FORWARDER] Ошибка при получении информации о канале (попытка {attempt + 1}/{max_retries}): {e}")

                # Получаем информацию о канале с повторными попытками
                channel = await get_channel_info_with_retry()
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
                parse_direction = config.get("parse_direction", "backward")  # "forward" или "backward" - по умолчанию backward для избежания FloodWait
                media_filter = config.get("media_filter", "media_only")  # "all" или "media_only"
                range_mode = config.get("range_mode", "all")  # "all" или "range"
                range_start_id = config.get("range_start_id")
                range_end_id = config.get("range_end_id")

                limit = 0
                if max_posts and max_posts > 0:
                    # Умножаем на 5, чтобы получить достаточное количество сообщений для поиска нужного числа постов
                    limit = max_posts * 5
                elif range_mode != 'range':
                    # Если нет лимита и не режим диапазона, ставим большой лимит для парсинга
                    limit = 2000
                
                logger.info(f"[FORWARDER] 🔍 Настройки парсинга: лимит сообщений={limit}, направление='{parse_direction}', фильтр='{media_filter}', диапазон='{range_mode}'")
                
                # --- Получение сообщений ---
                all_messages = []
                media_groups = {}
                try:
                    # Определяем лимит сообщений в зависимости от направления парсинга
                    if parse_direction == "forward":
                        # Для направления "от старых к новым" используем большой лимит вместо полного обхода
                        # Это предотвращает FloodWait от полного обхода канала
                        if max_posts and max_posts > 0:
                            history_limit = min(max_posts * 20, 50000)  # Максимум 50к сообщений для безопасности
                            logger.info(f"[FORWARDER] 🔍 Направление 'от старых к новым' - получаем до {history_limit} сообщений (лимит {max_posts} постов * 20) из канала {channel_id}...")
                        else:
                            # Если лимит постов не задан, используем разумное ограничение
                            history_limit = 10000  # 10к сообщений максимум для избежания FloodWait
                            logger.info(f"[FORWARDER] 🔍 Направление 'от старых к новым' без лимита постов - получаем до {history_limit} сообщений из канала {channel_id}...")
                            logger.warning(f"[FORWARDER] ⚠️  ВНИМАНИЕ: Получение большого количества сообщений может вызвать FloodWait! Рассмотрите установку лимита постов.")
                    else:
                        # Для направления "от новых к старым" получаем достаточное количество
                        if max_posts and max_posts > 0:
                            # Берем значительно больше лимита для учета фильтров и медиагрупп
                            # Увеличиваем множитель до 10, чтобы учесть фильтрацию по медиа и хэштегам
                            history_limit = max_posts * 10
                            logger.info(f"[FORWARDER] 🔍 Направление 'от новых к старым' - получаем {history_limit} сообщений (лимит {max_posts} постов * 10) из канала {channel_id}...")
                        else:
                            # Если лимит не задан, берем разумное количество последних сообщений
                            history_limit = 1000
                            logger.info(f"[FORWARDER] 🔍 Направление 'от новых к старым' без лимита - получаем {history_limit} последних сообщений из канала {channel_id}...")
                    
                    message_count = 0

                    # Функция для получения сообщений с поддержкой FloodWait
                    async def get_chat_history_with_retry():
                        nonlocal userbot, current_session_alias
                        messages_collected = []
                        media_groups_collected = {}

                        try:
                            async for message in userbot.get_chat_history(channel_id, limit=history_limit):
                                try:
                                    messages_collected.append(message)
                                    nonlocal message_count
                                    message_count += 1

                                    # Логируем прогресс
                                    # Для ограниченного количества логируем каждые 100 или 500 сообщений
                                    log_interval = min(500, max(100, history_limit // 10))
                                    if message_count % log_interval == 0:
                                        logger.info(f"[FORWARDER] 📊 Получено {message_count}/{history_limit} сообщений, текущее: ID {message.id}, дата: {message.date}")

                                    # Группируем сообщения по media_group_id
                                    if getattr(message, 'media_group_id', None):
                                        group_id = message.media_group_id
                                        if group_id not in media_groups_collected:
                                            media_groups_collected[group_id] = []
                                        media_groups_collected[group_id].append(message)
                                except (ValueError, KeyError) as e:
                                    if ("Peer id invalid" in str(e)) or ("ID not found" in str(e)):
                                        logger.warning(f"[FORWARDER][SKIP] Сообщение пропущено из-за ошибки peer: {e}")
                                        continue
                                    else:
                                        raise
                        except FloodWait as e:
                            logger.warning(f"[FORWARDER] FloodWait при получении истории сообщений: {e}")
                            # Переключаемся на следующую сессию
                            new_userbot, new_alias = await self.get_userbot_with_fallback(task="parsing", current_session_alias=current_session_alias)
                            if new_userbot:
                                userbot = new_userbot
                                current_session_alias = new_alias
                                if not hasattr(userbot, 'is_connected') or not userbot.is_connected:
                                    await userbot.start()
                                logger.info(f"[FORWARDER] Переключились на сессию {current_session_alias}, ждем {e.value} секунд")
                                await asyncio.sleep(e.value)
                                # Рекурсивно продолжаем с новой сессии
                                return await get_chat_history_with_retry()
                            else:
                                raise Exception("Нет доступных сессий для переключения при FloodWait")

                        return messages_collected, media_groups_collected

                    # Получаем сообщения с поддержкой FloodWait
                    all_messages, media_groups = await get_chat_history_with_retry()
                    
                    # Показываем информацию о самом старом и новом сообщении
                    if all_messages:
                        oldest_msg = min(all_messages, key=lambda x: x.date)
                        newest_msg = max(all_messages, key=lambda x: x.date)
                        logger.info(f"[FORWARDER] 📅 Самое старое сообщение: ID {oldest_msg.id}, дата: {oldest_msg.date}")
                        logger.info(f"[FORWARDER] 📅 Самое новое сообщение: ID {newest_msg.id}, дата: {newest_msg.date}")

                    logger.info(f"[FORWARDER] ✅ Собрано {len(all_messages)} сообщений (лимит: {history_limit}), найдено {len(media_groups)} медиагрупп")
                    if parse_direction == "forward" and not max_posts:
                        logger.info(f"[FORWARDER] ℹ️  Для получения большего количества сообщений установите лимит постов в настройках")
                    # Явно заполняем буфер медиагрупп ДО пересылки
                    if channel_id not in self._media_group_buffers:
                        self._media_group_buffers[channel_id] = {}
                    temp_media_groups = {str(group_id): msgs for group_id, msgs in media_groups.items()}
                    self._media_group_buffers[channel_id] = temp_media_groups
                    logger.debug(f"[FORWARDER] Буфер медиагрупп заполнен: {len(self._media_group_buffers[channel_id])} групп для канала {channel_id}")
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
                logger.debug(f"[FORWARDER] Буфер медиагрупп заполнен: {len(self._media_group_buffers[channel_id])} групп для канала {channel_id}")
                # --- Применяем новые фильтры и сортировку ---
                
                # 1. Фильтрация по диапазону ID
                if range_mode == "range" and range_start_id and range_end_id:
                    all_messages = [msg for msg in all_messages if range_start_id <= msg.id <= range_end_id]
                    logger.info(f"[FORWARDER] 🔍 После фильтрации по диапазону: {len(all_messages)} сообщений")
                
                # 2. Фильтрация по медиа
                if media_filter == "media_only":
                    before_count = len(all_messages)
                    all_messages = [msg for msg in all_messages if msg.media is not None]
                    after_count = len(all_messages)
                    logger.info(f"[FORWARDER] 🔍 После фильтрации по медиа: {after_count} сообщений (исключено {before_count - after_count} текстовых)")

                    # Подсчитываем медиагруппы среди оставшихся сообщений
                    media_groups_count = len(set(msg.media_group_id for msg in all_messages if getattr(msg, 'media_group_id', None)))
                    single_media_count = len([msg for msg in all_messages if msg.media and not getattr(msg, 'media_group_id', None)])
                    total_posts_estimated = media_groups_count + single_media_count
                    logger.info(f"[FORWARDER] 📊 После фильтрации: {media_groups_count} медиагрупп + {single_media_count} одиночных = ~{total_posts_estimated} постов")

                    # Показываем информацию о первых пропущенных сообщениях
                    if before_count > after_count:
                        logger.info(f"[FORWARDER] 📝 Исключено {before_count - after_count} текстовых сообщений")
                else:
                    # Подсчитываем потенциальные посты без фильтрации
                    all_groups_count = len(set(msg.media_group_id for msg in all_messages if getattr(msg, 'media_group_id', None)))
                    all_single_count = len([msg for msg in all_messages if msg.media and not getattr(msg, 'media_group_id', None)])
                    all_text_count = len([msg for msg in all_messages if not msg.media])
                    logger.info(f"[FORWARDER] 🔍 Фильтр медиа отключен: {len(all_messages)} сообщений ({all_groups_count} групп + {all_single_count} медиа + {all_text_count} текста)")
                
                # 3. Проверяем количество доступных постов после фильтрации
                if max_posts and max_posts > 0 and parse_direction == "backward":
                    # Подсчитываем доступные посты
                    available_groups = set(msg.media_group_id for msg in all_messages if getattr(msg, 'media_group_id', None))
                    available_singles = len([msg for msg in all_messages if msg.media and not getattr(msg, 'media_group_id', None)])
                    total_available_posts = len(available_groups) + available_singles

                    logger.info(f"[FORWARDER] 📊 Доступно постов после фильтрации: {total_available_posts} (лимит: {max_posts})")

                    # Если доступных постов меньше лимита и мы можем получить больше сообщений
                    if total_available_posts < max_posts and history_limit and len(all_messages) >= history_limit:
                        additional_limit = (max_posts - total_available_posts) * 5  # Получаем дополнительные сообщения
                        logger.info(f"[FORWARDER] 🔄 Недостаточно постов ({total_available_posts} < {max_posts}), получаем дополнительные {additional_limit} сообщений...")

                        additional_messages = []
                        oldest_message = min(all_messages, key=lambda x: x.date) if all_messages else None

                        if oldest_message:
                            async for message in userbot.get_chat_history(channel_id, limit=additional_limit, offset_id=oldest_message.id):
                                if message.date < oldest_message.date:  # Только более старые сообщения
                                    additional_messages.append(message)

                            if additional_messages:
                                logger.info(f"[FORWARDER] ➕ Получено {len(additional_messages)} дополнительных сообщений")

                                # Применяем фильтры к дополнительным сообщениям
                                if media_filter == "media_only":
                                    additional_messages = [msg for msg in additional_messages if msg.media is not None]

                                if range_mode == "range" and range_start_id and range_end_id:
                                    additional_messages = [msg for msg in additional_messages if range_start_id <= msg.id <= range_end_id]

                                # Добавляем к основному списку
                                all_messages.extend(additional_messages)

                                # Перегруппировываем медиагруппы
                                for message in additional_messages:
                                    if getattr(message, 'media_group_id', None):
                                        group_id = message.media_group_id
                                        if group_id not in media_groups:
                                            media_groups[group_id] = []
                                        media_groups[group_id].append(message)

                                logger.info(f"[FORWARDER] ✅ После добавления: {len(all_messages)} сообщений, {len(media_groups)} медиагрупп")

                # 4. Сортировка по направлению
                if parse_direction == "backward":
                    # От новых к старым (по умолчанию)
                    all_messages.sort(key=lambda x: x.date, reverse=True)
                    logger.info(f"[FORWARDER] 🔍 Сортировка: от новых к старым")
                elif parse_direction == "forward":
                    # От старых к новым
                    all_messages.sort(key=lambda x: x.date)
                    logger.info(f"[FORWARDER] 🔍 Сортировка: от старых к новым")
                
                logger.info(f"[FORWARDER] 🚀 Начинаем пересылку сообщений (направление: {parse_direction}, фильтр: {media_filter}, диапазон: {range_mode})...")
                
                processed_groups = set()
                posts_to_forward = []

                # --- ЭТАП 1: СБОР ПОСТОВ ДЛЯ ПЕРЕСЫЛКИ ---
                posts_to_forward = []
                processed_groups = set()
                logger.info(f"[FORWARDER] --- НАЧАЛО ЭТАПА 1: СБОР ПОСТОВ (лимит: {max_posts}) ---")

                for message in all_messages:
                    if max_posts and len(posts_to_forward) >= max_posts:
                        logger.info(f"Собрано достаточно постов ({len(posts_to_forward)}), прекращаем сбор.")
                        break
                    
                    group_id = getattr(message, 'media_group_id', None)
                    if group_id:
                        if group_id not in processed_groups:
                            posts_to_forward.append({'type': 'media_group', 'id': group_id, 'messages': self.media_groups.get(group_id, [])})
                            processed_groups.add(group_id)
                    else:
                        # Убедимся, что одиночное сообщение прошло медиа-фильтр (если он включен)
                        if media_filter == "media_only" and not message.media:
                            continue
                        posts_to_forward.append({'type': 'single', 'message': message})
                
                logger.info(f"[FORWARDER] --- ЗАВЕРШЕНИЕ ЭТАПА 1: Собрано {len(posts_to_forward)} постов для пересылки ---")
                
                # --- ЭТАП 2: ПЕРЕСЫЛКА ПОСТОВ ---
                logger.info(f"[FORWARDER] --- НАЧАЛО ЭТАПА 2: ПЕРЕСЫЛКА {len(posts_to_forward)} ПОСТОВ ---")
                
                # Инициализируем счетчики перед началом пересылки
                self._counters[channel_id] = self._counters.get(channel_id, {
                    'hashtag_paid_counter': 0,
                    'select_paid_counter': 0,
                    'media_group_paid_counter': 0,
                    'media_group_hashtag_paid_counter': 0
                })

                for post in posts_to_forward:
                    # Пересылаем пост во все целевые каналы
                    for target_channel in target_channels:
                        if post['type'] == 'single':
                            message = post['message']
                            is_paid = self._is_post_paid(config, False, [message], self._counters[channel_id])
                            forward_result = await self._forward_single_message(message, target_channel, hide_sender, add_footer, forward_mode, config, text_mode, paid_content_stars if is_paid else 0)

                            # --- РЕАКЦИИ ДЛЯ ОДИНОЧНЫХ ПОСТОВ ---
                            reactions_enabled = config.get('reactions_enabled', False)
                            reaction_emojis = config.get('reaction_emojis', [])
                            if reactions_enabled and reaction_emojis and self.reaction_manager:
                                try:
                                    # Получаем ID отправленного сообщения из истории чата
                                    # (аналогично медиагруппам, так как tg_bot не возвращает объект с ID)
                                    try:
                                        recent_messages = []
                                        async for msg in self._userbot.get_chat_history(target_channel, limit=5):
                                            recent_messages.append(msg)
                                        if recent_messages:
                                            sent_message_id = recent_messages[0].id
                                            logger.info(f"[REACTIONS] Получен ID последнего сообщения для реакции: {sent_message_id}")
                                            logger.info(f"[REACTIONS] Умное проставление реакций на одиночный пост: канал {target_channel}, сообщение {sent_message_id}")
                                            await self.reaction_manager.add_reactions_smart(
                                                chat_id=target_channel,
                                                message_id=sent_message_id,
                                                available_reactions=reaction_emojis
                                            )
                                        else:
                                            logger.warning("[REACTIONS] Не удалось получить последние сообщения для определения ID")
                                    except Exception as hist_error:
                                        logger.error(f"[REACTIONS] Не удалось получить историю для определения ID: {hist_error}")
                                except Exception as e:
                                    logger.error(f"[REACTIONS] Ошибка реакции на одиночный пост: {e}")

                        elif post['type'] == 'media_group':
                            group_id = post['id']
                            group_messages = post['messages']
                            is_paid_group = self._is_post_paid(config, True, group_messages, self._counters[channel_id])
                            forwarded_in_group = await self.forward_media_group(channel_id, group_id, target_channel, text_mode, add_footer, forward_mode, hide_sender, paid_content_stars if is_paid_group else 0, config, group_messages=group_messages, callback=None, max_posts=max_posts)
                            # forward_media_group возвращает количество пересланных сообщений в группе

                        if delay_seconds > 0:
                            await asyncio.sleep(delay_seconds)

                    # Увеличиваем счетчик постов только после пересылки во все каналы
                    forwarded_count += 1

                # Завершаем задачу
                task_info["status"] = "completed"
                task_info["completed_at"] = datetime.now().isoformat()
                logger.info(f"[FORWARDER] ✅ Парсинг+пересылка завершены. Переслано {forwarded_count} постов (медиагруппы и одиночные сообщения).")
                
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
            from shared.models import ForwardingConfig
            
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

    def _should_apply_watermark(self, message: PyrogramMessage, config: dict) -> bool:
        """
        Проверить, нужно ли применять watermark к данному сообщению
        
        Args:
            message: Pyrogram сообщение
            config: Конфигурация пересылки с настройками watermark
        
        Returns:
            True если нужно применить watermark, False иначе
        """
        # Проверяем, включен ли watermark
        if not config.get('watermark_enabled', False):
            return False
        
        # Проверяем, есть ли фото в сообщении
        if not message.photo:
            logger.debug("[FORWARDER] Нет фото в сообщении, watermark не применяется")
            return False
        
        watermark_mode = config.get('watermark_mode', 'all')
        
        # Режим "all" - применяем ко всем фото
        if watermark_mode == 'all':
            return True
        
        # Режим "random" - случайное применение
        if watermark_mode == 'random':
            chance = config.get('watermark_chance', 100)
            return random.randint(1, 100) <= chance
        
        # Режим "hashtag" - применяем если есть хэштег
        if watermark_mode == 'hashtag':
            hashtag = config.get('watermark_hashtag')
            if hashtag and message.text:
                # Убеждаемся, что хэштег начинается с #
                if not hashtag.startswith('#'):
                    hashtag = f'#{hashtag}'
                return hashtag.lower() in message.text.lower()
            return False
        
        # Режим "manual" - пока не реализован, возвращаем False
        if watermark_mode == 'manual':
            logger.debug("[FORWARDER] Режим manual пока не поддерживается")
            return False
        
        return False
    
    async def _apply_watermark_to_photo(
        self,
        message: PyrogramMessage,
        config: dict
    ) -> Optional[str]:
        """
        Применить watermark к фото из сообщения
        
        Args:
            message: Pyrogram сообщение с фото
            config: Конфигурация watermark
        
        Returns:
            Путь к обработанному изображению или None в случае ошибки
        """
        try:
            logger.info(f"[FORWARDER] Применяем watermark к сообщению {message.id}")
            
            # Скачиваем фото
            photo_path = await self._userbot.download_media(message.photo.file_id)
            if not photo_path:
                logger.error("[FORWARDER] Не удалось скачать фото для watermark")
                return None
            
            logger.info(f"[FORWARDER] Фото скачано: {photo_path}")
            
            # Подготавливаем конфигурацию watermark
            watermark_config = {
                'watermark_text': config.get('watermark_text'),
                'watermark_image_path': config.get('watermark_image_path'),
                'watermark_position': config.get('watermark_position', 'bottom_right'),
                'watermark_opacity': config.get('watermark_opacity', 128),
                'watermark_scale': config.get('watermark_scale', 0.3)
            }
            
            # Применяем watermark
            watermarked_path = self.watermark_processor.apply_watermark(
                photo_path,
                watermark_config
            )
            
            # Удаляем оригинальный файл если watermark применен успешно
            if watermarked_path != photo_path:
                try:
                    os.remove(photo_path)
                except Exception as e:
                    logger.warning(f"[FORWARDER] Не удалось удалить оригинальный файл: {e}")
            
            logger.info(f"[FORWARDER] Watermark применен: {watermarked_path}")
            return watermarked_path
            
        except Exception as e:
            logger.error(f"[FORWARDER] Ошибка применения watermark: {e}")
            logger.exception(e)
            return None

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
            # Преобразуем channel_id в строку для Bot API
            channel_id_str = str(channel_id)
            admins = await self.tg_bot.get_chat_administrators(channel_id_str)
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

    def create_parse_forward_task(self, source_channel: str, target_channels: List[str], config: dict) -> str:
        """Создает новую задачу парсинг+пересылки и возвращает task_id."""
        task_id = self._generate_task_id()
        task_info = {
            "task_id": task_id,
            "source_channel": source_channel,
            "target_channels": target_channels,
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
        logger.info(f"[FORWARDER] Получение статуса задачи {task_id}")
        task_info = self._parse_forward_tasks.get(task_id)
        if not task_info:
            logger.warning(f"[FORWARDER] Задача {task_id} не найдена")
            return {"error": "Task not found"}

        # Проверяем, завершился ли task
        if task_info["task"] and task_info["task"].done():
            if task_info["status"] == "running":
                task_info["status"] = "completed"
                task_info["completed_at"] = datetime.now().isoformat()

        # Используем target_channels вместо target_channel
        target_channels = task_info.get("target_channels", [])
        target_channel = target_channels[0] if target_channels else None

        result = {
            "task_id": task_id,
            "source_channel": task_info["source_channel"],
            "target_channel": target_channel,  # Исправлено: используем target_channels
            "target_channels": target_channels,  # Добавляем для совместимости
            "status": task_info["status"],
            "started_at": task_info["started_at"],
            "completed_at": task_info["completed_at"],
            "error": task_info["error"]
        }
        logger.debug(f"[FORWARDER] Статус задачи {task_id}: {result}")
        return result

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
        logger.info(f"[FORWARDER] Получение списка всех задач парсинг+пересылки. Всего задач: {len(self._parse_forward_tasks)}")
        result = []
        for task_id, task_info in self._parse_forward_tasks.items():
            try:
                # Проверяем, завершился ли task
                if task_info["task"] and task_info["task"].done():
                    if task_info["status"] == "running":
                        task_info["status"] = "completed"
                        task_info["completed_at"] = datetime.now().isoformat()

                # Используем target_channels вместо target_channel
                target_channels = task_info.get("target_channels", [])
                target_channel = target_channels[0] if target_channels else None

                task_data = {
                    "task_id": task_id,
                    "source_channel": task_info["source_channel"],
                    "target_channel": target_channel,  # Исправлено: используем target_channels
                    "target_channels": target_channels,  # Добавляем для совместимости
                    "status": task_info["status"],
                    "started_at": task_info["started_at"],
                    "completed_at": task_info["completed_at"],
                    "error": task_info["error"]
                }
                result.append(task_data)
                logger.debug(f"[FORWARDER] Задача {task_id}: source={task_info['source_channel']}, target={target_channel}, status={task_info['status']}")
            except KeyError as e:
                logger.error(f"[FORWARDER] Ошибка при обработке задачи {task_id}: отсутствует ключ {e}")
                logger.error(f"[FORWARDER] Данные задачи: {task_info}")
                # Пропускаем проблемную задачу
                continue
            except Exception as e:
                logger.error(f"[FORWARDER] Неожиданная ошибка при обработке задачи {task_id}: {e}")
                continue

        logger.info(f"[FORWARDER] Возвращено {len(result)} задач из {len(self._parse_forward_tasks)}")
        return result

    def _update_source_handler(self, channel_id):
        # Проверяем, что userbot инициализирован
        if self._userbot is None:
            logger.error(f"[FORWARDER][UPDATE_HANDLER] self._userbot равен None для channel_id={channel_id}")
            return
            
        # ДОБАВЛЕНО: Проверка и запуск userbot асинхронно
        async def ensure_userbot_started():
            try:
                if not hasattr(self._userbot, 'is_connected') or not self._userbot.is_connected:
                    logger.info(f"[FORWARDER][UPDATE_HANDLER] Userbot не запущен, запускаем...")
                    await self._userbot.start()
                    logger.info(f"[FORWARDER][UPDATE_HANDLER] Userbot успешно запущен")
            except Exception as e:
                logger.error(f"[FORWARDER][UPDATE_HANDLER] Ошибка при запуске userbot: {e}")
        
        # Запускаем проверку в фоновом режиме
        asyncio.create_task(ensure_userbot_started())
        
        # Удалить старый handler, если есть
        if channel_id in self._handlers:
            try:
                self._userbot.remove_handler(self._handlers[channel_id])
                logger.info(f"[FORWARDER][UPDATE_HANDLER] Старый handler для channel_id={channel_id} успешно удален")
            except ValueError as e:
                # Handler уже удален или не существует
                logger.info(f"[FORWARDER][UPDATE_HANDLER] Handler для channel_id={channel_id} уже удален или не существует: {e}")
            except Exception as e:
                logger.warning(f"[FORWARDER][UPDATE_HANDLER] Ошибка при удалении старого handler: {e}")
            finally:
                # Всегда удаляем из словаря, даже если не удалось удалить handler
                del self._handlers[channel_id]
        # Найти все target_channel для этого source_channel
        targets = [tgt_id for (src_id, tgt_id) in self._monitoring_tasks.keys() if src_id == channel_id]
        if not targets:
            return  # Нет активных мониторингов — handler не нужен
        @self._userbot.on_message(filters.chat(channel_id))
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
                        logger.info(f"[FORWARDER][PAID_DEBUG] Настройки платности: mode={paid_content_mode}, every={paid_content_every}, stars={paid_content_stars}")
                        logger.info(f"[FORWARDER][PAID_DEBUG] Полный config: {config}")
                        group_is_paid = False
                        if paid_content_mode == "select":
                            counters = self._counters[channel_id]
                            counters['media_group_paid_counter'] += 1
                            every = paid_content_every
                            try:
                                every = int(every)
                            except Exception:
                                every = 1
                            logger.info(f"[FORWARDER][PAID_DEBUG] Счетчик медиагрупп: {counters['media_group_paid_counter']}, каждый: {every}")
                            if every > 0 and (counters['media_group_paid_counter'] % every == 0):
                                group_is_paid = True
                                logger.info(f"[FORWARDER][PAID_DEBUG] Медиагруппа {group_id} будет ПЛАТНОЙ!")
                            else:
                                logger.info(f"[FORWARDER][PAID_DEBUG] Медиагруппа {group_id} будет обычной")
                        # --- Отправка медиагруппы во все target_channel ---
                        for (src_id2, tgt_id2), task2 in self._monitoring_tasks.items():
                            if src_id2 == channel_id:
                                try:
                                    result = await self.forward_media_group(
                                        channel_id,
                                        group_id,
                                        tgt_id2,
                                        config.get('text_mode', 'hashtags_only'),
                                        config.get('footer_text', ''),
                                        config.get('forward_mode', 'copy'),
                                        config.get('hide_sender', True),
                                        paid_content_stars if group_is_paid else 0,
                                        config,
                                        group_messages=group_messages,
                                        callback=None,
                                        max_posts=config.get('max_posts', 0)
                                    )
                                    if result > 0:
                                        logger.info(f"[FORWARDER][HANDLER] Медиагруппа {group_id} успешно переслана в {tgt_id2}")
                                    else:
                                        logger.warning(f"[FORWARDER][HANDLER] Медиагруппа {group_id} не была переслана в {tgt_id2} (результат: {result})")
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
                            config,
                            text_mode,
                            paid_content_stars if is_paid else 0
                        )
                        logger.info(f"[FORWARDER][HANDLER] Успешно переслано сообщение {getattr(message, 'id', None)} в {tgt_id}")
                    except Exception as e:
                        logger.error(f"[FORWARDER][HANDLER] Ошибка при пересылке в {tgt_id}: {e}")
        self._handlers[channel_id] = handle_new_message

    async def add_reaction(self, chat_id, message_id, reaction, session_names=None):
        """Add reaction to a message using multiple accounts"""
        if self.session_manager:
            return await self.session_manager.add_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=reaction,
                session_names=session_names
            )
        else:
            # If no session manager, use the default userbot
            try:
                if not hasattr(self._userbot, 'is_connected') or not self._userbot.is_connected:
                    await self._userbot.start()
                
                await self._userbot.send_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    emoji=reaction
                )
                return {"default": "success"}
            except Exception as e:
                logger.error(f"[FORWARDER] Error adding reaction: {e}")
                return {"default": f"error: {str(e)}"}

    async def get_userbot_for_monitoring(self):
        if self.session_manager:
            sessions = await self.session_manager.get_sessions_for_task('monitoring')
            if sessions:
                client = await self.session_manager.get_client(sessions[0].alias)
                if client:
                    return client
        return await self.get_userbot(task="monitoring")

    async def start_monitoring(self, source_channel: str, target_channel: str, config: dict, callback: Optional[Callable] = None):
        self._userbot = await self.get_userbot_for_monitoring()
        if not hasattr(self._userbot, 'is_connected') or not self._userbot.is_connected:
            await self._userbot.start()
        # ... остальной код ...

    async def _check_bot_admin_status(self, target_channel):
        """Проверяет, является ли бот администратором в канале"""
        try:
            if self.tg_bot:
                chat_admins = await self.tg_bot.get_chat_administrators(target_channel)
                bot_user = await self.tg_bot.get_me()
                for admin in chat_admins:
                    if admin.user.id == bot_user.id:
                        logger.info(f"[FORWARDER] ✅ Бот является администратором в канале {target_channel}")
                        return True
                logger.warning(f"[FORWARDER] ⚠️ Бот НЕ является администратором в канале {target_channel}")
                return False
            else:
                logger.warning(f"[FORWARDER] ⚠️ tg_bot недоступен для проверки админских прав")
                return False
        except Exception as e:
            logger.warning(f"[FORWARDER] Не удалось проверить админство бота в канале {target_channel}: {e}")
            return False

    async def _send_paid_media_group(self, chat_id, media_group, caption, stars):
        """Отправка платной медиагруппы через python-telegram-bot."""
        logger.info(f"[FORWARDER] 🎯 _send_paid_media_group: chat_id={chat_id}, stars={stars}, items={len(media_group)}")
        if not self.tg_bot:
            logger.error("[FORWARDER] python-telegram-bot не инициализирован!")
            return None

        media_payload = []
        sent_messages = None

        try:
            # 1. Формируем InputPaidMedia используя file_id из оригинальных сообщений
            for i, msg in enumerate(media_group):
                try:
                    if msg.photo:
                        # Используем file_id напрямую - файл уже на серверах Telegram
                        media_payload.append(InputPaidMediaPhoto(media=msg.photo.file_id))
                        logger.info(f"[FORWARDER] 📎 Используем file_id для фото: {msg.photo.file_id}")
                    elif msg.video:
                        # Используем file_id напрямую - файл уже на серверах Telegram
                        media_payload.append(InputPaidMediaVideo(media=msg.video.file_id))
                        logger.info(f"[FORWARDER] 📎 Используем file_id для видео: {msg.video.file_id}")
                    else:
                        logger.warning(f"[FORWARDER] ⚠️ Неподдерживаемый тип медиа в сообщении {msg.id}, пропускаем")
                        continue

                except Exception as e:
                    logger.error(f"[FORWARDER] ❌ Ошибка обработки медиа из сообщения {getattr(msg, 'id', 'N/A')}: {e}")
                    raise

            if not media_payload:
                logger.warning("[FORWARDER] Не удалось создать медиа для платной отправки.")
                return None

            # 3. Отправляем через bot api с повторными попытками при таймауте
            logger.info(f"[FORWARDER] 🚀 Отправляем платную медиагруппу с {stars} звездами...")
            contains_html = "</a>" in caption or "<b>" in caption or "<i>" in caption

            max_retries = 3
            retry_delay = 5  # секунды

            for attempt in range(max_retries):
                try:
                    sent_messages = await self.tg_bot.send_paid_media(
                        chat_id=str(chat_id),
                        star_count=stars,
                        media=media_payload,
                        caption=caption,
                        parse_mode="HTML" if contains_html else None
                    )
                    break  # Успешно отправили, выходим из цикла
                except (TimedOut, NetworkError, BadRequest) as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"[FORWARDER] ⏰ Сетевая ошибка при отправке платной медиагруппы (попытка {attempt + 1}/{max_retries}): {e}. Повтор через {retry_delay} сек...")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # Экспоненциальная задержка
                    else:
                        logger.error(f"[FORWARDER] ❌ Все попытки отправки платной медиагруппы завершились сетевыми ошибками")
                        raise  # Передаем ошибку выше для обработки
            logger.info(f"[FORWARDER] ✅ Платная медиагруппа отправлена.")
            return sent_messages

        except (TelegramError, TimedOut, NetworkError, BadRequest) as e:
            logger.error(f"[FORWARDER] ❌ Ошибка Telegram API при отправке платной медиагруппы: {e}")
            logger.error(f"[FORWARDER] Полная ошибка: {traceback.format_exc()}")
            return None
        except Exception as e:
            logger.error(f"[FORWARDER] ❌ Ошибка отправки платной медиагруппы: {e}")
            logger.error(f"[FORWARDER] Полная ошибка: {traceback.format_exc()}")
            return None
    

