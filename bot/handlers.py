import re
import asyncio
import logging
import os
import traceback
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
    FSM_FORWARD_CHANNEL, FSM_FORWARD_TARGET, FSM_FORWARD_TARGETS, FSM_FORWARD_SETTINGS, FSM_FORWARD_HASHTAG,
    FSM_FORWARD_DELAY, FSM_FORWARD_FOOTER, FSM_FORWARD_TEXT_MODE, FSM_FORWARD_LIMIT,
    FSM_FORWARD_DIRECTION, FSM_FORWARD_MEDIA_FILTER, FSM_FORWARD_RANGE, FSM_FORWARD_RANGE_START, FSM_FORWARD_RANGE_END,
    FSM_FORWARD_MENU,
    get_main_keyboard, get_channel_history_keyboard, get_target_channel_history_keyboard,
    get_forwarding_keyboard, get_forwarding_settings_keyboard, get_parse_mode_keyboard, get_text_mode_keyboard,
    get_direction_keyboard, get_media_filter_keyboard, get_range_mode_keyboard,
    posting_stats, get_forwarding_history_stats_api,
    clear_forwarding_history_api, get_channel_info, get_target_channel_info,
    get_stop_last_task_inline_keyboard, get_forwarding_inline_keyboard,
     format_channel_stats, format_forwarding_stats,
    start_forwarding_api, stop_forwarding_api, get_forwarding_stats_api, save_forwarding_config_api,
    get_channel_info, get_target_channel_info,
    FSM_REACTION_CHANNEL, FSM_REACTION_SETTINGS, FSM_REACTION_EMOJIS, FSM_REACTION_MODE, FSM_REACTION_HASHTAG, FSM_REACTION_DATE, FSM_REACTION_DATE_RANGE, FSM_REACTION_COUNT, FSM_REACTION_CONFIRM,
    get_reaction_settings_keyboard, get_reaction_inline_keyboard,
    FSM_TEXT_EDIT_CHANNEL, FSM_TEXT_EDIT_SETTINGS, FSM_TEXT_EDIT_LINK_TEXT, FSM_TEXT_EDIT_LINK_URL, FSM_TEXT_EDIT_LIMIT, FSM_TEXT_EDIT_FOOTER_EDIT, FSM_TEXT_EDIT_SPECIFIC_TEXT, FSM_TEXT_EDIT_CONFIRM,
    get_text_edit_menu_keyboard, get_text_edit_confirmation_keyboard, get_text_edit_inline_keyboard,
)
from bot.config import config
from bot.core import (
    show_main_menu, start_forwarding_api, stop_forwarding_api, get_forwarding_stats_api, save_forwarding_config_api,
    start_forwarding_parsing_api, get_forwarding_history_stats_api, clear_forwarding_history_api,
    get_channel_info, get_target_channel_info, get_actual_published_count, get_publish_stat_text
)
from bot.api_client import api_client
from bot.states import format_forwarding_config
from bot.text_editor_manager import TextEditorManager
from bot.watermark_manager import watermark_manager
from bot.states import (
    FSM_WATERMARK_TEXT_INPUT, FSM_WATERMARK_CHANCE, FSM_WATERMARK_HASHTAG,
    FSM_WATERMARK_OPACITY, FSM_WATERMARK_SCALE,
    get_watermark_menu_keyboard, get_watermark_type_keyboard,
    get_watermark_mode_keyboard, get_watermark_position_keyboard
)
import html
from unittest.mock import MagicMock

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

async def show_watermark_channel_selection(client, message, user_id):
    """Показать выбор канала для watermark настроек"""
    logger.info(f"[WATERMARK] >>> ENTERING show_watermark_channel_selection for user {user_id}")

    user_state = user_states.get(user_id, {})
    target_channels = user_state.get('forward_target_channels', [])

    if not target_channels:
        text = "❌ Нет каналов для настройки watermark"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_back")]])
    else:
        text = """
🎨 **Выберите канал для настройки watermark**

Для каждого канала можно настроить отдельные параметры watermark:
• Тип (текст/изображение)
• Режим применения
• Позиция и прозрачность

Выберите канал, чтобы увидеть его текущие настройки и внести изменения.
"""
        buttons = []
        for channel in target_channels:
            channel_id = str(channel['id'])
            channel_title = channel['title']
            # Асинхронно получаем статус watermark для каждого канала
            wm_settings = await watermark_manager.get_channel_watermark_settings(user_id, channel_id)
            wm_status = "✅" if wm_settings.get('watermark_enabled') else "❌"
            buttons.append([InlineKeyboardButton(f"{wm_status} {channel_title}", callback_data=f"watermark_channel_{channel_id}")])
        
        buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_forward_settings")])
        keyboard = InlineKeyboardMarkup(buttons)

    await safe_edit_message(client, message.chat.id, message.id, text, keyboard)
    logger.info(f"[WATERMARK] <<< EXITING show_watermark_channel_selection for user {user_id}")

