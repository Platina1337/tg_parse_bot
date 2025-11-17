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
import traceback

# Добавляем корневую директорию в путь
sys.path.append(str(Path(__file__).parent.parent))

from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.errors import RPCError
from pyrogram.types import BotCommand
from bot.config import config
from bot.states import (
    user_states,
    FSM_WATERMARK_IMAGE_UPLOAD,
    FSM_WATERMARK_TEXT_INPUT,
    FSM_WATERMARK_CHANCE,
    FSM_WATERMARK_HASHTAG,
    FSM_WATERMARK_OPACITY,
    FSM_WATERMARK_SCALE
)
from bot.handlers import start_command, text_handler, forwarding_callback_handler, monitorings_command, check_tasks_status_callback, process_callback_query
from bot.navigation_manager import navigation_menu_handler, navigation_text_handler
import bot.handlers  # Для регистрации всех callback-обработчиков
from bot.session_handlers import (
    sessions_command,  # добавляю импорт этой функции обратно
    add_session_callback,
    assign_session_callback,
    select_session_callback,
    assign_task_callback,
    remove_task_callback,
    delete_session_callback,
    confirm_delete_callback,
    delete_confirmed_callback,
    cancel_session_action_callback,
    resend_code_callback,
    handle_session_text_input  # добавляю импорт этой функции
)
from bot.reaction_handlers import (
    reactions_command,
    handle_reaction_text_input
)
from bot.reaction_master import start_reaction_master, process_reaction_fsm, reaction_callback_handler
from pyrogram.handlers import CallbackQueryHandler

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

# Удаляем неправильный декоратор on_start

# Регистрация обработчиков
@app.on_message(filters.command("start"))
async def start_handler(client, message):
    """Обработчик команды /start"""
    await start_command(client, message)

@app.on_message(filters.command("navigation"))
async def navigation_command_handler(client, message):
    """Обработчик команды /navigation для управления навигацией по хэштегам"""
    await navigation_menu_handler(client, message)

@app.on_message(filters.command("sessions"))
async def sessions_command_handler(client, message):
    """Handler for /sessions command for managing multiple Telegram sessions"""
    await sessions_command(client, message)

@app.on_message(filters.command("reactions"))
async def reactions_command_handler(client, message):
    await start_reaction_master(client, message)

@app.on_message(filters.command("monitorings"))
async def monitorings_command_handler(client, message):
    """Handler for /monitorings command for checking task status"""
    from bot.handlers import monitorings_command
    await monitorings_command(client, message)

@app.on_message(filters.command("public_groups"))
async def public_groups_command_handler(client, message):
    """Handler for /public_groups command for managing public groups forwarding"""
    from bot.public_groups_manager import start_public_groups_manager
    await start_public_groups_manager(client, message)



@app.on_message(filters.command("setup_commands"))
async def setup_commands_handler(client, message):
    """Handler for /setup_commands command for forcing menu setup"""
    from bot.handlers import setup_commands_command
    await setup_commands_command(client, message)

@app.on_message(filters.text & filters.private)
async def text_message_handler(client, message):
    """Обработчик текстовых сообщений"""
    user_id = message.from_user.id
    state = user_states.get(user_id, {}).get("state")
    
    # Добавляем логирование для отладки
    logger.info(f"[TEXT_HANDLER] User {user_id}, state: {state}, text: '{message.text.strip()}'")
    
    if state and state.startswith("navigation_"):
        logger.info(f"[TEXT_HANDLER] Handling navigation for user {user_id}")
        await navigation_text_handler(client, message)
    elif state and state.startswith("session_"):
        # Handle session management text input
        logger.info(f"[TEXT_HANDLER] Handling session for user {user_id}")
        handled = await handle_session_text_input(client, message)
        if handled:
            logger.info(f"[TEXT_HANDLER] Session handled for user {user_id}")
            return
        # Don't call text_handler if session input was handled
        logger.info(f"[TEXT_HANDLER] Session not handled, returning for user {user_id}")
        return
    elif state and state.startswith("reaction_"):
        logger.info(f"[TEXT_HANDLER] Handling reaction FSM for user {user_id}")
        await process_reaction_fsm(client, message)
        return
    elif state and state.startswith("public_groups_"):
        logger.info(f"[TEXT_HANDLER] Handling public groups FSM for user {user_id}")
        from bot.public_groups_manager import handle_public_groups_text
        handled = await handle_public_groups_text(client, message)
        if handled:
            return

    else:
        logger.info(f"[TEXT_HANDLER] Calling text_handler for user {user_id}")
        await text_handler(client, message)

