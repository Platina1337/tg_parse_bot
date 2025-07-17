import logging
import asyncio
from typing import Dict, List, Optional, Any
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.api_client import api_client
from bot.states import user_states
from bot.config import ADMIN_IDS

logger = logging.getLogger(__name__)

# States for reaction management
FSM_REACTION_CHANNEL = "reaction_channel_input"
FSM_REACTION_MESSAGE = "reaction_message_input"
FSM_REACTION_EMOJI = "reaction_emoji_input"
FSM_REACTION_SESSIONS = "reaction_sessions_input"

# Dictionary to store reaction data during the flow
reaction_data = {}

async def reactions_command(client: Client, message: Message):
    """Handler for /reactions command"""
    user_id = message.from_user.id
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await message.reply("You don't have permission to manage reactions.")
        return
    
    # Create keyboard
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add reaction", callback_data="add_reaction")],
        [InlineKeyboardButton("📊 View available sessions", callback_data="view_sessions")]
    ])
    
    await message.reply("🎭 **Reaction Management**\n\nUse reactions to interact with messages using multiple accounts.", reply_markup=keyboard)

@Client.on_callback_query(filters.regex("^add_reaction$"))
async def add_reaction_callback(client, callback_query):
    """Callback for adding a reaction"""
    user_id = callback_query.from_user.id
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage reactions.", show_alert=True)
        return
    
    # Set state for user
    user_states[user_id] = {"state": FSM_REACTION_CHANNEL}
    reaction_data[user_id] = {}
    
    await callback_query.message.reply("Enter the channel ID or username (e.g., @channel or -100123456789):")
    await callback_query.answer()

