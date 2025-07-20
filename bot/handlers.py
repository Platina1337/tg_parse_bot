import re
import asyncio
import logging
import os
from datetime import datetime
import pytz
from typing import Dict, Optional
import httpx
import textwrap
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, InputMediaPhoto, InputMediaVideo, ReplyKeyboardMarkup, KeyboardButton, CallbackQuery, BotCommand
from pyrogram.errors import MessageNotModified, ChatAdminRequired, PeerIdInvalid, ChannelPrivate
from shared.models import ParseConfig, ParseMode, PostingSettings
from bot.settings import get_user_settings, update_user_settings, clear_user_settings, get_user_templates, save_user_template, DB_PATH
from bot.states import (
    user_states, FSM_MAIN_MENU,
    FSM_FORWARD_CHANNEL, FSM_FORWARD_TARGET, FSM_FORWARD_SETTINGS, FSM_FORWARD_HASHTAG,
    FSM_FORWARD_DELAY, FSM_FORWARD_FOOTER, FSM_FORWARD_FOOTER_LINK, FSM_FORWARD_FOOTER_LINK_TEXT, FSM_FORWARD_TEXT_MODE, FSM_FORWARD_LIMIT,
    FSM_FORWARD_DIRECTION, FSM_FORWARD_MEDIA_FILTER, FSM_FORWARD_RANGE, FSM_FORWARD_RANGE_START, FSM_FORWARD_RANGE_END,
    get_main_keyboard, get_channel_history_keyboard, get_target_channel_history_keyboard,
    get_forwarding_keyboard, get_forwarding_settings_keyboard, get_parse_mode_keyboard, get_text_mode_keyboard,
    get_direction_keyboard, get_media_filter_keyboard, get_range_mode_keyboard,
    posting_stats, start_forwarding_parsing_api, get_forwarding_history_stats_api, 
    clear_forwarding_history_api, get_channel_info, get_target_channel_info,
    get_stop_last_task_inline_keyboard, get_forwarding_inline_keyboard,
     format_channel_stats, format_forwarding_stats,
    start_forwarding_api, stop_forwarding_api, get_forwarding_stats_api, save_forwarding_config_api,
    start_forwarding_parsing_api, get_forwarding_history_stats_api, clear_forwarding_history_api,
    get_channel_info, get_target_channel_info,
    FSM_REACTION_CHANNEL, FSM_REACTION_SETTINGS, FSM_REACTION_EMOJIS, FSM_REACTION_MODE, FSM_REACTION_HASHTAG, FSM_REACTION_DATE, FSM_REACTION_DATE_RANGE, FSM_REACTION_COUNT, FSM_REACTION_CONFIRM,
    get_reaction_settings_keyboard, get_reaction_inline_keyboard,
)
from bot.config import config
from bot.core import (
    show_main_menu, start_forwarding_api, stop_forwarding_api, get_forwarding_stats_api, save_forwarding_config_api,
    start_forwarding_parsing_api, get_forwarding_history_stats_api, clear_forwarding_history_api,
    get_channel_info, get_target_channel_info
)
from bot.api_client import api_client
from bot.states import format_forwarding_config
import html

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def safe_edit_message(client, chat_id: int, message_id: int, text: str, reply_markup=None):
    """Безопасное редактирование сообщения с обработкой ошибки MESSAGE_NOT_MODIFIED"""
    try:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup
        )
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" in str(e):
            # Если сообщение не изменилось, просто игнорируем ошибку
            logger.debug(f"Message not modified, ignoring: {e}")
            return
        else:
            # Если другая ошибка, логируем и пробуем отправить новое сообщение
            logger.error(f"Error editing message: {e}")
            await client.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup
            )

async def safe_edit_callback_message(callback_query, text: str, reply_markup=None):
    """Безопасное редактирование сообщения callback с обработкой ошибки MESSAGE_NOT_MODIFIED"""
    try:
        await callback_query.edit_message_text(text, reply_markup=reply_markup)
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" in str(e):
            # Если сообщение не изменилось, просто игнорируем ошибку
            logger.debug(f"Callback message not modified, ignoring: {e}")
            return
        else:
            # Если другая ошибка, логируем и пробуем отправить новое сообщение
            logger.error(f"Error editing callback message: {e}")
            await callback_query.message.reply(text, reply_markup=reply_markup)

# --- Обработчик команды /start ---
async def start_command(client: Client, message: Message):
    logger.info(f"[START_COMMAND] Получена команда /start от пользователя {message.from_user.id}")
    
    # Устанавливаем команды меню при первом использовании
    try:
        commands = [
            BotCommand("start", "🏠 Главное меню"),
            BotCommand("reactions", "⭐ Управление реакциями"),
            BotCommand("sessions", "🔐 Управление сессиями"),
            BotCommand("monitorings", "📊 Статус задач"),
            BotCommand("public_groups", "📢 Публичные группы"),
        ]
        await client.set_bot_commands(commands)
        logger.info("✅ Команды меню установлены при команде /start")
    except Exception as e:
        logger.error(f"❌ Ошибка при установке команд меню: {e}")
    
    await show_main_menu(client, message, "Привет! Я бот для управления парсером Telegram-каналов.\n\nВыберите действие:")

# --- Обработчик команды /setup_commands ---
async def setup_commands_command(client: Client, message: Message):
    """Установка команд меню"""
    logger.info(f"[SETUP_COMMANDS] Получена команда /setup_commands от пользователя {message.from_user.id}")
    try:
        commands = [
            BotCommand("start", "🏠 Главное меню"),
            BotCommand("reactions", "⭐ Управление реакциями"),
            BotCommand("sessions", "🔐 Управление сессиями"),
            BotCommand("monitorings", "📊 Статус задач"),
            BotCommand("public_groups", "📢 Публичные группы"),
        ]
        await client.set_bot_commands(commands)
        await message.reply("✅ Команды меню установлены!")
        logger.info("✅ Команды меню установлены при команде /setup_commands")
    except Exception as e:
        await message.reply(f"❌ Ошибка при установке команд: {e}")
        logger.error(f"❌ Ошибка при установке команд меню: {e}")