# Глобальный обработчик всех сообщений для отладки и обработки watermark
@app.on_message()
async def global_message_handler(client, message):
    """Глобальный обработчик всех сообщений для отладки и обработки watermark"""
    # Проверяем, что сообщение от пользователя (не служебное)
    if not hasattr(message, 'from_user') or message.from_user is None:
        logger.debug(f"[GLOBAL_MESSAGE] Skipping message without from_user: {type(message).__name__}")
        return

    user_id = message.from_user.id
    chat_type = "private" if message.chat.type == ChatType.PRIVATE else f"{message.chat.type} ({message.chat.id})"
    state = user_states.get(user_id, {}).get("state")

    logger.info(f"[GLOBAL_MESSAGE] User {user_id}, chat: {chat_type}, state: {state}, message_type: {type(message).__name__}")

    # Обрабатываем watermark загрузку изображений
    if state == FSM_WATERMARK_IMAGE_UPLOAD and message.chat.type == ChatType.PRIVATE:
        handled = False

        if hasattr(message, 'photo') and message.photo:
            logger.info(f"[GLOBAL_MESSAGE] Processing PHOTO watermark upload for user {user_id}")
            from bot.watermark_handlers import handle_watermark_image_upload
            await handle_watermark_image_upload(client, message)
            handled = True

        elif hasattr(message, 'document') and message.document:
            # Проверяем, что это изображение
            mime_type = getattr(message.document, 'mime_type', '')
            file_name = getattr(message.document, 'file_name', '')

            logger.info(f"[GLOBAL_MESSAGE] DOCUMENT received: {file_name}, mime={mime_type}")

            if mime_type.startswith('image/') or file_name.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                logger.info(f"[GLOBAL_MESSAGE] Processing DOCUMENT watermark upload for user {user_id}")
                from bot.watermark_handlers import handle_watermark_image_upload
                await handle_watermark_image_upload(client, message)
                handled = True

        if handled:
            logger.info(f"[GLOBAL_MESSAGE] Watermark upload handled, stopping further processing")
            return  # Останавливаем дальнейшую обработку только если обработали watermark

    # Продолжаем логирование для других сообщений
    if hasattr(message, 'photo') and message.photo:
        logger.info(f"[GLOBAL_MESSAGE] PHOTO received: size={message.photo.file_size}, unique_id={message.photo.file_unique_id}")
    elif hasattr(message, 'document') and message.document:
        logger.info(f"[GLOBAL_MESSAGE] DOCUMENT received: {message.document.file_name}, size={message.document.file_size}, mime={message.document.mime_type}")
    elif hasattr(message, 'text') and message.text:
        logger.info(f"[GLOBAL_MESSAGE] TEXT received: '{message.text[:50]}...'")
    else:
        logger.info(f"[GLOBAL_MESSAGE] OTHER message type: {message}")

    # Не возвращаем, чтобы позволить другим обработчикам работать

# Убираем отдельные обработчики, watermark обрабатывается в глобальном обработчике

# Убираем отдельный обработчик документов, обрабатываем все в глобальном обработчике
# @app.on_message(filters.document & filters.private)
# async def document_message_handler(client, message):

# Глобальный обработчик всех callback-запросов
@app.on_callback_query()
async def callback_query_handler(client, callback_query):
    try:
        handled = await process_callback_query(client, callback_query)
        if not handled:
            await callback_query.answer("Неизвестное действие", show_alert=True)
    except RPCError as e:
        logger.error(f"[CALLBACK_HANDLER] RPCError: {e}\n{traceback.format_exc()}")
        await callback_query.answer("Ошибка Telegram API", show_alert=True)
    except Exception as e:
        logger.error(f"[CALLBACK_HANDLER] Ошибка типа {type(e)}: {e}\n{traceback.format_exc()}")
        await callback_query.answer(f"Внутренняя ошибка: {e}", show_alert=True)

@app.on_callback_query(filters.regex("^add_session$"))
async def add_session_callback_decorator(client, callback_query):
    await add_session_callback(client, callback_query)

@app.on_callback_query(filters.regex("^assign_session$"))
async def assign_session_callback_decorator(client, callback_query):
    await assign_session_callback(client, callback_query)

@app.on_callback_query(filters.regex("^select_session:(.+)$"))
async def select_session_callback_decorator(client, callback_query):
    await select_session_callback(client, callback_query)

@app.on_callback_query(filters.regex("^assign_task:(.+):(.+)$"))
async def assign_task_callback_decorator(client, callback_query):
    await assign_task_callback(client, callback_query)

