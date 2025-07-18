import logging
import asyncio
from typing import Dict, List, Optional, Any
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.api_client import api_client
from bot.states import user_states
from bot.config import ADMIN_IDS
from traceback import format_exc
import re

logger = logging.getLogger(__name__)

# States for session management
FSM_SESSION_NAME = "session_name_input"
FSM_SESSION_API_ID = "session_api_id_input"
FSM_SESSION_API_HASH = "session_api_hash_input"
FSM_SESSION_PHONE = "session_phone_input"
FSM_SESSION_CODE = "session_code"
FSM_SESSION_PASSWORD = "session_password_input"
FSM_SESSION_TASK = "session_task_input"

# Dictionary to store authentication data during the flow
auth_data = {}

async def sessions_command(client: Client, message_or_query):
    """Handler for /sessions command or callback"""
    # Определяем user_id и способ отправки
    if hasattr(message_or_query, 'from_user'):
        user_id = int(message_or_query.from_user.id)
        send_func = lambda text, **kwargs: client.send_message(user_id, text, **kwargs)
    elif hasattr(message_or_query, 'message') and hasattr(message_or_query.message, 'chat'):
        user_id = int(message_or_query.message.chat.id)
        send_func = lambda text, **kwargs: client.send_message(user_id, text, **kwargs)
    else:
        logger.error("[SESSIONS_COMMAND] Не удалось определить user_id")
        return
    logger.info(f"[SESSIONS_COMMAND] user_id={user_id} (from {type(message_or_query)}), ADMIN_IDS={ADMIN_IDS}")
    if user_id not in ADMIN_IDS:
        await send_func("You don't have permission to manage sessions.")
        return
    
    # Get list of sessions
    try:
        response = await api_client.list_sessions()
        if not response.get("success", False):
            await send_func(f"Error getting sessions: {response.get('error', 'Unknown error')}")
            return
        
        sessions = response.get("sessions", [])
        assignments = response.get("assignments", {})
        
        # Format message
        text = "📱 **Session Management**\n\n"
        text += "**Available sessions:**\n"
        if sessions:
            for session in sessions:
                alias = session.get("alias", "")
                phone = session.get("phone", "")
                is_active = session.get("is_active", False)
                created_at = session.get("created_at", "")
                text += f"- <b>{alias}</b> | <code>{phone}</code> | {'🟢' if is_active else '🔴'} | {created_at}\n"
        else:
            text += "No sessions available.\n"
        
        text += "\n**Current assignments:**\n"
        for task, session in assignments.items():
            if task == "reactions":
                reaction_sessions = assignments.get("reactions", [])
                text += f"- Reactions: {', '.join(reaction_sessions) if reaction_sessions else 'none'}\n"
            else:
                text += f"- {task.capitalize()}: `{session}`\n"
        
        # Create keyboard
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add session", callback_data="add_session")],
            [InlineKeyboardButton("🔄 Assign session", callback_data="assign_session")],
            [InlineKeyboardButton("👍 Add reaction", callback_data="add_reaction")],
            [InlineKeyboardButton("❌ Delete session", callback_data="delete_session")]
        ])
        
        await send_func(text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error in sessions_command: {e}")
        await send_func(f"Error: {e}")

async def add_session_callback(client, callback_query):
    """Callback for adding a new session"""
    user_id = int(callback_query.from_user.id)
    logger.info(f"[ADD_SESSION_CALLBACK] user_id(from callback_query)={user_id}, ADMIN_IDS={ADMIN_IDS}")
    try:
        # Check if user is admin
        if user_id not in ADMIN_IDS:
            await callback_query.answer("You don't have permission to manage sessions.", show_alert=True)
            return
        
        # Set state for user
        user_states[user_id] = {"state": FSM_SESSION_NAME}
        
        await callback_query.message.reply("Enter a name for the new session:")
        await callback_query.answer()
    except Exception as e:
        logger.error(f"[CALLBACK_HANDLER][add_session] Ошибка: {e}\n{format_exc()}")
        await callback_query.answer(f"Ошибка: {e}", show_alert=True)

async def assign_session_callback(client, callback_query):
    """Callback for assigning a session to a task"""
    user_id = int(callback_query.from_user.id)
    logger.info(f"[ASSIGN_SESSION_CALLBACK] user_id(from callback_query)={user_id}, ADMIN_IDS={ADMIN_IDS}")
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage sessions.", show_alert=True)
        return
    
    # Get list of sessions
    try:
        response = await api_client.list_sessions()
        if not response.get("success", False):
            await callback_query.answer(f"Error: {response.get('error', 'Unknown error')}", show_alert=True)
            return
        
        sessions = response.get("sessions", [])
        
        if not sessions:
            await callback_query.answer("No sessions available.", show_alert=True)
            return
        
        # Create keyboard with sessions
        keyboard = []
        for session in sessions:
            alias = session.get("alias", "")
            safe_alias = re.sub(r'[^a-zA-Z0-9_-]', '', alias)[:32]
            keyboard.append([InlineKeyboardButton(alias, callback_data=f"select_session:{safe_alias}")])
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel_session_action")])
        await callback_query.edit_message_text(
            "Select a session to assign:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error in assign_session_callback: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

async def select_session_callback(client, callback_query):
    """Callback for selecting a session"""
    user_id = int(callback_query.from_user.id)
    logger.info(f"[SELECT_SESSION_CALLBACK] user_id(from callback_query)={user_id}, ADMIN_IDS={ADMIN_IDS}")
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage sessions.", show_alert=True)
        return
    
    # Get session name from callback data
    session_name = callback_query.data.split(":", 1)[1]
    
    # Store session name in user_states
    user_states[user_id] = {"state": FSM_SESSION_TASK, "session_name": session_name}
    
    # Create keyboard with tasks
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Parsing", callback_data=f"assign_task:parsing:{session_name}"),
            InlineKeyboardButton("Monitoring", callback_data=f"assign_task:monitoring:{session_name}")
        ],
        [
            InlineKeyboardButton("Reactions", callback_data=f"assign_task:reactions:{session_name}")
        ],
        [InlineKeyboardButton("Cancel", callback_data="cancel_session_action")]
    ])
    
    await callback_query.edit_message_text(
        f"Select a task to assign session `{session_name}` to:",
        reply_markup=keyboard
    )