# --- Обработчик текстовых сообщений ---
async def text_handler(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    if user_id not in user_states:
        user_states[user_id] = {}
    last_msg_id = user_states[user_id].get('last_msg_id')
    state = user_states[user_id].get('state', None)
    old_state = state
    print(f"[FSM][DEBUG][ENTER] user_id={user_id} | old_state={old_state} | text='{text}'")
    print(f"[FSM][DEBUG] user_states[{user_id}] на входе: {user_states[user_id]}")
    
    # --- FSM: обработка публичных групп ---
    from bot.public_groups_manager import handle_public_groups_text
    if await handle_public_groups_text(client, message):
        return  # Если обработано — не продолжаем дальше

    # --- FSM: обработка сессий ---
    # Убираем дублирующий вызов handle_session_text_input, так как он уже вызывается в bot_main.py
    # from bot.session_handlers import handle_session_text_input
    # if await handle_session_text_input(client, message):
    #     return  # Если обработано — не продолжаем дальше

    def set_state(new_state):
        nonlocal old_state
        print(f"[FSM][DEBUG][STATE_CHANGE] user_id={user_id} | from={old_state} -> to={new_state} | text='{text}'")
        user_states[user_id]['state'] = new_state
        old_state = new_state
        print(f"[FSM][DEBUG] user_states[{user_id}] после set_state: {user_states[user_id]}")
    
    # --- Главное меню ---
    if state == FSM_MAIN_MENU or state is None:
        print(f"[FSM][DEBUG] MAIN_MENU | text='{text}'")

        if text in ["📊 Статус задач"]:
            await monitorings_command(client, message)
            return
        elif text in ["Пересылка ⭐", "⭐ Пересылка"]:
            kb = await get_channel_history_keyboard(user_id)
            sent = await message.reply(
                "Выберите канал для пересылки из истории или введите ID/ссылку:",
                reply_markup=kb or ReplyKeyboardRemove()
            )
            if last_msg_id:
                try:
                    await client.delete_messages(message.chat.id, last_msg_id)
                except Exception:
                    pass
            if sent is not None:
                user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_FORWARD_CHANNEL, "last_msg_id": sent.id}
            else:
                user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_FORWARD_CHANNEL}
            return
        elif text in ["Навигация по хэштегам", "🧭 Навигация по хэштегам"]:
            from bot.navigation_manager import navigation_menu_handler
            await navigation_menu_handler(client, message)
            return
        elif text in ["📢 Публичные группы"]:
            # Обработка перенесена в public_groups_manager.py
            from bot.public_groups_manager import start_public_groups_manager
            await start_public_groups_manager(client, message)
            return

        elif text in ["Реакции ⭐", "⭐ Реакции"]:
            # Обработка перенесена в reaction_master.py
            from bot.reaction_master import start_reaction_master
            await start_reaction_master(client, message)
            return
        else:
            await show_main_menu(client, message, "Пожалуйста, выберите действие из меню:")
            return

    # В начале text_handler, перед обработкой любого пользовательского ввода для настроек пересылки:
    if user_states.get(user_id, {}).get("state") in [FSM_FORWARD_FOOTER_LINK, FSM_FORWARD_FOOTER_LINK_TEXT]:
        # Если пользователь переключился на другой режим, сбрасываем состояние
        user_states[user_id]["state"] = None

    # --- FSM: Пересылка ---
    if state == FSM_FORWARD_CHANNEL:
        print(f"[FSM][DEBUG] FSM_FORWARD_CHANNEL | text='{text}'")
        if text == "Назад":
            await show_main_menu(client, message, "Выберите действие:")
            return
        match = re.match(r"(.+) \(ID: (-?\d+)(?:, @(\w+))?\)", text)
        if match:
            channel_title = match.group(1)
            channel_id = match.group(2)
            username = match.group(3)
            channel_link = channel_id
            await api_client.update_user_channel_last_used(user_id, channel_id)
            user_states[user_id]["forward_channel_id"] = int(channel_id)
            user_states[user_id]["forward_channel_title"] = channel_title
            if username:
                user_states[user_id]["forward_channel_username"] = username
        else:
            # --- Новый вариант: нормализация ---
            channel_id, channel_title, channel_username = await resolve_channel(api_client, text)
            # Попробуем получить numeric id, если возможно
            real_id = None
            try:
                real_id = int(channel_id)
            except (ValueError, TypeError):
                # channel_id не приводится к int, пробуем получить через get_channel_stats
                stats = await api_client.get_channel_stats(channel_id)
                real_id = stats.get("id")  # Используем id из ответа API
                try:
                    real_id = int(real_id)
                except (ValueError, TypeError):
                    real_id = None
            
            if real_id is None:
                sent = await message.reply("❌ Не удалось определить ID канала. Введите корректный username или ID.", reply_markup=ReplyKeyboardRemove())
                if sent is not None:
                    user_states[user_id]["last_msg_id"] = sent.id
                return
            
            # Определяем, что ввел пользователь: username или ID
            is_username = not text.startswith("-100") and not text.isdigit()
            
            # Сохраняем в БД правильно
            if is_username:
                # Пользователь ввел username, сохраняем username в поле username, а ID в поле channel_id
                await api_client.add_user_channel(user_id, str(real_id), channel_title, text)
                user_states[user_id]["forward_channel_id"] = real_id
                user_states[user_id]["forward_channel_username"] = text  # username
            else:
                # Пользователь ввел ID, сохраняем ID в поле channel_id, а username в поле username
                await api_client.add_user_channel(user_id, str(real_id), channel_title, channel_username)
                user_states[user_id]["forward_channel_id"] = real_id
                if channel_username:
                    user_states[user_id]["forward_channel_username"] = channel_username
            
            user_states[user_id]["forward_channel_title"] = channel_title
        # --- ДОБАВЛЕНО: media_filter по умолчанию ---
        if "forward_settings" not in user_states[user_id]:
            user_states[user_id]["forward_settings"] = {}
        if "media_filter" not in user_states[user_id]["forward_settings"]:
            user_states[user_id]["forward_settings"]["media_filter"] = "media_only"
        # Переход к выбору целевого канала
        kb = await get_target_channel_history_keyboard(user_id)
        sent = await message.reply("Выберите целевой канал для пересылки:", reply_markup=kb or ReplyKeyboardRemove())
        if sent is not None:
            user_states[user_id]["last_msg_id"] = sent.id
        user_states[user_id]["state"] = FSM_FORWARD_TARGET
        return

    # --- FSM: Выбор целевого канала для пересылки ---
    if state == FSM_FORWARD_TARGET:
        print(f"[FSM][DEBUG] FSM_FORWARD_TARGET | text='{text}'")
        if text == "Назад":
            kb = await get_channel_history_keyboard(user_id)
            sent = await message.reply("Выберите канал для пересылки:", reply_markup=kb or ReplyKeyboardRemove())
            if sent is not None:
                user_states[user_id]["last_msg_id"] = sent.id
            user_states[user_id]["state"] = FSM_FORWARD_CHANNEL
            return
        match = re.match(r"(.+) \(ID: (-?\d+)(?:, @(\w+))?\)", text)
        if match:
            channel_title = match.group(1)
            channel_id = match.group(2)
            username = match.group(3)
            user_states[user_id]["forward_target_channel"] = channel_id
            user_states[user_id]["forward_target_title"] = channel_title
            if username:
                user_states[user_id]["forward_target_username"] = username
            await api_client.update_user_target_channel_last_used(user_id, channel_id)
        else:
            channel_id, channel_title, channel_username = await resolve_channel(api_client, text)
            
            # Определяем, что ввел пользователь: username или ID
            is_username = not text.startswith("-100") and not text.isdigit()
            
            # Получаем реальный ID из API
            stats = await api_client.get_channel_stats(channel_id)
            real_id = stats.get("id", channel_id)
            try:
                real_id = int(real_id)
            except (ValueError, TypeError):
                real_id = channel_id
            
            # Сохраняем в БД правильно
            if is_username:
                # Пользователь ввел username, сохраняем username в поле username, а ID в поле channel_id
                await api_client.add_user_target_channel(user_id, str(real_id), channel_title, text)
                user_states[user_id]["forward_target_channel"] = real_id
                user_states[user_id]["forward_target_username"] = text  # username
            else:
                # Пользователь ввел ID, сохраняем ID в поле channel_id, а username в поле username
                await api_client.add_user_target_channel(user_id, str(real_id), channel_title, channel_username)
                user_states[user_id]["forward_target_channel"] = real_id
                if channel_username:
                    user_states[user_id]["forward_target_username"] = channel_username
            
            user_states[user_id]["forward_target_title"] = channel_title
        user_states[user_id]['forward_settings'] = {
            'parse_mode': 'all',
            'hashtag_filter': None,
            'delay_seconds': 1,
            'footer_text': '🌐 <a href="https://t.me/TESAMSH/4026">_TSSH_Fans_</a>',
            'text_mode': 'hashtags_only',
            'max_posts': None,
            'hide_sender': True
        }
        # Показываем статистику канала и меню управления пересылкой
        try:
            stats = await api_client.get_channel_stats(str(user_states[user_id]['forward_channel_id']))
            stat_text = format_channel_stats(stats)
            channel_id = user_states[user_id]['forward_channel_id']
            target_channel = user_states[user_id].get('forward_target_channel')
            sent_stat = await message.reply(
                f"📊 Статистика канала {user_states[user_id]['forward_channel_title']}:\n\n{stat_text}\n\nВыберите действие:",
                reply_markup=get_forwarding_inline_keyboard(channel_id, target_channel)
            )
            if sent_stat is not None:
                user_states[user_id]["last_msg_id"] = sent_stat.id
            user_states[user_id]["state"] = FSM_FORWARD_SETTINGS
            return
        except Exception as e:
            sent = await message.reply(f"Ошибка при получении статистики: {e}", reply_markup=get_main_keyboard())
            user_states[user_id]["state"] = FSM_MAIN_MENU
            return
        # Сразу после этого — обычная клавиатура:
        await message.reply(
            "Выберите действие с помощью кнопок ниже.",
            reply_markup=get_forwarding_keyboard(channel_id, target_channel)
        )
        # Устанавливаю обычную клавиатуру для чата:
        await client.send_message(
            message.chat.id,
            " ",
            reply_markup=get_forwarding_keyboard(channel_id, target_channel)
        )







    # --- FSM: Настройки пересылки ---
    if state == FSM_FORWARD_SETTINGS:
        print(f"[FSM][DEBUG] FSM_FORWARD_SETTINGS | text='{text}'")
        forward_state = user_states[user_id].get('forward_state')
        if forward_state == 'paid_content_every_input':
            if text == "🔙 Назад":
                user_states[user_id]['forward_state'] = None
                await show_forwarding_settings(client, message, user_id)
                return
            try:
                every = int(text.strip())
                if every <= 0:
                    await message.reply("Введите положительное число (например, 3 — каждый третий пост будет платным)", reply_markup=ReplyKeyboardRemove())
                    return
                user_states[user_id]['forward_settings']['paid_content_every'] = every
                user_states[user_id]['forward_state'] = 'paid_content_stars_input'
                await message.reply("Введите стоимость платного поста (любое положительное число звезд):", reply_markup=ReplyKeyboardRemove())
                return
            except ValueError:
                await message.reply("Введите положительное число (например, 3 — каждый третий пост будет платным)", reply_markup=ReplyKeyboardRemove())
                return
        # --- Для hashtag_select: сначала хэштег, потом каждый N-й, потом стоимость ---
        if forward_state == 'paid_content_hashtag_input_for_every':
            if text == "🔙 Назад":
                user_states[user_id]['forward_state'] = None
                await show_forwarding_settings(client, message, user_id)
                return
            hashtag = text.strip().lstrip('#')
            user_states[user_id]['forward_settings']['paid_content_hashtag'] = hashtag
            user_states[user_id]['forward_state'] = 'paid_content_every_input_for_hashtag'
            await message.reply("Каждый какой пост с этим хэштегом делать платным? (например, 3 — каждый третий пост будет платным)", reply_markup=ReplyKeyboardRemove())
            return
        if forward_state == 'paid_content_every_input_for_hashtag':
            if text == "🔙 Назад":
                user_states[user_id]['forward_state'] = None
                await show_forwarding_settings(client, message, user_id)
                return
            try:
                every = int(text.strip())
                if every <= 0:
                    await message.reply("Введите положительное число (например, 3 — каждый третий пост будет платным)", reply_markup=ReplyKeyboardRemove())
                    return
                user_states[user_id]['forward_settings']['paid_content_every'] = every
                user_states[user_id]['forward_state'] = 'paid_content_stars_input'
                await message.reply("Введите стоимость платного поста (любое положительное число звезд):", reply_markup=ReplyKeyboardRemove())
                return
            except ValueError:
                await message.reply("Введите положительное число (например, 3 — каждый третий пост будет платным)", reply_markup=ReplyKeyboardRemove())
                return
        if forward_state == 'paid_content_hashtag_input':
            if text == "🔙 Назад":
                user_states[user_id]['forward_state'] = None
                await show_forwarding_settings(client, message, user_id)
                return
            hashtag = text.strip().lstrip('#')
            user_states[user_id]['forward_settings']['paid_content_hashtag'] = hashtag
            # Если выбран режим hashtag_random, после хэштега спрашиваем шанс
            if user_states[user_id]['forward_settings']['paid_content_mode'] == 'hashtag_random':
                user_states[user_id]['forward_state'] = 'paid_content_chance_input'
                await message.reply("Введите шанс (от 1 до 10), с которым пост будет платным:", reply_markup=ReplyKeyboardRemove())
                return
            else:
                user_states[user_id]['forward_state'] = 'paid_content_stars_input'
                await message.reply("Введите стоимость платного поста (любое положительное число звезд):", reply_markup=ReplyKeyboardRemove())
                return
        if forward_state == 'paid_content_chance_input':
            if text == "🔙 Назад":
                user_states[user_id]['forward_state'] = None
                await show_forwarding_settings(client, message, user_id)
                return
            try:
                chance = int(text.strip())
                if not (1 <= chance <= 10):
                    await message.reply("Введите число от 1 до 10 (например, 2 — 20% постов будут платными)", reply_markup=ReplyKeyboardRemove())
                    return
                user_states[user_id]['forward_settings']['paid_content_chance'] = chance
                user_states[user_id]['forward_state'] = 'paid_content_stars_input'
                await message.reply("Введите стоимость платного поста (любое положительное число звезд):", reply_markup=ReplyKeyboardRemove())
                return
            except ValueError:
                await message.reply("Введите число от 1 до 10 (например, 2 — 20% постов будут платными)", reply_markup=ReplyKeyboardRemove())
                return
        if forward_state == 'paid_content_stars_input':
            if text == "🔙 Назад":
                user_states[user_id]['forward_state'] = None
                await show_forwarding_settings(client, message, user_id)
                return
            try:
                stars = int(text.strip())
                if stars <= 0:
                    await message.reply("Введите положительное число (например, 10)", reply_markup=ReplyKeyboardRemove())
                    return
                user_states[user_id]['forward_settings']['paid_content_stars'] = stars
                user_states[user_id]['forward_state'] = None
                # Показываем меню настроек пересылки
                await show_forwarding_settings(client, message, user_id)
                return
            except ValueError:
                await message.reply("Введите положительное число (например, 10)", reply_markup=ReplyKeyboardRemove())
                return
        if forward_state == 'hashtag_input':
            # Обработка ввода хэштега
            if text == "🔙 Назад":
                # Возвращаемся к настройкам
                config = dict(user_states[user_id]['forward_settings'])
                config.setdefault('parse_direction', 'backward')
                config.setdefault('media_filter', 'media_only')
                config.setdefault('range_mode', 'all')
                config.setdefault('range_start_id', None)
                config.setdefault('range_end_id', None)
                config.setdefault('last_message_id', None)
                config_text = format_forwarding_config(config)
                sent = await message.reply(
                    f"Текущие настройки пересылки:\n\n{config_text}\n\nВыберите параметр для изменения:",
                    reply_markup=get_forwarding_settings_keyboard()
                )
                if sent is not None:
                    user_states[user_id]['last_msg_id'] = sent.id
                user_states[user_id]['forward_state'] = None
                return
            
            hashtag = text.strip()
            if hashtag.startswith('#'):
                hashtag = hashtag[1:]  # Убираем # если пользователь его ввел
            
            # Автоматически переключаем режим на "По хэштегам" если введен хэштег
            user_states[user_id]['forward_settings']['hashtag_filter'] = hashtag
            user_states[user_id]['forward_settings']['parse_mode'] = 'hashtags'  # Автоматически переключаем режим
            user_states[user_id]['forward_state'] = None  # Сбрасываем подсостояние
            
            # Отправляем новое сообщение с настройками вместо редактирования
            config = dict(user_states[user_id]['forward_settings'])
            config.setdefault('parse_direction', 'backward')
            config.setdefault('media_filter', 'media_only')
            config.setdefault('range_mode', 'all')
            config.setdefault('range_start_id', None)
            config.setdefault('range_end_id', None)
            config.setdefault('last_message_id', None)
            config_text = format_forwarding_config(config)
            sent = await message.reply(
                f"✅ Хэштег '{hashtag}' сохранен!\n\n"
                f"Текущие настройки пересылки:\n\n{config_text}\n\n"
                f"Выберите параметр для изменения:",
                reply_markup=get_forwarding_settings_keyboard()
            )
            if sent is not None:
                user_states[user_id]['last_msg_id'] = sent.id
            return
        elif forward_state == 'delay_input':
            # Обработка ввода задержки
            if text == "🔙 Назад":
                # Возвращаемся к настройкам
                config = dict(user_states[user_id]['forward_settings'])
                config.setdefault('parse_direction', 'backward')
                config.setdefault('media_filter', 'media_only')
                config.setdefault('range_mode', 'all')
                config.setdefault('range_start_id', None)
                config.setdefault('range_end_id', None)
                config.setdefault('last_message_id', None)
                config_text = format_forwarding_config(config)
                sent = await message.reply(
                    f"Текущие настройки пересылки:\n\n{config_text}\n\nВыберите параметр для изменения:",
                    reply_markup=get_forwarding_settings_keyboard()
                )
                if sent is not None:
                    user_states[user_id]['last_msg_id'] = sent.id
                user_states[user_id]['forward_state'] = None
                return
            
            try:
                delay = int(text.strip())
                if delay < 0:
                    delay = 0
                user_states[user_id]['forward_settings']['delay_seconds'] = delay
                user_states[user_id]['forward_state'] = None
                
                # Отправляем новое сообщение с настройками
                config = dict(user_states[user_id]['forward_settings'])
                config.setdefault('parse_direction', 'backward')
                config.setdefault('media_filter', 'media_only')
                config.setdefault('range_mode', 'all')
                config.setdefault('range_start_id', None)
                config.setdefault('range_end_id', None)
                config.setdefault('last_message_id', None)
                config_text = format_forwarding_config(config)
                sent = await message.reply(
                    f"✅ Задержка {delay} сек сохранена!\n\n"
                    f"Текущие настройки пересылки:\n\n{config_text}\n\n"
                    f"Выберите параметр для изменения:",
                    reply_markup=get_forwarding_settings_keyboard()
                )
                if sent is not None:
                    user_states[user_id]['last_msg_id'] = sent.id
                return
            except ValueError:
                await message.reply("Пожалуйста, введите число для задержки.")
                return
        elif forward_state == 'footer_input':
            # Обработка ввода приписки
            if text == "🔙 Назад":
                # Возвращаемся к настройкам
                config = dict(user_states[user_id]['forward_settings'])
                config.setdefault('parse_direction', 'backward')
                config.setdefault('media_filter', 'media_only')
                config.setdefault('range_mode', 'all')
                config.setdefault('range_start_id', None)
                config.setdefault('range_end_id', None)
                config.setdefault('last_message_id', None)
                config_text = format_forwarding_config(config)
                sent = await message.reply(
                    f"Текущие настройки пересылки:\n\n{config_text}\n\nВыберите параметр для изменения:",
                    reply_markup=get_forwarding_settings_keyboard()
                )
                if sent is not None:
                    user_states[user_id]['last_msg_id'] = sent.id
                user_states[user_id]['forward_state'] = None
                return
            
            footer = text.strip()
            if footer.lower() == 'убрать':
                footer = ''
            user_states[user_id]['forward_settings']['footer_text'] = footer
            user_states[user_id]['forward_state'] = None
            
            # Отправляем новое сообщение с настройками
            config = dict(user_states[user_id]['forward_settings'])
            config.setdefault('parse_direction', 'backward')
            config.setdefault('media_filter', 'media_only')
            config.setdefault('range_mode', 'all')
            config.setdefault('range_start_id', None)
            config.setdefault('range_end_id', None)
            config.setdefault('last_message_id', None)
            config_text = format_forwarding_config(config)
            footer_display = footer if footer else 'Нет'
            sent = await message.reply(
                f"✅ Приписка '{footer_display}' сохранена!\n\n"
                f"Текущие настройки пересылки:\n\n{config_text}\n\n"
                f"Выберите параметр для изменения:",
                reply_markup=get_forwarding_settings_keyboard()
            )
            if sent is not None:
                user_states[user_id]['last_msg_id'] = sent.id
            return
        elif forward_state == 'limit_input':
            # Обработка ввода лимита
            if text == "🔙 Назад":
                # Возвращаемся к настройкам
                config = dict(user_states[user_id]['forward_settings'])
                config.setdefault('parse_direction', 'backward')
                config.setdefault('media_filter', 'media_only')
                config.setdefault('range_mode', 'all')
                config.setdefault('range_start_id', None)
                config.setdefault('range_end_id', None)
                config.setdefault('last_message_id', None)
                config_text = format_forwarding_config(config)
                sent = await message.reply(
                    f"Текущие настройки пересылки:\n\n{config_text}\n\nВыберите параметр для изменения:",
                    reply_markup=get_forwarding_settings_keyboard()
                )
                if sent is not None:
                    user_states[user_id]['last_msg_id'] = sent.id
                user_states[user_id]['forward_state'] = None
                return
            
            try:
                if text.strip().lower() == '0' or text.strip().lower() == 'без лимита':
                    limit = None
                else:
                    limit = int(text.strip())
                    if limit < 0:
                        limit = None
                user_states[user_id]['forward_settings']['max_posts'] = limit
                user_states[user_id]['forward_state'] = None
                
                # Отправляем новое сообщение с настройками
                config = dict(user_states[user_id]['forward_settings'])
                config.setdefault('parse_direction', 'backward')
                config.setdefault('media_filter', 'media_only')
                config.setdefault('range_mode', 'all')
                config.setdefault('range_start_id', None)
                config.setdefault('range_end_id', None)
                config.setdefault('last_message_id', None)
                config_text = format_forwarding_config(config)
                limit_display = str(limit) if limit else 'Без лимита'
                sent = await message.reply(
                    f"✅ Лимит {limit_display} сохранен!\n\n"
                    f"Текущие настройки пересылки:\n\n{config_text}\n\n"
                    f"Выберите параметр для изменения:",
                    reply_markup=get_forwarding_settings_keyboard()
                )
                if sent is not None:
                    user_states[user_id]['last_msg_id'] = sent.id
                return
            except ValueError:
                await message.reply("Пожалуйста, введите число для лимита или '0' для снятия лимита.")
                return
        
        # --- Новые обработчики состояний ---
        elif forward_state == 'range_start_input':
            # Обработка ввода начального ID диапазона
            if text == "🔙 Назад":
                # Возвращаемся к настройкам
                config = dict(user_states[user_id]['forward_settings'])
                config.setdefault('parse_direction', 'backward')
                config.setdefault('media_filter', 'media_only')
                config.setdefault('range_mode', 'all')
                config.setdefault('range_start_id', None)
                config.setdefault('range_end_id', None)
                config.setdefault('last_message_id', None)
                config_text = format_forwarding_config(config)
                sent = await message.reply(
                    f"Текущие настройки пересылки:\n\n{config_text}\n\nВыберите параметр для изменения:",
                    reply_markup=get_forwarding_settings_keyboard()
                )
                if sent is not None:
                    user_states[user_id]['last_msg_id'] = sent.id
                user_states[user_id]['forward_state'] = None
                return
            
            try:
                start_id = int(text.strip())
                if start_id < 0:
                    await message.reply("ID сообщения должен быть положительным числом.")
                    return
                
                user_states[user_id]['forward_settings']['range_start_id'] = start_id
                user_states[user_id]['forward_state'] = 'range_end_input'
                
                sent = await message.reply(
                    f"✅ Начальный ID: {start_id}\n\nТеперь введите ID сообщения для конца диапазона:",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Назад")]], resize_keyboard=True)
                )
                if sent is not None:
                    user_states[user_id]['last_msg_id'] = sent.id
                return
            except ValueError:
                await message.reply("Пожалуйста, введите число для ID сообщения.")
                return
        
        elif forward_state == 'range_end_input':
            # Обработка ввода конечного ID диапазона
            if text == "🔙 Назад":
                # Возвращаемся к вводу начального ID
                user_states[user_id]['forward_state'] = 'range_start_input'
                sent = await message.reply(
                    "Введите ID сообщения для начала диапазона:",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Назад")]], resize_keyboard=True)
                )
                if sent is not None:
                    user_states[user_id]['last_msg_id'] = sent.id
                return
            
            try:
                end_id = int(text.strip())
                if end_id < 0:
                    await message.reply("ID сообщения должен быть положительным числом.")
                    return
                
                start_id = user_states[user_id]['forward_settings'].get('range_start_id')
                if start_id and end_id < start_id:
                    await message.reply("Конечный ID должен быть больше или равен начальному ID.")
                    return
                
                user_states[user_id]['forward_settings']['range_end_id'] = end_id
                user_states[user_id]['forward_state'] = None
                
                # Отправляем новое сообщение с настройками
                config = dict(user_states[user_id]['forward_settings'])
                config.setdefault('parse_direction', 'backward')
                config.setdefault('media_filter', 'media_only')
                config.setdefault('range_mode', 'all')
                config.setdefault('range_start_id', None)
                config.setdefault('range_end_id', None)
                config.setdefault('last_message_id', None)
                config_text = format_forwarding_config(config)
                sent = await message.reply(
                    f"✅ Диапазон ID: {start_id} - {end_id} сохранен!\n\n"
                    f"Текущие настройки пересылки:\n\n{config_text}\n\n"
                    f"Выберите параметр для изменения:",
                    reply_markup=get_forwarding_settings_keyboard()
                )
                if sent is not None:
                    user_states[user_id]['last_msg_id'] = sent.id
                return
            except ValueError:
                await message.reply("Пожалуйста, введите число для ID сообщения.")
                return
        elif text == "🏷️ Режим парсинга":
            # Переключаем режим парсинга
            current_mode = user_states[user_id]['forward_settings'].get('parse_mode', 'all')
            new_mode = 'hashtags' if current_mode == 'all' else 'all'
            user_states[user_id]['forward_settings']['parse_mode'] = new_mode
            
            # Если переключаем на "Все сообщения", очищаем хэштег
            if new_mode == 'all':
                user_states[user_id]['forward_settings']['hashtag_filter'] = None
            
            mode_text = "По хэштегам" if new_mode == 'hashtags' else "Все сообщения"
            
            # Отправляем новое сообщение с настройками
            config = dict(user_states[user_id]['forward_settings'])
            config.setdefault('parse_direction', 'backward')
            config.setdefault('media_filter', 'media_only')
            config.setdefault('range_mode', 'all')
            config.setdefault('range_start_id', None)
            config.setdefault('range_end_id', None)
            config.setdefault('last_message_id', None)
            config_text = format_forwarding_config(config)
            sent = await message.reply(
                f"✅ Режим парсинга: {mode_text}!\n\n"
                f"Текущие настройки пересылки:\n\n{config_text}\n\n"
                f"Выберите параметр для изменения:",
                reply_markup=get_forwarding_settings_keyboard()
            )
            if sent is not None:
                user_states[user_id]['last_msg_id'] = sent.id
            return
        elif text == "⏱️ Задержка":
            user_states[user_id]["state"] = FSM_FORWARD_DELAY
            # Запрашиваем ввод задержки
            current_delay = user_states[user_id]['forward_settings'].get('delay_seconds', 0)
            sent = await message.reply(
                f"Текущая задержка: {current_delay} сек\n\nВведите новую задержку в секундах:",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Назад")]], resize_keyboard=True)
            )
            if sent is not None:
                user_states[user_id]['last_msg_id'] = sent.id
            return
        elif text == "📝 Приписка":
            user_states[user_id]["state"] = FSM_FORWARD_FOOTER
            # Запрашиваем ввод приписки
            current_footer = user_states[user_id]['forward_settings'].get('footer_text', '')
            sent = await message.reply(
                f"Текущая приписка: '{current_footer or 'Нет'}'\\n\\nВведите новую приписку (или 'убрать' для удаления):",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Назад")]], resize_keyboard=True)
            )
            if sent is not None:
                user_states[user_id]['last_msg_id'] = sent.id
            return
        elif text == "📄 Режим текста":
            # Переключаем режим текста
            current_mode = user_states[user_id]['forward_settings'].get('text_mode', 'hashtags_only')
            modes = ['hashtags_only', 'as_is', 'no_text']
            current_index = modes.index(current_mode)
            new_index = (current_index + 1) % len(modes)
            new_mode = modes[new_index]
            user_states[user_id]['forward_settings']['text_mode'] = new_mode
            
            mode_texts = {
                'hashtags_only': 'Только хэштеги',
                'as_is': 'Как есть',
                'no_text': 'Удалить'
            }
            
            # Отправляем новое сообщение с настройками
            config = dict(user_states[user_id]['forward_settings'])
            config.setdefault('parse_direction', 'backward')
            config.setdefault('media_filter', 'media_only')
            config.setdefault('range_mode', 'all')
            config.setdefault('range_start_id', None)
            config.setdefault('range_end_id', None)
            config.setdefault('last_message_id', None)
            config_text = format_forwarding_config(config)
            sent = await message.reply(
                f"✅ Режим текста: {mode_texts[new_mode]}!\n\n"
                f"Текущие настройки пересылки:\n\n{config_text}\n\n"
                f"Выберите параметр для изменения:",
                reply_markup=get_forwarding_settings_keyboard()
            )
            if sent is not None:
                user_states[user_id]['last_msg_id'] = sent.id
            return
        elif text == "📊 Лимит постов":
            user_states[user_id]["state"] = FSM_FORWARD_LIMIT
            # Запрашиваем ввод лимита
            current_limit = user_states[user_id]['forward_settings'].get('max_posts')
            sent = await message.reply(
                f"Текущий лимит: {current_limit or 'Без лимита'}\n\nВведите новый лимит постов (или '0' для снятия лимита):",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Назад")]], resize_keyboard=True)
            )
            if sent is not None:
                user_states[user_id]['last_msg_id'] = sent.id
            return
        elif text == "🏷️ Хэштег фильтр":
            user_states[user_id]["state"] = FSM_FORWARD_HASHTAG
            # Запрашиваем ввод хэштега
            current_hashtag = user_states[user_id]['forward_settings'].get('hashtag_filter', '')
            sent = await message.reply(
                f"Текущий хэштег: '{current_hashtag or 'Нет'}'\\n\\nВведите хэштег для фильтрации (без #):",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Назад")]], resize_keyboard=True)
            )
            if sent is not None:
                user_states[user_id]['last_msg_id'] = sent.id
            return
        elif text == "🎯 Целевой канал":
            # Переходим к выбору целевого канала
            kb = await get_target_channel_history_keyboard(user_id)
            sent = await message.reply("Выберите целевой канал для пересылки:", reply_markup=kb or ReplyKeyboardRemove())
            if sent is not None:
                user_states[user_id]['last_msg_id'] = sent.id
            user_states[user_id]['state'] = FSM_FORWARD_TARGET
            return
        elif text == "💾 Сохранить":
            # Сохраняем конфигурацию
            success = await save_forwarding_config_api(user_id)
            if success:
                sent = await message.reply("✅ Настройки сохранены!", reply_markup=get_main_keyboard())
                if sent is not None:
                    user_states[user_id]['last_msg_id'] = sent.id
                user_states[user_id]['state'] = FSM_MAIN_MENU
            else:
                sent = await message.reply("❌ Ошибка сохранения настроек", reply_markup=get_forwarding_settings_keyboard())
                if sent is not None:
                    user_states[user_id]['last_msg_id'] = sent.id
            return
        elif text == "🔙 Назад":
            # Новый возврат: зависит от текущего состояния
            state = user_states[user_id].get("state")
            if state == FSM_FORWARD_SETTINGS:
                # После статистики — возвращаем к выбору целевого канала
                kb = await get_target_channel_history_keyboard(user_id)
                await safe_edit_callback_message(
                    callback_query,
                    "Выберите целевой канал для пересылки:",
                    reply_markup=kb or ReplyKeyboardRemove()
                )
                user_states[user_id]["state"] = FSM_FORWARD_TARGET
                return
            elif state == FSM_FORWARD_MONITORING or state == FSM_FORWARD_RUNNING:
                # После запуска пересылки/мониторинга — возвращаем к статистике
                stats = await api_client.get_channel_stats(str(user_states[user_id]['forward_channel_id']))
                stat_text = format_channel_stats(stats)
                channel_id = user_states[user_id]['forward_channel_id']
                target_channel = user_states[user_id].get('forward_target_channel')
                await safe_edit_callback_message(
                    callback_query,
                    f"📊 Статистика канала {user_states[user_id]['forward_channel_title']}:\n\n{stat_text}\n\nВыберите действие:",
                    reply_markup=get_forwarding_inline_keyboard(channel_id, target_channel)
                )
                user_states[user_id]["state"] = FSM_FORWARD_SETTINGS
                return
            else:
                # По умолчанию — главное меню
                await show_main_menu(client, callback_query.message, "Выберите действие:")
                user_states[user_id]["state"] = FSM_MAIN_MENU
                return
        else:
            # Если нет подсостояния, показываем главное меню
            await show_main_menu(client, message, "Пожалуйста, выберите действие из меню:")
            return

    # --- FSM: Ввод URL для гиперссылки в приписке ---
    if state == FSM_FORWARD_FOOTER_LINK:
        print(f"[FSM][DEBUG] FSM_FORWARD_FOOTER_LINK | text='{text}'")
        if text == "Назад":
            await show_forwarding_settings(client, message, user_id)
            user_states[user_id]["state"] = FSM_FORWARD_SETTINGS
            return
        # Сохраняем новый URL
        new_url = text.strip()
        forwarding_config = user_states[user_id]["forward_settings"]
        forwarding_config["footer_link"] = new_url
        forwarding_config["footer_link_text"] = None
        forwarding_config["footer_full_link"] = True
        user_states[user_id]["forward_settings"] = forwarding_config
        # Показываем меню управления гиперссылкой
        text_msg = f"Текущая ссылка в приписке: {new_url}\n\nВся приписка является гиперссылкой.\n\nВыберите действие:"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Изменить URL", callback_data="forward_footer_link_change")],
            [InlineKeyboardButton("📝 Изменить текст ссылки", callback_data="forward_footer_link_text")],
            [InlineKeyboardButton("🗑️ Удалить ссылку", callback_data="forward_footer_link_delete")],
            [InlineKeyboardButton("◀️ Назад", callback_data="forward_back_to_settings")]
        ])
        sent = await message.reply(text_msg, reply_markup=keyboard)
        if sent:
            user_states[user_id]["last_msg_id"] = sent.id
        user_states[user_id]["state"] = FSM_FORWARD_SETTINGS
        return

    # --- FSM: Настройка текста гиперссылки в приписке ---
    def insert_link_once(footer_text, link_text, url):
        # Заменить только первое вхождение link_text на ссылку
        return re.sub(re.escape(link_text), f'<a href="{url}">{link_text}</a>', footer_text, count=1)

    if state == FSM_FORWARD_FOOTER_LINK_TEXT:
        print(f"[FSM][DEBUG] FSM_FORWARD_FOOTER_LINK_TEXT | text='{text}'")
        if text == "Назад":
            await show_forwarding_settings(client, message, user_id)
            user_states[user_id]["state"] = FSM_FORWARD_SETTINGS
            return
        footer_text = user_states[user_id]["forward_settings"].get("footer_text", "")
        # Проверяем, что указанный текст присутствует в приписке
        if text not in footer_text:
            sent = await message.reply(
                f"⚠️ Текст «{text}» не найден в приписке.\n\n"
                f"Приписка: {footer_text}\n\n"
                f"Пожалуйста, введите текст, который присутствует в приписке."
            )
            if sent:
                user_states[user_id]["last_msg_id"] = sent.id
            return
        # Сохраняем текст для гиперссылки
        user_states[user_id]["forward_settings"]["footer_link_text"] = text
        user_states[user_id]["forward_settings"]["footer_full_link"] = False
        # Формируем превью того, как будет выглядеть приписка
        footer_link = user_states[user_id]["forward_settings"].get("footer_link", "")
        preview = insert_link_once(footer_text, text, footer_link)
        sent = await message.reply(
            f"✅ Гиперссылка настроена!\n\n"
            f"Так будет выглядеть приписка:\n{preview}",
        )
        if sent:
            user_states[user_id]["last_msg_id"] = sent.id
        await show_forwarding_settings(client, message, user_id)
        user_states[user_id]["state"] = FSM_FORWARD_SETTINGS
        return

    # Проверяем состояния сессий и реакций
    if state and state.startswith("session_"):
        # Эти состояния обрабатываются в других обработчиках
        return
    elif state and state.startswith("reaction_"):
        # Обработка реакций перенесена в reaction_master.py
        from bot.reaction_master import process_reaction_fsm
        await process_reaction_fsm(client, message)
        return
    
    # Если этап не определён
    await show_main_menu(client, message, "Пожалуйста, выберите действие из меню:")

