#!/usr/bin/env python3
"""
Telegram Parse Bot - Main Entry Point
Микросервисная архитектура с разделением на модули
"""

import logging
import os
import sys
import asyncio
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.append(str(Path(__file__).parent.parent))

from pyrogram import Client, filters
from bot.config import config
from bot.states import user_states
from bot.handlers import start_command, text_handler, forwarding_callback_handler
from bot.navigation_manager import navigation_menu_handler, navigation_text_handler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Инициализация клиента Pyrogram
app = Client(
    "telegram_parse_bot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN
)

# Регистрация обработчиков
@app.on_message(filters.command("start"))
async def start_handler(client, message):
    """Обработчик команды /start"""
    await start_command(client, message)

@app.on_message(filters.command("navigation"))
async def navigation_command_handler(client, message):
    """Обработчик команды /navigation для управления навигацией по хэштегам"""
    await navigation_menu_handler(client, message)

@app.on_message(filters.text & filters.private)
async def text_message_handler(client, message):
    """Обработчик текстовых сообщений"""
    user_id = message.from_user.id
    state = user_states.get(user_id, {}).get("state")
    if state and state.startswith("navigation_"):
        await navigation_text_handler(client, message)
    else:
        await text_handler(client, message)

@app.on_callback_query()
async def callback_query_handler(client, callback_query):
    """Обработчик callback запросов"""
    await forwarding_callback_handler(client, callback_query)

def run_bot():
    """Функция для запуска бота извне"""
    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("Программа завершена пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        sys.exit(1)

# Запускаем только при прямом вызове файла
if __name__ == "__main__":
    run_bot() 