import logging
import re
import asyncio
from pyrogram import Client
from pyrogram.types import Message, CallbackQuery, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from bot.states import (
    user_states, FSM_REACTION_CHANNEL, FSM_REACTION_SETTINGS, FSM_REACTION_CONFIRM, FSM_MAIN_MENU,
    get_reaction_settings_keyboard, get_reaction_inline_keyboard,
    get_unique_channels_keyboard
)
from bot.api_client import api_client

# --- FSM: Мастер массовых реакций ---
async def start_reaction_master(client: Client, message: Message):
    user_id = message.from_user.id
    user_states[user_id] = user_states.get(user_id, {})
    user_states[user_id]["state"] = FSM_REACTION_CHANNEL
    # По умолчанию выставляем эмодзи и задержку
    user_states[user_id]["reaction_settings"] = {
        "emojis": ["😍", "❤️"],
        "delay": 1
    }
    kb = await get_unique_channels_keyboard(user_id)  # используем новую клавиатуру
    sent = await message.reply("Введите ID или username канала для массовых реакций на посты:", reply_markup=kb or ReplyKeyboardRemove())
    user_states[user_id]["last_msg_id"] = sent.id if sent else None

async def process_reaction_fsm(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    state = user_states[user_id].get("state")
    logging.info(f"[FSM][REACTION] process_reaction_fsm called for user {user_id}, state={state}, text={text!r}")
    if state == FSM_REACTION_CHANNEL:
        # --- Добавлено: поддержка формата 'Название (ID: -100..., @username)' ---
        match = re.match(r"(.+) \(ID: (-?\d+)(?:, @(\w+))?\)", text)
        if match:
            channel_id = match.group(2)
            channel_title = match.group(1)
            channel_username = match.group(3)
            logging.info(f"[FSM][REACTION] parsed from button: channel_id={channel_id}, channel_title={channel_title}, username={channel_username}")
        else:
            channel_id, channel_title, channel_username = await resolve_channel(api_client, text)
            logging.info(f"[FSM][REACTION] resolved: channel_id={channel_id!r}, channel_title={channel_title!r}, channel_username={channel_username!r}")
        if not channel_id or channel_id in ("None", "null", ""):
            sent = await message.reply("❌ Не удалось определить ID канала. Введите корректный username или ID.")
            user_states[user_id]["last_msg_id"] = sent.id if sent else None
            logging.info(f"[FSM][REACTION] sent error message about channel_id")
            return
        user_states[user_id]["reaction_channel_id"] = channel_id
        user_states[user_id]["reaction_channel_title"] = channel_title
        try:
            logging.info(f"[FSM][REACTION] calling get_channel_stats for {channel_id}")
            stats = await api_client.get_channel_stats(channel_id)
            logging.info(f"[FSM][REACTION] got stats: {stats}")
            stat_text = format_channel_stats(stats)
        except Exception as e:
            logging.error(f"[FSM][REACTION] Ошибка получения статистики канала {channel_id}: {e}")
            stat_text = f"Ошибка получения статистики: {e}"
        sent = await message.reply(
            f"📊 Статистика канала {channel_title} (ID: {channel_id}):\n\n{stat_text}\n\nВыберите действие:",
            reply_markup=get_reaction_inline_keyboard(channel_id)
        )
        logging.info(f"[FSM][REACTION] sent stats message: {sent}")
        user_states[user_id]["last_msg_id"] = sent.id if sent else None
        user_states[user_id]["state"] = FSM_REACTION_SETTINGS
        # Не перезаписываем reaction_settings, чтобы сохранить дефолтные эмодзи
        return
    if state == FSM_REACTION_SETTINGS:
        reaction_state = user_states[user_id].get("reaction_state")
        reaction_settings = user_states[user_id].setdefault("reaction_settings", {})
        if reaction_state == "emojis_input":
            emojis = text.split()
            reaction_settings["emojis"] = emojis
            user_states[user_id]["reaction_state"] = None
            await message.reply(f"Эмодзи сохранены: {', '.join(emojis)}")
            return
        if reaction_state == "date_input":
            reaction_settings["date"] = text
            user_states[user_id]["reaction_state"] = None
            await message.reply(f"Дата сохранена: {text}")
            return
        if reaction_state == "count_input":
            try:
                count = int(text)
                reaction_settings["count"] = count
                user_states[user_id]["reaction_state"] = None
                await message.reply(f"Количество постов сохранено: {count}")
            except Exception:
                await message.reply("Введите целое число!")
            return
        if reaction_state == "hashtag_input":
            reaction_settings["hashtag"] = text.strip()
            user_states[user_id]["reaction_state"] = None
            await message.reply(f"Хэштег сохранён: {text.strip()}")
            return
        if reaction_state == "period_input":
            try:
                date_from, date_to = text.split()
                reaction_settings["date_from"] = date_from
                reaction_settings["date_to"] = date_to
                user_states[user_id]["reaction_state"] = None
                await message.reply(f"Период сохранён: {date_from} - {date_to}")
            except Exception:
                await message.reply("Введите период в формате: ГГГГ-ММ-ДД ГГГГ-ММ-ДД")
            return
        if reaction_state == "delay_input":
            try:
                delay = float(text)
                reaction_settings["delay"] = delay
                user_states[user_id]["reaction_state"] = None
                await message.reply(f"Задержка сохранена: {delay} сек.")
            except Exception:
                await message.reply("Введите число (секунды, например: 1 или 0.5)")
            return
        # Если нет активного подрежима — просто игнорируем текст
        return
    if state == FSM_REACTION_CONFIRM:
        channel_id = user_states[user_id]["reaction_channel_id"]
        settings = user_states[user_id]["reaction_settings"]
        result = await api_client.start_mass_reactions(channel_id, settings)
        if result.get("success"):
            task_id = result.get("task_id", "")
            await message.reply(f"✅ Массовая расстановка реакций запущена в фоновом режиме!\n\n{result.get('message','')}\n\n🆔 ID задачи: {task_id}")
        else:
            await message.reply(f"❌ Ошибка запуска: {result.get('error','')}")
        user_states[user_id]["state"] = FSM_MAIN_MENU
        return

async def reaction_callback_handler(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    data = callback_query.data
    if user_id not in user_states:
        await callback_query.answer("Ваша сессия устарела или неактивна. Пожалуйста, начните сначала через /start или /sessions", show_alert=True)
        return
    state = user_states[user_id].get("state")
    reaction_settings = user_states[user_id].setdefault("reaction_settings", {})
    channel_id = user_states[user_id].get("reaction_channel_id")
    if data == "reaction_start":
        user_states[user_id]["state"] = FSM_REACTION_CONFIRM
        await callback_query.answer("Запуск массовых реакций!")
        logging.info(f"[BOT][REACTIONS] Отправляем в API: channel_id={channel_id}, settings={reaction_settings}")
        result = await api_client.start_mass_reactions(channel_id, reaction_settings)
        if result.get("success"):
            task_id = result.get("task_id", "")
            await callback_query.message.reply(f"✅ Массовая расстановка реакций запущена в фоновом режиме!\n\n{result.get('message','')}\n\n🆔 ID задачи: {task_id}")
        else:
            await callback_query.message.reply(f"❌ Ошибка запуска: {result.get('error','')}")
        logging.info(f"[BOT][REACTIONS] Запущена массовая реакция для канала {channel_id}, настройки: {reaction_settings}, результат: {result}")
        user_states[user_id]["state"] = FSM_MAIN_MENU
        return
    if data == "reaction_settings":
        await callback_query.answer()
        await callback_query.message.edit_text(
            "Настройки массовых реакций:\n\nВыберите параметр для изменения:",
            reply_markup=get_reaction_settings_keyboard()
        )
        user_states[user_id]["state"] = FSM_REACTION_SETTINGS
        return
    if data == "reaction_emojis":
        await callback_query.answer()
        current_emojis = reaction_settings.get('emojis', ["😍", "❤️"])
        if not current_emojis:
            current_emojis = ["😍", "❤️"]
        await callback_query.message.edit_text(
            f"Текущие эмодзи: {', '.join(current_emojis)}\n\nВведите эмодзи через пробел (например: 😍 ❤️):",
            reply_markup=get_reaction_settings_keyboard()
        )
        user_states[user_id]["reaction_state"] = "emojis_input"
        return
    if data == "reaction_mode":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("На все посты за день", callback_data="reaction_mode_day")],
            [InlineKeyboardButton("На последние N постов", callback_data="reaction_mode_last_n")],
            [InlineKeyboardButton("С последнего сообщения вверх", callback_data="reaction_mode_from_last")],
            [InlineKeyboardButton("На посты с хэштегом", callback_data="reaction_mode_hashtag")],
            [InlineKeyboardButton("На посты за период", callback_data="reaction_mode_period")],
            [InlineKeyboardButton("🔙 Назад", callback_data="reaction_settings")],
        ])
        await callback_query.answer()
        await callback_query.message.edit_text("Выберите режим массовых реакций:", reply_markup=kb)
        user_states[user_id]["reaction_state"] = None
        return
    if data == "reaction_mode_day":
        reaction_settings["mode"] = "by_date"
        await callback_query.answer("Режим: все посты за день")
        await callback_query.message.edit_text("Введите дату (ГГГГ-ММ-ДД):", reply_markup=get_reaction_settings_keyboard())
        user_states[user_id]["reaction_state"] = "date_input"
        return
    if data == "reaction_mode_last_n":
        reaction_settings["mode"] = "last_n"
        await callback_query.answer("Режим: последние N постов")
        await callback_query.message.edit_text("Введите количество последних постов:", reply_markup=get_reaction_settings_keyboard())
        user_states[user_id]["reaction_state"] = "count_input"
        return
    if data == "reaction_mode_from_last":
        reaction_settings["mode"] = "from_last"
        await callback_query.answer("Режим: с последнего сообщения вверх")
        await callback_query.message.edit_text("Введите количество последних постов для реакции:", reply_markup=get_reaction_settings_keyboard())
        user_states[user_id]["reaction_state"] = "count_input"
        return
    if data == "reaction_mode_hashtag":
        reaction_settings["mode"] = "by_hashtag"
        await callback_query.answer("Режим: по хэштегу")
        await callback_query.message.edit_text("Введите хэштег (без #):", reply_markup=get_reaction_settings_keyboard())
        user_states[user_id]["reaction_state"] = "hashtag_input"
        return
    if data == "reaction_mode_period":
        reaction_settings["mode"] = "by_period"
        await callback_query.answer("Режим: за период")
        await callback_query.message.edit_text("Введите период в формате ГГГГ-ММ-ДД ГГГГ-ММ-ДД (от и до):", reply_markup=get_reaction_settings_keyboard())
        user_states[user_id]["reaction_state"] = "period_input"
        return
    if data == "reaction_hashtag":
        await callback_query.answer()
        await callback_query.message.edit_text(
            f"Текущий хэштег: {reaction_settings.get('hashtag', 'Нет')}\n\nВведите хэштег (без #):",
            reply_markup=get_reaction_settings_keyboard()
        )
        user_states[user_id]["reaction_state"] = "hashtag_input"
        return
    if data == "reaction_date_range":
        await callback_query.answer()
        await callback_query.message.edit_text(
            f"Текущий период: {reaction_settings.get('date_from', 'Нет')} - {reaction_settings.get('date_to', 'Нет')}\n\nВведите период в формате ГГГГ-ММ-ДД ГГГГ-ММ-ДД (от и до):",
            reply_markup=get_reaction_settings_keyboard()
        )
        user_states[user_id]["reaction_state"] = "period_input"
        return
    if data == "reaction_count":
        await callback_query.answer()
        await callback_query.message.edit_text(
            f"Текущее количество: {reaction_settings.get('count', 'Нет')}\n\nВведите количество последних постов:",
            reply_markup=get_reaction_settings_keyboard()
        )
        user_states[user_id]["reaction_state"] = "count_input"
        return
    if data == "reaction_delay":
        await callback_query.answer()
        delay_val = reaction_settings.get('delay', 1)
        await callback_query.message.edit_text(
            f"Текущая задержка: {delay_val} сек.\n\nВведите задержку между реакциями (секунды, например: 1):",
            reply_markup=get_reaction_settings_keyboard()
        )
        user_states[user_id]["reaction_state"] = "delay_input"
        return
    if data == "reaction_save":
        await callback_query.answer("Настройки сохранены!")
        await callback_query.message.edit_text(
            "Настройки массовых реакций сохранены!\n\nВыберите действие:",
            reply_markup=get_reaction_inline_keyboard(channel_id)
        )
        user_states[user_id]["state"] = FSM_REACTION_SETTINGS
        return
    if data == "reaction_back_to_stats":
        stats = await api_client.get_channel_stats(channel_id)
        stat_text = format_channel_stats(stats)
        await callback_query.answer()
        await callback_query.message.edit_text(
            f"📊 Статистика канала {user_states[user_id]['reaction_channel_title']} (ID: {channel_id}):\n\n{stat_text}\n\nВыберите действие:",
            reply_markup=get_reaction_inline_keyboard(channel_id)
        )
        user_states[user_id]["state"] = FSM_REACTION_SETTINGS
        return
    if data == "reaction_back":
        # Возврат к выбору канала, настройки reaction_settings сохраняются
        user_states[user_id]["state"] = FSM_REACTION_CHANNEL
        kb = await get_unique_channels_keyboard(user_id)
        await callback_query.answer()
        await callback_query.message.edit_text(
            "Введите ID или username канала для массовых реакций на посты:",
            reply_markup=kb or ReplyKeyboardRemove()
        )
        return
    await callback_query.answer("Неизвестное действие", show_alert=True)