async def show_forwarding_settings(client, message, user_id: int):
    config = dict(user_states[user_id]['forward_settings'])
    # Добавить значения по умолчанию для всех новых параметров
    config.setdefault('parse_direction', 'backward')
    config.setdefault('media_filter', 'media_only')
    config.setdefault('range_mode', 'all')
    config.setdefault('range_start_id', None)
    config.setdefault('range_end_id', None)
    config.setdefault('last_message_id', None)
    config_text = format_forwarding_config(config)
    kb = get_forwarding_settings_keyboard()
    if kb and hasattr(kb, 'keyboard'):
        kb.keyboard.append([KeyboardButton("Платные посты")])
    sent = await message.reply(
        f"Текущие настройки пересылки:\n\n{config_text}\n\nВыберите параметр для изменения:",
        reply_markup=kb
    )
    if sent is not None:
        user_states[user_id]['last_msg_id'] = sent.id

async def show_forwarding_menu(client, message, user_id: int):
    """Показать главное меню пересылки"""
    channel_id = user_states[user_id].get('forward_channel_id')
    target_channel = user_states[user_id].get('forward_target_channel')
    
    # Получаем информацию о каналах
    channel_info = await get_channel_info(str(channel_id))
    if target_channel:
        target_info = await get_target_channel_info(target_channel)
        target_display = target_info.get('channel_title', str(target_channel))
    else:
        target_display = 'Не выбран'
    channel_display = channel_info.get('channel_title', f"Канал {channel_id}")
    menu_text = f"📺 Канал: {channel_display}\n"
    menu_text += f"🎯 Целевой канал: {target_display}\n\n"
    sent = await message.reply(
        menu_text,
        reply_markup=get_forwarding_inline_keyboard(channel_id, target_channel)
    )
    if sent is not None:
        user_states[user_id]['last_msg_id'] = sent.id

