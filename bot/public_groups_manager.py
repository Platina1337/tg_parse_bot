import logging
import re
from typing import Optional, Dict, List
from pyrogram import Client, enums
from pyrogram.types import Message, BotCommand, CallbackQuery
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from bot.states import user_states, FSM_MAIN_MENU
from bot.core import api_client, show_main_menu
from bot.handlers import safe_edit_message, safe_edit_callback_message, resolve_channel, resolve_group

logger = logging.getLogger(__name__)

def safe_markdown_text(text: str) -> str:
    """Безопасно экранирует текст для MarkdownV2"""
    if not text:
        return ""
    # Экранируем специальные символы MarkdownV2
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def parse_group_input(text: str) -> str:
    """Парсит ввод пользователя и извлекает username или ID группы

    Поддерживаемые форматы:
    - @username -> username
    - https://t.me/username -> username
    - t.me/username -> username
    - название (@username) -> username
    - -1001234567890 -> -1001234567890
    - username -> username
    """
    text = text.strip()

    # Проверяем формат "@username"
    if text.startswith('@'):
        return text[1:]  # Убираем @

    # Проверяем формат URL (https://t.me/username или t.me/username)
    import re
    url_match = re.search(r'(?:https?://)?t\.me/([a-zA-Z0-9_]+)', text)
    if url_match:
        return url_match.group(1)  # Извлекаем username из URL

    # Проверяем формат "название (@username)"
    match = re.search(r'\(@([^)]+)\)', text)
    if match:
        return match.group(1)  # Извлекаем username из скобок

    # Проверяем формат "название [ID: -100...]"
    match_id = re.search(r'\[ID: (-100\d+)\]', text)
    if match_id:
        return match_id.group(1)

    # Проверяем формат ID (начинается с -100...)
    if text.startswith('-100') and text[1:].isdigit():
        return text

    # Остальное считаем username
    return text

# Состояния FSM для публичных групп
FSM_PUBLIC_GROUPS_SOURCE = "public_groups_source"
FSM_PUBLIC_GROUPS_TARGET = "public_groups_target"
FSM_PUBLIC_GROUPS_ADD_TARGET = "public_groups_add_target" # Cостояние для добавления новой группы
FSM_PUBLIC_GROUPS_SESSION = "public_groups_session"  # Новое состояние для выбора сессии
FSM_PUBLIC_GROUPS_SETTINGS = "public_groups_settings"

# Новые FSM состояния для настроек публичных групп
FSM_PUBLIC_GROUPS_POSTS_COUNT = "public_groups_posts_count"
FSM_PUBLIC_GROUPS_VIEWS_LIMIT = "public_groups_views_limit"
FSM_PUBLIC_GROUPS_DELAY = "public_groups_delay"
FSM_PUBLIC_GROUPS_INCLUDE_PAID = "public_groups_include_paid"

async def start_public_groups_manager(client: Client, message: Message):
    """Запуск менеджера публичных групп"""
    user_id = message.from_user.id
    
    # Сбрасываем состояние
    user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_PUBLIC_GROUPS_SOURCE}
    
    # Показываем меню выбора исходного канала
    kb = await get_source_channel_keyboard(user_id)
    sent = await message.reply(
        "🎯 **Менеджер публичных групп**\n\n"
        "Выберите канал-источник для пересылки в публичные группы:",
        reply_markup=kb or ReplyKeyboardRemove()
    )
    
    if sent is not None:
        user_states[user_id]["last_msg_id"] = sent.id

async def handle_public_groups_text(client: Client, message: Message) -> bool:
    user_id = message.from_user.id
    text = message.text.strip()
    state = user_states.get(user_id, {}).get('state')

    # Обработка состояний публичных групп
    if state and state.startswith('public_groups_'):
        # Делегирование на обработку ввода настроек
        if state in [FSM_PUBLIC_GROUPS_POSTS_COUNT, FSM_PUBLIC_GROUPS_VIEWS_LIMIT, FSM_PUBLIC_GROUPS_DELAY, FSM_PUBLIC_GROUPS_INCLUDE_PAID]:
            return await handle_settings_input(client, message)
        if state == FSM_PUBLIC_GROUPS_SOURCE:
            return await handle_source_selection(client, message)
        elif state == FSM_PUBLIC_GROUPS_TARGET:
            # В этом состоянии мы ожидаем callback'и, а не текст
            return True
        elif state == FSM_PUBLIC_GROUPS_ADD_TARGET:
            return await handle_add_target_group_input(client, message)
        elif state == FSM_PUBLIC_GROUPS_SESSION:
            return await handle_session_selection(client, message)
        elif state == FSM_PUBLIC_GROUPS_SETTINGS:
            # В состоянии настроек игнорируем текстовый ввод
            return True
    return False