@app.on_callback_query(filters.regex("^remove_task:(.+):(.+)$"))
async def remove_task_callback_decorator(client, callback_query):
    await remove_task_callback(client, callback_query)

@app.on_callback_query(filters.regex("^delete_session$"))
async def delete_session_callback_decorator(client, callback_query):
    await delete_session_callback(client, callback_query)

@app.on_callback_query(filters.regex("^confirm_delete:(.+)$"))
async def confirm_delete_callback_decorator(client, callback_query):
    await confirm_delete_callback(client, callback_query)

@app.on_callback_query(filters.regex("^delete_confirmed:(.+)$"))
async def delete_confirmed_callback_decorator(client, callback_query):
    await delete_confirmed_callback(client, callback_query)

@app.on_callback_query(filters.regex("^cancel_session_action$"))
async def cancel_session_action_callback_decorator(client, callback_query):
    await cancel_session_action_callback(client, callback_query)

@app.on_callback_query(filters.regex("^resend_code:(.+)$"))
async def resend_code_callback_decorator(client, callback_query):
    await resend_code_callback(client, callback_query)

@app.on_callback_query(filters.regex("^add_reaction$"))
async def add_reaction_callback_decorator(client, callback_query):
    from bot.session_handlers import add_reaction_callback
    await add_reaction_callback(client, callback_query)



# Обработчики для задач и мониторингов
@app.on_callback_query(filters.regex("^stop_task:"))
async def stop_task_handler(client, callback_query):
    from bot.handlers import stop_task_callback
    await stop_task_callback(client, callback_query)

@app.on_callback_query(filters.regex("^stop_reaction_task:"))
async def stop_reaction_task_handler(client, callback_query):
    from bot.handlers import stop_reaction_task_callback
    await stop_reaction_task_callback(client, callback_query)

@app.on_callback_query(filters.regex("^stop_monitoring:"))
async def stop_monitoring_handler(client, callback_query):
    from bot.handlers import stop_monitoring_callback
    await stop_monitoring_callback(client, callback_query)

@app.on_callback_query(filters.regex(r"^stop_all_tasks$"))
async def stop_all_tasks_handler(client, callback_query):
    from bot.handlers import stop_all_tasks_callback
    await stop_all_tasks_callback(client, callback_query)

@app.on_callback_query(filters.regex("^forward_back_to_stats$"))
async def forward_back_to_stats_handler(client, callback_query):
    await forwarding_callback_handler(client, callback_query)

@app.on_callback_query(filters.regex("^reaction_back_to_stats$"))
async def reaction_back_to_stats_handler(client, callback_query):
    await reaction_callback_handler(client, callback_query)

# Обработчики для пагинации и медиа
@app.on_callback_query(filters.regex("^page_"))
async def page_handler(client, callback_query):
    await forwarding_callback_handler(client, callback_query)

@app.on_callback_query(filters.regex("^showmedia_"))
async def showmedia_handler(client, callback_query):
    await forwarding_callback_handler(client, callback_query)

@app.on_callback_query(filters.regex("^close_pagination$"))
async def close_pagination_handler(client, callback_query):
    await forwarding_callback_handler(client, callback_query)

@app.on_callback_query(filters.regex("^publish_now$"))
async def publish_now_handler(client, callback_query):
    await forwarding_callback_handler(client, callback_query)

@app.on_callback_query(filters.regex("^noop$"))
async def noop_handler(client, callback_query):
    await callback_query.answer()

# Обработчики для реакций из reaction_handlers.py
@app.on_callback_query(filters.regex("^view_sessions$"))
async def view_sessions_handler(client, callback_query):
    from bot.reaction_handlers import view_sessions_callback
    await view_sessions_callback(client, callback_query)

@app.on_callback_query(filters.regex("^assign_reaction_session$"))
async def assign_reaction_session_handler(client, callback_query):
    from bot.reaction_handlers import assign_reaction_session_callback
    await assign_reaction_session_callback(client, callback_query)

@app.on_callback_query(filters.regex("^toggle_reaction_session:(.+)$"))
async def toggle_reaction_session_handler(client, callback_query):
    from bot.reaction_handlers import toggle_reaction_session_callback
    await toggle_reaction_session_callback(client, callback_query)

@app.on_callback_query(filters.regex("^back_to_sessions$"))
async def back_to_sessions_handler(client, callback_query):
    from bot.reaction_handlers import back_to_sessions_callback
    await back_to_sessions_callback(client, callback_query)

@app.on_callback_query(filters.regex("^back_to_reactions$"))
async def back_to_reactions_handler(client, callback_query):
    from bot.reaction_handlers import back_to_reactions_callback
    await back_to_reactions_callback(client, callback_query)

