import logging
import asyncio
from typing import Dict, List, Optional, Any
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.api_client import api_client
from bot.states import user_states
from bot.config import ADMIN_IDS

logger = logging.getLogger(__name__)

# States for session management
FSM_SESSION_NAME = "session_name_input"
FSM_SESSION_PHONE = "session_phone_input"
FSM_SESSION_CODE = "session_code_input"
FSM_SESSION_PASSWORD = "session_password_input"
FSM_SESSION_TASK = "session_task_input"

# Dictionary to store authentication data during the flow
auth_data = {}

async def sessions_command(client: Client, message: Message):
    """Handler for /sessions command"""
    user_id = message.from_user.id
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await message.reply("You don't have permission to manage sessions.")
        return
    
    # Get list of sessions
    try:
        response = await api_client.list_sessions()
        if not response.get("success", False):
            await message.reply(f"Error getting sessions: {response.get('error', 'Unknown error')}")
            return
        
        sessions = response.get("sessions", [])
        assignments = response.get("assignments", {})
        
        # Format message
        text = "📱 **Session Management**\n\n"
        text += "**Available sessions:**\n"
        
        if sessions:
            for session in sessions:
                text += f"- `{session}`\n"
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
        
        await message.reply(text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error in sessions_command: {e}")
        await message.reply(f"Error: {e}")

@Client.on_callback_query(filters.regex("^add_session$"))
async def add_session_callback(client, callback_query):
    """Callback for adding a new session"""
    user_id = callback_query.from_user.id
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage sessions.", show_alert=True)
        return
    
    # Set state for user
    user_states[user_id] = {"state": FSM_SESSION_NAME}
    
    await callback_query.message.reply("Enter a name for the new session:")
    await callback_query.answer()

@Client.on_callback_query(filters.regex("^assign_session$"))
async def assign_session_callback(client, callback_query):
    """Callback for assigning a session to a task"""
    user_id = callback_query.from_user.id
    
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
            keyboard.append([InlineKeyboardButton(session, callback_data=f"select_session:{session}")])
        
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel_session_action")])
        
        await callback_query.edit_message_text(
            "Select a session to assign:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error in assign_session_callback: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

@Client.on_callback_query(filters.regex("^select_session:(.+)$"))
async def select_session_callback(client, callback_query):
    """Callback for selecting a session"""
    user_id = callback_query.from_user.id
    
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
            InlineKeyboardButton("Forwarding", callback_data=f"assign_task:forwarding:{session_name}"),
            InlineKeyboardButton("Reactions", callback_data=f"assign_task:reactions:{session_name}")
        ],
        [InlineKeyboardButton("Cancel", callback_data="cancel_session_action")]
    ])
    
    await callback_query.edit_message_text(
        f"Select a task to assign session `{session_name}` to:",
        reply_markup=keyboard
    )

