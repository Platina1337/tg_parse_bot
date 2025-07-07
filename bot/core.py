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
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, InputMediaPhoto, InputMediaVideo
from pyrogram.errors import MessageNotModified
from shared.models import ParseConfig, ParseMode, PostingSettings
from bot.settings import get_user_settings, update_user_settings, clear_user_settings, get_user_templates, save_user_template, DB_PATH
from bot.states import (
    user_states, FSM_MAIN_MENU,
    FSM_AWAIT_MONITOR_CHANNEL, FSM_AWAIT_MONITOR_TARGET, FSM_AWAIT_MONITOR_STATUS,
    FSM_FORWARD_CHANNEL, FSM_FORWARD_TARGET, FSM_FORWARD_SETTINGS, FSM_FORWARD_HASHTAG,
    FSM_FORWARD_DELAY, FSM_FORWARD_FOOTER, FSM_FORWARD_TEXT_MODE, FSM_FORWARD_LIMIT,
    FSM_FORWARD_DIRECTION, FSM_FORWARD_MEDIA_FILTER, FSM_FORWARD_RANGE, FSM_FORWARD_RANGE_START, FSM_FORWARD_RANGE_END,
    get_main_keyboard, get_channel_history_keyboard, get_target_channel_history_keyboard,
    get_forwarding_keyboard, get_forwarding_settings_keyboard, get_parse_mode_keyboard, get_text_mode_keyboard,
    get_direction_keyboard, get_media_filter_keyboard, get_range_mode_keyboard,
    get_monitor_settings_keyboard, posting_stats, start_forwarding_parsing_api, get_forwarding_history_stats_api, 
    clear_forwarding_history_api, get_channel_info, get_target_channel_info
)
from bot.config import config
from bot.navigation_manager import *

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Client(
    "parser_bot",
    api_id=os.getenv("API_ID"),
    api_hash=os.getenv("API_HASH"),
    bot_token=os.getenv("BOT_TOKEN")
)

# --- Вспомогательные функции ---
async def show_main_menu(client, message, text="Выберите действие:"):
    user_id = message.from_user.id
    last_msg_id = user_states.get(user_id, {}).get("last_msg_id")
    try:
        if last_msg_id:
            await message.edit_text(text, reply_markup=get_main_keyboard())
        else:
            sent = await message.reply(text, reply_markup=get_main_keyboard())
            if sent is not None:
                user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_MAIN_MENU, "last_msg_id": sent.id}
            else:
                user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_MAIN_MENU}
    except MessageNotModified:
        pass
    except Exception:
        sent = await message.reply(text, reply_markup=get_main_keyboard())
        if sent is not None:
            user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_MAIN_MENU, "last_msg_id": sent.id}
        else:
            user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_MAIN_MENU}



async def show_message_with_media(client, msg, message):
    local_file_path = msg.get('local_file_path')
    msg_type = msg.get('type')
    text = msg.get('text') or '[без текста]'
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("Запостить", callback_data=f"post_{msg.get('id')}")]
    ])
    if local_file_path and os.path.exists(local_file_path):
        if msg_type and 'photo' in msg_type:
            with open(local_file_path, "rb") as f:
                await message.reply_photo(f, caption=text, reply_markup=buttons)
        elif msg_type and 'video' in msg_type:
            with open(local_file_path, "rb") as f:
                await message.reply_video(f, caption=text, reply_markup=buttons)
        elif msg_type and 'document' in msg_type:
            with open(local_file_path, "rb") as f:
                await message.reply_document(f, caption=text, reply_markup=buttons)
        else:
            await message.reply(f"ID: {msg.get('id')}\n{text}", reply_markup=buttons)
    elif msg_type and 'photo' in msg_type and local_file_path:
        with open(local_file_path, "rb") as f:
            await message.reply_photo(f, caption=text, reply_markup=buttons)
    elif msg_type and 'video' in msg_type and local_file_path:
        with open(local_file_path, "rb") as f:
            await message.reply_video(f, caption=text, reply_markup=buttons)
    else:
        await message.reply(f"ID: {msg.get('id')}\n{text}", reply_markup=buttons)

