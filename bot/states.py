import re
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from typing import Dict, Optional
import httpx
from bot.config import config
from bot.api_client import api_client
import textwrap
import os
from datetime import datetime
import pytz

# Словарь для хранения статистики
posting_stats: Dict[int, Dict] = {}

# --- Глобальный словарь для отслеживания состояния пользователя (FSM и last_msg_id) ---
user_states = {}

# --- FSM: этапы ---
FSM_MAIN_MENU = "main_menu"
FSM_AWAIT_MONITOR_CHANNEL = "await_monitor_channel"
FSM_AWAIT_MONITOR_TARGET = "await_monitor_target"
FSM_AWAIT_MONITOR_STATUS = "await_monitor_status"
FSM_NONE = None

# Новые состояния для пересылки
FSM_FORWARD_CHANNEL = "forward_channel"
FSM_FORWARD_TARGET = "forward_target"
FSM_FORWARD_SETTINGS = "forward_settings"
FSM_FORWARD_HASHTAG = "forward_hashtag"
FSM_FORWARD_DELAY = "forward_delay"
FSM_FORWARD_FOOTER = "forward_footer"
FSM_FORWARD_FOOTER_LINK = "forward_footer_link"
FSM_FORWARD_FOOTER_LINK_TEXT = "forward_footer_link_text"
FSM_FORWARD_TEXT_MODE = "forward_text_mode"
FSM_FORWARD_LIMIT = "forward_limit"
FSM_FORWARD_DIRECTION = "forward_direction"
FSM_FORWARD_MEDIA_FILTER = "forward_media_filter"
FSM_FORWARD_RANGE = "forward_range"
FSM_FORWARD_RANGE_START = "forward_range_start"
FSM_FORWARD_RANGE_END = "forward_range_end"

# --- Новые состояния для навигации по хэштегам ---
FSM_NAVIGATION_MENU = "navigation_menu"
FSM_NAVIGATION_AWAIT_CHANNEL = "navigation_await_channel"
FSM_NAVIGATION_CONFIRM = "navigation_confirm"

# --- Главное меню ---
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📡 Мониторить канал")],
            [KeyboardButton("⛔ Остановить мониторинг"), KeyboardButton("🧭 Навигация по хэштегам")],
            [KeyboardButton("📊 Статус задач")],
            [KeyboardButton("⭐ Пересылка")],
        ],
        resize_keyboard=True
    )

# --- Получить клавиатуру с историей каналов ---
async def get_channel_history_keyboard(user_id):
    channels = await api_client.get_user_channels(user_id)
    print(f"[DEBUG] Формирование клавиатуры истории каналов для user_id={user_id}. Текущие каналы: {channels}")
    if not channels:
        print(f"[DEBUG] Нет каналов в истории для user_id={user_id}")
        return None
    buttons = [[KeyboardButton(f"{ch['title']} (ID: {ch['id']})")] for ch in channels]
    buttons.append([KeyboardButton("Назад")])
    print(f"[DEBUG] Клавиатура для user_id={user_id}: {[ch['title'] for ch in channels]}")
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# --- Получить клавиатуру с историей целевых каналов ---
async def get_target_channel_history_keyboard(user_id):
    channels = await api_client.get_user_target_channels(user_id)
    print(f"[DEBUG] Формирование клавиатуры истории целевых каналов для user_id={user_id}. Текущие каналы: {channels}")
    if not channels:
        print(f"[DEBUG] Нет целевых каналов в истории для user_id={user_id}")
        return None
    buttons = [[KeyboardButton(f"{ch['title']} (ID: {ch['id']})")] for ch in channels]
    buttons.append([KeyboardButton("Назад")])
    print(f"[DEBUG] Клавиатура целевых каналов для user_id={user_id}: {[ch['title'] for ch in channels]}")
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# --- Новые клавиатуры для пересылки ---
def get_forwarding_keyboard(channel_id=None, target_channel=None):
    """Главная клавиатура пересылки после выбора канала и цели (обычная клавиатура)."""
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Статус задач")],
        [KeyboardButton("🔙 Назад")]
    ], resize_keyboard=True)