async def show_target_channels_management(client, message, user_id):
    """Показать управление выбранными целевыми каналами"""
    logger.info(f"[TARGET_CHANNELS] >>> ENTERING show_target_channels_management for user {user_id}")
    try:
        user_state = user_states.get(user_id, {})
        source_channel = user_state.get('forward_channel_title', 'Не выбран')
        target_channels = user_state.get('forward_target_channels', [])
        logger.info(f"[TARGET_CHANNELS] User state: {user_state}")
        logger.info(f"[TARGET_CHANNELS] Found {len(target_channels)} target channels: {target_channels}")

        if not target_channels:
            text = f"📥 Из: {source_channel}\n\n❌ Не выбрано ни одного целевого канала"
        else:
            text = f"📥 Из: {source_channel}\n\n📤 Выбранные каналы для пересылки:"
            for i, ch in enumerate(target_channels, 1):
                title = ch.get('title', ch['id'])
                username = ch.get('username', '')
                if username:
                    title += f" (@{username})"
                text += f"\n{i}. {title}"

        # Создаем клавиатуру для управления каналами
        keyboard_buttons = [
            [InlineKeyboardButton("➕ Добавить канал", callback_data="add_target_channel")],
            [InlineKeyboardButton("🎨 Watermark", callback_data="watermark_channel_select")],
            [InlineKeyboardButton("🚀 Начать пересылку", callback_data="forward_to_settings")],
            [InlineKeyboardButton("🔙 Назад", callback_data="forward_back")]
        ]
        keyboard = InlineKeyboardMarkup(keyboard_buttons)

        # Временно отключаем проверку доступа к каналам
        # # Проверяем доступ к каналам и показываем статус
        # access_checks = []
        # for ch in target_channels:
        #     try:
        #         # Проверяем доступ через API
        #         channel_info = await api_client.get_channel_info(str(ch['id']))
        #         if channel_info and channel_info.get('error'):
        #             access_checks.append(f"❌ {ch.get('title', ch['id'])} - нет доступа")
        #         else:
        #             access_checks.append(f"✅ {ch.get('title', ch['id'])}")
        #     except Exception as e:
        #         logger.warning(f"[ACCESS_CHECK] Error checking access for channel {ch['id']}: {e}")
        #         access_checks.append(f"❓ {ch.get('title', ch['id'])} - проверка не удалась")

        # Временно отключаем показ статуса доступа
        # if access_checks:
        #     text += "\n\n📊 Статус доступа к каналам:"
        #     for status in access_checks:
        #         text += f"\n{status}"

        # Удаляем кнопки удаления каналов если их больше 1
        if len(target_channels) > 1:
            remove_buttons = []
            for i, ch in enumerate(target_channels, 1):
                title = ch.get('title', ch['id'])
                if len(title) > 20:
                    title = title[:17] + "..."
                remove_buttons.append(InlineKeyboardButton(f"❌ {i}. {title}", callback_data=f"remove_target_channel:{i-1}"))
            # Добавляем кнопки удаления пачками по 2
            for i in range(0, len(remove_buttons), 2):
                keyboard.inline_keyboard.insert(-2, remove_buttons[i:i+2])

        logger.info(f"[TARGET_CHANNELS] About to send message with keyboard, text length: {len(text)}")
        logger.info(f"[TARGET_CHANNELS] Keyboard has {len(keyboard.inline_keyboard)} rows")
        sent = await message.reply(text, reply_markup=keyboard)
        logger.info(f"[TARGET_CHANNELS] Message sent successfully, message_id: {sent.id if sent else None}")
        if sent is not None:
            user_states[user_id]["last_msg_id"] = sent.id
        user_states[user_id]["state"] = FSM_FORWARD_TARGETS
        logger.info(f"[TARGET_CHANNELS] <<< EXITING show_target_channels_management successfully")

    except Exception as e:
        logger.error(f"[TARGET_CHANNELS] Error: {e}")
        await message.reply("❌ Ошибка при отображении управления каналами", reply_markup=get_main_keyboard())
        user_states[user_id]["state"] = FSM_MAIN_MENU

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
    
    # --- FSM: обработка watermark ---
    from bot.watermark_handlers import (
        handle_watermark_text_input, handle_watermark_chance_input,
        handle_watermark_hashtag_input, handle_watermark_opacity_input,
        handle_watermark_scale_input
    )
    if state in [FSM_WATERMARK_TEXT_INPUT, FSM_WATERMARK_CHANCE, FSM_WATERMARK_HASHTAG, 
                 FSM_WATERMARK_OPACITY, FSM_WATERMARK_SCALE]:
        if state == FSM_WATERMARK_TEXT_INPUT:
            await handle_watermark_text_input(client, message)
        elif state == FSM_WATERMARK_CHANCE:
            await handle_watermark_chance_input(client, message)
        elif state == FSM_WATERMARK_HASHTAG:
            await handle_watermark_hashtag_input(client, message)
        elif state == FSM_WATERMARK_OPACITY:
            await handle_watermark_opacity_input(client, message)
        elif state == FSM_WATERMARK_SCALE:
            await handle_watermark_scale_input(client, message)
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
            return
            return
        elif text in ["✏️ Редактирование текста"]:
            kb = await get_channel_history_keyboard(user_id)
            sent = await message.reply(
                "📺 **Выбор канала для редактирования текста**\n\n"
                "Выберите канал из истории или введите ID/ссылку канала:",
                reply_markup=kb or ReplyKeyboardRemove()
            )
            if last_msg_id:
                try:
                    await client.delete_messages(message.chat.id, last_msg_id)
                except Exception:
                    pass
            if sent is not None:
                user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_TEXT_EDIT_CHANNEL, "last_msg_id": sent.id}
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
            channel_info = await resolve_channel(api_client, text)
            if channel_info is None:
                sent = await message.reply("❌ Не удалось определить ID канала. Введите корректный username или ID.", reply_markup=ReplyKeyboardRemove())
                if sent is not None:
                    user_states[user_id]["last_msg_id"] = sent.id
                return
                
            channel_id = channel_info["id"]
            channel_title = channel_info["title"]
            channel_username = channel_info.get("username", "")
            
            # Попробуем получить numeric id, если возможно
            real_id = None
            try:
                real_id = int(channel_id)
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
        logger.info(f"[FSM] Processing FSM_FORWARD_TARGET for user {user_id}, text: '{text}'")
        if text == "Назад":
            kb = await get_channel_history_keyboard(user_id)
            sent = await message.reply("Выберите канал для пересылки:", reply_markup=kb or ReplyKeyboardRemove())
            if sent is not None:
                user_states[user_id]["last_msg_id"] = sent.id
            user_states[user_id]["state"] = FSM_FORWARD_CHANNEL
            return
        logger.info(f"[FSM] Checking regex match for text: '{text}'")
        match = re.match(r"(.+) \(ID: (-?\d+)(?:, @(\w+))?\)", text)
        if match:
            logger.info(f"[FSM] Regex matched! Groups: {match.groups()}")
            channel_title = match.group(1)
            channel_id = match.group(2)
            username = match.group(3)
            # Временно отключаем проверку доступа
            # # Проверяем доступ к каналу через API
            # try:
            #     channel_access_info = await api_client.get_channel_info(str(channel_id))
            #     if channel_access_info and channel_access_info.get('error'):
            #         sent = await message.reply(f"❌ Нет доступа к каналу '{channel_title}'. Убедитесь, что сессия подписана на этот канал.", reply_markup=ReplyKeyboardRemove())
            #         if sent is not None:
            #             user_states[user_id]["last_msg_id"] = sent.id
            #         return
            # except Exception as e:
            #     logger.warning(f"[ACCESS_CHECK] Не удалось проверить доступ к каналу {channel_id}: {e}")

            # Инициализируем список каналов если его нет
            if "forward_target_channels" not in user_states[user_id]:
                user_states[user_id]["forward_target_channels"] = []
            # Добавляем канал в список
            channel_info = {
                "id": channel_id,
                "title": channel_title,
                "username": username
            }
            if channel_info not in user_states[user_id]["forward_target_channels"]:
                user_states[user_id]["forward_target_channels"].append(channel_info)
            logger.info(f"[FSM] About to set forward_target_title to '{channel_title}'")
            user_states[user_id]["forward_target_title"] = channel_title
            if username:
                user_states[user_id]["forward_target_username"] = username
            logger.info(f"[FSM] About to update target channel last used for {channel_id}")
            await api_client.update_user_target_channel_last_used(user_id, channel_id)
            logger.info(f"[FSM] Successfully updated target channel last used")

            # После выбора целевого канала показываем управление каналами
            logger.info(f"[FSM] About to call show_target_channels_management for user {user_id}")
            await show_target_channels_management(client, message, user_id)
            logger.info(f"[FSM] Successfully showed target channels management")
            return
        else:
            logger.info(f"[FSM] Regex not matched, trying resolve_channel for text: '{text}'")
            channel_info = await resolve_channel(api_client, text)
            if channel_info is None:
                sent = await message.reply("❌ Не удалось определить ID канала. Введите корректный username или ID.", reply_markup=ReplyKeyboardRemove())
                if sent is not None:
                    user_states[user_id]["last_msg_id"] = sent.id
                return
                
            channel_id = channel_info["id"]
            channel_title = channel_info["title"]
            channel_username = channel_info.get("username", "")
            
            # Определяем, что ввел пользователь: username или ID
            is_username = not text.startswith("-100") and not text.isdigit()
            
            # Используем ID из channel_info
            real_id = channel_id
            try:
                real_id = int(real_id)
            except (ValueError, TypeError):
                real_id = channel_id

            # Временно отключаем проверку доступа
            # # Проверяем доступ к каналу через API
            # try:
            #     channel_access_info = await api_client.get_channel_info(str(real_id))
            #     if channel_access_info and channel_access_info.get('error'):
            #         sent = await message.reply(f"❌ Нет доступа к каналу '{channel_title}'. Убедитесь, что сессия подписана на этот канал.", reply_markup=ReplyKeyboardRemove())
            #         if sent is not None:
            #             user_states[user_id]["last_msg_id"] = sent.id
            #         return
            # except Exception as e:
            #     logger.warning(f"[ACCESS_CHECK] Не удалось проверить доступ к каналу {real_id}: {e}")

            # Сохраняем в БД правильно
            if is_username:
                # Пользователь ввел username, сохраняем username в поле username, а ID в поле channel_id
                await api_client.add_user_target_channel(user_id, str(real_id), channel_title, text)
                # Инициализируем список каналов если его нет
                if "forward_target_channels" not in user_states[user_id]:
                    user_states[user_id]["forward_target_channels"] = []
                # Добавляем канал в список
                channel_info = {
                    "id": str(real_id),
                    "title": channel_title,
                    "username": text
                }
                if channel_info not in user_states[user_id]["forward_target_channels"]:
                    user_states[user_id]["forward_target_channels"].append(channel_info)
                user_states[user_id]["forward_target_username"] = text  # username
            else:
                # Пользователь ввел ID, сохраняем ID в поле channel_id, а username в поле username
                await api_client.add_user_target_channel(user_id, str(real_id), channel_title, channel_username)
                # Инициализируем список каналов если его нет
                if "forward_target_channels" not in user_states[user_id]:
                    user_states[user_id]["forward_target_channels"] = []
                # Добавляем канал в список
                channel_info = {
                    "id": str(real_id),
                    "title": channel_title,
                    "username": channel_username
                }
                if channel_info not in user_states[user_id]["forward_target_channels"]:
                    user_states[user_id]["forward_target_channels"].append(channel_info)
                if channel_username:
                    user_states[user_id]["forward_target_username"] = channel_username
            
            user_states[user_id]["forward_target_title"] = channel_title

            # После выбора целевого канала показываем управление каналами
            logger.info(f"[FSM] About to call show_target_channels_management for user {user_id}")
            await show_target_channels_management(client, message, user_id)
            logger.info(f"[FSM] Successfully showed target channels management")







    # --- FSM: Управление выбранными каналами ---
    if state == FSM_FORWARD_TARGETS:
        print(f"[FSM][DEBUG] FSM_FORWARD_TARGETS | text='{text}'")
        # В состоянии управления каналами игнорируем любой текстовый ввод
        # Пользователь должен использовать только кнопки для навигации
        if text and not text.startswith("/"):
            await message.reply("⚠️ Используйте кнопки для управления каналами или настройки пересылки")
            return

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
            if text == "🔙 Назад":
                user_states[user_id]['forward_state'] = None
                await show_forwarding_settings(client, message, user_id)
                return

            if text.lower() == '0' or text.lower() == 'без лимита':
                user_states[user_id]['forward_settings']['max_posts'] = None
                await message.reply("✅ Лимит снят!", reply_markup=ReplyKeyboardRemove())
            else:
                try:
                    limit = int(text)
                    if limit <= 0:
                        raise ValueError
                    user_states[user_id]['forward_settings']['max_posts'] = limit
                    await message.reply(f"✅ Лимит установлен: {limit} постов", reply_markup=ReplyKeyboardRemove())
                except ValueError:
                    await message.reply("❌ Введите положительное число.", reply_markup=ReplyKeyboardRemove())
                    return
            
            user_states[user_id]['forward_state'] = None
            await show_forwarding_settings(client, message, user_id)
        
        elif forward_state == 'reactions_emojis_input':
            if text == "🔙 Назад":
                user_states[user_id]['forward_state'] = None
                # Re-show reactions menu on the original bot message
                last_bot_message_id = user_states[user_id].get('last_msg_id')
                settings = user_states[user_id].get('forward_settings', {})
                reactions_enabled = settings.get('reactions_enabled', False)
                emojis = settings.get('reaction_emojis', [])

                text = "🎭 Настройка автоматических реакций\n\n"
                if reactions_enabled:
                    text += f"Статус: Включено\n"
                    text += f"Эмодзи: {' '.join(emojis) if emojis else 'Не заданы'}"
                else:
                    text += "Статус: Отключено"

                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Включить" if not reactions_enabled else "❌ Отключить", callback_data="forward_reactions_toggle")],
                    [InlineKeyboardButton("😀 Изменить эмодзи", callback_data="forward_reactions_emojis")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]
                ])
                if last_bot_message_id:
                    await client.edit_message_text(message.chat.id, last_bot_message_id, text, reply_markup=kb)
                return

            emojis = text.split()
            user_states[user_id]['forward_settings']['reaction_emojis'] = emojis
            await message.reply(f"✅ Эмодзи сохранены: {' '.join(emojis)}", reply_markup=ReplyKeyboardRemove())
            
            user_states[user_id]['forward_state'] = None
            
            # Re-show reactions menu on the original bot message
            last_bot_message_id = user_states[user_id].get('last_msg_id')
            settings = user_states[user_id].get('forward_settings', {})
            reactions_enabled = settings.get('reactions_enabled', False)
            emojis = settings.get('reaction_emojis', [])

            text = "🎭 Настройка автоматических реакций\n\n"
            if reactions_enabled:
                text += f"Статус: Включено\n"
                text += f"Эмодзи: {' '.join(emojis) if emojis else 'Не заданы'}"
            else:
                text += "Статус: Отключено"

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Включить" if not reactions_enabled else "❌ Отключить", callback_data="forward_reactions_toggle")],
                [InlineKeyboardButton("😀 Изменить эмодзи", callback_data="forward_reactions_emojis")],
                [InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]
            ])
            if last_bot_message_id:
                try:
                    await client.edit_message_text(message.chat.id, last_bot_message_id, text, reply_markup=kb)
                except MessageNotModified:
                    pass
            
            return

        elif forward_state == 'range_start_input':
            if text == "🔙 Назад":
                user_states[user_id]['forward_state'] = None
                await show_forwarding_settings(client, message, user_id)
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

                # Получаем реальный последний ID сообщения
                real_last_message_id = await api_client.get_channel_last_message_id(str(user_states[user_id]['forward_channel_id']))
                if real_last_message_id is not None:
                    stats = stats.copy()  # Создаем копию чтобы не изменять оригинал
                    stats['last_message_id'] = real_last_message_id

                stat_text = format_channel_stats(stats)
                channel_id = user_states[user_id]['forward_channel_id']
                target_channel = user_states[user_id].get('forward_target_channel')

                # Формируем текст с информацией о каналах
                source_channel_info = f"📥 Из: {user_states[user_id]['forward_channel_title']}"
                target_channel_info = ""
                if target_channel:
                    target_title = user_states[user_id].get('forward_target_title', target_channel)
                    target_channel_info = f"\n📤 В: {target_title}"

                await safe_edit_callback_message(
                    callback_query,
                    f"📊 Статистика канала:\n{source_channel_info}{target_channel_info}\n\n{stat_text}\n\nВыберите действие:",
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


    # Проверяем состояния сессий и реакций
    if state and state.startswith("session_"):
        # Эти состояния обрабатываются в других обработчиках
        return
    elif state and state.startswith("reaction_"):
        # Обработка реакций перенесена в reaction_master.py
        from bot.reaction_master import process_reaction_fsm
        await process_reaction_fsm(client, message)
        return
    
    # === TEXT EDITING HANDLERS ===
    elif state == "text_edit_menu":
        if text == "🆕 Запустить редактирование":
            # Сначала показываем настройки
            await show_text_edit_settings(client, message, user_id)
            # Удаляем предыдущее сообщение если есть
            if last_msg_id:
                try:
                    await client.delete_messages(message.chat.id, last_msg_id)
                except Exception:
                    pass
        elif text == "⚙️ Настройки редактирования":
            await show_text_edit_settings(client, message, user_id)
        elif text == "📊 Статус задач редактирования":
            await show_text_edit_tasks_status(client, message, user_id)
        elif text == "⏹️ Остановить задачу":
            await show_text_edit_stop_menu(client, message, user_id)
        elif text == "🔙 Назад в главное меню":
            await show_main_menu(client, message)
        return
    
    elif state == FSM_TEXT_EDIT_CHANNEL:
        channel_info = await resolve_channel(api_client, text)
        if channel_info is None:
            await message.reply("❌ Канал не найден. Попробуйте еще раз:")
            return
            
        # Извлекаем числовой ID из строки
        numeric_id = extract_numeric_id(channel_info['id'])
        if numeric_id is None:
            await message.reply("❌ Не удалось извлечь числовой ID канала. Попробуйте еще раз:")
            return
            
        user_states[user_id]['text_edit_channel_id'] = numeric_id
        user_states[user_id]['text_edit_channel_title'] = channel_info['title']
        user_states[user_id]['text_edit_channel_username'] = channel_info.get('username')

        # Получаем настройки для отображения
        text_edit_settings = user_states[user_id].get('text_edit_settings', {})
        footer_text = text_edit_settings.get('footer_text', '')
        max_posts = text_edit_settings.get('max_posts', 100)
        require_hashtags = text_edit_settings.get('require_hashtags', False)
        require_specific_text = text_edit_settings.get('require_specific_text', False)
        specific_text = text_edit_settings.get('specific_text', '')
        require_old_footer = text_edit_settings.get('require_old_footer', True)

        # Проверяем заполненность настроек
        settings_complete = bool(footer_text.strip())

        # Показываем меню канала с inline кнопками
        channel_menu_text = f"📺 **Канал выбран**: {channel_info['title']}\n\n"

        if settings_complete:
            channel_menu_text += f"📝 **Приписка:** {footer_text[:50]}{'...' if len(footer_text) > 50 else ''}\n"
            channel_menu_text += f"📊 **Максимум постов:** {max_posts}\n"
            channel_menu_text += f"🏷️ **Хэштеги:** {'Да' if require_hashtags else 'Нет'}\n"
            channel_menu_text += f"🔤 **Текст:** {'Да' if require_specific_text else 'Нет'}"
            if require_specific_text and specific_text:
                channel_menu_text += f" ({specific_text[:20]}{'...' if len(specific_text) > 20 else ''})"
            channel_menu_text += "\n"
            channel_menu_text += f"📝 **Старая приписка:** {'Да' if require_old_footer else 'Нет'}\n\n"
            channel_menu_text += "✅ Настройки готовы. Можно запускать редактирование."
        else:
            channel_menu_text += "⚠️ **Приписка не установлена**\n\n"
            channel_menu_text += "Необходимо настроить приписку перед запуском редактирования."

        channel_menu_text += "\n\nВыберите действие:"

        sent = await message.reply(
            channel_menu_text,
            reply_markup=get_text_edit_inline_keyboard(channel_id=numeric_id)
        )
        if last_msg_id:
            try:
                await client.delete_messages(message.chat.id, last_msg_id)
            except Exception:
                pass
        if sent:
            user_states[user_id]['last_msg_id'] = sent.id
        return
    
    elif state == FSM_TEXT_EDIT_LINK_TEXT:
        if text == "🔙 Назад":
            kb = await get_channel_history_keyboard(user_id)
            sent = await message.reply(
                "📺 **Выбор канала для редактирования**\n\n"
                "Выберите канал из истории или введите ID/ссылку канала:",
                reply_markup=kb or ReplyKeyboardRemove()
            )
            if last_msg_id:
                try:
                    await client.delete_messages(message.chat.id, last_msg_id)
                except Exception:
                    pass
            if sent:
                user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_TEXT_EDIT_CHANNEL, "last_msg_id": sent.id}
            return
            
        # Инициализируем настройки если их нет
        if 'text_edit_settings' not in user_states[user_id]:
            user_states[user_id]['text_edit_settings'] = {}
        user_states[user_id]['text_edit_settings']['link_text'] = text
        
        sent = await message.reply(
            f"✏️ **Текст ссылки**: `{text}`\n\n"
            "🔗 **Введите URL для гиперссылки**\n\n"
            "Например: `https://t.me/yourchannel`\n"
            "На этот адрес будет вести ссылка.",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Назад")]], resize_keyboard=True)
        )
        if last_msg_id:
            try:
                await client.delete_messages(message.chat.id, last_msg_id)
            except Exception:
                pass
        if sent:
            user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_TEXT_EDIT_LINK_URL, "last_msg_id": sent.id}
        return
    
    elif state == FSM_TEXT_EDIT_LINK_URL:
        if text == "🔙 Назад":
            sent = await message.reply(
                f"📺 **Канал выбран**: {user_states[user_id].get('text_edit_channel_title', 'Неизвестно')}\n\n"
                "✏️ **Введите текст для гиперссылки**\n\n"
                "Например: `подписывайтесь на приватку`\n"
                "Этот текст будет добавлен к постам как кликабельная ссылка.",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Назад")]], resize_keyboard=True)
            )
            if last_msg_id:
                try:
                    await client.delete_messages(message.chat.id, last_msg_id)
                except Exception:
                    pass
            if sent:
                user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_TEXT_EDIT_LINK_TEXT, "last_msg_id": sent.id}
            return
            
        # Простая валидация URL
        if not (text.startswith('http://') or text.startswith('https://') or text.startswith('tg://')):
            await message.reply("❌ Неверный формат URL. Должен начинаться с http://, https:// или tg://")
            return
            
        # Инициализируем настройки если их нет
        if 'text_edit_settings' not in user_states[user_id]:
            user_states[user_id]['text_edit_settings'] = {}
        user_states[user_id]['text_edit_settings']['link_url'] = text
        
        sent = await message.reply(
            f"🔗 **URL ссылки**: `{text}`\n\n"
            "📊 **Введите лимит постов для редактирования**\n\n"
            "Например: `100` (будут изменены 100 последних постов)\n"
            "Введите число от 1 до 1000:",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("100"), KeyboardButton("50"), KeyboardButton("25")],
                [KeyboardButton("🔙 Назад")]
            ], resize_keyboard=True)
        )
        if last_msg_id:
            try:
                await client.delete_messages(message.chat.id, last_msg_id)
            except Exception:
                pass
        if sent:
            user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_TEXT_EDIT_LIMIT, "last_msg_id": sent.id}
        return
    
    elif state == FSM_TEXT_EDIT_LIMIT:
        if text == "🔙 Назад":
            sent = await message.reply(
                f"✏️ **Текст ссылки**: `{user_states[user_id].get('text_edit_link_text', 'Неизвестно')}`\n\n"
                "🔗 **Введите URL для гиперссылки**\n\n"
                "Например: `https://t.me/yourchannel`\n"
                "На этот адрес будет вести ссылка.",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Назад")]], resize_keyboard=True)
            )
            if last_msg_id:
                try:
                    await client.delete_messages(message.chat.id, last_msg_id)
                except Exception:
                    pass
            if sent:
                user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_TEXT_EDIT_LINK_URL, "last_msg_id": sent.id}
            return
            
        try:
            limit = int(text)
            if limit < 1 or limit > 1000:
                await message.reply("❌ Лимит должен быть от 1 до 1000")
                return
        except ValueError:
            await message.reply("❌ Введите корректное число")
            return
            
        # Инициализируем настройки если их нет
        if 'text_edit_settings' not in user_states[user_id]:
            user_states[user_id]['text_edit_settings'] = {}
        user_states[user_id]['text_edit_settings']['max_posts'] = limit
        
        # Показываем подтверждение
        channel_title = user_states[user_id].get('text_edit_channel_title', 'Неизвестно')
        # Получаем настройки из text_edit_settings
        text_edit_settings = user_states[user_id].get('text_edit_settings', {})
        link_text = text_edit_settings.get('link_text', 'Неизвестно')
        link_url = text_edit_settings.get('link_url', 'Неизвестно')
        
        sent = await message.reply(
            f"📋 **Подтверждение редактирования**\n\n"
            f"📺 **Канал**: {channel_title}\n"
            f"✏️ **Текст ссылки**: `{link_text}`\n"
            f"🔗 **URL**: `{link_url}`\n"
            f"📊 **Лимит постов**: {limit}\n\n"
            f"➡️ **Что будет сделано**:\n"
            f"К последним {limit} постам в канале будет добавлена ссылка:\n"
            f"`{link_text}` → `{link_url}`\n\n"
            f"⚠️ **Внимание**: Это действие изменит существующие посты!\n\n"
            f"Продолжить?",
            reply_markup=get_text_edit_confirmation_keyboard()
        )
        if last_msg_id:
            try:
                await client.delete_messages(message.chat.id, last_msg_id)
            except Exception:
                pass
        if sent:
            user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_TEXT_EDIT_CONFIRM, "last_msg_id": sent.id}
        return

    elif state == FSM_TEXT_EDIT_SETTINGS:
        # Этот обработчик теперь не нужен, так как настройки работают через callback'и
        return

    elif state == FSM_TEXT_EDIT_FOOTER_EDIT:
        # Сохраняем введенный footer текст
        # Инициализируем настройки если их нет
        if 'text_edit_settings' not in user_states[user_id]:
            user_states[user_id]['text_edit_settings'] = {}

        # Сохраняем footer текст
        user_states[user_id]['text_edit_settings']['footer_text'] = text

        # Возвращаемся к настройкам
        await show_text_edit_settings(client, message, user_id)
        return

    elif state == FSM_TEXT_EDIT_SPECIFIC_TEXT:
        # Сохраняем введенный текст для поиска
        if 'text_edit_settings' not in user_states[user_id]:
            user_states[user_id]['text_edit_settings'] = {}

        # Сохраняем текст и включаем требование
        user_states[user_id]['text_edit_settings']['specific_text'] = text
        user_states[user_id]['text_edit_settings']['require_specific_text'] = True

        # Возвращаемся к настройкам
        await show_text_edit_settings(client, message, user_id)
        return

    elif state == FSM_TEXT_EDIT_CONFIRM:
        if text == "✅ Запустить":
            await start_text_editing_task(client, message, user_id)
        elif text in ["❌ Отмена", "🔙 Назад"]:
            sent = await message.reply(
                "🛠 **Редактирование текста постов**\n\n"
                "Этот режим позволяет добавлять новые гиперссылки ко всем постам в канале.\n\n"
                "Выберите действие:",
                reply_markup=get_text_edit_menu_keyboard()
            )
            if last_msg_id:
                try:
                    await client.delete_messages(message.chat.id, last_msg_id)
                except Exception:
                    pass
            if sent:
                user_states[user_id] = {**user_states.get(user_id, {}), "state": "text_edit_menu", "last_msg_id": sent.id}
        return
    
    # Если этап не определён
    await show_main_menu(client, message, "Пожалуйста, выберите действие из меню:")

