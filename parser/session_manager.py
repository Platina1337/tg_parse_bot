import os
import logging
from typing import Dict, List, Optional, Any, Tuple
import asyncio
from pyrogram import Client
from pyrogram.errors import AuthKeyUnregistered, AuthKeyDuplicated, SessionPasswordNeeded, PhoneCodeInvalid
from parser.database import Database
from shared.models import SessionMeta

logger = logging.getLogger(__name__)

class SessionManager:
    """Manager for multiple Telegram sessions (now DB-backed)"""
    def __init__(self, db: Database, session_dir="sessions"):
        self.db = db
        self.session_dir = session_dir
        self.clients: Dict[str, Client] = {}  # alias -> Client
        self.ensure_session_dir()

    def ensure_session_dir(self):
        os.makedirs(self.session_dir, exist_ok=True)

    async def import_sessions_from_files(self):
        await self.db.import_existing_sessions(self.session_dir)

    async def load_clients(self):
        """Загружает все активные сессии из БД и создает Pyrogram Client для каждой."""
        sessions = await self.db.get_all_sessions()
        for session in sessions:
            if session.is_active:
                self.clients[session.alias] = Client(
                    name=session.session_path,
                    api_id=session.api_id,
                    api_hash=session.api_hash,
                    phone_number=session.phone if session.phone else None
                )

    async def add_account(self, alias: str, api_id: int, api_hash: str, phone: str) -> Dict[str, Any]:
        session_path = os.path.join(self.session_dir, alias)
        session = SessionMeta(
            id=0,
            alias=alias,
            api_id=api_id,
            api_hash=api_hash,
            phone=phone,
            session_path=session_path,
            is_active=True
        )
        session_id = await self.db.create_session(session)
        await self.load_clients()
        return {"success": True, "session_id": session_id, "alias": alias}

    async def get_all_sessions(self) -> List[SessionMeta]:
        return await self.db.get_all_sessions()

    async def assign_task(self, alias: str, task: str) -> Dict[str, Any]:
        session = await self.db.get_session_by_alias(alias)
        if not session:
            return {"success": False, "error": "Session not found"}
        await self.db.update_session(session.id, assigned_task=task)
        return {"success": True, "alias": alias, "task": task}

    async def delete_session(self, alias: str) -> Dict[str, Any]:
        session = await self.db.get_session_by_alias(alias)
        if not session:
            return {"success": False, "error": "Session not found"}
        await self.db.delete_session(session.id)
        if alias in self.clients:
            del self.clients[alias]
        return {"success": True, "alias": alias}

    async def get_client(self, alias: str) -> Optional[Client]:
        if alias not in self.clients:
            await self.load_clients()
        return self.clients.get(alias)

    async def send_code(self, alias: str, phone: str) -> Dict[str, Any]:
        """Send authentication code to the phone number"""
        client = self.clients.get(alias)
        if not client:
            return {"success": False, "error": "Session not found"}
        
        try:
            await client.connect()
            sent_code = await client.send_code(phone)
            return {
                "success": True,
                "phone_code_hash": sent_code.phone_code_hash
            }
        except Exception as e:
            logger.error(f"Error sending code: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if client.is_connected:
                await client.disconnect()
    
    async def sign_in(self, alias: str, phone: str, code: str, phone_code_hash: str) -> Dict[str, Any]:
        """Sign in with the received code"""
        client = self.clients.get(alias)
        if not client:
            return {"success": False, "error": "Session not found"}
        
        try:
            await client.connect()
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            is_authorized = await client.is_user_authorized()
            
            if is_authorized:
                me = await client.get_me()
                return {
                    "success": True,
                    "user_id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username
                }
            else:
                return {"success": False, "error": "Failed to authorize"}
        except SessionPasswordNeeded:
            return {"success": False, "error": "2FA required", "needs_password": True}
        except PhoneCodeInvalid:
            return {"success": False, "error": "Invalid code"}
        except Exception as e:
            logger.error(f"Error signing in: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if client.is_connected:
                await client.disconnect()
    
    async def sign_in_with_password(self, alias: str, password: str) -> Dict[str, Any]:
        """Sign in with 2FA password"""
        client = self.clients.get(alias)
        if not client:
            return {"success": False, "error": "Session not found"}
        
        try:
            await client.connect()
            await client.check_password(password)
            is_authorized = await client.is_user_authorized()
            
            if is_authorized:
                me = await client.get_me()
                return {
                    "success": True,
                    "user_id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username
                }
            else:
                return {"success": False, "error": "Failed to authorize"}
        except Exception as e:
            logger.error(f"Error signing in with password: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if client.is_connected:
                await client.disconnect()
    
    async def assign_session(self, alias: str, task: str) -> Dict[str, Any]:
        """Assign a session for a specific task"""
        client = self.clients.get(alias)
        if not client:
            return {"success": False, "error": "Session not found"}
        
        # The original code had reaction_sessions, which is removed.
        # Assuming the intent was to update the assigned_task in the DB.
        await self.db.update_session(client.name.replace(self.session_dir, ""), assigned_task=task)
        
        return {
            "success": True,
            "alias": alias,
            "task": task,
            "assignments": self.get_assignments()
        }
    
    async def remove_assignment(self, alias: str, task: str) -> Dict[str, Any]:
        """Remove a session assignment"""
        # The original code had reaction_sessions, which is removed.
        # Assuming the intent was to update the assigned_task in the DB.
        await self.db.update_session(self.clients[alias].name.replace(self.session_dir, ""), assigned_task="default")
        
        return {
            "success": True,
            "alias": alias,
            "assignments": self.get_assignments()
        }
    
    async def get_client(self, alias: str) -> Optional[Client]:
        if alias not in self.clients:
            await self.load_clients()
        return self.clients.get(alias)
    
    async def start_session(self, alias: str) -> bool:
        """Start a specific session"""
        client = self.clients.get(alias)
        if client:
            if not client.is_connected:
                try:
                    await client.start()
                    return True
                except Exception as e:
                    logger.error(f"Error starting session {alias}: {e}")
        return False
    
    async def stop_session(self, alias: str) -> bool:
        """Stop a specific session"""
        client = self.clients.get(alias)
        if client:
            if client.is_connected:
                try:
                    await client.stop()
                    return True
                except Exception as e:
                    logger.error(f"Error stopping session {alias}: {e}")
        return False
    
    async def start_all(self) -> Dict[str, str]:
        """Start all client sessions"""
        results = {}
        for alias, client in self.clients.items():
            try:
                if not client.is_connected:
                    await client.start()
                    results[alias] = "success"
                else:
                    results[alias] = "already_running"
            except Exception as e:
                logger.error(f"Error starting session {alias}: {e}")
                results[alias] = f"error: {str(e)}"
        return results
    
    async def stop_all(self) -> Dict[str, str]:
        """Stop all client sessions"""
        results = {}
        for alias, client in self.clients.items():
            try:
                if client.is_connected:
                    await client.stop()
                    results[alias] = "success"
                else:
                    results[alias] = "already_stopped"
            except Exception as e:
                logger.error(f"Error stopping session {alias}: {e}")
                results[alias] = f"error: {str(e)}"
        return results
    
    async def add_reaction(self, chat_id: str, message_id: int, reaction: str, session_names: Optional[List[str]] = None) -> Dict[str, str]:
        """Add reaction to a message using all or specific accounts"""
        results = {}
        sessions_to_use = session_names if session_names else list(self.clients.keys())
        
        for alias in sessions_to_use:
            client = self.clients.get(alias)
            if client:
                try:
                    # Check if client is connected
                    if not client.is_connected:
                        await client.start()
                    
                    # Add reaction
                    await client.send_reaction(
                        chat_id=chat_id,
                        message_id=message_id,
                        emoji=reaction
                    )
                    results[alias] = "success"
                except Exception as e:
                    logger.error(f"Error adding reaction with session {alias}: {e}")
                    results[alias] = f"error: {str(e)}"
        
        return results
    
    async def get_all_sessions(self) -> List[SessionMeta]:
        """Get list of all sessions"""
        return await self.db.get_all_sessions()
    
    def get_assignments(self) -> Dict[str, Any]:
        """Get current session assignments"""
        # This method needs to be updated to fetch assignments from the DB
        # For now, it will return a placeholder or raise an error
        # The original code had reaction_sessions, which is removed.
        # Assuming the intent was to fetch assigned_task from the DB.
        return {"parsing": "default", "monitoring": "default", "forwarding": "default"}
    
    async def check_session_status(self, alias: str) -> Dict[str, Any]:
        """Check if a session is valid and get user info"""
        client = self.clients.get(alias)
        if not client:
            return {"success": False, "error": "Session not found"}
        
        try:
            if not client.is_connected:
                await client.connect()
            
            is_authorized = await client.is_user_authorized()
            if is_authorized:
                me = await client.get_me()
                return {
                    "success": True,
                    "is_authorized": True,
                    "user_id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username
                }
            else:
                return {"success": True, "is_authorized": False}
        except (AuthKeyUnregistered, AuthKeyDuplicated) as e:
            logger.error(f"Auth key error for session {alias}: {e}")
            return {"success": False, "error": "Invalid session", "needs_reauth": True}
        except Exception as e:
            logger.error(f"Error checking session {alias}: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if client.is_connected:
                await client.disconnect()
    
    async def delete_session(self, alias: str) -> Dict[str, Any]:
        """Delete a session"""
        session = await self.db.get_session_by_alias(alias)
        if not session:
            return {"success": False, "error": "Session not found"}
        
        try:
            # Stop the client if it's running
            client = self.clients[alias]
            if client.is_connected:
                await client.stop()
            
            # Remove from clients dictionary
            del self.clients[alias]
            
            # Remove from assignments
            await self.db.update_session(session.id, assigned_task="default")
            
            # Try to delete the session file
            session_path = os.path.join(self.session_dir, alias + ".session")
            if os.path.exists(session_path):
                os.remove(session_path)
            
            return {"success": True, "message": f"Session {alias} deleted"}
        except Exception as e:
            logger.error(f"Error deleting session {alias}: {e}")
            return {"success": False, "error": str(e)} 