def get_forwarding_settings_keyboard():
    """Клавиатура настроек пересылки"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 Режим парсинга", callback_data="forward_parse_mode"),
            InlineKeyboardButton("🏷️ Хэштег фильтр", callback_data="forward_hashtag")
        ],
        [
            InlineKeyboardButton("⏱️ Задержка", callback_data="forward_delay"),
            InlineKeyboardButton("📝 Приписка", callback_data="forward_footer")
        ],
        [
            InlineKeyboardButton("🔗 Гиперссылка в приписке", callback_data="forward_footer_link")
        ],
        [
            InlineKeyboardButton("📄 Режим текста", callback_data="forward_text_mode"),
            InlineKeyboardButton("📊 Лимит постов", callback_data="forward_limit")
        ],
        [
            InlineKeyboardButton("⭐ Платные посты", callback_data="forward_paid_content"),
            InlineKeyboardButton("🗑️ Очистить историю", callback_data="forward_clear_history")
        ],
        [
            InlineKeyboardButton("🔄 Направление", callback_data="forward_direction"),
            InlineKeyboardButton("📷 Фильтр медиа", callback_data="forward_media_filter")
        ],
        [
            InlineKeyboardButton("📋 Диапазон ID", callback_data="forward_range")
        ],
        [
            InlineKeyboardButton("💾 Сохранить", callback_data="forward_save")
        ],
        [
            InlineKeyboardButton("🔙 Назад", callback_data="forward_back_to_stats")
        ]
    ])

def get_parse_mode_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🏷️ По хэштегам"), KeyboardButton("📄 Все сообщения")],
        [KeyboardButton("🔙 Назад")]
    ], resize_keyboard=True)

def get_text_mode_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🗑️ Удалить текст"), KeyboardButton("📄 Как есть")],
        [KeyboardButton("🏷️ Только хэштеги"), KeyboardButton("🔙 Назад")]
    ], resize_keyboard=True)

def get_direction_keyboard():
    """Клавиатура для выбора направления парсинга"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("🔄 От старых к новым"), KeyboardButton("🔄 От новых к старым")],
        [KeyboardButton("🔙 Назад")]
    ], resize_keyboard=True)

def get_media_filter_keyboard():
    """Клавиатура для выбора фильтра медиа"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("📄 Все сообщения"), KeyboardButton("📷 Только с медиа")],
        [KeyboardButton("🔙 Назад")]
    ], resize_keyboard=True)

def get_range_mode_keyboard():
    """Клавиатура для выбора режима диапазона"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("📄 Все сообщения"), KeyboardButton("📋 По диапазону ID")],
        [KeyboardButton("🔙 Назад")]
    ], resize_keyboard=True)



# --- Клавиатура для параметров мониторинга ---
def get_monitor_settings_keyboard(monitor_settings):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton('Режим текста')],
            [KeyboardButton('Добавить приписку'), KeyboardButton('Удалять медиа')],
            [KeyboardButton('Лимит сообщений')],
            [KeyboardButton('Назад')],
        ],
        resize_keyboard=True
    )

# --- Вспомогательные функции для пересылки ---
def format_channel_stats(stats: dict) -> str:
    """
    Форматирование статистики канала для вывода пользователю
    """
    return f"""
👥 Подписчиков: {stats.get('members_count', 'N/A')}
🆔 Последний ID сообщения: {stats.get('last_message_id', 'N/A')}
📝 Спаршено: {stats.get('parsed_posts', 'N/A')}
📄 Описание: {stats.get('description', 'N/A')[:100] if stats.get('description') else 'N/A'}...
"""