@Client.on_callback_query(filters.regex("^view_sessions$"))
async def view_sessions_callback(client, callback_query):
    """Callback for viewing available sessions"""
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
        assignments = response.get("assignments", {})
        
        # Format message
        text = "📱 **Available Sessions**\n\n"
        
        if sessions:
            for session in sessions:
                text += f"- `{session}`\n"
        else:
            text += "No sessions available.\n"
        
        text += "\n**Reaction Sessions:**\n"
        reaction_sessions = assignments.get("reactions", [])
        if reaction_sessions:
            for session in reaction_sessions:
                text += f"- `{session}`\n"
        else:
            text += "No sessions assigned for reactions.\n"
        
        # Create keyboard
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Assign session for reactions", callback_data="assign_reaction_session")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_reactions")]
        ])
        
        await callback_query.edit_message_text(text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error in view_sessions_callback: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

@Client.on_callback_query(filters.regex("^assign_reaction_session$"))
async def assign_reaction_session_callback(client, callback_query):
    """Callback for assigning a session for reactions"""
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
        assignments = response.get("assignments", {})
        reaction_sessions = assignments.get("reactions", [])
        
        if not sessions:
            await callback_query.answer("No sessions available.", show_alert=True)
            return
        
        # Create keyboard with sessions
        keyboard = []
        for session in sessions:
            # Show if session is already assigned for reactions
            is_assigned = session in reaction_sessions
            button_text = f"{session} {'✅' if is_assigned else ''}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"toggle_reaction_session:{session}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_sessions")])
        
        await callback_query.edit_message_text(
            "Select sessions to use for reactions (✅ = active):",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error in assign_reaction_session_callback: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

@Client.on_callback_query(filters.regex("^toggle_reaction_session:(.+)$"))
async def toggle_reaction_session_callback(client, callback_query):
    """Callback for toggling a session for reactions"""
    user_id = callback_query.from_user.id
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage sessions.", show_alert=True)
        return
    
    # Get session name from callback data
    session_name = callback_query.data.split(":", 1)[1]
    
    # Get current assignments
    try:
        response = await api_client.list_sessions()
        if not response.get("success", False):
            await callback_query.answer(f"Error: {response.get('error', 'Unknown error')}", show_alert=True)
            return
        
        assignments = response.get("assignments", {})
        reaction_sessions = assignments.get("reactions", [])
        
        # Toggle session
        if session_name in reaction_sessions:
            # Remove session
            response = await api_client.remove_assignment("reactions", session_name)
            if not response.get("success", False):
                await callback_query.answer(f"Error: {response.get('error', 'Unknown error')}", show_alert=True)
                return
            await callback_query.answer(f"Session {session_name} removed from reactions")
        else:
            # Add session
            response = await api_client.assign_session("reactions", session_name)
            if not response.get("success", False):
                await callback_query.answer(f"Error: {response.get('error', 'Unknown error')}", show_alert=True)
                return
            await callback_query.answer(f"Session {session_name} assigned to reactions")
        
        # Refresh the list
        await assign_reaction_session_callback(client, callback_query)
    except Exception as e:
        logger.error(f"Error in toggle_reaction_session_callback: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

@Client.on_callback_query(filters.regex("^back_to_sessions$"))
async def back_to_sessions_callback(client, callback_query):
    """Callback for going back to sessions view"""
    await view_sessions_callback(client, callback_query)

@Client.on_callback_query(filters.regex("^back_to_reactions$"))
async def back_to_reactions_callback(client, callback_query):
    """Callback for going back to reactions menu"""
    user_id = callback_query.from_user.id
    
    # Reset state
    if user_id in user_states:
        user_states[user_id] = {}
    
    # Create keyboard
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add reaction", callback_data="add_reaction")],
        [InlineKeyboardButton("📊 View available sessions", callback_data="view_sessions")]
    ])
    
    await callback_query.edit_message_text(
        "🎭 **Reaction Management**\n\nUse reactions to interact with messages using multiple accounts.",
        reply_markup=keyboard
    )

@Client.on_callback_query(filters.regex("^select_reaction:(.+)$"))
async def select_reaction_callback(client, callback_query):
    """Callback for selecting a reaction emoji"""
    user_id = callback_query.from_user.id
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage reactions.", show_alert=True)
        return
    
    # Get reaction from callback data
    reaction = callback_query.data.split(":", 1)[1]
    
    # Store reaction in reaction_data
    reaction_data[user_id]["reaction"] = reaction
    
    # Get list of sessions
    try:
        response = await api_client.list_sessions()
        if not response.get("success", False):
            await callback_query.answer(f"Error: {response.get('error', 'Unknown error')}", show_alert=True)
            return
        
        assignments = response.get("assignments", {})
        reaction_sessions = assignments.get("reactions", [])
        
        if not reaction_sessions:
            await callback_query.answer("No sessions assigned for reactions.", show_alert=True)
            
            # Ask if user wants to use default session
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Use default session", callback_data="use_default_session")],
                [InlineKeyboardButton("Assign sessions", callback_data="assign_reaction_session")],
                [InlineKeyboardButton("Cancel", callback_data="cancel_reaction")]
            ])
            
            await callback_query.edit_message_text(
                "No sessions are assigned for reactions. What would you like to do?",
                reply_markup=keyboard
            )
            return
        
        # Confirm reaction
        chat_id = reaction_data[user_id]["chat_id"]
        message_id = reaction_data[user_id]["message_id"]
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm", callback_data="confirm_reaction")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_reaction")]
        ])
        
        await callback_query.edit_message_text(
            f"Add reaction {reaction} to message {message_id} in chat {chat_id} using {len(reaction_sessions)} sessions?",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Error in select_reaction_callback: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

@Client.on_callback_query(filters.regex("^use_default_session$"))
async def use_default_session_callback(client, callback_query):
    """Callback for using default session"""
    user_id = callback_query.from_user.id
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage reactions.", show_alert=True)
        return
    
    # Confirm reaction with default session
    chat_id = reaction_data[user_id]["chat_id"]
    message_id = reaction_data[user_id]["message_id"]
    reaction = reaction_data[user_id]["reaction"]
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm_reaction_default")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_reaction")]
    ])
    
    await callback_query.edit_message_text(
        f"Add reaction {reaction} to message {message_id} in chat {chat_id} using default session?",
        reply_markup=keyboard
    )

