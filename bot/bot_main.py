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
from pyrogram.errors import RPCError
from pyrogram.types import BotCommand
from bot.config import config
from bot.states import user_states
from bot.handlers import start_command, text_handler, forwarding_callback_handler, monitorings_command, check_tasks_status_callback
from bot.navigation_manager import navigation_menu_handler, navigation_text_handler
import bot.handlers  # Для регистрации всех callback-обработчиков
from bot.session_handlers import (
    sessions_command,  # добавляю импорт этой функции обратно
    add_session_callback,
    assign_session_callback,
    select_session_callback,
    assign_task_callback,
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
    else:
        logger.info(f"[TEXT_HANDLER] Calling text_handler for user {user_id}")
        await text_handler(client, message)

# Удаляю глобальный обработчик @app.on_callback_query()
# @app.on_callback_query()
# async def callback_query_handler(client, callback_query):
#     try:
#         handled = await process_callback_query(client, callback_query)
#         if not handled:
#             await callback_query.answer("Неизвестное действие", show_alert=True)
#     except RPCError as e:
#         logger.error(f"[CALLBACK_HANDLER] RPCError: {e}\n{traceback.format_exc()}")
#         await callback_query.answer("Ошибка Telegram API", show_alert=True)
#     except Exception as e:
#         logger.error(f"[CALLBACK_HANDLER] Ошибка типа {type(e)}: {e}\n{traceback.format_exc()}")
#         await callback_query.answer(f"Внутренняя ошибка: {e}", show_alert=True)

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

# Универсальный обработчик для всех остальных callback_data (должен быть последним)
@app.on_callback_query()
async def universal_callback_handler(client, callback_query):
    logger.info(f"[UNIVERSAL_CALLBACK_HANDLER] Неизвестное действие | user_id={callback_query.from_user.id} | callback_data={callback_query.data}")
    await callback_query.answer("Неизвестное действие", show_alert=True)

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