def format_forwarding_config(config: dict) -> str:
    """Форматирование конфигурации пересылки"""
    paid_content_stars = config.get('paid_content_stars', 0)
    paid_content_status = f"Включены ({paid_content_stars} звездочек)" if paid_content_stars > 0 else "Отключены"
    direction = config.get('parse_direction', 'backward')
    direction_text = "От старых к новым" if direction == "forward" else "От новых к старым"
    media_filter = config.get('media_filter', 'media_only')
    media_filter_text = "Только с медиа" if media_filter == "media_only" else "Все сообщения"
    range_mode = config.get('range_mode', 'all')
    if range_mode == "range":
        start_id = config.get('range_start_id')
        end_id = config.get('range_end_id')
        if start_id and end_id:
            range_text = f"Диапазон: {start_id} - {end_id}"
        else:
            range_text = "Диапазон (не задан)"
        limit_text = "Без лимита"
    else:
        range_text = "Все сообщения"
        limit_val = config.get('max_posts')
        limit_text = f"{limit_val}" if limit_val else "Без лимита"
    last_id = config.get('last_message_id')
    last_id_text = f"Последний ID: {last_id}" if last_id else ""
    # Имя целевого канала
    target_channel = config.get('target_channel')
    target_channel_title = config.get('target_channel_title') or config.get('target_channel_name')
    if target_channel_title:
        target_channel_display = target_channel_title
    elif target_channel:
        target_channel_display = str(target_channel)
    else:
        target_channel_display = 'Не выбран'
    # Информация о гиперссылке
    footer_link = config.get('footer_link')
    footer_link_text = config.get('footer_link_text')
    footer_full_link = config.get('footer_full_link', False)
    
    if footer_link:
        if footer_full_link:
            hyperlink_info = f"🔗 Гиперссылка: Вся приписка → {footer_link}"
        elif footer_link_text:
            hyperlink_info = f"🔗 Гиперссылка: \"{footer_link_text}\" → {footer_link}"
        else:
            hyperlink_info = f"🔗 Гиперссылка: {footer_link}"
    else:
        hyperlink_info = ""
    
    return f"""
🏷️ Режим: {'По хэштегам' if config.get('parse_mode') == 'hashtags' else 'Все сообщения'}
{'🏷️ Хэштег: ' + config.get('hashtag_filter') if config.get('hashtag_filter') else ''}
⏱️ Задержка: {config.get('delay_seconds', 0)} сек
📝 Приписка: {config.get('footer_text') or 'Нет'}
{hyperlink_info}
📄 Текст: {'Удалить' if config.get('text_mode') == 'remove' else 'Как есть' if config.get('text_mode') == 'as_is' else 'Только хэштеги'}
📊 Лимит: {limit_text}
⭐️ Платные посты: {paid_content_status}
🔄 Направление: {direction_text}
📷 Фильтр медиа: {media_filter_text}
📋 Диапазон: {range_text}
{last_id_text}
🎯 Целевой канал: {target_channel_display}
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



# --- Вспомогательная функция для формирования текста мониторинга ---
def get_monitor_stat_text(stats, monitor_settings):
    title = stats.get('channel_title') or str(stats.get('channel_id'))
    stat_text = (
        f"Канал: {title}\n"
        f"ID: {stats.get('channel_id', '-') or '-'}\n"
        f"👥 Подписчиков: {stats.get('members_count', 'N/A')}\n"
        f"🆔 Последний ID сообщения: {stats.get('last_message_id', 'N/A')}\n"
        f"📝 Спаршено: {stats.get('parsed_posts', 'N/A')}\n"
        f"📄 Описание: {stats.get('description', 'N/A')[:100] if stats.get('description') else 'N/A'}...\n"
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

# --- Проверка статуса мониторинга ---
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

# --- API функции для пересылки ---
async def start_forwarding_api(user_id: int) -> bool:
    """Запуск пересылки через API"""
    try:
        return await api_client.start_forwarding(user_id)
    except Exception as e:
        print(f"[ERROR] Ошибка запуска пересылки через API: {e}")
        return False

async def stop_forwarding_api(user_id: int) -> bool:
    """Остановка пересылки через API"""
    try:
        return await api_client.stop_forwarding(user_id)
    except Exception as e:
        print(f"[ERROR] Ошибка остановки пересылки через API: {e}")
        return False

async def get_forwarding_stats_api(user_id: int) -> dict:
    """Получение статистики пересылки через API"""
    try:
        return await api_client.get_forwarding_stats(user_id)
    except Exception as e:
        print(f"[ERROR] Ошибка получения статистики пересылки через API: {e}")
        return {}

async def save_forwarding_config_api(user_id: int) -> bool:
    """Сохранение конфигурации пересылки через API"""
    try:
        # Получаем настройки из состояния пользователя
        settings = user_states.get(user_id, {}).get('forward_settings', {})
        if not settings:
            print(f"[ERROR] Нет настроек пересылки для пользователя {user_id}")
            return False
        
        # Добавляем target_channel если есть
        if 'forward_target_channel' in user_states.get(user_id, {}):
            settings['target_channel'] = user_states[user_id]['forward_target_channel']
        
        return await api_client.save_forwarding_config(user_id, settings)
    except Exception as e:
        print(f"[ERROR] Ошибка сохранения конфигурации пересылки через API: {e}")
        return False

async def start_forwarding_parsing_api(user_id: int) -> dict:
    """Запуск парсинга и пересылки через API в фоновом режиме"""
    try:
        # Получаем настройки из состояния пользователя
        settings = user_states.get(user_id, {}).get('forward_settings', {})
        if not settings:
            print(f"[ERROR] Нет настроек пересылки для пользователя {user_id}")
            return {"success": False, "error": "Нет настроек пересылки"}
        
        # Добавляем target_channel если есть
        if 'forward_target_channel' in user_states.get(user_id, {}):
            settings['target_channel'] = user_states[user_id]['forward_target_channel']
        
        # Получаем source_channel
        source_channel = user_states.get(user_id, {}).get('forward_channel_id')
        target_channel = user_states.get(user_id, {}).get('forward_target_channel')
        
        if not source_channel or not target_channel:
            return {"success": False, "error": "Не указан исходный или целевой канал"}
        
        # Запускаем парсинг и пересылку в фоновом режиме
        result = await api_client.start_parsing_background(str(source_channel), str(target_channel), settings)
        
        if result.get("status") == "started":
            return {
                "success": True, 
                "task_id": result.get("task_id"),
                "message": result.get("message", "Парсинг+пересылка запущены в фоновом режиме")
            }
        else:
            return {"success": False, "error": result.get("error", "Неизвестная ошибка")}
            
    except Exception as e:
        print(f"[ERROR] Ошибка запуска парсинга и пересылки через API: {e}")
        return {"success": False, "error": str(e)}

async def clear_forwarding_history_api(channel_id: int = None, target_channel: str = None) -> dict:
    """Очистка истории пересылки через API"""
    try:
        return await api_client.clear_forwarding_history(channel_id, target_channel)
    except Exception as e:
        print(f"[ERROR] Ошибка очистки истории пересылки через API: {e}")
        return {}

async def get_forwarding_history_stats_api(channel_id: int = None, target_channel: str = None) -> dict:
    """Получение статистики истории пересылки через API"""
    try:
        return await api_client.get_forwarding_history_stats(channel_id, target_channel)
    except Exception as e:
        print(f"[ERROR] Ошибка получения статистики истории пересылки через API: {e}")
        return {}

async def get_channel_info(channel_id: str) -> dict:
    """Получение информации о канале через API"""
    try:
        return await api_client.get_channel_stats(channel_id)
    except Exception as e:
        print(f"[ERROR] Ошибка получения информации о канале через API: {e}")
        return {
            "id": channel_id,
            "title": f"Канал {channel_id}",
            "username": "",
            "members_count": "N/A",
            "last_message_id": "N/A",
            "parsed_posts": "0",
            "description": ""
        }

async def get_target_channel_info(target_channel: str) -> dict:
    """Получение информации о целевом канале через API"""
    try:
        return await api_client.get_channel_stats(target_channel)
    except Exception as e:
        print(f"[ERROR] Ошибка получения информации о целевом канале через API: {e}")
        return {
            "id": target_channel,
            "title": f"Канал {target_channel}",
            "username": "",
            "members_count": "N/A",
            "last_message_id": "N/A",
            "parsed_posts": "0",
            "description": ""
        }

# Новая функция для inline-кнопки остановки последней задачи
def get_stop_last_task_inline_keyboard(task_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏹️ Остановить задачу", callback_data=f"stop_task:{task_id}")]
    ])

def get_forwarding_inline_keyboard(channel_id=None, target_channel=None, last_task_id=None):
    buttons = [
        [InlineKeyboardButton("▶️ Запустить", callback_data="forward_start"),
         InlineKeyboardButton("📥 Парсинг + пересылка", callback_data="forward_parse_and_forward")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="forward_settings")],
    ]
    if last_task_id:
        buttons.append([InlineKeyboardButton("⏹️ Остановить задачу", callback_data=f"stop_task:{last_task_id}")])
    buttons.append([InlineKeyboardButton("📊 Статус задач", callback_data="check_tasks_status")])
    buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="forward_back")])
    return InlineKeyboardMarkup(buttons) 