@Client.on_callback_query(filters.regex("^confirm_reaction$"))
async def confirm_reaction_callback(client, callback_query):
    """Callback for confirming reaction"""
    user_id = callback_query.from_user.id
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage reactions.", show_alert=True)
        return
    
    # Add reaction
    try:
        chat_id = reaction_data[user_id]["chat_id"]
        message_id = reaction_data[user_id]["message_id"]
        reaction = reaction_data[user_id]["reaction"]
        
        response = await api_client.add_reaction(chat_id, message_id, reaction)
        
        if not response.get("success", False):
            await callback_query.answer(f"Error: {response.get('error', 'Unknown error')}", show_alert=True)
            return
        
        # Get results
        results = response.get("results", {})
        summary = response.get("summary", {})
        
        # Format message
        text = f"✅ Reaction {reaction} added to message {message_id} in chat {chat_id}\n\n"
        text += f"Total sessions: {summary.get('total', 0)}\n"
        text += f"Success: {summary.get('success', 0)}\n"
        text += f"Error: {summary.get('error', 0)}\n\n"
        
        text += "**Details:**\n"
        for session, status in results.items():
            text += f"- `{session}`: {status}\n"
        
        # Create keyboard
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Add another reaction", callback_data="add_reaction")],
            [InlineKeyboardButton("🔙 Back to menu", callback_data="back_to_reactions")]
        ])
        
        await callback_query.edit_message_text(text, reply_markup=keyboard)
        
        # Clean up reaction_data
        if user_id in reaction_data:
            del reaction_data[user_id]
    except Exception as e:
        logger.error(f"Error in confirm_reaction_callback: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

@Client.on_callback_query(filters.regex("^confirm_reaction_default$"))
async def confirm_reaction_default_callback(client, callback_query):
    """Callback for confirming reaction with default session"""
    user_id = callback_query.from_user.id
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback_query.answer("You don't have permission to manage reactions.", show_alert=True)
        return
    
    # Add reaction
    try:
        chat_id = reaction_data[user_id]["chat_id"]
        message_id = reaction_data[user_id]["message_id"]
        reaction = reaction_data[user_id]["reaction"]
        
        # Use empty session_names to use default session
        response = await api_client.add_reaction(chat_id, message_id, reaction, session_names=[])
        
        if not response.get("success", False):
            await callback_query.answer(f"Error: {response.get('error', 'Unknown error')}", show_alert=True)
            return
        
        # Get results
        results = response.get("results", {})
        
        # Format message
        text = f"✅ Reaction {reaction} added to message {message_id} in chat {chat_id}\n\n"
        text += "**Details:**\n"
        for session, status in results.items():
            text += f"- `{session}`: {status}\n"
        
        # Create keyboard
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Add another reaction", callback_data="add_reaction")],
            [InlineKeyboardButton("🔙 Back to menu", callback_data="back_to_reactions")]
        ])
        
        await callback_query.edit_message_text(text, reply_markup=keyboard)
        
        # Clean up reaction_data
        if user_id in reaction_data:
            del reaction_data[user_id]
    except Exception as e:
        logger.error(f"Error in confirm_reaction_default_callback: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

@Client.on_callback_query(filters.regex("^cancel_reaction$"))
async def cancel_reaction_callback(client, callback_query):
    """Callback for canceling reaction"""
    user_id = callback_query.from_user.id
    
    # Reset state
    if user_id in user_states:
        user_states[user_id] = {}
    
    # Clean up reaction_data
    if user_id in reaction_data:
        del reaction_data[user_id]
    
    # Back to reactions menu
    await back_to_reactions_callback(client, callback_query)

async def handle_reaction_text_input(client: Client, message: Message):
    """Handle text input for reaction management"""
    user_id = message.from_user.id
    text = message.text.strip()
    state = user_states.get(user_id, {}).get("state")
    
    if state == FSM_REACTION_CHANNEL:
        # Handle channel input
        chat_id = text
        
        # Store chat_id in reaction_data
        if user_id not in reaction_data:
            reaction_data[user_id] = {}
        reaction_data[user_id]["chat_id"] = chat_id
        
        # Set next state
        user_states[user_id] = {"state": FSM_REACTION_MESSAGE}
        
        await message.reply(f"Enter the message ID to add a reaction to:")
        return True
    
    elif state == FSM_REACTION_MESSAGE:
        # Handle message ID input
        try:
            message_id = int(text)
            
            # Store message_id in reaction_data
            reaction_data[user_id]["message_id"] = message_id
            
            # Get available reactions
            response = await api_client.get_available_reactions()
            
            if not response.get("success", False):
                await message.reply(f"Error getting available reactions: {response.get('error', 'Unknown error')}")
                user_states[user_id] = {}
                return True
            
            reactions = response.get("reactions", [])
            
            # Create keyboard with reactions
            keyboard = []
            row = []
            
            for i, reaction in enumerate(reactions):
                row.append(InlineKeyboardButton(reaction, callback_data=f"select_reaction:{reaction}"))
                
                # 8 reactions per row
                if len(row) == 8:
                    keyboard.append(row)
                    row = []
            
            # Add remaining reactions
            if row:
                keyboard.append(row)
            
            # Add cancel button
            keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel_reaction")])
            
            await message.reply(
                f"Select a reaction to add to message {message_id} in chat {reaction_data[user_id]['chat_id']}:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            # Set next state
            user_states[user_id] = {"state": FSM_REACTION_EMOJI}
        except ValueError:
            await message.reply("Message ID must be a number. Please try again:")
        except Exception as e:
            logger.error(f"Error in handle_reaction_text_input (message_id): {e}")
            await message.reply(f"Error: {e}")
            user_states[user_id] = {}
        
        return True
    
    return False 