# --- Обработчик callback запросов ---
async def forwarding_callback_handler(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    if user_id not in user_states:
        await callback_query.answer("Ваша сессия устарела или неактивна. Пожалуйста, начните сначала через /start или /sessions", show_alert=True)
        return
    state = user_states[user_id].get("state")
    
    # Извлекаем действие из callback_data
    action = data.replace("forward_", "")
    
    # Получаем текущие настройки пересылки
    forwarding_config = user_states[user_id].get("forwarding_config", {})
    if not forwarding_config and "forward_settings" in user_states[user_id]:
        forwarding_config = user_states[user_id]["forward_settings"]

    # --- Обработка действий для гиперссылки в приписке ---
    if action == "footer_link":
        # Проверяем, есть ли уже приписка (footer_text)
        footer_text = forwarding_config.get("footer_text", "")
        # Проверяем, есть ли гиперссылка в приписке (HTML <a href=...>)
        import re
        link_match = re.search(r'<a href=["\\\']([^"\\\']+)["\\\']>(.*?)</a>', footer_text)
        footer_link = forwarding_config.get("footer_link")
        if not footer_link and link_match:
            forwarding_config["footer_link"] = link_match.group(1)
            forwarding_config["footer_link_text"] = link_match.group(2)
            # Проверяем: вся ли приписка — гиперссылка
            # Удаляем HTML-теги и пробелы для сравнения
            import html
            text_inside_link = html.unescape(link_match.group(2)).strip()
            text_no_link = re.sub(r'<a href=["\\\']([^"\\\']+)["\\\']>(.*?)</a>', text_inside_link, footer_text).strip()
            # Если вся приписка — только ссылка (без эмодзи и пробелов)
            if footer_text.strip() == link_match.group(0).strip():
                forwarding_config["footer_full_link"] = True
            else:
                forwarding_config["footer_full_link"] = False
            user_states[user_id]["forward_settings"] = forwarding_config
            footer_link = link_match.group(1)
        if footer_link:
            text = f"Текущая ссылка в приписке: {footer_link}\n\n"
            if forwarding_config.get("footer_full_link", False):
                text += "Вся приписка является гиперссылкой.\n\n"
            elif forwarding_config.get("footer_link_text"):
                text += f"Текст ссылки: {forwarding_config.get('footer_link_text')}\n\n"
            text += "Выберите действие:"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Изменить URL", callback_data="forward_footer_link_change")],
                [InlineKeyboardButton("📝 Изменить текст ссылки", callback_data="forward_footer_link_text")],
                [InlineKeyboardButton("✅ Вся приписка ссылкой", callback_data="forward_footer_link_full")],
                [InlineKeyboardButton("🗑️ Удалить ссылку", callback_data="forward_footer_link_delete")],
                [InlineKeyboardButton("◀️ Назад", callback_data="forward_back_to_settings")]
            ])
        else:
            text = "В приписке нет гиперссылки. Введите URL для добавления гиперссылки:"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Отмена", callback_data="forward_back_to_settings")]
            ])
            user_states[user_id]["state"] = FSM_FORWARD_FOOTER_LINK
            await callback_query.edit_message_text(text, reply_markup=keyboard)
            await callback_query.answer()
            return
        await callback_query.edit_message_text(text, reply_markup=keyboard)
        await callback_query.answer()
        return
    
    elif action == "footer_link_change":
        # Показываем форму для ввода нового URL, не дублируем текст и не показываем меню
        text = "Введите новый URL для гиперссылки:"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Отмена", callback_data="forward_back_to_settings")]
        ])
        await callback_query.edit_message_text(text, reply_markup=keyboard)
        user_states[user_id]["state"] = FSM_FORWARD_FOOTER_LINK
        await callback_query.answer()
        return
    elif action == "footer_link_text":
        footer_text = forwarding_config.get("footer_text", "")
        text = f"Текущая приписка: {footer_text}\n\nВведите часть текста приписки, которую нужно сделать гиперссылкой:"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Вся приписка как ссылка", callback_data="forward_footer_link_full")],
            [InlineKeyboardButton("◀️ Отмена", callback_data="forward_back_to_settings")]
        ])
        await callback_query.edit_message_text(text, reply_markup=keyboard)
        user_states[user_id]["state"] = FSM_FORWARD_FOOTER_LINK_TEXT
        await callback_query.answer()
        return
    
    elif action == "footer_link_full":
        # Делаем всю приписку гиперссылкой
        forwarding_config["footer_full_link"] = True
        
        # Если был текст ссылки, удаляем его (теперь вся приписка - ссылка)
        if "footer_link_text" in forwarding_config:
            del forwarding_config["footer_link_text"]
            
        # Обновляем настройки
        user_states[user_id]["forward_settings"] = forwarding_config
        
        await callback_query.answer("✅ Вся приписка будет гиперссылкой!")
        await show_forwarding_settings(client, callback_query.message, user_id)
        user_states[user_id]["state"] = FSM_FORWARD_SETTINGS
        return
    
    elif action == "footer_link_delete":
        # Удаляем настройки гиперссылки
        if "footer_link" in forwarding_config:
            del forwarding_config["footer_link"]
        if "footer_link_text" in forwarding_config:
            del forwarding_config["footer_link_text"]
        if "footer_full_link" in forwarding_config:
            del forwarding_config["footer_full_link"]
            
        # Обновляем настройки
        user_states[user_id]["forward_settings"] = forwarding_config
        
        await callback_query.answer("✅ Гиперссылка удалена!")
        await show_forwarding_settings(client, callback_query.message, user_id)
        user_states[user_id]["state"] = FSM_FORWARD_SETTINGS
        return
        
    elif action == "back_to_settings":
        # Возвращаемся к настройкам пересылки
        await show_forwarding_settings(client, callback_query.message, user_id)
        await callback_query.answer()
        return

    if data == 'start_monitoring':
        settings = user_states[user_id]['monitor_settings']
        monitor_channel_id = user_states[user_id]['monitor_channel_id']
        monitor_target_channel = user_states[user_id]['monitor_target_channel']
        # --- ДОБАВЛЯЕМ ПЛАТНЫЕ ПАРАМЕТРЫ ИЗ forward_settings ---
        forward_settings = user_states[user_id].get('forward_settings', {})
        # ЛОГИРУЕМ forward_settings ДО копирования
        logger.info(f"[DEBUG][MONITOR] forward_settings перед копированием: {forward_settings}")
        for key in [
            'paid_content_mode',
            'paid_content_stars',
            'paid_content_hashtag',
            'paid_content_every',
            'paid_content_chance',
        ]:
            if key in forward_settings and forward_settings[key] is not None:
                settings[key] = forward_settings[key]
        # ЛОГИРУЕМ settings после копирования
        logger.info(f"[DEBUG][MONITOR] monitor_settings после копирования: {settings}")
        if 'paid_content_every' in settings:
            try:
                settings['paid_content_every'] = int(settings['paid_content_every'])
            except Exception:
                settings['paid_content_every'] = 1
        monitor_config = {
            "user_id": user_id,
            "source_channel_id": monitor_channel_id,
            "target_channel_id": monitor_target_channel,
            "parse_mode": settings.get('parse_mode', 'all'),
            "hashtag_filter": settings.get('hashtag_filter'),
            "delay_seconds": settings.get('delay_seconds', 0),
            "footer_text": settings.get('footer_text', ''),
            "text_mode": settings.get('text_mode', 'hashtags_only'),
            "max_posts": settings.get('max_posts'),
            "hide_sender": settings.get('hide_sender', True),
            "paid_content_mode": settings.get('paid_content_mode', 'off'),
            "paid_content_stars": settings.get('paid_content_stars', 0),
            "paid_content_hashtag": settings.get('paid_content_hashtag'),
            "paid_content_every": settings.get('paid_content_every'),
            "paid_content_chance": settings.get('paid_content_chance'),
            "source_channel_username": user_states[user_id].get('forward_channel_username'),
            "target_channel_username": user_states[user_id].get('forward_target_username'),
        }
        logger.info(f"[DEBUG][MONITOR] Итоговый monitor_config: {monitor_config}")
        logger.info(f"[BOT][MONITOR] Отправляю запрос на мониторинг: {monitor_config}")
        try:
            # Используем новый метод API клиента для запуска мониторинга
            response = await api_client.start_monitoring(str(monitor_channel_id), str(monitor_target_channel), monitor_config)
            if response.get("status") == "success":
                await api_client.add_user_monitoring(user_id, str(monitor_channel_id), str(monitor_target_channel))
                await callback_query.answer('Мониторинг запущен!')
                await client.send_message(callback_query.message.chat.id, f"Мониторинг запущен!\n\nБот будет следить за каналом и публиковать новые посты в {monitor_target_channel}.", reply_markup=get_main_keyboard())
            else:
                await callback_query.answer('Ошибка запуска мониторинга!', show_alert=True)
                await client.send_message(callback_query.message.chat.id, f"Ошибка запуска мониторинга: {response.get('message', 'Неизвестная ошибка')}", reply_markup=get_main_keyboard())
        except Exception as e:
            await callback_query.answer('Ошибка запуска мониторинга!', show_alert=True)
            await client.send_message(callback_query.message.chat.id, f"Ошибка запуска мониторинга: {e}", reply_markup=get_main_keyboard())
        user_states[user_id]["state"] = FSM_MAIN_MENU
        return

    if data == 'publish_now':
        if 'publish_settings' not in user_states[user_id]:
            user_states[user_id]['publish_settings'] = {'delay': 0, 'mode': 'все', 'text_mode': 'с текстом', 'footer': '', 'order': 'old_to_new', 'max_posts': 0}
        publish_settings = user_states[user_id]['publish_settings']
        channel_id = user_states[user_id].get('publish_channel_id')
        target_channel_id = user_states[user_id].get('publish_target_channel')
        payload = {
            'channel_id': channel_id,
            'target_channel_id': target_channel_id,
            'posting_delay': publish_settings.get('delay', 0),
            'order': publish_settings.get('order', 'old_to_new'),
            'text_mode': publish_settings.get('text_mode', 'с текстом'),
            'mode': publish_settings.get('mode', 'все'),
            'footer': publish_settings.get('footer', ''),
            'max_posts': publish_settings.get('max_posts', 0),
            'parse_mode': publish_settings.get('parse_mode', 'html'),
            'disable_web_page_preview': publish_settings.get('disable_web_page_preview', False),
            'disable_notification': publish_settings.get('disable_notification', False),
            'protect_content': publish_settings.get('protect_content', False),
            'add_source_link': publish_settings.get('add_source_link', True),
            'add_hashtags': publish_settings.get('add_hashtags', True),
            'custom_hashtags': publish_settings.get('custom_hashtags', []),
            'watermark_text': publish_settings.get('watermark_text'),
            'max_message_length': publish_settings.get('max_message_length', 4096),
            'truncate_long_messages': publish_settings.get('truncate_long_messages', True),
            'add_footer': publish_settings.get('add_footer', True),
            'footer_text': publish_settings.get('footer_text'),
            'add_header': publish_settings.get('add_header', True),
            'header_text': publish_settings.get('header_text'),
            'filter_words': publish_settings.get('filter_words', []),
            'replace_words': publish_settings.get('replace_words', {}),
            'add_timestamp': publish_settings.get('add_timestamp', True),
            'timestamp_format': publish_settings.get('timestamp_format', '%Y-%m-%d %H:%M:%S'),
            'timezone': publish_settings.get('timezone', 'UTC'),
            'max_posts_per_day': publish_settings.get('max_posts_per_day'),
            'min_posts_per_day': publish_settings.get('min_posts_per_day'),
            'posting_interval': publish_settings.get('posting_interval'),
        }
        try:
            async with httpx.AsyncClient() as api:
                resp = await api.post(f'{config.PARSER_SERVICE_URL}/publish', json=payload)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('status') == 'ok':
                    await callback_query.answer('Публикация запущена!')
                    await client.send_message(callback_query.message.chat.id, f"✅ {data.get('detail', 'Публикация запущена!')}")
                    published_count = await get_actual_published_count(channel_id, target_channel_id)
                    async with httpx.AsyncClient() as client_api:
                        stats_resp = await client_api.get(f"{config.PARSER_SERVICE_URL}/channel/stats/{channel_id}")
                        stats = stats_resp.json() if stats_resp.status_code == 200 else {}
                        stat_text = get_publish_stat_text(stats, publish_settings, published_count)
                        inline_kb = InlineKeyboardMarkup([[InlineKeyboardButton('Публиковать', callback_data='publish_now')]])
                        await client.send_message(callback_query.message.chat.id, stat_text, reply_markup=inline_kb)
                    await show_main_menu(client, callback_query.message, "Выберите действие:")
                    user_states[user_id] = {"state": FSM_MAIN_MENU}
                else:
                    await callback_query.answer('Ошибка публикации!', show_alert=True)
                    await client.send_message(callback_query.message.chat.id, f"❌ Ошибка публикации: {data.get('detail', 'Неизвестная ошибка')}")
            else:
                await callback_query.answer('Ошибка публикации!', show_alert=True)
                await client.send_message(callback_query.message.chat.id, f"❌ Ошибка публикации: {resp.text}")
        except Exception as e:
            await callback_query.answer('Ошибка публикации!', show_alert=True)
            await client.send_message(callback_query.message.chat.id, f"❌ Ошибка публикации: {e}")
        return

    # --- Остальная логика (pagination, showmedia и т.д.) ---
    if data.startswith('page_'):
        page_idx = int(data.split("_")[1])
        pag = user_states.get(user_id, {}).get('pagination')
        if not pag:
            await callback_query.answer("Нет данных для пагинации", show_alert=True)
            return
        posts = pag['posts']
        total_posts = pag['total_posts']
        total_pages = pag['total_pages']
        page_size = pag['page_size']
        msg_id = pag['msg_id']
        chat_id = pag['chat_id']
        def render_page(page_idx):
            start = page_idx * page_size
            end = start + page_size
            chunk = posts[start:end]
            text = []
            for post in chunk:
                if post['type'] == 'media_group':
                    text.append(f"Пост (медиагруппа):\n  media_group_id: {post['media_group_id']}\n  id сообщений: {', '.join(str(i) for i in post['ids'])}\n  Текст: {textwrap.shorten(post['text'], width=100)}")
                else:
                    text.append(f"Пост (одиночный):\n  id: {post['ids'][0]}\n  Текст: {textwrap.shorten(post['text'], width=100)}")
            return '\n\n'.join(text)
        text = render_page(page_idx)
        nav_buttons = []
        if total_pages > 1:
            nav_buttons.append([
                InlineKeyboardButton('⏪', callback_data=f'page_0'),
                InlineKeyboardButton('◀️', callback_data=f'page_{max(0, page_idx-1)}'),
                InlineKeyboardButton(f'{page_idx+1}/{total_pages}', callback_data='noop'),
                InlineKeyboardButton('▶️', callback_data=f'page_{min(total_pages-1, page_idx+1)}'),
                InlineKeyboardButton('⏩', callback_data=f'page_{total_pages-1}')
            ])
        nav_buttons.append([InlineKeyboardButton('Показать медиа этой страницы', callback_data=f'showmedia_{page_idx}')])
        nav_buttons.append([InlineKeyboardButton('Закрыть', callback_data='close_pagination')])
        sent = await client.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=f"Всего постов: {total_posts}\nСтраница {page_idx+1} из {total_pages}\n\n{text}",
            reply_markup=InlineKeyboardMarkup(nav_buttons)
        )
        await callback_query.answer()
        return
    if data == 'close_pagination':
        pag = user_states.get(user_id, {}).get('pagination')
        if pag:
            try:
                await client.delete_messages(pag['chat_id'], pag['msg_id'])
            except Exception:
                pass
            user_states[user_id]['pagination'] = None
        await callback_query.answer('Пагинация закрыта', show_alert=True)
        return
    if data == 'noop':
        await callback_query.answer()
        return
    
    # --- Обработчики очистки истории пересылки ---
    if data in ["clear_all_history", "clear_channel_history", "clear_target_history", "back_to_settings"]:
        await handle_clear_history_callback(client, callback_query, user_id)
        return
    
    # --- Обработчики настроек пересылки ---
    if data == "forward_parse_mode":
        # Показываем меню выбора режима парсинга
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📝 Все сообщения", callback_data="forward_parse_all"),
                InlineKeyboardButton("🏷️ Только с хэштегами", callback_data="forward_parse_hashtags")
            ],
            [InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]
        ])
        await safe_edit_callback_message(
            callback_query,
            "Выберите режим парсинга:",
            reply_markup=kb
        )
        return
    
    if data == "forward_parse_all":
        user_states[user_id]['forward_settings']['parse_mode'] = 'all'
        # Очищаем хэштег при переключении на "Все сообщения"
        user_states[user_id]['forward_settings']['hashtag_filter'] = None
        await callback_query.answer("✅ Режим: все сообщения")
        await show_forwarding_settings(client, callback_query.message, user_id)
        return
    
    if data == "forward_parse_hashtags":
        user_states[user_id]['forward_settings']['parse_mode'] = 'hashtags'
        await callback_query.answer("✅ Режим: только с хэштегами")
        await show_forwarding_settings(client, callback_query.message, user_id)
        return
    
    if data == "forward_hashtag":
        # Запрашиваем ввод хэштега
        await safe_edit_callback_message(
            callback_query,
            "Введите хэштег для фильтрации (например: #news):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]])
        )
        user_states[user_id]['forward_state'] = 'hashtag_input'
        return
    
    if data == "forward_delay":
        # Запрашиваем ввод задержки
        current_delay = user_states[user_id]['forward_settings'].get('delay_seconds', 0)
        await safe_edit_callback_message(
            callback_query,
            f"Текущая задержка: {current_delay} сек\n\nВведите новую задержку в секундах (0 - без задержки):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]])
        )
        user_states[user_id]['forward_state'] = 'delay_input'
        return
    
    if data == "forward_footer":
        # Запрашиваем ввод приписки
        current_footer = user_states[user_id]['forward_settings'].get('footer_text', '')
        await safe_edit_callback_message(
            callback_query,
            f"Текущая приписка: {current_footer or 'Нет'}\n\nВведите новую приписку (или 'убрать' для удаления):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]])
        )
        user_states[user_id]['forward_state'] = 'footer_input'
        return
    
    if data == "forward_text_mode":
        # Показываем меню выбора режима текста
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📝 Как есть", callback_data="forward_text_as_is"),
                InlineKeyboardButton("🏷️ Только хэштеги", callback_data="forward_text_hashtags_only")
            ],
            [
                InlineKeyboardButton("❌ Без текста", callback_data="forward_text_no_text"),
                InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")
            ]
        ])
        await safe_edit_callback_message(
            callback_query,
            "Выберите режим обработки текста:",
            reply_markup=kb
        )
        return
    
    if data == "forward_text_as_is":
        user_states[user_id]['forward_settings']['text_mode'] = 'as_is'
        await callback_query.answer("✅ Текст: как есть")
        await show_forwarding_settings(client, callback_query.message, user_id)
        return
    
    if data == "forward_text_hashtags_only":
        user_states[user_id]['forward_settings']['text_mode'] = 'hashtags_only'
        await callback_query.answer("✅ Текст: только хэштеги")
        await show_forwarding_settings(client, callback_query.message, user_id)
        return
    
    if data == "forward_text_no_text":
        user_states[user_id]['forward_settings']['text_mode'] = 'no_text'
        await callback_query.answer("✅ Текст: без текста")
        await show_forwarding_settings(client, callback_query.message, user_id)
        return
    
    if data == "forward_limit":
        # Запрашиваем ввод лимита
        current_limit = user_states[user_id]['forward_settings'].get('max_posts')
        await safe_edit_callback_message(
            callback_query,
            f"Текущий лимит: {current_limit or 'Без лимита'}\n\nВведите новый лимит постов (или '0' для снятия лимита):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]])
        )
        user_states[user_id]['forward_state'] = 'limit_input'
        return
    
    if data == "forward_paid_content":
        # Показываем меню выбора режима платных постов
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Отключить", callback_data="forward_paid_off"),
                InlineKeyboardButton("По хэштегу", callback_data="forward_paid_hashtag")
            ],
            [
                InlineKeyboardButton("Рандомно", callback_data="forward_paid_random"),
                InlineKeyboardButton("По хэштегу + рандомно", callback_data="forward_paid_hashtag_random")
            ],
            [
                InlineKeyboardButton("По хэштегу + выбор", callback_data="forward_paid_hashtag_select"),
                InlineKeyboardButton("Выбор", callback_data="forward_paid_select")
            ],
            [InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]
        ])
        await safe_edit_callback_message(
            callback_query,
            "Выберите режим платных постов:",
            reply_markup=kb
        )
        return
    if data == "forward_paid_select":
        user_states[user_id]['forward_settings']['paid_content_mode'] = 'select'
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_paid_content")]])
        await safe_edit_callback_message(
            callback_query,
            "Каждый какой пост делать платным? (например, 3 — каждый третий пост будет платным)",
            reply_markup=kb
        )
        user_states[user_id]['forward_state'] = 'paid_content_every_input'
        return
    if data == "forward_paid_off":
        user_states[user_id]['forward_settings']['paid_content_mode'] = 'off'
        user_states[user_id]['forward_settings']['paid_content_stars'] = 0
        user_states[user_id]['forward_settings']['paid_content_hashtag'] = None
        user_states[user_id]['forward_settings']['paid_content_chance'] = None
        await callback_query.answer("Платные посты отключены!")
        await show_forwarding_settings(client, callback_query.message, user_id)
        return
    if data == "forward_paid_hashtag":
        user_states[user_id]['forward_settings']['paid_content_mode'] = 'hashtag'
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_paid_content")]])
        await safe_edit_callback_message(
            callback_query,
            "Введите хэштег (без #), который будет делать пост платным:",
            reply_markup=kb
        )
        user_states[user_id]['forward_state'] = 'paid_content_hashtag_input'
        return
    if data == "forward_paid_random":
        user_states[user_id]['forward_settings']['paid_content_mode'] = 'random'
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_paid_content")]])
        await safe_edit_callback_message(
            callback_query,
            "Введите шанс (от 1 до 10), с которым пост будет платным:",
            reply_markup=kb
        )
        user_states[user_id]['forward_state'] = 'paid_content_chance_input'
        return
    if data == "forward_paid_hashtag_random":
        user_states[user_id]['forward_settings']['paid_content_mode'] = 'hashtag_random'
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_paid_content")]])
        await safe_edit_callback_message(
            callback_query,
            "Введите хэштег (без #), который будет делать пост платным:",
            reply_markup=kb
        )
        user_states[user_id]['forward_state'] = 'paid_content_hashtag_input'
        user_states[user_id]['forward_settings']['paid_content_chance'] = None
        return
    if data == "forward_paid_hashtag_select":
        user_states[user_id]['forward_settings']['paid_content_mode'] = 'hashtag_select'
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_paid_content")]])
        await safe_edit_callback_message(
            callback_query,
            "Введите хэштег (без #), который будет делать пост платным:",
            reply_markup=kb
        )
        user_states[user_id]['forward_state'] = 'paid_content_hashtag_input_for_every'
        return
    # Обработка ввода стоимости (звезд), хэштега и шанса для платных постов
    if user_states[user_id].get('forward_state') == 'paid_content_stars_input' and callback_query.message:
        text = callback_query.message.text
        # Ожидаем, что пользователь отправит число в чат (реализовать через text_handler)
        # Здесь просто возвращаемся назад по кнопке
        return
    if user_states[user_id].get('forward_state') == 'paid_content_hashtag_input' and callback_query.message:
        text = callback_query.message.text
        return
    if user_states[user_id].get('forward_state') == 'paid_content_chance_input' and callback_query.message:
        text = callback_query.message.text
        return
    
    if data == "forward_save":
        # Сохраняем конфигурацию
        success = await save_forwarding_config_api(user_id)
        if success:
            try:
                await callback_query.answer("✅ Настройки сохранены!")
            except Exception:
                pass
            await show_forwarding_menu(client, callback_query.message, user_id)
        else:
            try:
                await callback_query.answer("❌ Ошибка сохранения настроек", show_alert=True)
            except Exception:
                pass
        return
    
    if data == "forward_back":
        # Новый возврат: зависит от текущего состояния
        state = user_states[user_id].get("state")
        if state == FSM_FORWARD_SETTINGS:
            # После статистики — возвращаем к выбору целевого канала
            kb = await get_target_channel_history_keyboard(user_id)
            await safe_edit_callback_message(
                callback_query,
                "Выберите целевой канал для пересылки:",
                reply_markup=kb or ReplyKeyboardRemove()
            )
            user_states[user_id]["state"] = FSM_FORWARD_TARGET
            return
        elif state == FSM_FORWARD_MONITORING or state == FSM_FORWARD_RUNNING:
            # После запуска пересылки/мониторинга — возвращаем к статистике
            stats = await api_client.get_channel_stats(str(user_states[user_id]['forward_channel_id']))
            stat_text = format_channel_stats(stats)
            channel_id = user_states[user_id]['forward_channel_id']
            target_channel = user_states[user_id].get('forward_target_channel')
            await safe_edit_callback_message(
                callback_query,
                f"📊 Статистика канала {user_states[user_id]['forward_channel_title']}:\n\n{stat_text}\n\nВыберите действие:",
                reply_markup=get_forwarding_inline_keyboard(channel_id, target_channel)
            )
            user_states[user_id]["state"] = FSM_FORWARD_SETTINGS
            return
        else:
            # По умолчанию — главное меню
            await show_main_menu(client, callback_query.message, "Выберите действие:")
            user_states[user_id]["state"] = FSM_MAIN_MENU
            return
    
    # --- Новые обработчики настроек ---
    if data == "forward_direction":
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 От старых к новым", callback_data="forward_direction_forward"),
                InlineKeyboardButton("🔄 От новых к старым", callback_data="forward_direction_backward")
            ],
            [InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]
        ])
        await safe_edit_callback_message(
            callback_query,
            "Выберите направление парсинга:",
            reply_markup=kb
        )
        return
    if data == "forward_direction_forward":
        user_states[user_id]['forward_settings']['parse_direction'] = 'forward'
        await callback_query.answer("Направление: от старых к новым!")
        await show_forwarding_settings(client, callback_query.message, user_id)
        return
    if data == "forward_direction_backward":
        user_states[user_id]['forward_settings']['parse_direction'] = 'backward'
        await callback_query.answer("Направление: от новых к старым!")
        await show_forwarding_settings(client, callback_query.message, user_id)
        return
    if data == "forward_media_filter":
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📄 Все сообщения", callback_data="forward_media_all"),
                InlineKeyboardButton("📷 Только с медиа", callback_data="forward_media_only")
            ],
            [InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]
        ])
        await safe_edit_callback_message(
            callback_query,
            "Выберите фильтр медиа:",
            reply_markup=kb
        )
        return
    if data == "forward_media_all":
        user_states[user_id]['forward_settings']['media_filter'] = 'all'
        await callback_query.answer("Фильтр: все сообщения!")
        await show_forwarding_settings(client, callback_query.message, user_id)
        return
    if data == "forward_media_only":
        user_states[user_id]['forward_settings']['media_filter'] = 'media_only'
        await callback_query.answer("Фильтр: только с медиа!")
        await show_forwarding_settings(client, callback_query.message, user_id)
        return
    if data == "forward_range":
        channel_id = user_states[user_id].get('forward_channel_id')
        if not channel_id:
            await callback_query.answer("Сначала выберите канал!", show_alert=True)
            return
        try:
            async with httpx.AsyncClient() as client_api:
                resp = await client_api.get(f"{config.PARSER_SERVICE_URL}/channel/last-message/{channel_id}")
                if resp.status_code == 200:
                    data_api = resp.json()
                    last_id = data_api.get('last_message_id')
                    user_states[user_id]['forward_settings']['last_message_id'] = last_id
                    msg = f"Последний ID сообщения в канале: {last_id}\n\nВведите ID сообщения для начала диапазона:"
                    kb = ReplyKeyboardMarkup([[KeyboardButton("🔙 Назад")]], resize_keyboard=True)
                    await safe_edit_callback_message(callback_query, msg, reply_markup=kb)
                    user_states[user_id]['forward_state'] = 'range_start_input'
                else:
                    await callback_query.answer("Ошибка получения последнего ID!", show_alert=True)
                    await show_forwarding_settings(client, callback_query.message, user_id)
            return
        except Exception as e:
            await callback_query.answer(f"Ошибка: {str(e)}", show_alert=True)
            await show_forwarding_settings(client, callback_query.message, user_id)
        return
    
    if data == "forward_last_id":
        channel_id = user_states[user_id].get('forward_channel_id')
        if not channel_id:
            await callback_query.answer("Сначала выберите канал!", show_alert=True)
            return
        try:
            async with httpx.AsyncClient() as client_api:
                resp = await client_api.get(f"{config.PARSER_SERVICE_URL}/channel/last-message/{channel_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    last_id = data.get('last_message_id')
                    await callback_query.answer(f"Последний ID: {last_id}")
                    info_text = f"📊 Информация о последнем сообщении:\n\n"
                    info_text += f"ID: {last_id}\n"
                    info_text += f"Дата: {data.get('last_message_date', 'N/A')}\n"
                    info_text += f"Есть медиа: {'Да' if data.get('has_media') else 'Нет'}\n"
                    info_text += f"Тип медиа: {data.get('media_type', 'N/A')}\n"
                    info_text += f"Длина текста: {data.get('text_length', 0)} символов"
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]])
                    await safe_edit_callback_message(
                        callback_query,
                        info_text,
                        reply_markup=kb
                    )
                else:
                    await callback_query.answer("Ошибка получения ID!", show_alert=True)
        except Exception as e:
            await callback_query.answer(f"Ошибка: {str(e)}", show_alert=True)
        return
    
    if data == "forward_back_to_settings":
        # Возвращаемся к настройкам
        await show_forwarding_settings(client, callback_query.message, user_id)
        user_states[user_id]["state"] = FSM_FORWARD_SETTINGS
        await callback_query.answer()
        return
    
    if data.startswith('showmedia_'):
        page_idx = int(data.split('_')[1])
        pag = user_states.get(user_id, {}).get('pagination')
        if not pag:
            try:
                await callback_query.answer("Нет данных для пагинации", show_alert=True)
            except Exception:
                pass
            return
        # Сразу отвечаем на callback, чтобы не было ошибки Telegram
        try:
            await callback_query.answer('Загрузка медиа...', show_alert=False)
        except Exception:
            pass
        posts = pag['posts']
        page_size = pag['page_size']
        start = page_idx * page_size
        end = start + page_size
        chunk = posts[start:end]
        media_sent = 0
        for idx, post in enumerate(chunk):
            print(f"[DEBUG] Обработка поста #{idx+1} на странице: {post}")
            # Для медиагруппы отправляем все фото/видео одной группой
            if post['type'] == 'media_group':
                media_files = post.get('media_files') or []
                print(f"[DEBUG] media_files для медиагруппы: {media_files}")
                media_objs = []
                for f in media_files:
                    print(f"[DEBUG] Проверяю файл: {f}, существует: {os.path.exists(f)}")
                    if f and os.path.exists(f):
                        if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                            media_objs.append(InputMediaPhoto(f))
                        elif f.lower().endswith(('.mp4', '.mov', '.avi', '.webm')):
                            media_objs.append(InputMediaVideo(f))
                        else:
                            print(f"[DEBUG] Неизвестный тип файла: {f}")
                    else:
                        print(f"[DEBUG] Файл не найден: {f}")
                if media_objs:
                    try:
                        await client.send_media_group(callback_query.message.chat.id, media=media_objs)
                        media_sent += 1
                        print(f"[DEBUG] Отправлено медиагрупп: {media_objs}")
                    except Exception as e:
                        print(f"[DEBUG] Ошибка отправки медиагруппы: {e}")
                else:
                    await client.send_message(callback_query.message.chat.id, "Нет медиафайлов для этого поста.")
                    print(f"[DEBUG] Нет медиафайлов для медиагруппы!")
            else:
                media_files = post.get('media_files') or []
                print(f"[DEBUG] media_files для одиночного поста: {media_files}")
                found = False
                for f in media_files:
                    print(f"[DEBUG] Проверяю файл: {f}, существует: {os.path.exists(f)}")
                    if f and os.path.exists(f):
                        found = True
                        if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                            try:
                                with open(f, "rb") as img:
                                    await client.send_photo(callback_query.message.chat.id, img)
                                media_sent += 1
                                print(f"[DEBUG] Отправлено фото: {f}")
                            except Exception as e:
                                print(f"[DEBUG] Ошибка отправки фото: {e}")
                        elif f.lower().endswith(('.mp4', '.mov', '.avi', '.webm')):
                            try:
                                with open(f, "rb") as vid:
                                    await client.send_video(callback_query.message.chat.id, vid)
                                media_sent += 1
                                print(f"[DEBUG] Отправлено видео: {f}")
                            except Exception as e:
                                print(f"[DEBUG] Ошибка отправки видео: {e}")
                        else:
                            print(f"[DEBUG] Неизвестный тип файла: {f}")
                    else:
                        print(f"[DEBUG] Файл не найден: {f}")
                if not found:
                    await client.send_message(callback_query.message.chat.id, "Нет медиафайлов для этого поста.")
                    print(f"[DEBUG] Нет медиафайлов для одиночного поста!")
        print(f"[DEBUG] Всего отправлено медиа: {media_sent}")
        # После отправки медиа отправляем обычное сообщение с результатом
        await client.send_message(callback_query.message.chat.id, f'Медиа отправлено: {media_sent}')
        return
    
    # --- Обработчики очистки истории пересылки ---
    if data == "forward_clear_history":
        # Показываем меню очистки истории
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗑️ Очистить всё", callback_data="forward_clear_all_history"),
                InlineKeyboardButton("📺 Только канал", callback_data="forward_clear_channel_history")
            ],
            [
                InlineKeyboardButton("🎯 Только целевой", callback_data="forward_clear_target_history"),
                InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")
            ]
        ])
        await safe_edit_callback_message(
            callback_query,
            "Выберите что очистить:",
            reply_markup=kb
        )
        return
    
    if data == "forward_clear_all_history":
        # Очищаем всю историю
        channel_id = user_states[user_id].get('forward_channel_id')
        target_channel = user_states[user_id].get('forward_target_channel')
        result = await clear_forwarding_history_api(channel_id, target_channel)
        if result.get('status') == 'success':
            try:
                await callback_query.answer("✅ Вся история очищена!")
            except Exception:
                pass
        else:
            try:
                await callback_query.answer("❌ Ошибка очистки истории", show_alert=True)
            except Exception:
                pass
        await show_forwarding_settings(client, callback_query.message, user_id)
        return
    
    if data == "forward_clear_channel_history":
        # Очищаем историю канала
        channel_id = user_states[user_id].get('forward_channel_id')
        result = await clear_forwarding_history_api(channel_id=channel_id)
        if result.get('status') == 'success':
            try:
                await callback_query.answer("✅ История канала очищена!")
            except Exception:
                pass
        else:
            try:
                await callback_query.answer("❌ Ошибка очистки истории", show_alert=True)
            except Exception:
                pass
        await show_forwarding_settings(client, callback_query.message, user_id)
        return
    
    if data == "forward_clear_target_history":
        # Очищаем историю целевого канала
        target_channel = user_states[user_id].get('forward_target_channel')
        result = await clear_forwarding_history_api(target_channel=target_channel)
        if result.get('status') == 'success':
            try:
                await callback_query.answer("✅ История целевого канала очищена!")
            except Exception:
                pass
        else:
            try:
                await callback_query.answer("❌ Ошибка очистки истории", show_alert=True)
            except Exception:
                pass
        await show_forwarding_settings(client, callback_query.message, user_id)
        return
    
    if data == "forward_history_stats":
        # Показываем статистику истории
        channel_id = user_states[user_id].get('forward_channel_id')
        target_channel = user_states[user_id].get('forward_target_channel')
        
        # Получаем информацию о каналах
        channel_info = await get_channel_info(str(channel_id))
        target_info = await get_target_channel_info(target_channel)
        
        # Формируем отображаемые имена каналов
        channel_display = channel_info.get('title', f"Канал {channel_id}")
        target_display = target_info.get('title', target_channel)
        
        stats = await get_forwarding_history_stats_api(channel_id, target_channel)
        
        if stats.get('status') == 'success':
            stats_data = stats.get('data', {})
            stats_text = f"📊 Статистика истории пересылки:\n\n"
            stats_text += f"📺 Канал: {channel_display}\n"
            stats_text += f"🎯 Целевой: {target_display}\n"
            stats_text += f"📤 Всего переслано: {stats_data.get('total_forwarded', 0)}\n"
            stats_text += f"📅 Сегодня: {stats_data.get('today_forwarded', 0)}\n"
            stats_text += f"📅 Вчера: {stats_data.get('yesterday_forwarded', 0)}\n"
            stats_text += f"📅 За неделю: {stats_data.get('week_forwarded', 0)}\n"
            stats_text += f"📅 За месяц: {stats_data.get('month_forwarded', 0)}\n"
        else:
            stats_text = "❌ Не удалось получить статистику истории"
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]])
        await safe_edit_callback_message(callback_query, stats_text, reply_markup=kb)
        return
    
    if data.startswith('showmedia_'):
        page_idx = int(data.split('_')[1])
        pag = user_states.get(user_id, {}).get('pagination')
        if not pag:
            try:
                await callback_query.answer("Нет данных для пагинации", show_alert=True)
            except Exception:
                pass
            return
        # Сразу отвечаем на callback, чтобы не было ошибки Telegram
        try:
            await callback_query.answer('Загрузка медиа...', show_alert=False)
        except Exception:
            pass
        posts = pag['posts']
        page_size = pag['page_size']
        start = page_idx * page_size
        end = start + page_size
        chunk = posts[start:end]
        media_sent = 0
        for idx, post in enumerate(chunk):
            print(f"[DEBUG] Обработка поста #{idx+1} на странице: {post}")
            # Для медиагруппы отправляем все фото/видео одной группой
            if post['type'] == 'media_group':
                media_files = post.get('media_files') or []
                print(f"[DEBUG] media_files для медиагруппы: {media_files}")
                media_objs = []
                for f in media_files:
                    print(f"[DEBUG] Проверяю файл: {f}, существует: {os.path.exists(f)}")
                    if f and os.path.exists(f):
                        if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                            media_objs.append(InputMediaPhoto(f))
                        elif f.lower().endswith(('.mp4', '.mov', '.avi', '.webm')):
                            media_objs.append(InputMediaVideo(f))
                        else:
                            print(f"[DEBUG] Неизвестный тип файла: {f}")
                    else:
                        print(f"[DEBUG] Файл не найден: {f}")
                if media_objs:
                    try:
                        await client.send_media_group(callback_query.message.chat.id, media=media_objs)
                        media_sent += 1
                        print(f"[DEBUG] Отправлено медиагрупп: {media_objs}")
                    except Exception as e:
                        print(f"[DEBUG] Ошибка отправки медиагруппы: {e}")
                else:
                    await client.send_message(callback_query.message.chat.id, "Нет медиафайлов для этого поста.")
                    print(f"[DEBUG] Нет медиафайлов для медиагруппы!")
            else:
                media_files = post.get('media_files') or []
                print(f"[DEBUG] media_files для одиночного поста: {media_files}")
                found = False
                for f in media_files:
                    print(f"[DEBUG] Проверяю файл: {f}, существует: {os.path.exists(f)}")
                    if f and os.path.exists(f):
                        found = True
                        if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                            try:
                                with open(f, "rb") as img:
                                    await client.send_photo(callback_query.message.chat.id, img)
                                media_sent += 1
                                print(f"[DEBUG] Отправлено фото: {f}")
                            except Exception as e:
                                print(f"[DEBUG] Ошибка отправки фото: {e}")
                        elif f.lower().endswith(('.mp4', '.mov', '.avi', '.webm')):
                            try:
                                with open(f, "rb") as vid:
                                    await client.send_video(callback_query.message.chat.id, vid)
                                media_sent += 1
                                print(f"[DEBUG] Отправлено видео: {f}")
                            except Exception as e:
                                print(f"[DEBUG] Ошибка отправки видео: {e}")
                        else:
                            print(f"[DEBUG] Неизвестный тип файла: {f}")
                    else:
                        print(f"[DEBUG] Файл не найден: {f}")
                if not found:
                    await client.send_message(callback_query.message.chat.id, "Нет медиафайлов для этого поста.")
                    print(f"[DEBUG] Нет медиафайлов для одиночного поста!")
        print(f"[DEBUG] Всего отправлено медиа: {media_sent}")
        # После отправки медиа отправляем обычное сообщение с результатом
        await client.send_message(callback_query.message.chat.id, f'Медиа отправлено: {media_sent}')
        return
    
    # --- Обработчики управления пересылкой ---
    if data == "forward_start":
        # Раньше здесь была проверка прав userbot через check_userbot_admin_rights
        # Теперь сразу запускаем пересылку через API
        try:
            success = await start_forwarding_api(user_id)
            if success:
                try:
                    await callback_query.answer("✅ Пересылка запущена!")
                except Exception:
                    pass
                await show_forwarding_menu(client, callback_query.message, user_id)
            else:
                try:
                    await callback_query.answer("❌ Ошибка запуска пересылки", show_alert=True)
                except Exception:
                    pass
        except Exception as e:
            try:
                await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
            except Exception:
                pass
        return
    
    if data == "forward_stop":
        # Останавливаем пересылку
        try:
            success = await stop_forwarding_api(user_id)
            if success:
                try:
                    await callback_query.answer("⏸️ Пересылка остановлена!")
                except Exception:
                    pass
                await show_forwarding_menu(client, callback_query.message, user_id)
            else:
                try:
                    await callback_query.answer("❌ Ошибка остановки пересылки", show_alert=True)
                except Exception:
                    pass
        except Exception as e:
            try:
                await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
            except Exception:
                pass
        return
    
    if data == "forward_parse_and_forward":
        # Запускаем парсинг и пересылку в фоновом режиме
        try:
            result = await start_forwarding_parsing_api(user_id)
            if result.get("success"):
                task_id = result.get("task_id", "")
                message_text = result.get("message", "✅ Парсинг и пересылка запущены в фоновом режиме!")
                if task_id:
                    message_text += f"\n\n🆔 ID задачи: {task_id[:20]}..."
                    await safe_edit_callback_message(callback_query, message_text, reply_markup=get_stop_last_task_inline_keyboard(task_id))
                else:
                    await safe_edit_callback_message(callback_query, message_text)
                try:
                    await callback_query.answer("✅ Задача запущена!")
                except Exception:
                    pass
            else:
                error_msg = result.get("error", "Неизвестная ошибка")
                try:
                    await callback_query.answer(f"❌ Ошибка: {error_msg}", show_alert=True)
                except Exception:
                    pass
        except Exception as e:
            try:
                await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
            except Exception:
                pass
        return
    
    if data == "forward_stats":
        # Показываем статистику пересылки
        channel_id = user_states[user_id].get('forward_channel_id')
        target_channel = user_states[user_id].get('forward_target_channel')
        
        # Получаем информацию о каналах
        channel_info = await get_channel_info(str(channel_id))
        target_info = await get_target_channel_info(target_channel)
        
        # Формируем отображаемые имена каналов
        channel_display = channel_info.get('title', f"Канал {channel_id}")
        target_display = target_info.get('title', target_channel)
        
        stats = await get_forwarding_stats_api(channel_id)
        
        if stats.get('status') == 'success':
            stats_data = stats.get('data', {})
            stats_text = f"📊 Статистика пересылки:\n\n"
            stats_text += f"📺 Канал: {channel_display}\n"
            stats_text += f"🎯 Целевой: {target_display}\n"
            stats_text += f"📤 Всего переслано: {stats_data.get('total_forwarded', 0)}\n"
            stats_text += f"📅 Сегодня: {stats_data.get('today_forwarded', 0)}\n"
            stats_text += f"🏷️ По хэштегам: {stats_data.get('hashtag_matches', 0)}\n"
            stats_text += f"❌ Ошибок: {stats_data.get('errors_count', 0)}\n"
            stats_text += f"🕐 Последняя активность: {stats_data.get('last_activity', 'N/A')}\n"
            
        else:
            stats_text = "❌ Не удалось получить статистику пересылки"
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_back")]])
        await safe_edit_callback_message(callback_query, stats_text, reply_markup=kb)
        return
    
    if data == "forward_settings":
        # Показываем настройки пересылки
        await show_forwarding_settings(client, callback_query.message, user_id)
        return

    elif data == "start_forwarding":
        # Начинаем пересылку
        user_id = callback_query.from_user.id
        channel_id = user_states[user_id]['forward_channel_id']
        target_channel = user_states[user_id]['forward_target_channel']
        
        if not target_channel:
            await callback_query.answer("❌ Сначала выберите целевой канал!", show_alert=True)
            return
        
        # Сохраняем конфигурацию
        success = await save_forwarding_config_api(user_id)
        if not success:
            await callback_query.answer("❌ Ошибка сохранения конфигурации!", show_alert=True)
            return
        
        # Запускаем пересылку
        success = await start_forwarding(user_id, channel_id, target_channel)
        if success:
            await callback_query.answer("✅ Пересылка запущена!", show_alert=True)
            await show_forwarding_settings(client, callback_query.message, user_id)
        else:
            await callback_query.answer("❌ Ошибка запуска пересылки!", show_alert=True)

    if data == "check_tasks_status":
        await check_tasks_status_callback(client, callback_query)
        return

    # --- FSM: Настройка приписки ---
    if state == FSM_FORWARD_FOOTER:
        print(f"[FSM][DEBUG] FSM_FORWARD_FOOTER | text='{text}'")
        if text == "Назад":
            await show_forwarding_settings(client, message, user_id)
            return
        user_states[user_id]["forward_settings"]["footer_text"] = text
        await message.reply(f"✅ Приписка сохранена: '{text}'")
        await show_forwarding_settings(client, message, user_id)
        return

    # --- Удалены дублирующиеся обработчики FSM_FORWARD_FOOTER_LINK и FSM_FORWARD_FOOTER_LINK_TEXT ---

    if data == "forward_back_to_stats":
        # Возвращаемся к статистике канала
        stats = await api_client.get_channel_stats(str(user_states[user_id]['forward_channel_id']))
        stat_text = format_channel_stats(stats)
        channel_id = user_states[user_id]['forward_channel_id']
        target_channel = user_states[user_id].get('forward_target_channel')
        await safe_edit_callback_message(
            callback_query,
            f"📊 Статистика канала {user_states[user_id]['forward_channel_title']}:\n\n{stat_text}\n\nВыберите действие:",
            reply_markup=get_forwarding_inline_keyboard(channel_id, target_channel)
        )
        user_states[user_id]["state"] = FSM_FORWARD_SETTINGS
        return