async def show_text_edit_tasks_status(client, message, user_id):
    """Показать статус всех задач редактирования текста"""
    try:
        text_editor = TextEditorManager()
        result = await text_editor.get_all_tasks()
        
        formatted_message = text_editor.format_all_tasks_message(result)
        
        await message.reply(
            formatted_message,
            reply_markup=get_text_edit_menu_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Ошибка при получении статуса задач: {e}")
        await message.reply(
            f"❌ **Ошибка**: {str(e)}",
            reply_markup=get_text_edit_menu_keyboard()
        )

async def show_text_edit_stop_menu(client, message, user_id):
    """Показать меню остановки задач редактирования"""
    try:
        text_editor = TextEditorManager()
        result = await text_editor.get_all_tasks()
        
        if result.get('status') == 'error':
            await message.reply(
                f"❌ **Ошибка получения задач**: {result.get('message', 'Неизвестная ошибка')}",
                reply_markup=get_text_edit_menu_keyboard()
            )
            return
            
        tasks = result.get('tasks', [])
        running_tasks = [t for t in tasks if t.get('status') == 'running']
        
        if not running_tasks:
            await message.reply(
                "📝 **Остановка задач редактирования**\n\n"
                "Нет активных задач для остановки.",
                reply_markup=get_text_edit_menu_keyboard()
            )
            return
            
        # Создаем кнопки для каждой активной задачи
        buttons = []
        for task in running_tasks:
            task_id = task.get('task_id', 'Неизвестно')
            channel_id = task.get('channel_id', 'Неизвестно')
            buttons.append([KeyboardButton(f"⏹️ {task_id} ({channel_id})")])
            
        buttons.append([KeyboardButton("🔙 Назад")])
        
        await message.reply(
            "📝 **Остановка задач редактирования**\n\n"
            "Выберите задачу для остановки:",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True)
        )
        
        # Переходим в состояние выбора задачи для остановки
        user_states[user_id] = {**user_states.get(user_id, {}), "state": "text_edit_stop_select"}
        
    except Exception as e:
        logger.error(f"Ошибка при показе меню остановки: {e}")
        await message.reply(
            f"❌ **Ошибка**: {str(e)}",
            reply_markup=get_text_edit_menu_keyboard()
        )

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
    user_states[user_id]['state'] = FSM_FORWARD_SETTINGS