async def handle_source_selection(client: Client, message: Message) -> bool:
    """Обработка выбора исходного канала"""
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == "🔙 Назад":
        await show_main_menu(client, message, "Выберите действие:")
        return True
    
    # Парсим выбор канала из истории
    match = re.match(r"(.+) \(ID: (-?\d+)(?:, @(\w+))?\)", text)
    if match:
        channel_title = match.group(1)
        channel_id = match.group(2)
        username = match.group(3)
        
        user_states[user_id]["public_source_id"] = int(channel_id)
        user_states[user_id]["public_source_title"] = channel_title
        if username:
            user_states[user_id]["public_source_username"] = username
    else:
        # Пользователь ввел новый канал
        channel_info = await resolve_channel(api_client, text)
        
        if channel_info is None:
            sent = await message.reply("❌ Не удалось определить канал. Введите корректный username или ID.")
            if sent is not None:
                user_states[user_id]["last_msg_id"] = sent.id
            return True
        
        channel_id = channel_info["id"]
        channel_title = channel_info["title"]
        channel_username = channel_info.get("username", "")
        
        user_states[user_id]["public_source_id"] = int(channel_id)
        user_states[user_id]["public_source_title"] = channel_title
        if channel_username:
            user_states[user_id]["public_source_username"] = channel_username
    
    # Переходим к выбору публичных групп
    user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_TARGET
    await show_target_groups_management(client, message, user_id)
    return True

async def handle_add_target_group_input(client: Client, message: Message) -> bool:
    """Обработка ввода новой группы для добавления в список."""
    user_id = message.from_user.id
    text = message.text.strip()

    if text == "🔙 Назад":
        user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_TARGET
        # При возврате нужно убрать ReplyKeyboard и показать Inline-клавиатуру
        await message.reply("Отмена добавления.", reply_markup=ReplyKeyboardRemove())
        await show_target_groups_management(client, message, user_id)
        return True

    # Парсим ввод пользователя для извлечения username или ID
    parsed_input = parse_group_input(text)
    logger.info(f"[PUBLIC_GROUPS] Парсинг ввода для добавления: '{text}' -> '{parsed_input}'")

    # Разрешаем группу (получаем ID, title, username)
    group_info = await resolve_group(api_client, parsed_input)

    if group_info is None:
        error_msg = "❌ Не удалось найти группу. Проверьте корректность username или ID."
        await message.reply(error_msg)
        return True

    group_id = str(group_info["id"])
    group_title = group_info["title"]
    username = group_info.get("username", "")

    # Добавляем группу в `public_target_groups`
    target_groups = user_states[user_id].get("public_target_groups", [])
    if any(g['id'] == group_id for g in target_groups):
        await message.reply("Эта группа уже в списке.")
    else:
        target_groups.append({"id": group_id, "title": group_title, "username": username})
        user_states[user_id]["public_target_groups"] = target_groups
        await message.reply(f"✅ Группа '{group_title}' добавлена.", reply_markup=ReplyKeyboardRemove())

    # Возвращаемся к управлению списком
    user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_TARGET
    await show_target_groups_management(client, message, user_id)
    return True


async def handle_target_selection(client: Client, message: Message) -> bool:
    """Обработка выбора групп"""
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == "🔙 Назад":
        kb = await get_source_channel_keyboard(user_id)
        sent = await message.reply(
            "Выберите канал-источник:",
            reply_markup=kb or ReplyKeyboardRemove()
        )
        if sent is not None:
            user_states[user_id]["last_msg_id"] = sent.id
        user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_SOURCE
        return True
    
    # Этот обработчик больше не нужен в старом виде, так как выбор идет через callback'и
    # Но оставим логику добавления новой группы, если пользователь вводит текст
    await message.reply("Для управления списком групп используйте кнопки.")
    return True


async def handle_session_selection(client: Client, message: Message) -> bool:
    """Обработка выбора сессии"""
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == "🔙 Назад":
        # Возвращаемся к выбору группы
        user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_TARGET
        await show_target_groups_management(client, message, user_id)
        return True
    
    if text == "⏭️ Пропустить":
        # Пропускаем выбор сессии и используем автоматический выбор
        user_states[user_id].pop("public_session_name", None)
        await message.reply("✅ Будет использована первая доступная сессия из назначенных для публичных групп.")
        await show_public_groups_settings(client, message, user_id)
        return True
    
    # Парсим выбор сессии
    # Формат: "session_name | phone | 🟢 | created_at"
    parts = text.split("|")
    if parts:
        session_name = parts[0].strip()
        user_states[user_id]["public_session_name"] = session_name
        await message.reply(f"✅ Выбрана сессия: {session_name}")
        await show_public_groups_settings(client, message, user_id)
        return True
    
    await message.reply("❌ Неверный формат. Выберите сессию из списка или нажмите 'Пропустить'.")
    return True