async def start_forwarding(user_id: int, channel_id: int, target_channel: int) -> bool:
    """Запуск пересылки через API"""
    try:
        # Получаем настройки пересылки
        forwarding_config = user_states[user_id].get("forward_settings", {})
        
        # Подготавливаем данные для запроса
        request_data = {
            'user_id': user_id,
            'source_channel_id': channel_id,
            'target_channel_id': target_channel,
            'parse_mode': forwarding_config.get('parse_mode', 'all'),
            'hashtag_filter': forwarding_config.get('hashtag_filter'),
            'delay_seconds': forwarding_config.get('delay_seconds', 0),
            'footer_text': forwarding_config.get('footer_text', ''),
            'text_mode': forwarding_config.get('text_mode', 'hashtags_only'),
            'max_posts': forwarding_config.get('max_posts'),
            'hide_sender': forwarding_config.get('hide_sender', True),
            'parse_direction': forwarding_config.get('parse_direction', 'backward'),
            'media_filter': forwarding_config.get('media_filter', 'all'),
            'range_mode': forwarding_config.get('range_mode', 'all'),
            'range_start_id': forwarding_config.get('range_start_id'),
            'range_end_id': forwarding_config.get('range_end_id'),
            'paid_content_mode': forwarding_config.get('paid_content_mode', 'off'),
            'paid_content_stars': forwarding_config.get('paid_content_stars', 0),
            'paid_content_hashtag': forwarding_config.get('paid_content_hashtag'),
            'paid_content_chance': forwarding_config.get('paid_content_chance'),
            # Добавляем настройки гиперссылки
            'footer_link': forwarding_config.get('footer_link'),
            'footer_link_text': forwarding_config.get('footer_link_text'),
            'footer_full_link': forwarding_config.get('footer_full_link', False)
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{config.PARSER_SERVICE_URL}/forwarding/start",
                json=request_data
            )
        print(f"[DEBUG] start_forwarding response: {resp.status_code} - {resp.text}")
        # Явно добавляем целевой канал в историю
        try:
            await api_client.add_user_target_channel(user_id, str(target_channel), str(target_channel))
        except Exception as e:
            print(f"[DEBUG] Не удалось добавить целевой канал в историю: {e}")
        return resp.status_code == 200
    except Exception as e:
        print(f"[ERROR] Ошибка запуска пересылки: {e}")
        return False