async def show_forwarding_menu(client, message, user_id: int):
    """Показать главное меню пересылки"""
    user_state = user_states.get(user_id, {})
    source_channel_title = user_state.get('forward_channel_title', 'Не выбран')
    target_channels = user_state.get('forward_target_channels', [])
    channel_id = user_state.get('forward_channel_id')

    menu_text = f"📥 Из: {html.escape(source_channel_title)}\n\n"

    if not target_channels:
        menu_text += "❌ Не выбрано ни одного целевого канала"
    else:
        menu_text += "📤 В:"
        for i, ch in enumerate(target_channels, 1):
            title = ch.get('title', ch['id'])
            username = ch.get('username', '')
            if username:
                title += f" (@{username})"
            menu_text += f"\n{i}. {html.escape(title)}"

    menu_text += "\n\nВыберите действие:"

    # Вместо reply редактируем сообщение
    await safe_edit_message(
        client,
        message.chat.id,
        message.id,
        menu_text,
        reply_markup=get_forwarding_inline_keyboard(channel_id, None)
    )

    user_states[user_id]['state'] = FSM_FORWARD_MENU

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

        
    elif action == "back_to_settings":
        # Возвращаемся к настройкам пересылки
        await show_forwarding_settings(client, callback_query.message, user_id)
        await callback_query.answer()
        return
    
    # === ОБРАБОТКА WATERMARK CALLBACK ===
    if data.startswith('watermark') or data.startswith('wm_'):
        from bot.watermark_handlers import (
            handle_watermark_settings, handle_wm_toggle, handle_wm_type,
            handle_wm_type_text, handle_wm_type_image, handle_wm_mode,
            handle_wm_mode_all, handle_wm_mode_random, handle_wm_mode_hashtag,
            handle_wm_mode_manual, handle_wm_position, handle_wm_position_set,
            handle_wm_opacity, handle_wm_scale, handle_wm_save, handle_wm_menu
        )

        if data == 'watermark_settings':
            await handle_watermark_settings(client, callback_query)
        elif data == 'watermark_channel_select':
            # Показать список каналов для выбора watermark
            await show_watermark_channel_selection(client, callback_query.message, user_id)
            await callback_query.answer()
            return
        elif data.startswith('watermark_channel_'):
            # Обработка выбора канала для watermark
            channel_id = data.replace('watermark_channel_', '')
            user_states[user_id]['current_watermark_channel_id'] = channel_id
            # Автоматически применяем настройки канала
            await watermark_manager.apply_channel_watermark(user_id, channel_id)
            await handle_watermark_settings(client, callback_query)
        elif data == 'wm_toggle':
            await handle_wm_toggle(client, callback_query)
        elif data == 'wm_type':
            await handle_wm_type(client, callback_query)
        elif data == 'wm_type_text':
            await handle_wm_type_text(client, callback_query)
        elif data == 'wm_type_image':
            await handle_wm_type_image(client, callback_query)
        elif data == 'wm_mode':
            await handle_wm_mode(client, callback_query)
        elif data == 'wm_mode_all':
            await handle_wm_mode_all(client, callback_query)
        elif data == 'wm_mode_random':
            await handle_wm_mode_random(client, callback_query)
        elif data == 'wm_mode_hashtag':
            await handle_wm_mode_hashtag(client, callback_query)
        elif data == 'wm_mode_manual':
            await handle_wm_mode_manual(client, callback_query)
        elif data == 'wm_position':
            await handle_wm_position(client, callback_query)
        elif data.startswith('wm_pos_'):
            position = data.replace('wm_pos_', '')
            await handle_wm_position_set(client, callback_query, position)
        elif data == 'wm_opacity':
            await handle_wm_opacity(client, callback_query)
        elif data == 'wm_scale':
            await handle_wm_scale(client, callback_query)
        elif data == 'wm_save':
            await handle_wm_save(client, callback_query)
        elif data == 'wm_menu':
            await handle_wm_menu(client, callback_query)
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
            'parse_mode': publish_settings.get('parse_mode', 'HTML'),
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
        # Показываем меню настройки приписки
        current_footer = user_states[user_id]['forward_settings'].get('footer_text', '')
        footer_preview = current_footer if current_footer else "Приписка не установлена"

        # Создаем клавиатуру с опциями
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Изменить текст", callback_data="forward_footer_edit")],
            [InlineKeyboardButton("📋 Шаблоны", callback_data="forward_footer_templates")],
            [InlineKeyboardButton("🔗 Редактировать ссылки", callback_data="forward_footer_links")],
            [InlineKeyboardButton("🗑️ Удалить", callback_data="forward_footer_delete")],
            [InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]
        ])

        text = f"📝 Настройка приписки к сообщениям\n\n"
        text += f"Текущая приписка:\n{footer_preview}\n\n"
        text += f"Приписка добавляется к каждому пересылаемому сообщению.\n\n"
        text += f"🔗 Для создания кликабельных ссылок используйте HTML:\n"
        text += f"<code>&lt;a href=\"ВАША_ССЫЛКА\"&gt;ТЕКСТ&lt;/a&gt;</code>\n\n"
        text += f"Примеры ссылок:\n"
        text += f"• <code>https://t.me/channel</code> - публичный канал\n"
        text += f"• <code>https://t.me/+invite</code> - приватный канал\n"
        text += f"• <code>https://donate.url</code> - донат"

        await safe_edit_callback_message(
            callback_query,
            text,
            reply_markup=keyboard
        )
        return

    elif data == "forward_footer_links":
        # Помогаем редактировать ссылки в существующей приписке
        current_footer = user_states[user_id]['forward_settings'].get('footer_text', '')

        if not current_footer:
            await callback_query.answer("❌ Приписка не установлена! Сначала создайте приписку.", show_alert=True)
            return

        import re
        # Ищем все ссылки в приписке
        links = re.findall(r'<a href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', current_footer)

        if not links:
            text = f"🔗 В приписке нет ссылок для редактирования\n\n"
            text += f"Текущая приписка: {current_footer}\n\n"
            text += f"Используйте 'Изменить текст' чтобы добавить ссылки."
        else:
            text = f"🔗 Найденные ссылки в приписке:\n\n"
            for i, (url, link_text) in enumerate(links, 1):
                text += f"{i}. {link_text}\n   {url}\n\n"

            text += f"Чтобы изменить ссылки, используйте 'Изменить текст'\n"
            text += f"и замените YOUR_CHANNEL на свои значения."

        await safe_edit_callback_message(
            callback_query,
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_footer")]])
        )
        return

    elif data == "forward_footer_edit":
        # Запрашиваем ввод новой приписки
        current_footer = user_states[user_id]['forward_settings'].get('footer_text', '')
        examples = [
            '<a href="https://t.me/channel">Подписаться на канал</a>',
            '<a href="https://t.me/+invite_link">Приватный канал</a>',
            '<a href="https://donate.url">Поддержать автора</a>'
        ]

        text = f"✏️ Введите новую приписку\n\n"
        text += f"Текущая: {current_footer or 'Нет'}\n\n"
        text += f"🔗 Гиперссылки создаются с помощью HTML-тегов:\n\n"
        text += f"Формат: <code>&lt;a href=\"ВАША_ССЫЛКА\"&gt;ТЕКСТ&lt;/a&gt;</code>\n\n"
        text += f"Примеры:\n"
        text += f"• Публичный канал:\n"
        text += f"  <code>&lt;a href=\"https://t.me/channel\"&gt;Подписаться&lt;/a&gt;</code>\n\n"
        text += f"• Приватный канал:\n"
        text += f"  <code>&lt;a href=\"https://t.me/+invite_link\"&gt;Приватный канал&lt;/a&gt;</code>\n\n"
        text += f"• Донат:\n"
        text += f"  <code>&lt;a href=\"https://donate.url\"&gt;Поддержать&lt;/a&gt;</code>\n\n"
        text += f"💡 Замените YOUR_CHANNEL на свой username канала\n\n"
        text += f"Или введите 'убрать' для удаления приписки:"

        await safe_edit_callback_message(
            callback_query,
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_footer")]])
        )
        user_states[user_id]['forward_state'] = 'footer_input'
        return

    elif data == "forward_footer_templates":
        # Показываем готовые шаблоны
        templates = [
            ('📢 Подписаться на канал', '📢 <a href="https://t.me/YOUR_CHANNEL">Подпишись на новый канал</a> 📢'),
            ('🔒 Приватный канал', '🔒 <a href="https://t.me/+YOUR_PRIVATE_LINK">Приватный канал / Подписаться</a>'),
            ('💰 Донат', '💰 <a href="https://donate.url">Поддержать автора</a>'),
        ]

        keyboard_buttons = []
        for i, (name, template) in enumerate(templates):
            keyboard_buttons.append([InlineKeyboardButton(f"{name}", callback_data=f"forward_footer_template_{i}")])

        keyboard_buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="forward_footer")])

        text = f"📋 Готовые шаблоны приписок\n\n"
        text += f"Выберите шаблон и замените:\n"
        text += f"• YOUR_CHANNEL на username вашего канала\n"
        text += f"• YOUR_PRIVATE_LINK на invite-ссылку приватного канала\n"
        text += f"• donate.url на вашу ссылку для донатов\n\n"

        for i, (name, template) in enumerate(templates):
            text += f"{i+1}. {name}\n   {template}\n\n"

        await safe_edit_callback_message(
            callback_query,
            text,
            reply_markup=InlineKeyboardMarkup(keyboard_buttons)
        )
        # Сохраняем шаблоны в состоянии для использования в обработчиках
        user_states[user_id]['footer_templates'] = templates
        return

    elif data == "forward_footer_delete":
        # Удаляем приписку
        if 'footer_text' in user_states[user_id]['forward_settings']:
            del user_states[user_id]['forward_settings']['footer_text']

        await callback_query.answer("✅ Приписка удалена!")
        await show_forwarding_settings(client, callback_query.message, user_id)
        return

    elif data.startswith("forward_footer_template_"):
        # Применяем выбранный шаблон
        template_index = int(data.replace("forward_footer_template_", ""))

        # Определяем шаблоны заново (чтобы не хранить в состоянии)
        templates = [
            ('📢 Подписаться на канал', '📢 <a href="https://t.me/YOUR_CHANNEL">Подпишись на новый канал</a> 📢'),
            ('🔒 Приватный канал', '🔒 <a href="https://t.me/+YOUR_PRIVATE_LINK">Приватный канал / Подписаться</a>'),
            ('💰 Донат', '💰 <a href="https://donate.url">Поддержать автора</a>'),
        ]

        if 0 <= template_index < len(templates):
            template_name, template_text = templates[template_index]
            user_states[user_id]['forward_settings']['footer_text'] = template_text

            # Показываем сообщение с инструкцией по редактированию
            await callback_query.answer(f"✅ Шаблон '{template_name}' применен! Отредактируйте ссылки в настройках.", show_alert=True)
            await show_forwarding_settings(client, callback_query.message, user_id)
        else:
            await callback_query.answer("❌ Шаблон не найден!", show_alert=True)
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

            # Получаем реальный последний ID сообщения
            real_last_message_id = await api_client.get_channel_last_message_id(str(user_states[user_id]['forward_channel_id']))
            if real_last_message_id is not None:
                stats = stats.copy()  # Создаем копию чтобы не изменять оригинал
                stats['last_message_id'] = real_last_message_id

            stat_text = format_channel_stats(stats)
            channel_id = user_states[user_id]['forward_channel_id']
            target_channel = user_states[user_id].get('forward_target_channel')

            # Формируем текст с информацией о каналах
            source_channel_info = f"📥 Из: {user_states[user_id]['forward_channel_title']}"
            target_channel_info = ""
            if target_channel:
                target_title = user_states[user_id].get('forward_target_title', target_channel)
                target_channel_info = f"\n📤 В: {target_title}"

            await safe_edit_callback_message(
                callback_query,
                f"📊 Статистика канала:\n{source_channel_info}{target_channel_info}\n\n{stat_text}\n\nВыберите действие:",
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
            if result:  # result является bool из core.py
                message_text = "✅ Парсинг и пересылка запущены!"
                await safe_edit_callback_message(callback_query, message_text)
                # Сбрасываем состояние пользователя в главное меню
                user_states[user_id]["state"] = FSM_MAIN_MENU
                try:
                    await callback_query.answer("✅ Задача запущена!")
                except Exception:
                    pass
            else:
                try:
                    await callback_query.answer("❌ Ошибка запуска парсинга", show_alert=True)
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

        # Обработка специальных команд
        if text.lower() == "убрать":
            if 'footer_text' in user_states[user_id]['forward_settings']:
                del user_states[user_id]['forward_settings']['footer_text']
            await message.reply("✅ Приписка удалена!")
            await show_forwarding_settings(client, message, user_id)
            return

        # Валидация HTML (простая проверка)
        import re
        html_tags = re.findall(r'<[^>]+>', text)
        if html_tags:
            # Проверяем, что все теги закрыты правильно
            open_tags = []
            for tag in html_tags:
                if tag.startswith('</'):
                    # Закрывающий тег
                    tag_name = tag[2:-1].split()[0]  # убираем </ и >, берем имя тега
                    if open_tags and open_tags[-1] == tag_name:
                        open_tags.pop()
                    else:
                        await message.reply("❌ Ошибка в HTML: несоответствие открывающих и закрывающих тегов!")
                        return
                elif not tag.endswith('/>') and not tag.startswith('<!'):
                    # Открывающий тег
                    tag_name = tag[1:].split()[0].split('>')[0]  # убираем <, берем имя тега
                    if tag_name not in ['br', 'img']:  # Самозакрывающиеся теги
                        open_tags.append(tag_name)

            if open_tags:
                await message.reply(f"❌ Ошибка в HTML: незакрытые теги: {', '.join(open_tags)}")
                return

        # Сохраняем приписку
        user_states[user_id]["forward_settings"]["footer_text"] = text

        # Показываем превью
        preview_text = f"📝 Приписка сохранена!\n\nКак будет выглядеть:\n{text}\n\n"
        preview_text += "Примечание: HTML-ссылки будут кликабельными в Telegram."

        await message.reply(preview_text)
        await show_forwarding_settings(client, message, user_id)
        return

    # --- Удалены дублирующиеся обработчики FSM_FORWARD_FOOTER_LINK и FSM_FORWARD_FOOTER_LINK_TEXT ---

    if data == "forward_back_to_stats":
        # Возвращаемся к статистике канала
        stats = await api_client.get_channel_stats(str(user_states[user_id]['forward_channel_id']))

        # Получаем реальный последний ID сообщения
        real_last_message_id = await api_client.get_channel_last_message_id(str(user_states[user_id]['forward_channel_id']))
        if real_last_message_id is not None:
            stats = stats.copy()  # Создаем копию чтобы не изменять оригинал
            stats['last_message_id'] = real_last_message_id

        stat_text = format_channel_stats(stats)
        channel_id = user_states[user_id]['forward_channel_id']
        target_channel = user_states[user_id].get('forward_target_channel')

        # Формируем текст с информацией о каналах
        source_channel_info = f"📥 Из: {user_states[user_id]['forward_channel_title']}"
        target_channel_info = ""
        if target_channel:
            target_title = user_states[user_id].get('forward_target_title', target_channel)
            target_channel_info = f"\n📤 В: {target_title}"

        await safe_edit_callback_message(
            callback_query,
            f"📊 Статистика канала:\n{source_channel_info}{target_channel_info}\n\n{stat_text}\n\nВыберите действие:",
            reply_markup=get_forwarding_inline_keyboard(channel_id, target_channel)
        )
        user_states[user_id]["state"] = FSM_FORWARD_SETTINGS
        return

    if data == "forward_reactions":
        # Показываем меню настройки реакций
        settings = user_states[user_id].get('forward_settings', {})
        reactions_enabled = settings.get('reactions_enabled', False)
        emojis = settings.get('reaction_emojis', [])

        text = "🎭 Настройка автоматических реакций\n\n"
        if reactions_enabled:
            text += f"Статус: Включено\n"
            text += f"Эмодзи: {' '.join(emojis) if emojis else 'Не заданы'}"
        else:
            text += "Статус: Отключено"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Включить" if not reactions_enabled else "❌ Отключить", callback_data="forward_reactions_toggle")],
            [InlineKeyboardButton("😀 Изменить эмодзи", callback_data="forward_reactions_emojis")],
            [InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]
        ])
        await safe_edit_callback_message(
            callback_query,
            text,
            reply_markup=kb
        )
        return

    if data == "forward_reactions_toggle":
        settings = user_states[user_id].get('forward_settings', {})
        settings['reactions_enabled'] = not settings.get('reactions_enabled', False)
        if settings['reactions_enabled'] and not settings.get('reaction_emojis'):
            settings['reaction_emojis'] = ['❤️', '😘', '😍']
        await callback_query.answer(f"Реакции {'включены' if settings['reactions_enabled'] else 'отключены'}!")
        
        # Re-show reactions menu
        settings = user_states[user_id].get('forward_settings', {})
        reactions_enabled = settings.get('reactions_enabled', False)
        emojis = settings.get('reaction_emojis', [])

        text = "🎭 Настройка автоматических реакций\n\n"
        if reactions_enabled:
            text += f"Статус: Включено\n"
            text += f"Эмодзи: {' '.join(emojis) if emojis else 'Не заданы'}"
        else:
            text += "Статус: Отключено"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Включить" if not reactions_enabled else "❌ Отключить", callback_data="forward_reactions_toggle")],
            [InlineKeyboardButton("😀 Изменить эмодзи", callback_data="forward_reactions_emojis")],
            [InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_settings")]
        ])
        await safe_edit_callback_message(
            callback_query,
            text,
            reply_markup=kb
        )
        return

    if data == "forward_reactions_emojis":
        await safe_edit_callback_message(
            callback_query,
            "Введите один или несколько эмодзи через пробел:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="forward_reactions")]])
        )
        user_states[user_id]['forward_state'] = 'reactions_emojis_input'
        return

    # --- Обработка управления выбранными каналами ---
    if data == "add_target_channel":
        await callback_query.message.delete()
        kb = await get_target_channel_history_keyboard(user_id)
        sent = await client.send_message(
            chat_id=user_id,
            text="Выберите канал для добавления в список пересылки или введите новый:",
            reply_markup=kb or ReplyKeyboardRemove()
        )
        if sent:
            user_states[user_id]['last_msg_id'] = sent.id
        user_states[user_id]["state"] = FSM_FORWARD_TARGET
        await callback_query.answer()
        return

    if data == "forward_to_settings":
        # Переходим к меню пересылки с кнопками мониторинга, парсинга и т.д.
        await show_forwarding_menu(client, callback_query.message, user_id)
        await callback_query.answer()
        return

    if data.startswith("remove_target_channel:"):
        # Удаляем канал из списка
        try:
            index = int(data.split(":")[1])
            target_channels = user_states[user_id].get('forward_target_channels', [])
            if 0 <= index < len(target_channels):
                removed_channel = target_channels.pop(index)
                await callback_query.answer(f"Канал '{removed_channel['title']}' удален")
            else:
                await callback_query.answer("Ошибка: индекс канала вне диапазона")
        except Exception as e:
            await callback_query.answer(f"Ошибка удаления канала: {e}")

        # Показываем обновленный список
        await show_target_channels_management(client, callback_query.message, user_id)
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