@Client.on_callback_query(filters.regex("^assign_task:(.+):(.+)$"))
async def assign_task_callback(client, callback_query):
    """Callback for assigning a task to a session"""
    user_id = callback_query.from_user.id
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage sessions.", show_alert=True)
        return
    
    # Get task and session name from callback data
    parts = callback_query.data.split(":", 2)
    task = parts[1]
    session_name = parts[2]
    
    # Assign session to task
    try:
        response = await api_client.assign_session(task, session_name)
        if not response.get("success", False):
            await callback_query.answer(f"Error: {response.get('error', 'Unknown error')}", show_alert=True)
            return
        
        await callback_query.answer(f"Session {session_name} assigned to {task}!", show_alert=True)
        
        # Update message with new assignments
        await sessions_command(client, callback_query.message)
    except Exception as e:
        logger.error(f"Error in assign_task_callback: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

@Client.on_callback_query(filters.regex("^delete_session$"))
async def delete_session_callback(client, callback_query):
    """Callback for deleting a session"""
    user_id = callback_query.from_user.id
    
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

@Client.on_callback_query(filters.regex("^confirm_delete:(.+)$"))
async def confirm_delete_callback(client, callback_query):
    """Callback for confirming session deletion"""
    user_id = callback_query.from_user.id
    
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

@Client.on_callback_query(filters.regex("^delete_confirmed:(.+)$"))
async def delete_confirmed_callback(client, callback_query):
    """Callback for confirmed session deletion"""
    user_id = callback_query.from_user.id
    
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
        await sessions_command(client, callback_query.message)
    except Exception as e:
        logger.error(f"Error in delete_confirmed_callback: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

@Client.on_callback_query(filters.regex("^cancel_session_action$"))
async def cancel_session_action_callback(client, callback_query):
    """Callback for canceling session action"""
    user_id = callback_query.from_user.id
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage sessions.", show_alert=True)
        return
    
    # Reset state
    if user_id in user_states:
        user_states[user_id] = {}
    
    # Update message with sessions
    await sessions_command(client, callback_query.message)

async def handle_session_text_input(client: Client, message: Message):
    """Handle text input for session management"""
    user_id = message.from_user.id
    text = message.text.strip()
    state = user_states.get(user_id, {}).get("state")
    
    if state == FSM_SESSION_NAME:
        # Handle session name input
        session_name = text
        
        # Store session name in auth_data
        auth_data[user_id] = {"session_name": session_name}
        
        # Set next state
        user_states[user_id] = {"state": FSM_SESSION_PHONE}
        
        await message.reply(f"Enter phone number for session `{session_name}`:")
        return True
    
    elif state == FSM_SESSION_PHONE:
        # Handle phone input
        phone = text
        
        # Store phone in auth_data
        auth_data[user_id]["phone"] = phone
        
        # Add session
        try:
            session_name = auth_data[user_id]["session_name"]
            response = await api_client.add_session(session_name, phone=phone)
            
            if not response.get("success", False):
                await message.reply(f"Error adding session: {response.get('error', 'Unknown error')}")
                user_states[user_id] = {}
                return True
            
            # Send code
            response = await api_client.send_code(session_name, phone)
            
            if not response.get("success", False):
                await message.reply(f"Error sending code: {response.get('error', 'Unknown error')}")
                user_states[user_id] = {}
                return True
            
            # Store phone_code_hash in auth_data
            auth_data[user_id]["phone_code_hash"] = response.get("phone_code_hash")
            
            # Set next state
            user_states[user_id] = {"state": FSM_SESSION_CODE}
            
            await message.reply("Enter the verification code sent to your phone:")
        except Exception as e:
            logger.error(f"Error in handle_session_text_input (phone): {e}")
            await message.reply(f"Error: {e}")
            user_states[user_id] = {}
        
        return True
    
    elif state == FSM_SESSION_CODE:
        # Handle code input
        code = text
        
        # Sign in
        try:
            session_name = auth_data[user_id]["session_name"]
            phone = auth_data[user_id]["phone"]
            phone_code_hash = auth_data[user_id]["phone_code_hash"]
            
            response = await api_client.sign_in(session_name, phone, code, phone_code_hash)
            
            if not response.get("success", False):
                if response.get("needs_password", False):
                    # 2FA required
                    user_states[user_id] = {"state": FSM_SESSION_PASSWORD}
                    await message.reply("2FA is enabled. Please enter your password:")
                    return True
                
                await message.reply(f"Error signing in: {response.get('error', 'Unknown error')}")
                user_states[user_id] = {}
                return True
            
            # Success
            user_info = f"First name: {response.get('first_name', 'N/A')}\n"
            user_info += f"Last name: {response.get('last_name', 'N/A')}\n"
            user_info += f"Username: @{response.get('username', 'N/A')}"
            
            await message.reply(f"✅ Session `{session_name}` added successfully!\n\n{user_info}")
            
            # Reset state
            user_states[user_id] = {}
            
            # Clean up auth_data
            if user_id in auth_data:
                del auth_data[user_id]
        except Exception as e:
            logger.error(f"Error in handle_session_text_input (code): {e}")
            await message.reply(f"Error: {e}")
            user_states[user_id] = {}
        
        return True
    
    elif state == FSM_SESSION_PASSWORD:
        # Handle password input
        password = text
        
        # Sign in with password
        try:
            session_name = auth_data[user_id]["session_name"]
            
            response = await api_client.sign_in_with_password(session_name, password)
            
            if not response.get("success", False):
                await message.reply(f"Error signing in: {response.get('error', 'Unknown error')}")
                user_states[user_id] = {}
                return True
            
            # Success
            user_info = f"First name: {response.get('first_name', 'N/A')}\n"
            user_info += f"Last name: {response.get('last_name', 'N/A')}\n"
            user_info += f"Username: @{response.get('username', 'N/A')}"
            
            await message.reply(f"✅ Session `{session_name}` added successfully!\n\n{user_info}")
            
            # Reset state
            user_states[user_id] = {}
            
            # Clean up auth_data
            if user_id in auth_data:
                del auth_data[user_id]
        except Exception as e:
            logger.error(f"Error in handle_session_text_input (password): {e}")
            await message.reply(f"Error: {e}")
            user_states[user_id] = {}
        
        return True
    
    return False 