async def check_userbot_admin_rights(client, channel_id):
    try:
        chat = await client.get_chat(channel_id)
        if hasattr(chat, 'permissions') and chat.permissions:
            # Для супергрупп
            return chat.permissions.can_post_messages or chat.permissions.can_send_media_messages
        if hasattr(chat, 'administrator_rights') and chat.administrator_rights:
            # Для каналов
            return chat.administrator_rights.is_admin
        return False
    except (ChatAdminRequired, PeerIdInvalid, ChannelPrivate):
        return False
    except Exception as e:
        print(f"[ERROR] Ошибка проверки прав userbot: {e}")
        return False

# --- Функция для нормализации канала ---
async def resolve_channel(api_client, text):
    stats = await api_client.get_channel_stats(text)
    if stats and stats.get("id"):
        return stats["id"], stats.get("title", ""), stats.get("username", "")
    return text, text, ""  # Возвращаем исходный текст как fallback

# --- Функция для нормализации группы ---
async def resolve_group(api_client, text):
    stats = await api_client.get_channel_stats(text)
    if stats and stats.get("id"):
        return stats["id"], stats.get("title", ""), stats.get("username", "")
    return text, text, ""  # Возвращаем исходный текст как fallback

def format_channel(cfg, channel_id_key="channel_id", title_key="channel_title", username_key="username"):
    channel_id = cfg.get(channel_id_key) or cfg.get("source_channel") or cfg.get("target_channel")
    title = cfg.get(title_key) or ""
    username = cfg.get(username_key) or ""
    if title and username:
        return f"{title} (@{username})\n      ID: {channel_id}"
    elif title:
        return f"{title}\n      ID: {channel_id}"
    elif username:
        return f"@{username}\n      ID: {channel_id}"
    else:
        return f"ID: {channel_id}"

