import re
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from bot.states import user_states, FSM_MAIN_MENU, get_target_channel_history_keyboard
from bot.config import config
from bot.api_client import api_client
import httpx

# --- FSM состояния для навигации ---
FSM_NAVIGATION_AWAIT_CHANNEL = "navigation_await_channel"
FSM_NAVIGATION_MENU = "navigation_menu"
FSM_NAVIGATION_ACTION = "navigation_action"

NAVIGATION_TEMPLATE = (
    "Для вашего удобства сделали очень удобный навигационный лист по хештегам\n"
    "Поиск стал намного удобнее:\n\n"
)

# --- Хранение message_id навигационного сообщения ---
# Используем channel_id как str всегда
async def get_navigation_message_id(channel_id: str) -> int | None:
    try:
        return await api_client.get_navigation_message_id(channel_id)
    except Exception as e:
        print(f"[ERROR] Ошибка получения navigation message ID: {e}")
        return None

async def save_navigation_message_id(channel_id: str, message_id: int):
    try:
        await api_client.save_navigation_message_id(channel_id, message_id)
    except Exception as e:
        print(f"[ERROR] Ошибка сохранения navigation message ID: {e}")

async def ensure_navigation_table():
    # Эта функция больше не нужна, так как таблица создается в парсере
    pass

# --- Новый способ сбора уникальных хэштегов через userbot API ---
async def get_unique_hashtags_via_api(channel_id: str) -> list[str]:
    try:
        return await api_client.get_channel_hashtags(channel_id)
    except Exception as e:
        print(f"[ERROR] Ошибка при запросе хэштегов: {e}")
        return [f"❌ Ошибка при запросе хэштегов: {e}"]

# --- Создание/обновление навигационного сообщения ---
async def create_or_update_navigation_message(client: Client, channel_id: int, hashtags: list[str], nav_msg_id: int = None) -> int:
    text = NAVIGATION_TEMPLATE + "\n".join(hashtags)
    if nav_msg_id:
        try:
            await client.edit_message_text(channel_id, nav_msg_id, text)
            return nav_msg_id
        except Exception:
            # Если не удалось отредактировать (например, удалено) — создаём новое
            sent = await client.send_message(channel_id, text)
            return sent.id
    else:
        sent = await client.send_message(channel_id, text)
        return sent.id

# --- FSM: Хэндлеры ---
async def navigation_menu_handler(client: Client, message: Message):
    user_id = message.from_user.id
    # Сразу просим выбрать канал
    kb = await get_target_channel_history_keyboard(user_id)
    if kb is None:
        kb = ReplyKeyboardMarkup([[KeyboardButton("Ввести ID канала")], [KeyboardButton("Назад")]], resize_keyboard=True)
    else:
        kb.keyboard.append([KeyboardButton("Ввести ID канала")])
        kb.keyboard.append([KeyboardButton("Назад")])
    sent = await message.reply("Выберите канал для навигации:", reply_markup=kb)
    user_states[user_id] = {"state": FSM_NAVIGATION_AWAIT_CHANNEL, "last_msg_id": sent.id, "delete_old": True}