# --- Функция для извлечения числового ID из строки ---
def extract_numeric_id(channel_id_str):
    """Извлекает числовой ID из строки вида 'Name (ID: -1234567890, @username)'"""
    if not channel_id_str:
        return None
        
    # Если это уже число, возвращаем как есть
    try:
        return int(channel_id_str)
    except (ValueError, TypeError):
        pass
    
    # Ищем паттерн (ID: -числа)
    import re
    match = re.search(r'\(ID:\s*(-?\d+)', str(channel_id_str))
    if match:
        return int(match.group(1))
    
    # Если начинается с -100 (стандартный канал ID)
    if str(channel_id_str).startswith('-100') and str(channel_id_str).replace('-', '').isdigit():
        return int(channel_id_str)
        
    return None

# --- Функция для нормализации входных данных канала ---
def normalize_channel_input(text: str) -> str:
    """Нормализует входные данные пользователя для канала

    Поддерживает форматы:
    - t.me/username -> username
    - @username -> username
    - username -> username
    - -100xxxxxxxxx -> -100xxxxxxxxx
    - xxxxxxxxxx -> xxxxxxxxxx (если число)
    - Название (ID: -100xxxxxxxxx, @username) -> username или ID
    """
    text = text.strip()

    # Удаляем https:// если есть
    if text.startswith('https://'):
        text = text[8:]
    elif text.startswith('http://'):
        text = text[7:]

    # Обрабатываем t.me/username
    if text.startswith('t.me/'):
        username = text[5:]  # убираем 't.me/'
        # Удаляем query параметры если есть
        if '?' in username:
            username = username.split('?')[0]
        return username

    # Обрабатываем @username
    if text.startswith('@'):
        return text[1:]  # убираем '@'

    # Обрабатываем формат "Название (ID: -100xxxxxxxxx, @username)"
    import re
    channel_pattern = re.search(r'\(ID:\s*(-?\d+),\s*@([^)]+)\)', text)
    if channel_pattern:
        channel_id = channel_pattern.group(1)
        username = channel_pattern.group(2)
        # Предпочитаем использовать username, так как он более читаемый
        if username:
            return username
        # Если username пустой, используем ID
        return channel_id

    # Если это число или начинается с -100, возвращаем как есть
    if text.isdigit() or (text.startswith('-') and text[1:].isdigit()):
        return text

    # Иначе считаем что это username
    return text