async def show_session_selection(client: Client, message: Message, user_id: int):
    """Показать выбор сессий"""
    try:
        # Получаем список сессий
        response = await api_client.list_sessions()
        
        if not response.get("success", False):
            await message.reply(f"❌ Ошибка получения списка сессий: {response.get('error', 'Неизвестная ошибка')}")
            # Переходим к настройкам без выбора сессии
            await show_public_groups_settings(client, message, user_id)
            return
        
        sessions = response.get("sessions", [])
        assignments = response.get("assignments", {})
        public_groups_sessions = assignments.get("public_groups", [])
        
        if not sessions:
            await message.reply(
                "⚠️ Нет доступных сессий.\n\n"
                "Создайте сессию через команду /sessions и назначьте её на задачу 'Public Groups'."
            )
            # Переходим к настройкам без выбора сессии
            await show_public_groups_settings(client, message, user_id)
            return
        
        # Создаем клавиатуру с сессиями
        keyboard_buttons = []
        
        # Если есть назначенные сессии, показываем их первыми
        if public_groups_sessions:
            for session in sessions:
                alias = session.get("alias", "")
                if alias in public_groups_sessions or str(alias) in [str(s) if not isinstance(s, str) else s for s in public_groups_sessions]:
                    phone = session.get("phone", "")
                    is_active = session.get("is_active", False)
                    status_emoji = "🟢" if is_active else "🔴"
                    button_text = f"{alias} | {phone} | {status_emoji}"
                    keyboard_buttons.append([button_text])
        
        # Показываем остальные сессии
        for session in sessions:
            alias = session.get("alias", "")
            if alias not in public_groups_sessions and str(alias) not in [str(s) if not isinstance(s, str) else s for s in public_groups_sessions]:
                phone = session.get("phone", "")
                is_active = session.get("is_active", False)
                status_emoji = "🟢" if is_active else "🔴"
                button_text = f"{alias} | {phone} | {status_emoji}"
                keyboard_buttons.append([button_text])
        
        keyboard_buttons.append(["⏭️ Пропустить"])
        keyboard_buttons.append(["🔙 Назад"])
        
        keyboard = ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)
        
        # Формируем текст сообщения
        text = "📱 <b>Выберите сессию для пересылки</b>\n\n"
        
        if public_groups_sessions:
            text += "✅ <b>Назначенные сессии для публичных групп:</b>\n"
            for i, session in enumerate(sessions, 1):
                alias = session.get("alias", "")
                if alias in public_groups_sessions or str(alias) in [str(s) if not isinstance(s, str) else s for s in public_groups_sessions]:
                    phone = session.get("phone", "")
                    is_active = session.get("is_active", False)
                    status_emoji = "🟢" if is_active else "🔴"
                    text += f"  • <b>{alias}</b> | <code>{phone}</code> | {status_emoji}\n"
            text += "\n"
        
        text += "📋 <b>Все доступные сессии:</b>\n"
        for i, session in enumerate(sessions, 1):
            alias = session.get("alias", "")
            phone = session.get("phone", "")
            is_active = session.get("is_active", False)
            status_emoji = "🟢" if is_active else "🔴"
            text += f"{i}. <b>{alias}</b> | <code>{phone}</code> | {status_emoji}\n"
        
        text += "\n💡 <i>Выберите сессию из списка или нажмите 'Пропустить' для автоматического выбора</i>"
        
        sent = await message.reply(text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
        if sent is not None:
            user_states[user_id]["last_msg_id"] = sent.id
        user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_SESSION
        
    except Exception as e:
        logger.error(f"[PUBLIC_GROUPS] Ошибка при получении списка сессий: {e}")
        await message.reply(f"❌ Ошибка: {str(e)}\n\nПереходим к настройкам...")
        # Переходим к настройкам без выбора сессии
        await show_public_groups_settings(client, message, user_id)

async def handle_settings_input(client: Client, message: Message) -> bool:
    user_id = message.from_user.id
    text = message.text.strip()
    state = user_states.get(user_id, {}).get('state')

    if 'public_settings' not in user_states[user_id]:
        user_states[user_id]['public_settings'] = {}
    settings = user_states[user_id]['public_settings']

    if state == FSM_PUBLIC_GROUPS_POSTS_COUNT:
        try:
            count = int(text)
            if count <= 0:
                try:
                    await message.reply("Введите положительное число!")
                except Exception as e:
                    logger.error(f"[PUBLIC_GROUPS] Ошибка отправки сообщения: {e}")
                return True
            settings['posts_count'] = count
            try:
                await message.reply(f"✅ Количество последних постов для анализа установлено: {count}")
            except Exception as e:
                logger.error(f"[PUBLIC_GROUPS] Ошибка отправки сообщения: {e}")
        except Exception:
            try:
                await message.reply("Введите число!")
            except Exception as e:
                logger.error(f"[PUBLIC_GROUPS] Ошибка отправки сообщения: {e}")
            return True
        user_states[user_id]['state'] = FSM_PUBLIC_GROUPS_SETTINGS
        await show_public_groups_settings(client, message, user_id)
        return True
    elif state == FSM_PUBLIC_GROUPS_VIEWS_LIMIT:
        try:
            limit = int(text)
            if limit <= 0:
                try:
                    await message.reply("Введите положительное число!")
                except Exception as e:
                    logger.error(f"[PUBLIC_GROUPS] Ошибка отправки сообщения: {e}")
                return True
            settings['views_limit'] = limit
            try:
                await message.reply(f"✅ Лимит просмотров установлен: {limit}")
            except Exception as e:
                logger.error(f"[PUBLIC_GROUPS] Ошибка отправки сообщения: {e}")
        except Exception:
            try:
                await message.reply("Введите число!")
            except Exception as e:
                logger.error(f"[PUBLIC_GROUPS] Ошибка отправки сообщения: {e}")
            return True
        user_states[user_id]['state'] = FSM_PUBLIC_GROUPS_SETTINGS
        await show_public_groups_settings(client, message, user_id)
        return True
    elif state == FSM_PUBLIC_GROUPS_DELAY:
        try:
            delay = int(text)
            if delay < 0:
                try:
                    await message.reply("Введите неотрицательное число!")
                except Exception as e:
                    logger.error(f"[PUBLIC_GROUPS] Ошибка отправки сообщения: {e}")
                return True
            settings['delay_seconds'] = delay
            try:
                await message.reply(f"✅ Задержка между пересылками установлена: {delay} сек")
            except Exception as e:
                logger.error(f"[PUBLIC_GROUPS] Ошибка отправки сообщения: {e}")
        except Exception:
            try:
                await message.reply("Введите число!")
            except Exception as e:
                logger.error(f"[PUBLIC_GROUPS] Ошибка отправки сообщения: {e}")
            return True
        user_states[user_id]['state'] = FSM_PUBLIC_GROUPS_SETTINGS
        await show_public_groups_settings(client, message, user_id)
        return True
    return False

async def show_target_groups_management(client: Client, message_or_query, user_id: int):
    """Показать управление выбранными целевыми группами"""
    logger.info(f"[TARGET_GROUPS] >>> ENTERING show_target_groups_management for user {user_id}")
    
    is_callback = isinstance(message_or_query, CallbackQuery)
    message = message_or_query.message if is_callback else message_or_query

    user_state = user_states.get(user_id, {})
    source_channel = user_state.get('public_source_title', 'Не выбран')
    target_groups = user_state.get('public_target_groups', [])
    
    text = f"📤 Источник: {source_channel}\n\n"
    if not target_groups:
        text += "❌ Не выбрано ни одной целевой группы."
    else:
        text += "📢 Выбранные группы для пересылки:"
        for i, group in enumerate(target_groups, 1):
            title = group.get('title', group['id'])
            username = group.get('username', '')
            if username:
                title += f" (@{username})"
            text += f"\n{i}. {title}"

    buttons = [
        [InlineKeyboardButton("➕ Добавить группу", callback_data="public_add_target_group")],
        [InlineKeyboardButton("💾 Готово", callback_data="public_target_selection_done")],
        [InlineKeyboardButton("🔙 Назад к выбору источника", callback_data="public_back_to_source")]
    ]
    
    # Кнопки для удаления групп
    if target_groups:
        remove_buttons = []
        for i, group in enumerate(target_groups):
            title = group.get('title', group['id'])
            if len(title) > 20:
                title = title[:17] + "..."
            remove_buttons.append(InlineKeyboardButton(f"❌ {i+1}. {title}", callback_data=f"public_remove_target_group:{i}"))
        
        # Добавляем кнопки удаления в раскладку
        for i in range(0, len(remove_buttons), 2):
            buttons.insert(1, remove_buttons[i:i+2])

    keyboard = InlineKeyboardMarkup(buttons)
    
    if is_callback:
        await safe_edit_callback_message(message_or_query, text, keyboard)
    else:
        sent = await message.reply(text, reply_markup=keyboard)
        if sent:
            user_states[user_id]['last_msg_id'] = sent.id
            
    user_states[user_id]['state'] = FSM_PUBLIC_GROUPS_TARGET
    logger.info(f"[TARGET_GROUPS] <<< EXITING show_target_groups_management for user {user_id}")

async def show_public_groups_selection(client: Client, message: Message):
    """Показать выбор групп (старая версия, заменяется на show_target_groups_management)"""
    user_id = message.from_user.id
    user_states[user_id]['public_target_groups'] = [] # Инициализируем список выбранных групп
    await show_target_groups_management(client, message, user_id)


async def show_public_groups_settings(client, message_or_callback, user_id):
    """Показать настройки для публичных групп"""
    logger.info(f"[PUBLIC_GROUPS_SETTINGS] show_public_groups_settings called for user_id={user_id}")

    if user_id not in user_states:
        user_states[user_id] = {}
    user = user_states.get(user_id, {})
    source_title = user.get("public_source_title", "Неизвестно")
    target_groups = user.get("public_target_groups", [])
    target_names = [g.get('title', g.get('id', 'Неизвестно')) for g in target_groups]
    target_name = ", ".join(target_names) if target_names else "Не выбраны"
    session_name = user.get("public_session_name", "Автовыбор")
    settings = user.get('public_settings', {})
    posts_count = settings.get('posts_count', 20)
    views_limit = settings.get('views_limit', 50)
    delay_seconds = settings.get('delay_seconds', 0)
    one_from_group = settings.get('forward_one_from_group', False)
    include_paid_posts = settings.get('include_paid_posts', True)

    logger.info(f"[PUBLIC_GROUPS_SETTINGS] user_state keys: {list(user.keys())}")
    logger.info(f"[PUBLIC_GROUPS_SETTINGS] settings: {settings}")

    kb = get_public_groups_settings_keyboard(user_id)
    logger.info(f"[PUBLIC_GROUPS_SETTINGS] keyboard created: {kb is not None}")

    # Формируем текст с HTML форматированием
    text = f"""⚙️ <b>Настройки пересылки в публичные группы</b>

📤 Источник: {source_title}
📢 Цели: {target_name}
📱 Сессия: {session_name}

🔢 Кол-во последних постов: {posts_count}
👁️ Лимит просмотров: {views_limit}
⏱️ Задержка: {delay_seconds} сек
📷 Только одно из медиагруппы: {'ВКЛ' if one_from_group else 'ВЫКЛ'}
💰 Включать платные посты: {'ВКЛ' if include_paid_posts else 'ВЫКЛ'}

Выберите настройки:"""

    logger.info(f"[PUBLIC_GROUPS_SETTINGS] text length: {len(text)}")

    # Если это callback_query, используем edit_text, иначе reply
    if isinstance(message_or_callback, CallbackQuery):
        logger.info(f"[PUBLIC_GROUPS_SETTINGS] Editing callback message")
        try:
            await message_or_callback.edit_message_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            logger.info(f"[PUBLIC_GROUPS_SETTINGS] Successfully edited callback message")
        except Exception as e:
            logger.error(f"[PUBLIC_GROUPS] Ошибка edit_text с html: {e}")
            # Fallback: отправляем без форматирования
            try:
                text_plain = text.replace('<b>', '').replace('</b>', '')
                await message_or_callback.edit_message_text(text_plain, reply_markup=kb)
                logger.info(f"[PUBLIC_GROUPS_SETTINGS] Successfully edited with plain text fallback")
            except Exception as e2:
                logger.error(f"[PUBLIC_GROUPS_SETTINGS] Plain text fallback failed: {e2}")
                # Последний fallback: отправляем новое сообщение
                try:
                    sent = await message_or_callback.message.reply(text_plain, reply_markup=kb)
                    if sent is not None:
                        user_states[user_id]["last_msg_id"] = sent.id
                    logger.info(f"[PUBLIC_GROUPS_SETTINGS] New message fallback sent")
                except Exception as e3:
                    logger.error(f"[PUBLIC_GROUPS_SETTINGS] All fallbacks failed: {e3}")
    else:
        # Это обычное message
        logger.info(f"[PUBLIC_GROUPS_SETTINGS] Replying to message")
        try:
            sent = await message_or_callback.reply(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            if sent is not None:
                user_states[user_id]["last_msg_id"] = sent.id
            logger.info(f"[PUBLIC_GROUPS_SETTINGS] Successfully replied with settings")
        except Exception as e:
            logger.error(f"[PUBLIC_GROUPS] Ошибка reply с html: {e}")
            # Fallback: отправляем без форматирования
            try:
                text_plain = text.replace('<b>', '').replace('</b>', '')
                sent = await message_or_callback.reply(text_plain, reply_markup=kb)
                if sent is not None:
                    user_states[user_id]["last_msg_id"] = sent.id
                logger.info(f"[PUBLIC_GROUPS_SETTINGS] Successfully replied with plain text fallback")
            except Exception as e2:
                logger.error(f"[PUBLIC_GROUPS_SETTINGS] Fallback also failed: {e2}")
    user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_SETTINGS

async def get_source_channel_keyboard(user_id: int) -> Optional[ReplyKeyboardMarkup]:
    """Получить клавиатуру с историей каналов-источников"""
    channels = await api_client.get_user_channels(user_id)
    
    if not channels:
        return None
    
    buttons = []
    for ch in channels:
        title = ch.get('title', '')
        channel_id = ch.get('id', '')
        username = ch.get('username', '')
        if username:
            btn_text = f"{title} (ID: {channel_id}, @{username})"
        else:
            btn_text = f"{title} (ID: {channel_id})"
        buttons.append([KeyboardButton(btn_text)])
    
    buttons.append([KeyboardButton("🔙 Назад")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)



def get_public_groups_settings_keyboard(user_id) -> InlineKeyboardMarkup:
    logger.info(f"[PUBLIC_GROUPS_KEYBOARD] Creating keyboard for user_id={user_id}")
    settings = user_states[user_id].get('public_settings', {})
    one_from_group = settings.get('forward_one_from_group', False)
    logger.info(f"[PUBLIC_GROUPS_KEYBOARD] settings={settings}, one_from_group={one_from_group}")

    # Создаем кнопки по отдельности для лучшего контроля
    session_button = InlineKeyboardButton("📱 Изменить сессию", callback_data="public_change_session")
    posts_button = InlineKeyboardButton(f"🔢 Кол-во последних постов", callback_data="public_posts_count")
    views_button = InlineKeyboardButton(f"👁️ Лимит просмотров", callback_data="public_views_limit")
    delay_button = InlineKeyboardButton(f"⏱️ Задержка", callback_data="public_delay")
    toggle_button = InlineKeyboardButton(f"📷 Только одно из медиагруппы: {'ВКЛ' if one_from_group else 'ВЫКЛ'}", callback_data="public_one_from_group_toggle")
    paid_posts_button = InlineKeyboardButton(f"💰 Включать платные посты: {'ВКЛ' if settings.get('include_paid_posts', True) else 'ВЫКЛ'}", callback_data="public_include_paid_toggle")
    start_button = InlineKeyboardButton("▶️ Запустить", callback_data="public_start")
    stop_button = InlineKeyboardButton("⏹️ Остановить", callback_data="public_stop")
    back_button = InlineKeyboardButton("🔙 Назад", callback_data="public_back")

    logger.info(f"[PUBLIC_GROUPS_KEYBOARD] Created buttons with callback_data: posts={posts_button.callback_data}, views={views_button.callback_data}, etc.")

    keyboard = InlineKeyboardMarkup([
        [session_button],
        [posts_button, views_button],
        [delay_button],
        [toggle_button],
        [paid_posts_button],
        [start_button, stop_button],
        [back_button]
    ])

    logger.info(f"[PUBLIC_GROUPS_KEYBOARD] Keyboard created with {len(keyboard.inline_keyboard)} rows")
    return keyboard



async def handle_public_groups_callback(client: Client, callback_query) -> bool:
    """Обработчик callback для менеджера публичных групп"""
    data = callback_query.data
    user_id = callback_query.from_user.id

    logger.info(f"[PUBLIC_GROUPS_CALLBACK] Получен callback: data='{data}', user_id={user_id}")

    if not data.startswith('public_'):
        logger.warning(f"[PUBLIC_GROUPS_CALLBACK] Callback не для публичных групп: {data}")
        return False

    try:
        if data == "public_stats":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_stats для user_id={user_id}")
            await callback_query.answer()
            await show_public_stats(client, callback_query)
        elif data == "public_settings":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_settings для user_id={user_id}")
            await callback_query.answer()
            await show_public_settings(client, callback_query)
        elif data == "public_start":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_start для user_id={user_id}")
            await callback_query.answer()
            await start_public_forwarding(client, callback_query)
        elif data == "public_stop":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_stop для user_id={user_id}")
            await callback_query.answer()
            await stop_public_forwarding(client, callback_query)
        elif data == "public_back":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_back для user_id={user_id}")
            await callback_query.answer()
            # Вернуться к управлению списком групп
            user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_TARGET
            await show_target_groups_management(client, callback_query, user_id)
            return True
        elif data == "public_back_to_source":
             logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_back_to_source для user_id={user_id}")
             await callback_query.answer()
             user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_SOURCE
             kb = await get_source_channel_keyboard(user_id)
             await safe_edit_callback_message(callback_query, "Выберите канал-источник:", kb)
             return True
        elif data.startswith("public_remove_target_group:"):
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка {data} для user_id={user_id}")
            index = int(data.split(":")[1])
            target_groups = user_states[user_id].get("public_target_groups", [])
            if 0 <= index < len(target_groups):
                removed_group = target_groups.pop(index)
                await callback_query.answer(f"Группа '{removed_group['title']}' удалена")
                user_states[user_id]["public_target_groups"] = target_groups
            await show_target_groups_management(client, callback_query, user_id)
            return True
        elif data == "public_add_target_group":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_add_target_group для user_id={user_id}")
            user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_ADD_TARGET
            await callback_query.answer()

            # --- NEW LOGIC ---
            # 1. Fetch user's saved groups
            groups = await api_client.get_user_groups(user_id)
            
            # 2. Build ReplyKeyboardMarkup
            keyboard_buttons = []
            if groups:
                for group in groups:
                    group_title = group.get('group_title', 'Без названия')
                    username = group.get('username', '')
                    if username:
                        button_text = f"{group_title} (@{username})"
                    else:
                        group_id = group.get('group_id', '')
                        button_text = f"{group_title} [ID: {group_id}]"
                    keyboard_buttons.append([button_text])
            
            keyboard_buttons.append([KeyboardButton("🔙 Назад")])
            reply_kb = ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True, one_time_keyboard=True)

            # 3. Send a new message, edit the old one to remove keyboard
            await callback_query.message.edit_reply_markup(reply_markup=None)
            
            sent = await callback_query.message.reply(
                "Выберите группу из истории или введите новую (ID/@username):",
                reply_markup=reply_kb
            )
            if sent:
                user_states[user_id]["last_msg_id"] = sent.id
            # --- END NEW LOGIC ---
            return True
        elif data == "public_back_to_target_list":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_back_to_target_list для user_id={user_id}")
            user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_TARGET
            await callback_query.answer()
            await show_target_groups_management(client, callback_query, user_id)
            return True
        elif data == "public_target_selection_done":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_target_selection_done для user_id={user_id}")
            target_groups = user_states[user_id].get("public_target_groups", [])
            if not target_groups:
                await callback_query.answer("❌ Вы не выбрали ни одной группы.", show_alert=True)
                return True
            await callback_query.answer()
            await show_session_selection(client, callback_query.message, user_id)
            return True
        elif data == "public_posts_count":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_posts_count для user_id={user_id}")
            await callback_query.answer()
            user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_POSTS_COUNT
            try:
                cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="public_settings_cancel")]])
                await callback_query.message.reply(
                    "📊 Введите количество последних постов для анализа (например, 20):\n\n"
                    "💡 Примечание: Медиагруппы считаются как один пост",
                    reply_markup=cancel_kb
                )
            except Exception as e:
                logger.error(f"[PUBLIC_GROUPS] Ошибка отправки запроса количества постов: {e}")
        elif data == "public_views_limit":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_views_limit для user_id={user_id}")
            await callback_query.answer()
            user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_VIEWS_LIMIT
            try:
                cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="public_settings_cancel")]])
                await callback_query.message.reply(
                    "👁️ Введите лимит просмотров (например, 50):",
                    reply_markup=cancel_kb
                )
            except Exception as e:
                logger.error(f"[PUBLIC_GROUPS] Ошибка отправки запроса лимита просмотров: {e}")
        elif data == "public_delay":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_delay для user_id={user_id}")
            await callback_query.answer()
            user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_DELAY
            try:
                cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="public_settings_cancel")]])
                await callback_query.message.reply(
                    "⏱️ Введите задержку между пересылками в секундах (например, 60):",
                    reply_markup=cancel_kb
                )
            except Exception as e:
                logger.error(f"[PUBLIC_GROUPS] Ошибка отправки запроса задержки: {e}")
        elif data == "public_one_from_group_toggle":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_one_from_group_toggle для user_id={user_id}")
            await callback_query.answer()
            if user_id not in user_states:
                user_states[user_id] = {}
            settings = user_states[user_id].setdefault('public_settings', {})
            settings['forward_one_from_group'] = not settings.get('forward_one_from_group', False)
            # Обновляем клавиатуру
            kb = get_public_groups_settings_keyboard(user_id)
            await callback_query.message.edit_reply_markup(reply_markup=kb)
        elif data == "public_include_paid_toggle":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_include_paid_toggle для user_id={user_id}")
            await callback_query.answer()
            if user_id not in user_states:
                user_states[user_id] = {}
            settings = user_states[user_id].setdefault('public_settings', {})
            settings['include_paid_posts'] = not settings.get('include_paid_posts', True)
            # Обновляем клавиатуру
            kb = get_public_groups_settings_keyboard(user_id)
            await callback_query.message.edit_reply_markup(reply_markup=kb)
            if "public_source_title" not in user_states[user_id]:
                user_states[user_id]["public_source_title"] = "Неизвестно"
            if "public_target_name" not in user_states[user_id]:
                user_states[user_id]["public_target_name"] = "Неизвестно"
            print(f"[DEBUG] TOGGLE one_from_group user_id={user_id}, settings={user_states[user_id]['public_settings']}")
            await show_public_groups_settings(client, callback_query, user_id)  # Передаем callback_query для редактирования
            await callback_query.answer(f"Только одно из медиагруппы: {'ВКЛ' if settings['forward_one_from_group'] else 'ВЫКЛ'}")
            return True
        elif data == "public_change_session":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_change_session для user_id={user_id}")
            await callback_query.answer()
            # Переходим к выбору сессии
            await show_session_selection(client, callback_query.message, user_id)
            return True
        elif data == "public_settings_cancel":
            logger.info(f"[PUBLIC_GROUPS_CALLBACK] Обработка public_settings_cancel для user_id={user_id}")
            # Возвращаем пользователя в меню настроек
            user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_SETTINGS
            await callback_query.answer("❌ Отменено")
            await show_public_groups_settings(client, callback_query.message, user_id)
            return True
        else:
            logger.warning(f"[PUBLIC_GROUPS_CALLBACK] Неизвестный callback: {data}")

        logger.info(f"[PUBLIC_GROUPS_CALLBACK] Callback обработан успешно: {data}")
        return True

    except Exception as e:
        logger.error(f"[PUBLIC_GROUPS_CALLBACK] Ошибка обработки callback {data}: {e}")
        try:
            await callback_query.answer("Произошла ошибка при обработке запроса")
        except:
            pass
        return True