# --- API функции для пересылки ---
async def start_forwarding_api(user_id: int) -> bool:
    """Запуск пересылки через API"""
    try:
        channel_id = user_states[user_id]['forward_channel_id']
        target_channel = user_states[user_id]['forward_target_channel']
        forward_settings = user_states[user_id]['forward_settings']
        # Останавливаем старую пересылку, если она есть
        # await stop_forwarding_api(user_id)
        payload = {
            'user_id': user_id,
            'source_channel_id': channel_id,
            'target_channel_id': target_channel,
            'parse_mode': forward_settings.get('parse_mode', 'all'),
            'hashtag_filter': forward_settings.get('hashtag_filter'),
            'delay_seconds': forward_settings.get('delay_seconds', 0),
            'footer_text': forward_settings.get('footer_text', ''),
            'text_mode': forward_settings.get('text_mode', 'hashtags_only'),
            'max_posts': forward_settings.get('max_posts'),
            'hide_sender': forward_settings.get('hide_sender', True),
            'paid_content_mode': forward_settings.get('paid_content_mode', 'off'),
            'paid_content_stars': forward_settings.get('paid_content_stars', 0),
            'paid_content_hashtag': forward_settings.get('paid_content_hashtag'),
            'paid_content_every': forward_settings.get('paid_content_every'),
            'paid_content_chance': forward_settings.get('paid_content_chance'),
        }
        print(f"[DEBUG][FORWARD] payload для /forwarding/start: {payload}")
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{config.PARSER_SERVICE_URL}/forwarding/start", json=payload)
        print(f"[DEBUG] start_forwarding_api response: {resp.status_code} - {resp.text}")
        return resp.status_code == 200
    except Exception as e:
        print(f"[ERROR] Ошибка запуска пересылки: {e}")
        return False

async def start_forwarding_parsing_api(user_id: int) -> bool:
    """Запуск парсинга и пересылки через API (гарантированно как через Postman)"""
    try:
        channel_id = user_states[user_id]['forward_channel_id']
        target_channel = user_states[user_id]['forward_target_channel']
        forward_settings = user_states[user_id]['forward_settings']
        forward_config = dict(forward_settings)
        forward_config.setdefault('forward_mode', 'copy')
        # Добавляем новые поля с значениями по умолчанию
        forward_config.setdefault('parse_direction', forward_settings.get('parse_direction', 'backward'))
        forward_config.setdefault('media_filter', forward_settings.get('media_filter', 'media_only'))
        forward_config.setdefault('range_mode', 'all')
        payload = {
            "source_channel": str(channel_id),
            "target_channel": str(target_channel),
            "config": forward_config
        }
        logging.getLogger(__name__).info(f"[BOT][FORWARDING_PARSE] Отправляю запрос: {payload}")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{config.PARSER_SERVICE_URL}/forwarding/parse",
                json=payload,
                timeout=30.0
            )
        logging.getLogger(__name__).info(f"[BOT][FORWARDING_PARSE] Ответ: {response.status_code} {response.text}")
        return response.status_code == 200
    except Exception as e:
        logging.getLogger(__name__).error(f"[BOT][FORWARDING_PARSE] Ошибка: {e}")
        return False

async def stop_forwarding_api(user_id: int) -> bool:
    """Остановка пересылки через API"""
    try:
        channel_id = user_states[user_id]['forward_channel_id']
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{config.PARSER_SERVICE_URL}/forwarding/stop", json={'channel_id': channel_id, 'user_id': user_id})
        return resp.status_code == 200
    except Exception as e:
        print(f"[ERROR] Ошибка остановки пересылки: {e}")
        return False

async def get_forwarding_stats_api(channel_id: int) -> dict:
    """Получить статистику пересылки для канала"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{config.PARSER_SERVICE_URL}/forwarding/stats/{channel_id}")
            if response.status_code == 200:
                return {"status": "success", "data": response.json()}
            else:
                return {"status": "error", "message": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def save_forwarding_config_api(user_id: int) -> bool:
    """Сохранить конфигурацию пересылки через API"""
    try:
        user_state = user_states.get(user_id, {})
        forward_settings = user_state.get('forward_settings', {})
        channel_id = user_state.get('forward_channel_id')
        target_channel = user_state.get('forward_target_channel')
        
        if not channel_id or not target_channel:
            return False
        
        # Формируем данные согласно схеме ForwardingConfigRequest
        config_data = {
            "user_id": user_id,
            "source_channel_id": channel_id,
            "target_channel_id": target_channel,
            "parse_mode": forward_settings.get('parse_mode', 'all'),
            "hashtag_filter": forward_settings.get('hashtag_filter'),
            "delay_seconds": forward_settings.get('delay_seconds', 0),
            "footer_text": forward_settings.get('footer_text', ''),
            "text_mode": forward_settings.get('text_mode', 'hashtags_only'),
            "max_posts": forward_settings.get('max_posts'),
            "hide_sender": forward_settings.get('hide_sender', True),
            "paid_content_stars": forward_settings.get('paid_content_stars', 0),
            # --- Новые поля ---
            "parse_direction": forward_settings.get('parse_direction', 'backward'),
            "media_filter": forward_settings.get('media_filter', 'media_only'),
            "range_mode": forward_settings.get('range_mode', 'all'),
            "range_start_id": forward_settings.get('range_start_id'),
            "range_end_id": forward_settings.get('range_end_id'),
            "paid_content_every": forward_settings.get('paid_content_every'),
        }
        
        # Подробное логирование конфигурации
        paid_stars = config_data.get('paid_content_stars', 0)
        print(f"[BOT] 🔍 save_forwarding_config_api: paid_content_stars={paid_stars} (тип: {type(paid_stars)})")
        print(f"[BOT] 🔍 Все ключи forward_settings: {list(forward_settings.keys())}")
        print(f"[BOT] 🔍 Все ключи config_data: {list(config_data.keys())}")
        
        logger.info(f"Saving forwarding config: {config_data}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{config.PARSER_SERVICE_URL}/forwarding/config", json=config_data)
            logger.info(f"Forwarding config save response: {response.status_code} - {response.text}")
            return response.status_code == 200
    except Exception as e:
        logger.error(f"Error saving forwarding config: {e}")
        return False

# --- Вспомогательные функции для форматирования ---
def format_channel_stats(stats: dict) -> str:
    """Форматирование статистики канала"""
    return f"""