# --- Функция для нормализации канала ---
async def resolve_channel(api_client, text):
    # Сначала нормализуем входные данные
    normalized_text = normalize_channel_input(text)
    print(f"[DEBUG] resolve_channel: input='{text}' -> normalized='{normalized_text}'")

    stats = await api_client.get_channel_stats(normalized_text)
    print(f"[DEBUG] resolve_channel: stats from api: {stats}")

    # Проверяем, что получили валидный ответ
    if stats and stats.get("id"):
        channel_id = stats.get("id")
        title = stats.get("title", "")
        username = stats.get("username", "")

        # Если канал найден, id будет числом (числовой ID Telegram)
        # Если канал не найден, id будет строкой (username или то что ввел пользователь)
        if isinstance(channel_id, int) or (isinstance(channel_id, str) and channel_id.startswith("-")):
            print(f"[DEBUG] resolve_channel: канал найден: id={channel_id}, title='{title}', username='{username}'")
            return stats
        else:
            # Канал не найден - id остался строковым username'ом
            print(f"[DEBUG] resolve_channel: канал '{normalized_text}' не найден")
            return None

    print(f"[DEBUG] resolve_channel: нет валидного ответа от API")
    return None  # Возвращаем None если канал не найден или есть ошибка

# --- Функция для нормализации группы ---
async def resolve_group(api_client, text):
    stats = await api_client.get_channel_stats(text)
    if stats and stats.get("id") and not stats.get("error"):
        return stats
    return None  # Возвращаем None если группа не найдена или есть ошибка

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
        return str(val)
    msg = "*📊 Статус задач и мониторингов:*\n\n"
    if updated:
        now = datetime.now().strftime("%H:%M:%S")
        msg += f"_🔄 Список обновлен: {now}_\n\n"
    buttons = []
    # Мониторинги
    if monitorings:
        msg += "*📡 Мониторинги:*\n"
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
            msg += f"{idx}. *Канал:* {safe(channel_info)}\n"
            msg += f"   *Статус:* {status}\n"
            msg += f"   *Цель:* {safe(target_info)}\n"
            msg += f"   *Режим:* {safe(cfg.get('parse_mode'))}\n"
            msg += f"   *Хэштег:* {safe(cfg.get('hashtag_filter'))}\n"
            msg += f"   *Лимит:* {safe(cfg.get('max_posts'))}\n"
            msg += f"   *Платные:* {safe(cfg.get('paid_content_stars'))}⭐\n\n"
            # Кнопка остановки мониторинга если есть оба id (даже если нет в истории)
            if active and task_running and channel_id is not None and target_channel_id is not None:
                buttons.append([InlineKeyboardButton(f"⏹️ Остановить мониторинг {idx}", callback_data=f"stop_monitoring:{channel_id}:{target_channel_id}")])
    # Задачи парсинг+пересылки
    if tasks:
        msg += "*🚀 Задачи парсинг+пересылки:*\n"
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
            msg += f"*{idx}. Задача {safe(task_id)[:15]}...*\n"
            msg += f"   📤 *Источник:* {safe(source)}\n"
            msg += f"   📥 *Цель:* {safe(target)}\n"
            msg += f"   {status_emoji} *Статус:* {status}\n"
            msg += f"   🕐 *Запущена:* {started_at}\n"
            if completed_at and completed_at != "—":
                msg += f"   ✅ *Завершена:* {completed_at}\n"
            if error and error != "—":
                msg += f"   ❌ *Ошибка:* {error[:50]}...\n"
            msg += "\n"
            if status == "running" and task_id:
                buttons.append([InlineKeyboardButton(f"⏹️ Остановить задачу {idx}", callback_data=f"stop_task:{task_id}")])
    
    # Задачи реакций
    if reaction_tasks:
        msg += "*💫 Задачи реакций:*\n"
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
            msg += f"*{idx}. Задача реакций {safe(task_id)[:15]}...*\n"
            msg += f"   📺 *Канал:* {safe(chat_id)}\n"
            msg += f"   😊 *Эмодзи:* {', '.join(emojis) if emojis else '—'}\n"
            msg += f"   🎯 *Режим:* {safe(mode)}\n"
            if count:
                msg += f"   📊 *Количество:* {safe(count)}\n"
            msg += f"   {status_emoji} *Статус:* {status}\n"
            msg += f"   🕐 *Запущена:* {started_at}\n"
            if completed_at and completed_at != "—":
                msg += f"   ✅ *Завершена:* {completed_at}\n"
            if error and error != "—":
                msg += f"   ❌ *Ошибка:* {error[:50]}...\n"
            msg += "\n"
            if status == "running" and task_id:
                buttons.append([InlineKeyboardButton(f"⏹️ Остановить реакцию {idx}", callback_data=f"stop_reaction_task:{task_id}")])
    
    # Задачи публичных групп
    if public_groups_tasks:
        msg += "*📢 Пересылка в публичные группы:*\n"
        for idx, task in enumerate(public_groups_tasks, 1):
            task_id = task.get("task_id")
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
                f"{idx}. *Источник:* {source}\n"
                f"   *Цель:* {target}\n"
                f"   {status_emoji} *Статус:* {status}\n"
                f"   📤 *Переслано:* {forwarded}\n"
                f"   👁️ *Лимит просмотров:* {views_limit}\n"
                f"   🔢 *Диапазон:* {posts_count}\n"
                "\n"
            )
            
            # Добавляем кнопку остановки для запущенных задач
            if status == "running" and task_id:
                buttons.append([InlineKeyboardButton(f"⏹️ Остановить публичную группу {idx}", callback_data=f"stop_public_task:{task_id}")])
    
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
    # Отправляем без parse_mode, так как Telegram может не поддерживать Markdown в некоторых случаях
    try:
        if callback_query:
            await callback_query.edit_message_text(msg, reply_markup=keyboard)
        elif message:
            await message.reply(msg, reply_markup=keyboard)
    except MessageNotModified:
        logger.warning("[STATUS_UNIFIED] MESSAGE_NOT_MODIFIED: текст не изменился")
    except Exception as e:
        logger.error(f"[STATUS_UNIFIED] Ошибка при отправке/редактировании: {e}")

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