async def show_public_stats(client: Client, callback_query):
    """Показать статистику публичных групп"""
    user_id = callback_query.from_user.id
    
    try:
        # Получаем все задачи публичных групп
        result = await api_client.get_all_public_groups_tasks()
        
        if result.get("status") == "success":
            tasks = result.get("tasks", [])
            user_tasks = [task for task in tasks if task.get("user_id") == user_id]
            
            if user_tasks:
                total_forwarded = sum(task.get("forwarded_count", 0) for task in user_tasks)
                active_tasks = [task for task in user_tasks if task.get("status") == "running"]
                
                text = f"📊 *Статистика пересылки в публичные группы*\n\n"
                text += f"📤 Всего переслано: {total_forwarded}\n"
                text += f"🔄 Активных задач: {len(active_tasks)}\n"
                text += f"📋 Всего задач: {len(user_tasks)}\n\n"
                
                # Показываем детали активных задач
                if active_tasks:
                    text += "🔴 *Активные задачи:*\n\n"
                    for idx, task in enumerate(active_tasks, 1):
                        settings = task.get("settings", {})
                        text += f"{idx}. 📤 `{task.get('source_channel', 'N/A')}` → 📢 `{task.get('target_group', 'N/A')}`\n"
                        text += f"   📊 Переслано: {task.get('forwarded_count', 0)}\n"
                        text += f"   ⚙️ Настройки: {settings.get('posts_count', 20)} постов, лимит {settings.get('views_limit', 50)} просмотров\n"
                        text += f"   🆔 ID: `{task.get('task_id', 'N/A')}`\n\n"
            else:
                text = "📊 *Статистика пересылки в публичные группы*\n\n"
                text += "📤 Переслано: 0\n"
                text += "🔄 Активных задач: 0\n"
                text += "📋 Всего задач: 0\n"
        else:
            text = f"❌ Ошибка получения статистики: {result.get('message', 'Неизвестная ошибка')}"
        
        await safe_edit_callback_message(callback_query, text)
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики публичных групп: {e}")
        await safe_edit_callback_message(callback_query, f"❌ Ошибка: {str(e)}")