async def get_channel_info_map(user_id):
    """Возвращает dict: channel_id -> {'title': ..., 'username': ...} для user_channels и user_target_channels"""
    user_channels = await api_client.get_user_channels(user_id)
    target_channels = await api_client.get_user_target_channels(user_id)
    info = {}
    for ch in user_channels:
        info[str(ch.get('id'))] = {'title': ch.get('title'), 'username': ch.get('username')}
    for ch in target_channels:
        info[str(ch.get('id'))] = {'title': ch.get('title'), 'username': ch.get('username')}
    return info

async def get_group_info_map(user_id):
    """Возвращает dict: group_id -> {'title': ..., 'username': ...} для user_groups"""
    user_groups = await api_client.get_user_groups(user_id)
    info = {}
    for group in user_groups:
        info[str(group.get('group_id'))] = {'title': group.get('group_title'), 'username': group.get('username')}
    return info

def format_channel_display(channel_id, info_map):
    if channel_id is None:
        return "—"
    ch = info_map.get(str(channel_id))
    if ch:
        title = ch.get('title') or ''
        username = ch.get('username') or ''
        if title and username:
            return f"{title} (@{username}) [ID: {channel_id}]"
        elif title:
            return f"{title} [ID: {channel_id}]"
        elif username:
            return f"@{username} [ID: {channel_id}]"
    return f"ID: {channel_id}"