# Обработчик остановки задачи публичной группы
async def stop_public_task_callback(client: Client, callback_query):
    try:
        data = callback_query.data
        if data.startswith("stop_public_task:"):
            task_id = data.split(":", 1)[1]
            logger.info(f"[STOP_PUBLIC_TASK] Остановка задачи {task_id}")
            result = await api_client.stop_public_groups_forwarding(task_id)
            logger.info(f"[STOP_PUBLIC_TASK] Результат: {result}")
            if result.get("status") == "success":
                await callback_query.answer("✅ Задача публичной группы остановлена!")
                await check_tasks_status_callback(client, callback_query)
            else:
                error_msg = result.get("message", "Неизвестная ошибка")
                await callback_query.answer(f"❌ Ошибка: {error_msg}")
    except Exception as e:
        logger.error(f"[STOP_PUBLIC_TASK] Ошибка при остановке задачи: {e}")
        await callback_query.answer("❌ Ошибка при остановке задачи")

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

    # Session management callbacks
    if data == "assign_session":
        from bot.session_handlers import assign_session_callback
        await assign_session_callback(client, callback_query)
        return True
    if data.startswith("select_session:"):
        from bot.session_handlers import select_session_callback
        await select_session_callback(client, callback_query)
        return True
    if data.startswith("assign_task:"):
        from bot.session_handlers import assign_task_callback
        await assign_task_callback(client, callback_query)
        return True
    if data.startswith("remove_task:"):
        from bot.session_handlers import remove_task_callback
        await remove_task_callback(client, callback_query)
        return True
    if data == "delete_session":
        from bot.session_handlers import delete_session_callback
        await delete_session_callback(client, callback_query)
        return True
    if data.startswith("confirm_delete:"):
        from bot.session_handlers import confirm_delete_callback
        await confirm_delete_callback(client, callback_query)
        return True
    if data.startswith("delete_confirmed:"):
        from bot.session_handlers import delete_confirmed_callback
        await delete_confirmed_callback(client, callback_query)
        return True
    if data == "cancel_session_action":
        from bot.session_handlers import cancel_session_action_callback
        await cancel_session_action_callback(client, callback_query)
        return True
    if data.startswith("resend_code:"):
        from bot.session_handlers import resend_code_callback
        await resend_code_callback(client, callback_query)
        return True
    if data == "add_session":
        from bot.session_handlers import add_session_callback
        await add_session_callback(client, callback_query)
        return True
    if data == "add_reaction":
        from bot.session_handlers import add_reaction_callback
        await add_reaction_callback(client, callback_query)
        return True

    # Reaction callbacks
    if data.startswith("reaction_"):
        from bot.reaction_master import reaction_callback_handler
        await reaction_callback_handler(client, callback_query)
        return True

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
    # stop_reaction_task
    if data.startswith("stop_reaction_task:"):
        await stop_reaction_task_callback(client, callback_query)
        return True
    # stop_public_task
    if data.startswith("stop_public_task:"):
        await stop_public_task_callback(client, callback_query)
        return True
    # check_tasks_status
    if data == "check_tasks_status":
        await check_tasks_status_callback(client, callback_query)
        return True
    # check_reaction_tasks_status
    if data == "check_reaction_tasks_status":
        await check_reaction_tasks_status_callback(client, callback_query)
        return True
    # text_edit_settings
    if data == "text_edit_settings":
        user_id = callback_query.from_user.id
        await show_text_edit_settings(client, callback_query.message, user_id)
        await callback_query.answer()
        return True
    # text_edit_change_text
    if data == "text_edit_change_text":
        user_id = callback_query.from_user.id
        user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_TEXT_EDIT_LINK_TEXT, "last_msg_id": None}
        await callback_query.message.reply("📝 **Введите текст для гиперссылки:**\n\nНапример: 'Подписаться на канал'", reply_markup=ReplyKeyboardRemove())
        await callback_query.answer()
        return True
    # text_edit_change_url
    if data == "text_edit_change_url":
        user_id = callback_query.from_user.id
        user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_TEXT_EDIT_LINK_URL, "last_msg_id": None}
        await callback_query.message.reply("🔗 **Введите URL для гиперссылки:**\n\nНапример: https://t.me/example", reply_markup=ReplyKeyboardRemove())
        await callback_query.answer()
        return True
    # text_edit_change_limit
    if data == "text_edit_change_limit":
        user_id = callback_query.from_user.id
        user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_TEXT_EDIT_LIMIT, "last_msg_id": None}
        await callback_query.message.reply("📊 **Введите максимальное количество постов для обработки:**\n\nНапример: 100", reply_markup=ReplyKeyboardRemove())
        await callback_query.answer()
        return True
    # text_edit_settings_done
    if data == "text_edit_settings_done":
        user_id = callback_query.from_user.id
        # Запускаем задачу редактирования с текущими настройками
        await start_text_editing_task(client, callback_query.message, user_id)
        await callback_query.answer()
        return True
    # text_edit_settings_back
    if data == "text_edit_settings_back":
        user_id = callback_query.from_user.id
        # Возвращаемся в главное меню редактирования текста
        await callback_query.message.reply(
            "🛠 **Редактирование текста постов**\n\n"
            "Этот режим позволяет добавлять новые гиперссылки ко всем постам в канале.\n\n"
            "Выберите действие:",
            reply_markup=get_text_edit_menu_keyboard()
        )
        user_states[user_id] = {**user_states.get(user_id, {}), "state": "text_edit_menu"}
        await callback_query.answer()
        return True
    # text_edit_start
    if data == "text_edit_start":
        user_id = callback_query.from_user.id
        await start_text_editing_task(client, callback_query.message, user_id)
        await callback_query.answer()
        return True
    # text_edit_back_to_channel
    if data == "text_edit_back_to_channel":
        user_id = callback_query.from_user.id
        kb = await get_channel_history_keyboard(user_id)
        await callback_query.message.reply(
            "📺 **Выбор канала для редактирования текста**\n\n"
            "Выберите канал из истории или введите ID/ссылку канала:",
            reply_markup=kb or ReplyKeyboardRemove()
        )
        user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_TEXT_EDIT_CHANNEL}
        await callback_query.answer()
        return True
    # text_edit_back
    if data == "text_edit_back":
        user_id = callback_query.from_user.id
        await show_main_menu(client, callback_query.message)
        await callback_query.answer()
        return True
    # check_text_edit_tasks_status
    if data == "check_text_edit_tasks_status":
        user_id = callback_query.from_user.id
        await show_text_edit_tasks_status(client, callback_query.message, user_id)
        await callback_query.answer()
        return True
    # text_edit_footer
    if data == "text_edit_footer":
        user_id = callback_query.from_user.id
        # Показываем меню настройки приписки для редактора текста
        text_edit_settings = user_states[user_id].get('text_edit_settings', {})
        current_footer = text_edit_settings.get('footer_text', '')
        footer_preview = current_footer if current_footer else "Приписка не установлена"

        # Создаем клавиатуру с опциями
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Изменить текст", callback_data="text_edit_footer_edit")],
            [InlineKeyboardButton("📋 Шаблоны", callback_data="text_edit_footer_templates")],
            [InlineKeyboardButton("🔗 Редактировать ссылки", callback_data="text_edit_footer_links")],
            [InlineKeyboardButton("🗑️ Удалить", callback_data="text_edit_footer_delete")],
            [InlineKeyboardButton("🔙 Назад", callback_data="text_edit_settings")]
        ])

        text = f"📝 Настройка приписки к сообщениям\n\n"
        text += f"Текущая приписка:\n{footer_preview}\n\n"
        text += f"Приписка добавляется к каждому редактируемому сообщению.\n\n"
        text += f"🔗 Для создания кликабельных ссылок используйте HTML:\n"
        text += f"<code>&lt;a href=\"ВАША_ССЫЛКА\"&gt;ТЕКСТ&lt;/a&gt;</code>\n\n"
        text += f"Примеры ссылок:\n"
        text += f"• <code>https://t.me/channel</code> - публичный канал\n"
        text += f"• <code>https://t.me/+invite</code> - приватный канал\n"
        text += f"• <code>https://donate.url</code> - донат"

        await safe_edit_callback_message(
            callback_query,
            text,
            reply_markup=keyboard
        )
        await callback_query.answer()
        return True
    # text_edit_footer_links
    if data == "text_edit_footer_links":
        user_id = callback_query.from_user.id
        text_edit_settings = user_states[user_id].get('text_edit_settings', {})
        footer_text = text_edit_settings.get('footer_text', '')

        if not footer_text:
            await safe_edit_callback_message(
                callback_query,
                "❌ Приписка не установлена. Сначала установите текст приписки.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="text_edit_footer")]])
            )
            await callback_query.answer()
            return True

        # Ищем ссылки в тексте
        import re
        links = re.findall(r'href="([^"]*)"', footer_text)
        text_links = re.findall(r'<a[^>]*>([^<]*)</a>', footer_text)

        if not links:
            await safe_edit_callback_message(
                callback_query,
                f"🔗 Ссылки не найдены в приписке:\n\n{footer_text}\n\nИспользуйте HTML-теги для создания ссылок.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="text_edit_footer")]])
            )
            await callback_query.answer()
            return True

        text = f"🔗 Найденные ссылки в приписке:\n\n"
        for i, (link_text, url) in enumerate(zip(text_links, links)):
            text += f"{i+1}. {link_text} → {url}\n"

        text += f"\n💡 Для редактирования используйте 'Изменить текст' и замените ссылки вручную."

        await safe_edit_callback_message(
            callback_query,
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="text_edit_footer")]])
        )
        await callback_query.answer()
        return True
    # text_edit_footer_edit
    if data == "text_edit_footer_edit":
        user_id = callback_query.from_user.id
        text_edit_settings = user_states[user_id].get('text_edit_settings', {})
        current_footer = text_edit_settings.get('footer_text', '')

        # Устанавливаем состояние для ввода footer
        user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_TEXT_EDIT_FOOTER_EDIT, "last_msg_id": None}

        await callback_query.message.reply(
            f"✏️ Введите новый текст приписки:\n\n"
            f"Текущая приписка:\n{current_footer if current_footer else 'Не установлена'}\n\n"
            f"💡 Используйте HTML для ссылок:\n"
            f"<code>&lt;a href=\"ссылка\"&gt;текст&lt;/a&gt;</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="text_edit_footer")]])
        )
        await callback_query.answer()
        return True
    # text_edit_footer_templates
    if data == "text_edit_footer_templates":
        user_id = callback_query.from_user.id
        # Показываем готовые шаблоны
        templates = [
            ('📢 Подписаться на канал', '📢 <a href="https://t.me/YOUR_CHANNEL">Подпишись на новый канал</a> 📢'),
            ('🔒 Приватный канал', '🔒 <a href="https://t.me/+YOUR_PRIVATE_LINK">Приватный канал / Подписаться</a>'),
            ('💰 Донат', '💰 <a href="https://donate.url">Поддержать автора</a>'),
        ]

        keyboard_buttons = []
        for i, (name, template) in enumerate(templates):
            keyboard_buttons.append([InlineKeyboardButton(f"{name}", callback_data=f"text_edit_footer_template_{i}")])

        keyboard_buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="text_edit_footer")])

        text = f"📋 Готовые шаблоны приписок\n\n"
        text += f"Выберите шаблон и замените:\n"
        text += f"• YOUR_CHANNEL на username вашего канала\n"
        text += f"• YOUR_PRIVATE_LINK на invite-ссылку приватного канала\n"
        text += f"• donate.url на вашу ссылку для донатов\n\n"

        for i, (name, template) in enumerate(templates):
            text += f"{i+1}. {name}\n   {template}\n\n"

        await safe_edit_callback_message(
            callback_query,
            text,
            reply_markup=InlineKeyboardMarkup(keyboard_buttons)
        )
        # Сохраняем шаблоны в состоянии для использования в обработчиках
        user_states[user_id]['text_edit_footer_templates'] = templates
        await callback_query.answer()
        return True
    # text_edit_footer_delete
    if data == "text_edit_footer_delete":
        user_id = callback_query.from_user.id
        # Удаляем приписку
        text_edit_settings = user_states[user_id].get('text_edit_settings', {})
        if 'footer_text' in text_edit_settings:
            del text_edit_settings['footer_text']
            user_states[user_id]['text_edit_settings'] = text_edit_settings

        await callback_query.answer("✅ Приписка удалена!")
        await show_text_edit_settings(client, callback_query.message, user_id)
        return True
    # text_edit_footer_template_*
    if data.startswith("text_edit_footer_template_"):
        user_id = callback_query.from_user.id
        template_index = int(data.replace("text_edit_footer_template_", ""))
        templates = user_states[user_id].get('text_edit_footer_templates', [])

        if template_index < len(templates):
            _, template_text = templates[template_index]
            # Сохраняем шаблон как footer_text
            text_edit_settings = user_states[user_id].get('text_edit_settings', {})
            text_edit_settings['footer_text'] = template_text
            user_states[user_id]['text_edit_settings'] = text_edit_settings

            await callback_query.answer("✅ Шаблон применен!")
            await show_text_edit_settings(client, callback_query.message, user_id)
        else:
            await callback_query.answer("❌ Шаблон не найден!")
        return True
    # text_edit_require_hashtags
    if data == "text_edit_require_hashtags":
        user_id = callback_query.from_user.id
        text_edit_settings = user_states[user_id].get('text_edit_settings', {})
        current_value = text_edit_settings.get('require_hashtags', False)
        text_edit_settings['require_hashtags'] = not current_value
        user_states[user_id]['text_edit_settings'] = text_edit_settings

        await callback_query.answer(f"🏷️ Требовать хэштеги: {'Да' if not current_value else 'Нет'}")
        await show_text_edit_settings(client, callback_query.message, user_id)
        return True
    # text_edit_require_text
    if data == "text_edit_require_text":
        user_id = callback_query.from_user.id
        text_edit_settings = user_states[user_id].get('text_edit_settings', {})
        current_value = text_edit_settings.get('require_specific_text', False)

        if current_value:
            # Выключаем требование текста
            text_edit_settings['require_specific_text'] = False
            user_states[user_id]['text_edit_settings'] = text_edit_settings
            await callback_query.answer("🔤 Требование текста отключено")
            await show_text_edit_settings(client, callback_query.message, user_id)
        else:
            # Включаем требование текста и просим ввести текст
            user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_TEXT_EDIT_SPECIFIC_TEXT, "last_msg_id": None}

            await callback_query.message.reply(
                "🔤 Введите текст, который должен содержаться в сообщении для редактирования:\n\n"
                "💡 Примеры:\n"
                "• #hashtag\n"
                "• определенное слово\n"
                "• _TSSH_Fans_\n"
                "• Приватный канал",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="text_edit_settings")]])
            )
            await callback_query.answer()
        return True
    # text_edit_require_old_footer
    if data == "text_edit_require_old_footer":
        user_id = callback_query.from_user.id
        text_edit_settings = user_states[user_id].get('text_edit_settings', {})
        current_value = text_edit_settings.get('require_old_footer', True)
        text_edit_settings['require_old_footer'] = not current_value
        user_states[user_id]['text_edit_settings'] = text_edit_settings

        await callback_query.answer(f"📝 Заменять старую приписку: {'Да' if not current_value else 'Нет'}")
        await show_text_edit_settings(client, callback_query.message, user_id)
        return True

    # Public groups callbacks
    if data.startswith("public_"):
        from bot.public_groups_manager import handle_public_groups_callback
        await handle_public_groups_callback(client, callback_query)
        return True

    # ... можно добавить другие кастомные обработчики ...
    # Если не обработано — fallback: вызываем forwarding_callback_handler
    await forwarding_callback_handler(client, callback_query)
    return True