async def show_public_settings(client: Client, callback_query):
    """Показать настройки публичных групп"""
    user_id = callback_query.from_user.id
    
    text = """⚙️ *Настройки пересылки в публичные группы*

🔍 Режим: Все сообщения
⏱️ Задержка: 0 сек
📝 Приписка: Нет
"""
    
    await safe_edit_callback_message(callback_query, text)

async def start_public_forwarding(client: Client, callback_query):
    """Запустить пересылку в публичные группы"""
    user_id = callback_query.from_user.id
    
    source_id = user_states[user_id].get("public_source_id")
    target_groups = user_states[user_id].get("public_target_groups", [])
    
    if not source_id or not target_groups:
        await safe_edit_callback_message(callback_query, "❌ Не выбраны источник или целевые группы")
        return
    
    target_ids = [g['id'] for g in target_groups]

    try:
        # Получаем настройки пользователя
        settings = user_states[user_id].get('public_settings', {})
        posts_count = settings.get('posts_count', 20)
        views_limit = settings.get('views_limit', 50)
        delay_seconds = settings.get('delay_seconds', 0)
        forward_one_from_group = settings.get('forward_one_from_group', False)
        include_paid_posts = settings.get('include_paid_posts', True)
        
        # Получаем выбранную сессию
        session_name = user_states[user_id].get('public_session_name')
        
        # Собираем настройки для API
        api_settings = {
            "posts_count": posts_count,
            "views_limit": views_limit,
            "delay_seconds": delay_seconds,
            "media_filter": "all",
            "footer_text": "",
            "forward_one_from_group": forward_one_from_group,
            "include_paid_posts": include_paid_posts
        }
        
        # Добавляем session_name, если он выбран
        if session_name:
            api_settings["session_name"] = session_name
        # Запускаем пересылку через API
        result = await api_client.start_public_groups_forwarding(
            str(source_id),
            target_ids,
            user_id,
            api_settings
        )
        
        if result.get("status") == "success":
            task_id = result.get("task_id")
            user_states[user_id]["public_task_id"] = task_id
            
            target_names = ", ".join([g['title'] for g in target_groups])
            text = f"✅ Пересылка запущена!\n\n📤 Источник: {user_states[user_id].get('public_source_title')}\n📢 Цели: {target_names}\n🆔 Задача: {task_id}"
            
            # Добавляем кнопки для управления задачей
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("⏹️ Остановить", callback_data="public_stop")],
                [InlineKeyboardButton("🔙 Назад", callback_data="public_back")]
            ])
            
            try:
                await callback_query.edit_message_text(text, reply_markup=keyboard)
            except Exception as e:
                logger.error(f"Ошибка редактирования сообщения: {e}")
                await callback_query.message.reply(text, reply_markup=keyboard)
        else:
            text = f"❌ Ошибка запуска: {result.get('message', 'Неизвестная ошибка')}"
            await safe_edit_callback_message(callback_query, text)
        
    except Exception as e:
        logger.error(f"Ошибка запуска пересылки в публичные группы: {e}")
        await safe_edit_callback_message(callback_query, f"❌ Ошибка: {str(e)}")