👥 Подписчиков: {stats.get('members_count', 'N/A')}
📊 Сообщений: {stats.get('total_posts', 'N/A')}
📝 Спаршено: {stats.get('parsed_posts', 'N/A')}
📅 Создан: {stats.get('created_at', 'N/A')}
📄 Описание: {stats.get('description', 'N/A')[:100]}...
"""

def format_forwarding_stats(stats: dict) -> str:
    """Форматирование статистики пересылки"""
    return f"""
📈 Статистика пересылки:
📤 Всего переслано: {stats.get('total_forwarded', 0)}
📅 Сегодня: {stats.get('today_forwarded', 0)}
🏷️ По хэштегам: {stats.get('hashtag_matches', 0)}
❌ Ошибок: {stats.get('errors_count', 0)}
🕐 Последняя активность: {stats.get('last_activity', 'N/A')}
"""

async def check_monitoring_status(user_id, channel_id):
    try:
        async with httpx.AsyncClient() as client_api:
            resp = await client_api.get(f"{config.PARSER_SERVICE_URL}/monitor/status/{channel_id}")
            if resp.status_code == 200:
                data = resp.json()
                is_active = data.get("is_active", False)
                started_at = data.get("started_at")
                return f"Мониторинг {'запущен' if is_active else 'не запущен'} для канала {channel_id}." + (f"\nСтарт: {started_at}" if started_at else "")
            else:
                return f"Ошибка получения статуса мониторинга: {resp.text}"
    except Exception as e:
        return f"Ошибка при обращении к сервису мониторинга: {e}"

async def get_actual_published_count(channel_id, target_channel_id):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{config.PARSER_SERVICE_URL}/published_count/{channel_id}/{target_channel_id}")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("published_count", 0)
    except Exception as e:
        print(f"[DEBUG] Ошибка получения published_count: {e}")
    return 0

def get_publish_stat_text(stats, publish_settings, published_count=None):
    title = stats.get('channel_title') or str(stats.get('channel_id'))
    stat_text = (
        f"Канал: {title}\n"
        f"ID: {stats.get('channel_id', '-') or '-'}\n"
        f"Всего постов: {stats.get('total_posts', '-') or '-'}\n"
        f"Спаршено: {stats.get('parsed_posts', '-') or '-'}\n"
        f"Медиагрупп: {stats.get('parsed_media_groups', '-') or '-'}\n"
        f"Одиночных: {stats.get('parsed_singles', '-') or '-'}\n"
        f"ID диапазон: {stats.get('min_id', '-')} - {stats.get('max_id', '-') }\n"
        f"Последний спаршенный: {stats.get('last_parsed_id', '-')} ({stats.get('last_parsed_date', '-')})\n"
        f"Опубликовано: {published_count if published_count is not None else '-'} сообщений\n"
        f"\n"
        f"Параметры публикации:\n"
        f"Порядок: {publish_settings.get('order', 'old_to_new')}\n"
        f"Задержка: {publish_settings.get('delay', 0)} сек\n"
        f"Режим: {publish_settings.get('mode', 'все')}\n"
        f"Текст: {publish_settings.get('text_mode', 'с текстом')}\n"
        f"Приписка: {publish_settings.get('footer', '-') or '-'}\n"
        f"Лимит: {publish_settings.get('max_posts', 0) or 'все'} сообщений\n"
    )
    return stat_text

def get_monitor_stat_text(stats, monitor_settings):
    title = stats.get('channel_title') or str(stats.get('channel_id'))
    stat_text = (
        f"Канал: {title}\n"
        f"ID: {stats.get('channel_id', '-') or '-'}\n"
        f"Всего постов: {stats.get('total_posts', '-') or '-'}\n"
        f"Спаршено: {stats.get('parsed_posts', '-') or '-'}\n"
        f"Медиагрупп: {stats.get('parsed_media_groups', '-') or '-'}\n"
        f"Одиночных: {stats.get('parsed_singles', '-') or '-'}\n"
        f"ID диапазон: {stats.get('min_id', '-')} - {stats.get('max_id', '-') }\n"
        f"Последний спаршенный: {stats.get('last_parsed_id', '-')} ({stats.get('last_parsed_date', '-')})\n"
        f"Опубликовано: - сообщений\n"
        f"\n"
        f"Параметры мониторинга:\n"
        f"Порядок: {monitor_settings.get('order', 'old_to_new')}\n"
        f"Задержка: {monitor_settings.get('delay', 0)} сек\n"
        f"Режим текста: {monitor_settings.get('text_mode', 'с текстом')}\n"
        f"Приписка: {monitor_settings.get('footer', '-') or '-'}\n"
        f"Удалять медиа: {'да' if monitor_settings.get('delete_media', True) else 'нет'}\n"
        f"Лимит: {monitor_settings.get('max_posts', 0) or 'все'} сообщений\n"
    )
    return stat_text

# --- Асинхронная инициализация БД при старте бота ---
async def async_init():
    await db.init()

async def clear_forwarding_history_api(channel_id: int = None, target_channel: str = None) -> dict:
    """Очистить историю пересланных постов через API"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{config.PARSER_SERVICE_URL}/forwarding/clear_history",
                json={
                    "channel_id": channel_id,
                    "target_channel": target_channel
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to clear forwarding history: {response.text}")
                return {"status": "error", "message": f"Ошибка API: {response.text}"}
                
    except Exception as e:
        logger.error(f"Error clearing forwarding history: {e}")
        return {"status": "error", "message": str(e)}

async def get_forwarding_history_stats_api(channel_id: int = None, target_channel: str = None) -> dict:
    """Получить статистику истории пересылки"""
    try:
        params = {}
        if channel_id:
            params['channel_id'] = channel_id
        if target_channel:
            params['target_channel'] = target_channel
            
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{config.PARSER_SERVICE_URL}/forwarding/history/stats", params=params)
            if response.status_code == 200:
                return {"status": "success", "data": response.json()}
            else:
                return {"status": "error", "message": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def get_channel_info(channel_id: str) -> dict:
    """Получить информацию о канале по ID"""
    try:
        # Пытаемся получить информацию через API парсера
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{config.PARSER_SERVICE_URL}/channel/stats/{channel_id}")
            if response.status_code == 200:
                data = response.json()
                return {
                    "id": channel_id,
                    "title": data.get("channel_title", f"Канал {channel_id}"),
                    "username": data.get("channel_username", ""),
                    "members_count": data.get("members_count", "N/A")
                }
    except Exception as e:
        logger.error(f"Error getting channel info: {e}")
    
    # Если не удалось получить через API, возвращаем базовую информацию
    return {
        "id": channel_id,
        "title": f"Канал {channel_id}",
        "username": "",
        "members_count": "N/A"
    }

async def get_target_channel_info(target_channel: str) -> dict:
    """Получить информацию о целевом канале"""
    try:
        # Для целевого канала пытаемся извлечь информацию из строки
        if target_channel.startswith("-100"):
            return {
                "id": target_channel,
                "title": f"Канал {target_channel}",
                "username": "",
                "members_count": "N/A"
            }
        elif target_channel.startswith("@"):
            return {
                "id": target_channel,
                "title": target_channel,
                "username": target_channel,
                "members_count": "N/A"
            }
        else:
            return {
                "id": target_channel,
                "title": target_channel,
                "username": target_channel,
                "members_count": "N/A"
            }
    except Exception as e:
        logger.error(f"Error getting target channel info: {e}")
        return {
            "id": target_channel,
            "title": f"Канал {target_channel}",
            "username": "",
            "members_count": "N/A"
        }

async def main():
    # Удаляем инициализацию базы данных
    # await db.init()
    pass

if __name__ == "__main__":
    # Удаляем инициализацию базы данных
    # loop = asyncio.get_event_loop()
    # loop.run_until_complete(db.init())
    pass 