# Обработка реакций перенесена в reaction_master.py

# === TEXT EDITING HELPER FUNCTIONS ===

async def start_text_editing_task(client, message, user_id):
    """Запуск задачи редактирования текста"""
    try:
        channel_id = user_states[user_id].get('text_edit_channel_id')
        # Получаем настройки из text_edit_settings
        text_edit_settings = user_states[user_id].get('text_edit_settings', {})
        footer_text = text_edit_settings.get('footer_text', '')
        limit = text_edit_settings.get('max_posts', 100)
        require_hashtags = text_edit_settings.get('require_hashtags', False)
        require_specific_text = text_edit_settings.get('require_specific_text', False)
        specific_text = text_edit_settings.get('specific_text', '')
        require_old_footer = text_edit_settings.get('require_old_footer', True)

        # Проверяем заполненность настроек
        if not footer_text.strip():
            await message.reply(
                "❌ **Невозможно запустить редактирование**\n\n"
                "Необходимо настроить приписку в настройках.\n"
                "Нажмите '⚙️ Настройки' для настройки параметров.",
                reply_markup=get_text_edit_inline_keyboard(channel_id=channel_id)
            )
            return

        # Убеждаемся, что channel_id - это число
        if not isinstance(channel_id, int):
            numeric_id = extract_numeric_id(channel_id)
            if numeric_id is None:
                await message.reply("❌ Ошибка: некорректный ID канала")
                return
            channel_id = numeric_id

        text_editor = TextEditorManager()
        result = await text_editor.start_text_editing(
            channel_id=channel_id,
            footer_text=footer_text,
            max_posts=limit,
            require_hashtags=require_hashtags,
            require_specific_text=require_specific_text,
            specific_text=specific_text,
            require_old_footer=require_old_footer
        )
        
        if result.get('status') == 'success':
            task_id = result.get('task_id')
            await message.reply(
                f"✅ **Редактирование запущено!**\n\n"
                f"📋 **ID задачи**: `{task_id}`\n\n"
                f"Процесс редактирования запущен в фоновом режиме.\n"
                f"Используйте '📊 Статус задач редактирования' для отслеживания прогресса.",
                reply_markup=get_text_edit_menu_keyboard()
            )
        else:
            await message.reply(
                f"❌ **Ошибка запуска редактирования**\n\n"
                f"{result.get('message', 'Неизвестная ошибка')}",
                reply_markup=get_text_edit_menu_keyboard()
            )
            
        # Возвращаемся в меню редактирования текста
        user_states[user_id] = {**user_states.get(user_id, {}), "state": "text_edit_menu"}
        
    except Exception as e:
        logger.error(f"Ошибка при запуске редактирования: {e}")
        await message.reply(
            f"❌ **Ошибка**: {str(e)}",
            reply_markup=get_text_edit_menu_keyboard()
        )
        user_states[user_id] = {**user_states.get(user_id, {}), "state": "text_edit_menu"}

async def show_text_edit_settings(client, message, user_id):
    """Показать меню настроек редактирования текста"""
    # Получаем текущие настройки редактирования или устанавливаем значения по умолчанию
    text_edit_settings = user_states[user_id].get('text_edit_settings', {
        'link_text': '',
        'link_url': '',
        'max_posts': 100
    })

    # Форматируем настройки для отображения
    footer_text = text_edit_settings.get('footer_text', '')
    require_hashtags = text_edit_settings.get('require_hashtags', False)
    require_specific_text = text_edit_settings.get('require_specific_text', False)
    specific_text = text_edit_settings.get('specific_text', '')
    require_old_footer = text_edit_settings.get('require_old_footer', True)

    settings_text = "⚙️ **Настройки редактирования текста:**\n\n"
    settings_text += f"📝 **Приписка:** {footer_text[:50]}{'...' if len(footer_text) > 50 else '' if footer_text else 'Не установлена'}\n"
    settings_text += f"📊 **Максимум постов:** {text_edit_settings.get('max_posts', 100)}\n"
    settings_text += f"🏷️ **Требовать хэштеги:** {'Да' if require_hashtags else 'Нет'}\n"
    settings_text += f"🔤 **Требовать текст:** {'Да' if require_specific_text else 'Нет'}\n"
    if require_specific_text and specific_text:
        settings_text += f"📄 **Текст для поиска:** {specific_text[:30]}{'...' if len(specific_text) > 30 else ''}\n"
    settings_text += f"📝 **Заменять старую приписку:** {'Да' if require_old_footer else 'Нет'}\n\n"
    settings_text += "Выберите параметр для изменения:"

    # Создаем inline клавиатуру для настроек редактирования
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📝 Приписка и ссылки", callback_data="text_edit_footer")
        ],
        [
            InlineKeyboardButton("🏷️ Хэштеги", callback_data="text_edit_require_hashtags"),
            InlineKeyboardButton("🔤 Текст", callback_data="text_edit_require_text")
        ],
        [
            InlineKeyboardButton("📝 Старая приписка", callback_data="text_edit_require_old_footer"),
            InlineKeyboardButton("📊 Лимит постов", callback_data="text_edit_change_limit")
        ],
        [
            InlineKeyboardButton("✅ Готово", callback_data="text_edit_settings_done"),
            InlineKeyboardButton("🔙 Назад", callback_data="text_edit_settings_back")
        ]
    ])

    sent = await message.reply(settings_text, reply_markup=kb)
    if sent is not None:
        user_states[user_id]['last_msg_id'] = sent.id

async def show_text_edit_tasks_status(client, message, user_id):
    """Показать статус всех задач редактирования текста"""
    try:
        text_editor = TextEditorManager()
        result = await text_editor.get_all_tasks()
        
        formatted_message = text_editor.format_all_tasks_message(result)
        
        await message.reply(
            formatted_message,
            reply_markup=get_text_edit_menu_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Ошибка при получении статуса задач: {e}")
        await message.reply(
            f"❌ **Ошибка**: {str(e)}",
            reply_markup=get_text_edit_menu_keyboard()
        )

async def show_text_edit_stop_menu(client, message, user_id):
    """Показать меню остановки задач редактирования"""
    try:
        text_editor = TextEditorManager()
        result = await text_editor.get_all_tasks()
        
        if result.get('status') == 'error':
            await message.reply(
                f"❌ **Ошибка получения задач**: {result.get('message', 'Неизвестная ошибка')}",
                reply_markup=get_text_edit_menu_keyboard()
            )
            return
            
        tasks = result.get('tasks', [])
        running_tasks = [t for t in tasks if t.get('status') == 'running']
        
        if not running_tasks:
            await message.reply(
                "📝 **Остановка задач редактирования**\n\n"
                "Нет активных задач для остановки.",
                reply_markup=get_text_edit_menu_keyboard()
            )
            return
            
        # Создаем кнопки для каждой активной задачи
        buttons = []
        for task in running_tasks:
            task_id = task.get('task_id', 'Неизвестно')
            channel_id = task.get('channel_id', 'Неизвестно')
            buttons.append([KeyboardButton(f"⏹️ {task_id} ({channel_id})")])
            
        buttons.append([KeyboardButton("🔙 Назад")])
        
        await message.reply(
            "📝 **Остановка задач редактирования**\n\n"
            "Выберите задачу для остановки:",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True)
        )
        
        # Переходим в состояние выбора задачи для остановки
        user_states[user_id] = {**user_states.get(user_id, {}), "state": "text_edit_stop_select"}
        
    except Exception as e:
        logger.error(f"Ошибка при показе меню остановки: {e}")
        await message.reply(
            f"❌ **Ошибка**: {str(e)}",
            reply_markup=get_text_edit_menu_keyboard()
        )