# --- Вспомогательная функция для resolve_channel ---
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

async def resolve_channel(api_client, text):
    # Сначала нормализуем входные данные
    normalized_text = normalize_channel_input(text)
    logging.info(f"[REACTION][resolve_channel] input='{text}' -> normalized='{normalized_text}'")

    stats = await api_client.get_channel_stats(normalized_text)
    logging.info(f"[REACTION][resolve_channel] stats from api: {stats}")

    # Проверяем, что получили валидный ответ
    if stats and stats.get("id"):
        channel_id = stats.get("id")
        title = stats.get("title", "")
        username = stats.get("username", "")

        # Если канал найден, id будет числом (числовой ID Telegram)
        # Если канал не найден, id будет строкой (username или то что ввел пользователь)
        if isinstance(channel_id, int) or (isinstance(channel_id, str) and channel_id.startswith("-")):
            logging.info(f"[REACTION][resolve_channel] канал найден: id={channel_id}, title='{title}', username='{username}'")
            return channel_id, title, username
        else:
            # Канал не найден - id остался строковым username'ом
            logging.info(f"[REACTION][resolve_channel] канал '{normalized_text}' не найден")
            return None, None, None

    logging.info(f"[REACTION][resolve_channel] нет валидного ответа от API")
    return None, None, None  # Возвращаем None если канал не найден или есть ошибка

# --- Вспомогательная функция для форматирования статистики канала ---
def format_channel_stats(stats):
    """Форматирует статистику канала для отображения"""
    if not stats:
        return "❌ Не удалось получить статистику"
    
    subscribers = stats.get("members_count", "N/A")  # Исправлено: members_count вместо subscribers
    last_message_id = stats.get("last_message_id", "N/A")  # Исправлено: N/A вместо None
    parsed_count = stats.get("parsed_posts", 0)  # Исправлено: parsed_posts вместо parsed_count
    description = stats.get("description", "N/A")
    
    return f"👥 Подписчиков: {subscribers}\n🆔 Последний ID сообщения: {last_message_id}\n📝 Спаршено: {parsed_count}\n📄 Описание: {description}"

# --- Экспортируем функции для использования в bot_main.py ---
__all__ = [
    "start_reaction_master",
    "process_reaction_fsm",
    "reaction_callback_handler"
] 