async def build_tasks_monitorings_status_text_and_keyboard(user_id, monitorings, tasks, reaction_tasks, public_groups_tasks=None, updated=False, back_to="forward_back_to_stats"):
    info_map = await get_channel_info_map(user_id)
    def safe(val):
        if val is None or val == "N/A":
            return "—"
        return html.escape(str(val))
    msg = "<b>📊 Статус задач и мониторингов:</b>\n\n"
    if updated:
        now = datetime.now().strftime("%H:%M:%S")
        msg += f"<i>🔄 Список обновлен: {now}</i>\n\n"
    buttons = []
    # Мониторинги
    if monitorings:
        msg += "<b>📡 Мониторинги:</b>\n"
        for idx, m in enumerate(monitorings, 1):
            cfg = m.get("config", {})
            channel_id = m.get("channel_id")
            target_channel_id = m.get("target_channel")
            channel_info = format_channel_display(channel_id, info_map)
            # Fallback: если нет в истории — всё равно показываем id
            if target_channel_id is not None:
                target_info = format_channel_display(target_channel_id, info_map)
            else:
                target_info = "—"
            active = m.get("active", False)
            task_running = m.get("task_running", False)
            status = "🟢 Активен" if active and task_running else "🔴 Остановлен"
            msg += f"{idx}. <b>Канал:</b> {safe(channel_info)}\n"
            msg += f"   <b>Статус:</b> {status}\n"
            msg += f"   <b>Цель:</b> {safe(target_info)}\n"
            msg += f"   <b>Режим:</b> {safe(cfg.get('parse_mode'))}\n"
            msg += f"   <b>Хэштег:</b> {safe(cfg.get('hashtag_filter'))}\n"
            msg += f"   <b>Лимит:</b> {safe(cfg.get('max_posts'))}\n"
            msg += f"   <b>Платные:</b> {safe(cfg.get('paid_content_stars'))}⭐\n\n"
            # Кнопка остановки мониторинга если есть оба id (даже если нет в истории)
            if active and task_running and channel_id is not None and target_channel_id is not None:
                buttons.append([InlineKeyboardButton(f"⏹️ Остановить мониторинг {idx}", callback_data=f"stop_monitoring:{channel_id}:{target_channel_id}")])
    # Задачи парсинг+пересылки
    if tasks:
        msg += "<b>🚀 Задачи парсинг+пересылки:</b>\n"
        for idx, task in enumerate(tasks, 1):
            task_id = task.get("task_id")
            source_id = task.get("source_channel")
            target_id = task.get("target_channel")
            source = format_channel_display(source_id, info_map)
            target = format_channel_display(target_id, info_map)
            status = task.get("status", "unknown")
            started_at = safe(task.get("started_at"))
            completed_at = safe(task.get("completed_at"))
            error = safe(task.get("error"))
            status_emoji = {
                "running": "🟢",
                "completed": "✅",
                "stopped": "⏹️",
                "error": "❌"
            }.get(status, "❓")
            msg += f"<b>{idx}. Задача {safe(task_id)[:15]}...</b>\n"
            msg += f"   📤 <b>Источник:</b> {safe(source)}\n"
            msg += f"   📥 <b>Цель:</b> {safe(target)}\n"
            msg += f"   {status_emoji} <b>Статус:</b> {status}\n"
            msg += f"   🕐 <b>Запущена:</b> {started_at}\n"
            if completed_at and completed_at != "—":
                msg += f"   ✅ <b>Завершена:</b> {completed_at}\n"
            if error and error != "—":
                msg += f"   ❌ <b>Ошибка:</b> {error[:50]}...\n"
            msg += "\n"
            if status == "running" and task_id:
                buttons.append([InlineKeyboardButton(f"⏹️ Остановить задачу {idx}", callback_data=f"stop_task:{task_id}")])
    
    # Задачи реакций
    if reaction_tasks:
        msg += "<b>💫 Задачи реакций:</b>\n"
        for idx, task in enumerate(reaction_tasks, 1):
            task_id = task.get("task_id")
            chat_id = task.get("chat_id")
            emojis = task.get("emojis", [])
            mode = task.get("mode")
            count = task.get("count")
            status = task.get("status", "unknown")
            started_at = safe(task.get("started_at"))
            completed_at = safe(task.get("completed_at"))
            error = safe(task.get("error"))
            status_emoji = {
                "running": "🟢",
                "completed": "✅",
                "stopped": "⏹️",
                "error": "❌"
            }.get(status, "❓")
            msg += f"<b>{idx}. Задача реакций {safe(task_id)[:15]}...</b>\n"
            msg += f"   📺 <b>Канал:</b> {safe(chat_id)}\n"
            msg += f"   😊 <b>Эмодзи:</b> {', '.join(emojis) if emojis else '—'}\n"
            msg += f"   🎯 <b>Режим:</b> {safe(mode)}\n"
            if count:
                msg += f"   📊 <b>Количество:</b> {safe(count)}\n"
            msg += f"   {status_emoji} <b>Статус:</b> {status}\n"
            msg += f"   🕐 <b>Запущена:</b> {started_at}\n"
            if completed_at and completed_at != "—":
                msg += f"   ✅ <b>Завершена:</b> {completed_at}\n"
            if error and error != "—":
                msg += f"   ❌ <b>Ошибка:</b> {error[:50]}...\n"
            msg += "\n"
            if status == "running" and task_id:
                buttons.append([InlineKeyboardButton(f"⏹️ Остановить реакцию {idx}", callback_data=f"stop_reaction_task:{task_id}")])
    
    # Задачи публичных групп
    if public_groups_tasks:
        msg += "<b>📢 Пересылка в публичные группы:</b>\n"
        for idx, task in enumerate(public_groups_tasks, 1):
            source = safe(task.get("source_channel"))
            target = safe(task.get("target_group"))
            status = task.get("status", "unknown")
            forwarded = safe(task.get("forwarded_count", 0))
            settings = task.get("settings", {}) or {}
            views_limit = safe(settings.get("views_limit"))
            posts_count = safe(settings.get("posts_count"))
            status_emoji = {
                "running": "🟢",
                "completed": "✅",
                "stopped": "⏹️",
                "error": "❌"
            }.get(status, "❓")
            msg += (
                f"{idx}. <b>Источник:</b> {source}\n"
                f"   <b>Цель:</b> {target}\n"
                f"   {status_emoji} <b>Статус:</b> {status}\n"
                f"   📤 <b>Переслано:</b> {forwarded}\n"
                f"   👁️ <b>Лимит просмотров:</b> {views_limit}\n"
                f"   🔢 <b>Диапазон:</b> {posts_count}\n"
                "\n"
            )
    
    # Кнопка остановить все
    has_running_tasks = (
        (monitorings and any(m.get("active") and m.get("task_running") and m.get("channel_id") is not None and m.get("target_channel") is not None for m in monitorings)) or 
        (tasks and any(t.get("status") == "running" and t.get("task_id") for t in tasks)) or
        (reaction_tasks and any(t.get("status") == "running" and t.get("task_id") for t in reaction_tasks)) or
        (public_groups_tasks and any(t.get("status") == "running" for t in public_groups_tasks))
    )
    if has_running_tasks:
        buttons.append([InlineKeyboardButton("⏹️ Остановить все", callback_data="stop_all_tasks")])
    # Кнопки управления
    if back_to == "reaction_back_to_stats":
        buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data="check_reaction_tasks_status")])
    else:
        buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data="check_tasks_status")])
    buttons.append([InlineKeyboardButton("🔙 Назад", callback_data=back_to)])
    keyboard = InlineKeyboardMarkup(buttons)
    return msg, keyboard

async def send_or_edit_status_message(message=None, callback_query=None, back_to="forward_back_to_stats", context="forwarding"):
    # Получаем user_id
    user_id = None
    if callback_query:
        user_id = callback_query.from_user.id
    elif message:
        user_id = message.from_user.id
    monitoring_data = await api_client.get_monitoring_status()
    monitorings = monitoring_data.get("monitorings", [])
    logger.info(f"[STATUS_UNIFIED] Получено monitorings: {monitorings}")
    tasks_data = await api_client.get_all_tasks()
    tasks = tasks_data.get("tasks", [])
    logger.info(f"[STATUS_UNIFIED] Получено tasks: {tasks}")
    reaction_tasks_data = await api_client.get_all_reaction_tasks()
    reaction_tasks = reaction_tasks_data.get("tasks", [])
    logger.info(f"[STATUS_UNIFIED] Получено reaction_tasks: {reaction_tasks}")
    public_groups_tasks_data = await api_client.get_all_public_groups_tasks()
    public_groups_tasks = public_groups_tasks_data.get("tasks", [])
    logger.info(f"[STATUS_UNIFIED] Получено public_groups_tasks: {public_groups_tasks}")
    updated = bool(callback_query)
    if not monitorings and not tasks and not reaction_tasks and not public_groups_tasks:
        text = "📊 Нет активных задач и мониторингов."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад", callback_data=back_to)]
        ])
        if callback_query:
            try:
                await callback_query.edit_message_text(text, reply_markup=keyboard)
            except MessageNotModified:
                pass
        elif message:
            await message.reply(text, reply_markup=keyboard)
        return
    msg, keyboard = await build_tasks_monitorings_status_text_and_keyboard(
        user_id, monitorings, tasks, reaction_tasks, public_groups_tasks, updated=updated, back_to=back_to)
    logger.info(f"[STATUS_UNIFIED] Итоговое сообщение: {msg}")
    try:
        if callback_query:
            await callback_query.edit_message_text(msg, reply_markup=keyboard, parse_mode="html")
        elif message:
            await message.reply(msg, reply_markup=keyboard, parse_mode="html")
    except MessageNotModified:
        logger.warning("[STATUS_UNIFIED] MESSAGE_NOT_MODIFIED: текст не изменился")
    except Exception as e:
        logger.error(f"[STATUS_UNIFIED] Ошибка при отправке/редактировании с parse_mode=html: {e}")
        try:
            if callback_query:
                await callback_query.edit_message_text(msg, reply_markup=keyboard)
            elif message:
                await message.reply(msg, reply_markup=keyboard)
        except MessageNotModified:
            logger.warning("[STATUS_UNIFIED] MESSAGE_NOT_MODIFIED: текст не изменился (fallback)")
        except Exception as e2:
            logger.error(f"[STATUS_UNIFIED] Ошибка при отправке/редактировании без parse_mode: {e2}")

# Отдельная функция для статуса задач реакций
async def send_or_edit_reaction_status_message(message=None, callback_query=None):
    """Отдельная функция для статуса задач реакций с правильной кнопкой назад"""
    await send_or_edit_status_message(message=message, callback_query=callback_query, back_to="reaction_back_to_stats", context="reactions")

# Команда из клавиатуры
async def monitorings_command(client: Client, message: Message):
    await send_or_edit_status_message(message=message)

# Inline-кнопка для статуса задач пересылки
async def check_tasks_status_callback(client: Client, callback_query):
    await send_or_edit_status_message(callback_query=callback_query)

# Inline-кнопка для статуса задач реакций
async def check_reaction_tasks_status_callback(client: Client, callback_query):
    await send_or_edit_reaction_status_message(callback_query=callback_query)

# Обработчик остановки задач парсинг+пересылки
async def stop_task_callback(client: Client, callback_query):
    try:
        data = callback_query.data
        if data.startswith("stop_task:"):
            task_id = data.split(":", 1)[1]
            result = await api_client.stop_task(task_id)
            if result.get("status") == "stopped":
                await callback_query.answer("✅ Задача остановлена!")
                await check_tasks_status_callback(client, callback_query)
            else:
                await callback_query.answer("❌ Ошибка при остановке задачи")
    except Exception as e:
        logger.error(f"Ошибка при остановке задачи: {e}")
        await callback_query.answer("❌ Ошибка при остановке задачи")

# Обработчик остановки задач реакций
async def stop_reaction_task_callback(client: Client, callback_query):
    try:
        data = callback_query.data
        if data.startswith("stop_reaction_task:"):
            task_id = data.split(":", 1)[1]
            result = await api_client.stop_reaction_task(task_id)
            if result.get("success"):
                await callback_query.answer("✅ Задача реакций остановлена!")
                await check_reaction_tasks_status_callback(client, callback_query)
            else:
                await callback_query.answer("❌ Ошибка при остановке задачи реакций")
    except Exception as e:
        logger.error(f"Ошибка при остановке задачи реакций: {e}")
        await callback_query.answer("❌ Ошибка при остановке задачи реакций")

# Обработчик остановки мониторинга
async def stop_monitoring_callback(client, callback_query):
    parts = callback_query.data.split(":", 2)
    channel_id = parts[1]
    target_channel_id = parts[2]
    logger.info(f"[STOP_MONITORING] channel_id={channel_id}, target_channel_id={target_channel_id}")
    try:
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.post(f"{config.PARSER_SERVICE_URL}/forwarding/stop", json={"channel_id": int(channel_id), "target_channel_id": str(target_channel_id)})
        logger.info(f"[STOP_MONITORING] API resp: {resp.status_code} {resp.text}")
        if resp.status_code == 200:
            await callback_query.answer("✅ Мониторинг остановлен!")
        else:
            await callback_query.answer(f"❌ Ошибка: {resp.text}")
    except Exception as e:
        logger.error(f"[STOP_MONITORING] Ошибка: {e}")
        await callback_query.answer(f"❌ Ошибка: {e}")
    await send_or_edit_status_message(callback_query=callback_query)

# Обработчик остановки всех задач и мониторингов
async def stop_all_tasks_callback(client, callback_query):
    user_id = callback_query.from_user.id
    errors = []
    logger.info(f"[STOP_ALL_TASKS] user_id={user_id}")
    # Остановить все мониторинги
    monitoring_data = await api_client.get_monitoring_status()
    monitorings = monitoring_data.get("monitorings", [])
    # Собираем уникальные пары (channel_id, target_channel_id)
    pairs = set()
    for m in monitorings:
        channel_id = m.get("channel_id")
        target_channel_id = m.get("target_channel")
        if channel_id and target_channel_id:
            pairs.add((channel_id, target_channel_id))
    for channel_id, target_channel_id in pairs:
        try:
            async with httpx.AsyncClient() as http_client:
                resp = await http_client.post(f"{config.PARSER_SERVICE_URL}/forwarding/stop", json={"channel_id": int(channel_id), "target_channel_id": str(target_channel_id)})
            logger.info(f"[STOP_ALL_TASKS] stop_monitoring {channel_id} -> {target_channel_id}: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"[STOP_ALL_TASKS] Ошибка: {e}")
            errors.append(str(e))
    # Остановить все задачи парсинг+пересылки
    tasks_data = await api_client.get_all_tasks()
    tasks = tasks_data.get("tasks", [])
    for t in tasks:
        task_id = t.get("task_id")
        if task_id:
            try:
                resp = await api_client.stop_task(task_id)
                logger.info(f"[STOP_ALL_TASKS] stop_task {task_id}: {resp}")
            except Exception as e:
                logger.error(f"[STOP_ALL_TASKS] Ошибка: {e}")
                errors.append(str(e))
    # Остановить все задачи реакций
    reaction_tasks_data = await api_client.get_all_reaction_tasks()
    reaction_tasks = reaction_tasks_data.get("tasks", [])
    for t in reaction_tasks:
        task_id = t.get("task_id")
        if task_id:
            try:
                resp = await api_client.stop_reaction_task(task_id)
                logger.info(f"[STOP_ALL_TASKS] stop_reaction_task {task_id}: {resp}")
            except Exception as e:
                logger.error(f"[STOP_ALL_TASKS] Ошибка: {e}")
                errors.append(str(e))
    if errors:
        await callback_query.answer(f"❌ Ошибки: {'; '.join(errors)[:50]}")
    else:
        await callback_query.answer("✅ Все задачи и мониторинги остановлены!")
    # Определяем, откуда был вызван статус задач, и используем соответствующую функцию
    # По умолчанию используем обычный статус задач
    await send_or_edit_status_message(callback_query=callback_query)

async def process_callback_query(client, callback_query):
    """
    Универсальный обработчик callback-запросов: вызывает forwarding_callback_handler только если callback не обработан ни одним из специализированных обработчиков.
    Возвращает True если callback обработан, иначе False.
    """
    data = callback_query.data
    # Проверяем все специализированные обработчики
    if data is None:
        return False
    # stop_monitoring
    if data.startswith("stop_monitoring:"):
        # Импортируем и вызываем напрямую
        await stop_monitoring_callback(client, callback_query)
        return True
    # stop_all_tasks
    if data == "stop_all_tasks":
        await stop_all_tasks_callback(client, callback_query)
        return True
    # stop_task
    if data.startswith("stop_task:"):
        await stop_task_callback(client, callback_query)
        return True
    # check_tasks_status
    if data == "check_tasks_status":
        await check_tasks_status_callback(client, callback_query)
        return True
    # check_reaction_tasks_status
    if data == "check_reaction_tasks_status":
        await check_reaction_tasks_status_callback(client, callback_query)
        return True

    # ... можно добавить другие кастомные обработчики ...
    # Если не обработано — fallback: вызываем forwarding_callback_handler
    await forwarding_callback_handler(client, callback_query)
    return True





# Обработка реакций перенесена в reaction_master.py