async def assign_task_callback(client, callback_query):
    """Callback for assigning a task to a session"""
    user_id = int(callback_query.from_user.id)
    logger.info(f"[ASSIGN_TASK_CALLBACK] user_id(from callback_query)={user_id}, ADMIN_IDS={ADMIN_IDS}, callback_data={callback_query.data}")
    parts = callback_query.data.split(":", 2)
    logger.info(f"[ASSIGN_TASK_CALLBACK] parts={parts}")
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        logger.warning(f"[ASSIGN_TASK_CALLBACK][PERMISSION_DENIED] user_id={user_id} not in ADMIN_IDS={ADMIN_IDS}")
        await callback_query.answer("You don't have permission to manage sessions.", show_alert=True)
        return
    
    # Get task and session name from callback data
    task = parts[1]
    session_name = parts[2]
    
    # Assign session to task
    try:
        response = await api_client.assign_session(task, session_name)
        if not response.get("success", False):
            await callback_query.answer(f"Error: {response.get('error', 'Unknown error')}", show_alert=True)
            return
        
        await callback_query.answer(f"Session {session_name} assigned to {task}!", show_alert=True)
        
        # Теперь вызываем sessions_command с callback_query, чтобы user_id был корректный
        await sessions_command(client, callback_query)
    except Exception as e:
        logger.error(f"Error in assign_task_callback: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

async def delete_session_callback(client, callback_query):
    """Callback for deleting a session"""
    user_id = int(callback_query.from_user.id)
    logger.info(f"[DELETE_SESSION_CALLBACK] user_id(from callback_query)={user_id}, ADMIN_IDS={ADMIN_IDS}")
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage sessions.", show_alert=True)
        return
    
    # Get list of sessions
    try:
        response = await api_client.list_sessions()
        if not response.get("success", False):
            await callback_query.answer(f"Error: {response.get('error', 'Unknown error')}", show_alert=True)
            return
        
        sessions = response.get("sessions", [])
        
        if not sessions:
            await callback_query.answer("No sessions available.", show_alert=True)
            return
        
        # Create keyboard with sessions
        keyboard = []
        for session in sessions:
            keyboard.append([InlineKeyboardButton(session, callback_data=f"confirm_delete:{session}")])
        
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel_session_action")])
        
        await callback_query.edit_message_text(
            "Select a session to delete:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error in delete_session_callback: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

async def confirm_delete_callback(client, callback_query):
    """Callback for confirming session deletion"""
    user_id = int(callback_query.from_user.id)
    logger.info(f"[CONFIRM_DELETE_CALLBACK] user_id(from callback_query)={user_id}, ADMIN_IDS={ADMIN_IDS}")
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage sessions.", show_alert=True)
        return
    
    # Get session name from callback data
    session_name = callback_query.data.split(":", 1)[1]
    
    # Confirm deletion
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes, delete", callback_data=f"delete_confirmed:{session_name}"),
            InlineKeyboardButton("No, cancel", callback_data="cancel_session_action")
        ]
    ])
    
    await callback_query.edit_message_text(
        f"Are you sure you want to delete session `{session_name}`?",
        reply_markup=keyboard
    )