async def stop_public_forwarding(client: Client, callback_query):
    """Остановить пересылку в публичные группы"""
    user_id = callback_query.from_user.id
    
    task_id = user_states[user_id].get("public_task_id")
    
    logger.info(f"[PUBLIC_GROUPS] Попытка остановить задачу для user_id={user_id}, task_id={task_id}")
    
    if not task_id:
        await safe_edit_callback_message(callback_query, "❌ Нет активной задачи для остановки")
        return
    
    try:
        # Останавливаем пересылку через API
        logger.info(f"[PUBLIC_GROUPS] Отправка запроса на остановку задачи {task_id}")
        result = await api_client.stop_public_groups_forwarding(task_id)
        logger.info(f"[PUBLIC_GROUPS] Результат остановки: {result}")
        
        if result.get("status") == "success":
            text = "⏹️ Пересылка остановлена"
            # Очищаем task_id из состояния
            user_states[user_id].pop("public_task_id", None)
        else:
            text = f"❌ Ошибка остановки: {result.get('message', 'Неизвестная ошибка')}"
        
        await safe_edit_callback_message(callback_query, text)
        
    except Exception as e:
        logger.error(f"Ошибка остановки пересылки в публичные группы: {e}")
        await safe_edit_callback_message(callback_query, f"❌ Ошибка: {str(e)}")

async def go_back_to_public_groups(client: Client, callback_query):
    """Вернуться к выбору публичных групп"""
    user_id = callback_query.from_user.id
    user_states[user_id]['state'] = FSM_PUBLIC_GROUPS_TARGET
    await show_target_groups_management(client, callback_query.message, user_id)

 