async def navigation_text_handler(client: Client, message: Message):
    user_id = message.from_user.id
    state = user_states.get(user_id, {}).get("state")
    text = message.text.strip()
    if state == FSM_NAVIGATION_AWAIT_CHANNEL:
        if text == "Назад":
            from bot.core import show_main_menu
            await show_main_menu(client, message, "Выберите действие:")
            return
        match = re.match(r"(.+) \(ID: (-?\d+)\)", text)
        if match:
            channel_id = match.group(2)
            async with httpx.AsyncClient() as api_client:
                stats_url = f"{config.PARSER_SERVICE_URL}/channel/stats/{channel_id}"
                stats_resp = await api_client.get(stats_url)
                stats_data = stats_resp.json()
                username = stats_data.get("username")
                is_public = stats_data.get("is_public")
                if is_public and username:
                    channel_id = username
        else:
            channel_id = text.strip()
        user_states[user_id]["navigation_channel_id"] = str(channel_id)
        nav_msg_id = await get_navigation_message_id(str(channel_id))
        hashtags = user_states[user_id].get(f"hashtags_cache_{channel_id}")
        hashtags_count = len(hashtags) if hashtags else 0
        user_states[user_id]["nav_msg_id"] = nav_msg_id
        # Формируем текст
        text_nav = f"Канал: {channel_id}\n"
        if nav_msg_id:
            text_nav += f"Навигационное сообщение: ID {nav_msg_id}\n"
        text_nav += f"Хэштеги ({hashtags_count})"
        # Кнопки
        kb_buttons = [[KeyboardButton("Применить")], [KeyboardButton("Отправить новое")], [KeyboardButton("Обновить")], [KeyboardButton("Показать")], [KeyboardButton("Удалять старое: Да")], [KeyboardButton("Назад")]] if nav_msg_id else [[KeyboardButton("Применить")], [KeyboardButton("Обновить")], [KeyboardButton("Показать")], [KeyboardButton("Назад")]]
        kb = ReplyKeyboardMarkup(kb_buttons, resize_keyboard=True)
        sent = await message.reply(text_nav, reply_markup=kb)
        user_states[user_id]["state"] = FSM_NAVIGATION_ACTION
        user_states[user_id]["last_msg_id"] = sent.id
        return
    elif state == FSM_NAVIGATION_ACTION:
        channel_id = user_states[user_id].get("navigation_channel_id")
        nav_msg_id = user_states[user_id].get("nav_msg_id")
        hashtags = user_states[user_id].get(f"hashtags_cache_{channel_id}")
        delete_old = user_states[user_id].get("delete_old", True)
        if text == "Назад":
            await navigation_menu_handler(client, message)
            return
        if text == "Показать":
            hashtags = user_states[user_id].get(f"hashtags_cache_{channel_id}")
            await message.reply("\n".join(hashtags) if hashtags else "Нет хэштегов")
            return
        if text == "Обновить":
            try:
                hashtags = await get_unique_hashtags_via_api(str(channel_id))
                user_states[user_id][f"hashtags_cache_{channel_id}"] = hashtags
                hashtags_count = len(hashtags) if hashtags else 0
                text_nav = f"Канал: {channel_id}\n"
                if nav_msg_id:
                    text_nav += f"Навигационное сообщение: ID {nav_msg_id}\n"
                text_nav += f"Хэштеги ({hashtags_count})"
                kb_buttons = [[KeyboardButton("Применить")], [KeyboardButton("Отправить новое")], [KeyboardButton("Обновить")], [KeyboardButton("Показать")], [KeyboardButton(f"Удалять старое: {'Да' if delete_old else 'Нет'}")], [KeyboardButton("Назад")]] if nav_msg_id else [[KeyboardButton("Применить")], [KeyboardButton("Обновить")], [KeyboardButton("Показать")], [KeyboardButton("Назад")]]
                kb = ReplyKeyboardMarkup(kb_buttons, resize_keyboard=True)
                await message.reply(text_nav, reply_markup=kb)
            except Exception as e:
                await message.reply(f"❌ Ошибка при обновлении хэштегов: {e}")
            return
        if text == "Применить":
            if not nav_msg_id:
                await message.reply("Сначала создайте навигационное сообщение через 'Отправить новое'.")
                return
            if not hashtags:
                await message.reply("Сначала обновите хэштеги!")
                return
            text_nav = NAVIGATION_TEMPLATE + ("\n".join(hashtags) if hashtags else "ℹ️ Нет хэштегов для отображения.")
            try:
                await client.edit_message_text(channel_id, nav_msg_id, text_nav)
                await message.reply(f"✅ Навигационное сообщение обновлено! ID: {nav_msg_id}")
            except Exception:
                await message.reply("❌ Не удалось обновить сообщение. Попробуйте 'Отправить новое'.")
            return
        if text == "Отправить новое":
            if not hashtags:
                await message.reply("Сначала обновите хэштеги!")
                return
            text_nav = NAVIGATION_TEMPLATE + ("\n".join(hashtags) if hashtags else "ℹ️ Нет хэштегов для отображения.")
            sent = await client.send_message(channel_id, text_nav)
            if nav_msg_id and delete_old:
                try:
                    await client.delete_messages(channel_id, nav_msg_id)
                except Exception:
                    pass
            await save_navigation_message_id(str(channel_id), sent.id)
            user_states[user_id]["nav_msg_id"] = sent.id
            await message.reply(f"✅ Навигационное сообщение создано! ID: {sent.id}")
            return
        if text.startswith("Удалять старое"):
            user_states[user_id]["delete_old"] = not delete_old
            hashtags_count = len(hashtags) if hashtags else 0
            text_nav = f"Канал: {channel_id}\n"
            if nav_msg_id:
                text_nav += f"Навигационное сообщение: ID {nav_msg_id}\n"
            text_nav += f"Хэштеги ({hashtags_count})"
            kb_buttons = [[KeyboardButton("Применить")], [KeyboardButton("Отправить новое")], [KeyboardButton("Обновить")], [KeyboardButton("Показать")], [KeyboardButton(f"Удалять старое: {'Да' if not delete_old else 'Нет'}")], [KeyboardButton("Назад")]] if nav_msg_id else [[KeyboardButton("Применить")], [KeyboardButton("Обновить")], [KeyboardButton("Показать")], [KeyboardButton("Назад")]]
            kb = ReplyKeyboardMarkup(kb_buttons, resize_keyboard=True)
            await message.reply(text_nav, reply_markup=kb)
            return
    else:
        await navigation_menu_handler(client, message) 