async def delete_confirmed_callback(client, callback_query):
    """Callback for confirmed session deletion"""
    user_id = int(callback_query.from_user.id)
    logger.info(f"[DELETE_CONFIRMED_CALLBACK] user_id(from callback_query)={user_id}, ADMIN_IDS={ADMIN_IDS}")
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage sessions.", show_alert=True)
        return
    
    # Get session name from callback data
    session_name = callback_query.data.split(":", 1)[1]
    
    # Delete session
    try:
        response = await api_client.delete_session(session_name)
        if not response.get("success", False):
            await callback_query.answer(f"Error: {response.get('error', 'Unknown error')}", show_alert=True)
            return
        
        await callback_query.answer(f"Session {session_name} deleted!", show_alert=True)
        
        # Update message with new sessions
        await sessions_command(client, callback_query)
    except Exception as e:
        logger.error(f"Error in delete_confirmed_callback: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

async def resend_code_callback(client, callback_query):
    """Callback for resending authentication code"""
    user_id = int(callback_query.from_user.id)
    logger.info(f"[RESEND_CODE_CALLBACK] user_id(from callback_query)={user_id}, ADMIN_IDS={ADMIN_IDS}")
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage sessions.", show_alert=True)
        return
    
    # Get session name from callback data
    session_name = callback_query.data.split(":", 1)[1]
    
    # Get phone from auth_data
    phone = auth_data.get(user_id, {}).get("phone")
    if not phone:
        await callback_query.answer("Phone number not found. Please start over.", show_alert=True)
        return
    
    try:
        # Send new code
        response = await api_client.send_code(session_name, phone)
        if response.get("success"):
            auth_data[user_id]["phone_code_hash"] = response["phone_code_hash"]
            user_states[user_id]["state"] = FSM_SESSION_CODE
            await callback_query.message.reply("📱 Новый код отправлен! Введите код из Telegram:")
            await callback_query.answer("Новый код отправлен!")
        else:
            await callback_query.answer(f"Ошибка отправки кода: {response.get('error')}", show_alert=True)
    except Exception as e:
        logger.error(f"Error in resend_code_callback: {e}")
        await callback_query.answer(f"Ошибка: {e}", show_alert=True)

async def cancel_session_action_callback(client, callback_query):
    """Callback for canceling session action"""
    user_id = int(callback_query.from_user.id)
    logger.info(f"[CANCEL_SESSION_ACTION_CALLBACK] user_id(from callback_query)={user_id}, ADMIN_IDS={ADMIN_IDS}")
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage sessions.", show_alert=True)
        return
    
    # Reset state
    if user_id in user_states:
        user_states[user_id] = {}
    
    # Update message with sessions
    await sessions_command(client, callback_query)

async def handle_session_text_input(client: Client, message: Message):
    """Handle text input for session management"""
    user_id = int(message.from_user.id)
    logger.info(f"[HANDLE_SESSION_TEXT_INPUT] user_id(from message)={user_id}, ADMIN_IDS={ADMIN_IDS}")
    text = message.text.strip()
    
    logger.info(f"[SESSION_FSM] Processing message for user {user_id}: '{text}'")
    
    # Проверяем, что это не команда
    if text.startswith('/'):
        logger.info(f"[SESSION_FSM] Skipping command: {text}")
        return False
    
    # Проверяем, что у пользователя есть активное состояние FSM
    if user_id not in user_states or not user_states[user_id].get("state"):
        logger.info(f"[SESSION_FSM] No active FSM state for user {user_id}")
        return False
    
    state = user_states[user_id].get("state")
    logger.info(f"[SESSION_FSM] User {user_id} state: {state}")
    logger.info(f"[SESSION_FSM] Full user_states[{user_id}]: {user_states[user_id]}")
    try:
        logger.info(f"[SESSION_FSM] Processing state {state} for user {user_id}")
        if state == FSM_SESSION_NAME:
            # Handle session name input
            session_name = text
            # Store session name in auth_data
            auth_data[user_id] = {"session_name": session_name}
            # Инициализируем интерактивную сессию на парсере
            try:
                response = await api_client.init_session(session_name)
                if response.get("success"):
                    await message.reply(f"Название сессии сохранено: {session_name}\n\nДальнейшая авторизация (API ID, API Hash, номер, код) производится в терминале парсера. Запустите парсер и следуйте инструкциям в консоли.")
                else:
                    await message.reply(f"Ошибка инициализации сессии на парсере: {response.get('error')}")
            except Exception as e:
                await message.reply(f"Ошибка инициализации сессии: {e}")
            user_states[user_id] = {}
            if user_id in auth_data:
                del auth_data[user_id]
            return True
        # Удаляю обработку FSM_SESSION_API_ID и FSM_SESSION_API_HASH
        return False
    except Exception as e:
        logger.error(f"[CALLBACK_HANDLER][handle_session_text_input] Ошибка: {e}\n{format_exc()}")
        await message.reply(f"Внутренняя ошибка: {e}")
        user_states[user_id] = {}
        return True 