@app.on_callback_query(filters.regex("^select_reaction:(.+)$"))
async def select_reaction_handler(client, callback_query):
    from bot.reaction_handlers import select_reaction_callback
    await select_reaction_callback(client, callback_query)

@app.on_callback_query(filters.regex("^use_default_session$"))
async def use_default_session_handler(client, callback_query):
    from bot.reaction_handlers import use_default_session_callback
    await use_default_session_callback(client, callback_query)

@app.on_callback_query(filters.regex("^confirm_reaction$"))
async def confirm_reaction_handler(client, callback_query):
    from bot.reaction_handlers import confirm_reaction_callback
    await confirm_reaction_callback(client, callback_query)

@app.on_callback_query(filters.regex("^confirm_reaction_default$"))
async def confirm_reaction_default_handler(client, callback_query):
    from bot.reaction_handlers import confirm_reaction_default_callback
    await confirm_reaction_default_callback(client, callback_query)

@app.on_callback_query(filters.regex("^cancel_reaction$"))
async def cancel_reaction_handler(client, callback_query):
    from bot.reaction_handlers import cancel_reaction_callback
    await cancel_reaction_callback(client, callback_query)

# Обработчики для публичных групп теперь обрабатываются в process_callback_query

@app.on_callback_query(filters.regex("^check_tasks_status$"))
async def check_tasks_status_handler(client, callback_query):
    await check_tasks_status_callback(client, callback_query)

@app.on_callback_query(filters.regex("^check_reaction_tasks_status$"))
async def check_reaction_tasks_status_handler(client, callback_query):
    from bot.handlers import check_reaction_tasks_status_callback
    await check_reaction_tasks_status_callback(client, callback_query)

# Обработчики для кнопок из states.py
@app.on_callback_query(filters.regex("^forward_"))
async def forward_callback_handler(client, callback_query):
    await forwarding_callback_handler(client, callback_query)

@app.on_callback_query(filters.regex("^reaction_"))
async def reaction_callback_handler_decorator(client, callback_query):
    await reaction_callback_handler(client, callback_query)

# Универсальный обработчик теперь реализован через process_callback_query выше

@app.on_message(filters.command("monitorings"))
async def monitorings_handler(client, message):
    await monitorings_command(client, message)

# После инициализации app:
# app.add_handler(CallbackQueryHandler(add_session_callback, filters.regex("^add_session$")))
# app.add_handler(CallbackQueryHandler(assign_session_callback, filters.regex("^assign_session$")))
# app.add_handler(CallbackQueryHandler(select_session_callback, filters.regex("^select_session:(.+)$")))
# app.add_handler(CallbackQueryHandler(assign_task_callback, filters.regex("^assign_task:(.+):(.+)$")))
# app.add_handler(CallbackQueryHandler(delete_session_callback, filters.regex("^delete_session$")))
# app.add_handler(CallbackQueryHandler(confirm_delete_callback, filters.regex("^confirm_delete:(.+)$")))
# app.add_handler(CallbackQueryHandler(delete_confirmed_callback, filters.regex("^delete_confirmed:(.+)$")))
# app.add_handler(CallbackQueryHandler(cancel_session_action_callback, filters.regex("^cancel_session_action$")))
# app.add_handler(CallbackQueryHandler(resend_code_callback, filters.regex("^resend_code:(.+)$")))

# Добавляю catch-all обработчик последним
# async def catch_all_callback_handler(client, callback_query):
#     await callback_query.answer("Неизвестное действие", show_alert=True)
# app.add_handler(CallbackQueryHandler(catch_all_callback_handler))

async def setup_bot_commands():
    """Установка команд меню при запуске бота"""
    try:
        commands = [
            BotCommand("start", "🏠 Главное меню"),
            BotCommand("reactions", "⭐ Управление реакциями"),
            BotCommand("sessions", "🔐 Управление сессиями"),
            BotCommand("monitorings", "📊 Статус задач"),
            BotCommand("public_groups", "📢 Публичные группы"),
        ]
        await app.set_bot_commands(commands)
        logger.info("✅ Команды меню установлены при запуске бота")
    except Exception as e:
        logger.error(f"❌ Ошибка при установке команд меню: {e}")



def run_bot():
    """Функция для запуска бота извне"""
    try:
        # Запускаем бота стандартным способом Pyrogram
        app.run()
    except KeyboardInterrupt:
        logger.info("Программа завершена пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        sys.exit(1)

# Запускаем только при прямом вызове файла
if __name__ == "__main__":
    run_bot() 