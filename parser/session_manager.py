import os
import logging
from typing import Dict, List, Optional, Any, Tuple
import asyncio
from pyrogram import Client
from pyrogram.errors import AuthKeyUnregistered, AuthKeyDuplicated, SessionPasswordNeeded, PhoneCodeInvalid
from .database import Database
from shared.models import SessionMeta
from .config import config

logger = logging.getLogger(__name__)

class SessionManager:
    """Manager for multiple Telegram sessions (now DB-backed)"""
    def __init__(self, db: Database, session_dir=None):
        self.db = db
        self.session_dir = session_dir or config.SESSIONS_DIR
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
                # Используем только alias для имени файла сессии
                alias = os.path.basename(session.alias if hasattr(session, 'alias') else session.session_path)
                # Используем абсолютный путь для session_dir
                session_dir_abs = os.path.abspath(self.session_dir)
                session_path = os.path.join(session_dir_abs, alias)
                self.clients[alias] = Client(
                    name=session_path,
                    api_id=session.api_id,
                    api_hash=session.api_hash,
                    phone_number=session.phone if session.phone else None
                )

    async def add_account(self, alias: str, api_id: int, api_hash: str, phone: str) -> Dict[str, Any]:
        # Формируем путь к файлу сессии только по alias
        session_dir_abs = os.path.abspath(self.session_dir)
        os.makedirs(session_dir_abs, exist_ok=True)
        session_path = alias  # сохраняем только alias
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
        # Блокируем назначение forwarding как задачи
        if task == 'forwarding':
            return {"success": False, "error": "Назначение задачи 'forwarding' больше не поддерживается. Используйте 'monitoring', 'parsing' или 'public_groups'."}
        session = await self.db.get_session_by_alias(alias)
        if not session:
            return {"success": False, "error": "Session not found"}
        if task != 'reactions':
            # Single-assignment mode: удаляем все старые назначения для этой задачи
            all_sessions = await self.db.get_all_sessions()
            for s in all_sessions:
                await self.db.remove_session_assignment(s.id, task)
        # Для reactions не удаляем прошлые назначения (можно несколько)
        await self.db.add_session_assignment(session.id, task)
        assignments = await self.get_assignments()
        return {"success": True, "alias": alias, "task": task, "assignments": assignments}

    async def delete_session(self, alias: str) -> Dict[str, Any]:
        session = await self.db.get_session_by_alias(alias)
        if not session:
            return {"success": False, "error": "Session not found"}
        await self.db.delete_session(session.id)
        if alias in self.clients:
            del self.clients[alias]
        return {"success": True, "alias": alias}

    async def get_client(self, alias: str) -> Optional[Client]:
        logger.debug(f"[SESSION_MANAGER][get_client] Запрашиваем клиента для alias: {alias}")

        if alias not in self.clients:
            logger.debug(f"[SESSION_MANAGER][get_client] Клиент {alias} не найден в кэше, загружаем все клиенты")
            await self.load_clients()

        client = self.clients.get(alias)
        if not client:
            logger.debug(f"[SESSION_MANAGER][get_client] Клиент {alias} не найден в кэше, пытаемся создать из БД")
            # Попробовать создать клиента из БД
            try:
                session = await self.db.get_session_by_alias(alias)
                if session:
                    logger.debug(f"[SESSION_MANAGER][get_client] Найдена сессия в БД: id={session.id}, alias={session.alias}, is_active={session.is_active}")
                    session_dir_abs = os.path.abspath(self.session_dir)
                    # Используем только alias для имени файла сессии
                    alias_clean = os.path.basename(session.alias if hasattr(session, 'alias') else session.session_path)
                    session_path = os.path.join(session_dir_abs, alias_clean)

                    logger.debug(f"[SESSION_MANAGER][get_client] Создаем клиента: session_path={session_path}, api_id={session.api_id}, phone={session.phone}")
                    client = Client(
                        name=session_path,
                        api_id=session.api_id,
                        api_hash=session.api_hash,
                        workdir=session_dir_abs,
                        phone_number=session.phone
                    )
                    self.clients[alias_clean] = client
                    logger.debug(f"[SESSION_MANAGER][get_client] Клиент создан и сохранен в кэше под ключом {alias_clean}")
                else:
                    logger.warning(f"[SESSION_MANAGER][get_client] Сессия {alias} не найдена в БД")
            except Exception as e:
                logger.error(f"[SESSION_MANAGER][get_client] Ошибка при получении сессии {alias} из БД: {e}", exc_info=True)
                return None

        if client:
            logger.debug(f"[SESSION_MANAGER][get_client] Возвращаем клиента для {alias}: connected={getattr(client, 'is_connected', 'unknown')}")
        else:
            logger.warning(f"[SESSION_MANAGER][get_client] Клиент для {alias} не найден")

        return self.clients.get(alias)

    async def send_code(self, alias: str, phone: str) -> Dict[str, Any]:
        """Send authentication code to the phone number"""
        logger.info(f"[SEND_CODE] alias={alias}, phone={phone}, session_dir={self.session_dir}")
        logger.info(f"[SEND_CODE] sessions_dir exists: {os.path.exists(self.session_dir)}")
        client = self.clients.get(alias)
        logger.info(f"[SEND_CODE] client for alias '{alias}': {client}")
        if not client:
            logger.error(f"[SEND_CODE] No client found for alias '{alias}'")
            return {"success": False, "error": "Session not found"}
        try:
            logger.info(f"[SEND_CODE] Connecting client for alias '{alias}'...")
            await client.connect()
            logger.info(f"[SEND_CODE] Connected. Sending code to {phone}...")
            sent_code = await client.send_code(phone)
            logger.info(f"[SEND_CODE] Code sent. phone_code_hash={sent_code.phone_code_hash}")
            return {
                "success": True,
                "phone_code_hash": sent_code.phone_code_hash
            }
        except Exception as e:
            logger.error(f"[SEND_CODE] Error sending code for alias '{alias}': {e}", exc_info=True)
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
            # В Pyrogram 2.0+ используем правильную сигнатуру с именованными аргументами
            try:
                # Логируем параметры для отладки
                logger.info(f"[SIGN_IN] Parameters: phone='{phone}', code='{code}', phone_code_hash='{phone_code_hash}'")
                logger.info(f"[SIGN_IN] Code type: {type(code)}, length: {len(code) if code else 0}")
                
                # Пробуем с именованными аргументами
                await client.sign_in(
                    phone_number=phone,
                    phone_code=code,
                    phone_code_hash=phone_code_hash
                )
                logger.info(f"[SIGN_IN] sign_in completed")
            except Exception as e:
                logger.error(f"[SIGN_IN] sign_in error: {e}")
                raise e
                
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
        session = await self.db.get_session_by_alias(alias)
        if not session:
            return {"success": False, "error": "Session not found"}
        await self.db.remove_session_assignment(session.id, task)
        assignments = await self.get_assignments()
        return {"success": True, "alias": alias, "assignments": assignments}
    
    async def get_client(self, alias: str) -> Optional[Client]:
        logger.debug(f"[SESSION_MANAGER][get_client_v2] Запрашиваем клиента для alias: {alias}")

        if alias not in self.clients:
            logger.debug(f"[SESSION_MANAGER][get_client_v2] Клиент {alias} не найден в кэше, загружаем все клиенты")
            await self.load_clients()

        client = self.clients.get(alias)
        if client and not client.is_connected:
            logger.info(f"[SESSION_MANAGER][get_client_v2] Клиент {alias} найден но не подключен, запускаем сессию")
            logger.debug(f"[SESSION_MANAGER][get_client_v2] Информация о клиенте: name={getattr(client, 'name', 'unknown')}, api_id={getattr(client, 'api_id', 'unknown')}")

            try:
                # Добавляем диагностику состояния БД перед запуском
                logger.debug(f"[SESSION_MANAGER][get_client_v2] Диагностика состояния БД перед запуском сессии {alias}")
                db_path = getattr(self.db, 'db_path', 'unknown')
                logger.debug(f"[SESSION_MANAGER][get_client_v2] Путь к БД: {db_path}")

                # Проверяем, существует ли файл БД
                if os.path.exists(db_path):
                    logger.debug(f"[SESSION_MANAGER][get_client_v2] Файл БД существует, размер: {os.path.getsize(db_path)} байт")
                    # Проверяем права доступа
                    try:
                        with open(db_path, 'rb') as f:
                            f.read(1)
                        logger.debug(f"[SESSION_MANAGER][get_client_v2] Файл БД доступен для чтения")
                    except Exception as access_error:
                        logger.error(f"[SESSION_MANAGER][get_client_v2] Ошибка доступа к файлу БД: {access_error}")
                else:
                    logger.warning(f"[SESSION_MANAGER][get_client_v2] Файл БД не существует: {db_path}")

                # Проверяем, не заблокирована ли БД другой транзакцией
                try:
                    await self.db.conn.execute("SELECT 1")
                    logger.debug(f"[SESSION_MANAGER][get_client_v2] БД доступна для запросов")
                except Exception as db_error:
                    logger.error(f"[SESSION_MANAGER][get_client_v2] БД недоступна перед запуском сессии: {db_error}")

                logger.debug(f"[SESSION_MANAGER][get_client_v2] Запускаем client.start() для {alias}")
                await client.start()
                logger.info(f"[SESSION_MANAGER][get_client_v2] ✅ Успешно запущена сессия {alias}")

                # Проверяем статус после запуска
                try:
                    me = await client.get_me()
                    logger.debug(f"[SESSION_MANAGER][get_client_v2] Сессия авторизована как: {me.first_name} (@{me.username})")
                except Exception as auth_error:
                    logger.warning(f"[SESSION_MANAGER][get_client_v2] Сессия запущена но не авторизована: {auth_error}")

            except Exception as e:
                logger.error(f"[SESSION_MANAGER][get_client_v2] ❌ Критическая ошибка запуска сессии {alias}: {e}", exc_info=True)

                # Дополнительная диагностика ошибки
                if "database is locked" in str(e).lower():
                    logger.error(f"[SESSION_MANAGER][get_client_v2] 🔒 ОШИБКА 'DATABASE IS LOCKED' для сессии {alias}")
                    logger.error(f"[SESSION_MANAGER][get_client_v2] 🔍 Детали ошибки блокировки БД:")
                    logger.error(f"[SESSION_MANAGER][get_client_v2]   - Тип ошибки: {type(e).__name__}")
                    logger.error(f"[SESSION_MANAGER][get_client_v2]   - Сообщение: {str(e)}")
                    logger.error(f"[SESSION_MANAGER][get_client_v2]   - Путь к БД: {getattr(self.db, 'db_path', 'unknown')}")
                    logger.error(f"[SESSION_MANAGER][get_client_v2]   - Соединение с БД активно: {self.db.conn is not None}")

                    # Проверяем статус соединения с БД
                    if self.db.conn:
                        try:
                            # Пытаемся выполнить простой запрос
                            await self.db.conn.execute("SELECT 1")
                            logger.error(f"[SESSION_MANAGER][get_client_v2]   - БД отвечает на простые запросы: ДА")
                        except Exception as test_error:
                            logger.error(f"[SESSION_MANAGER][get_client_v2]   - БД отвечает на простые запросы: НЕТ ({test_error})")

                    # Проверяем файл БД
                    db_path = getattr(self.db, 'db_path', 'unknown')
                    if os.path.exists(db_path):
                        file_size = os.path.getsize(db_path)
                        logger.error(f"[SESSION_MANAGER][get_client_v2]   - Файл БД существует: ДА, размер: {file_size} байт")

                        # Проверяем, не является ли файл БД поврежденным
                        try:
                            with open(db_path, 'rb') as f:
                                header = f.read(100)
                            logger.error(f"[SESSION_MANAGER][get_client_v2]   - Файл БД читается: ДА")
                        except Exception as file_error:
                            logger.error(f"[SESSION_MANAGER][get_client_v2]   - Файл БД читается: НЕТ ({file_error})")
                    else:
                        logger.error(f"[SESSION_MANAGER][get_client_v2]   - Файл БД существует: НЕТ")

                    logger.error(f"[SESSION_MANAGER][get_client_v2] 💡 РЕКОМЕНДАЦИИ:")
                    logger.error(f"[SESSION_MANAGER][get_client_v2]   1. Проверьте, нет ли других процессов, использующих БД")
                    logger.error(f"[SESSION_MANAGER][get_client_v2]   2. Попробуйте перезапустить все сервисы")
                    logger.error(f"[SESSION_MANAGER][get_client_v2]   3. Проверьте права доступа к файлу parser.db")
                    logger.error(f"[SESSION_MANAGER][get_client_v2]   4. Возможно, файл БД поврежден - попробуйте восстановить из бэкапа")

                return None

        if client:
            logger.debug(f"[SESSION_MANAGER][get_client_v2] Возвращаем клиента для {alias}: connected={getattr(client, 'is_connected', 'unknown')}")
        else:
            logger.warning(f"[SESSION_MANAGER][get_client_v2] Клиент для {alias} не найден")

        return client
    
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
                    try:
                        await client.stop()
                        results[alias] = "success"
                    except asyncio.CancelledError:
                        results[alias] = "cancelled"
                        # Не логируем как ошибку, это штатная ситуация при shutdown
                    except Exception as e:
                        logger.error(f"Error stopping session {alias}: {e}")
                        results[alias] = f"error: {str(e)}"
                else:
                    results[alias] = "already_stopped"
            except Exception as e:
                logger.error(f"Error in stop_all for {alias}: {e}")
                results[alias] = f"error: {str(e)}"
        return results
    
    async def add_reaction(self, chat_id: str, message_id: int, reaction: str, session_names: Optional[List[str]] = None) -> Dict[str, str]:
        """Add reaction to a message using all or specific accounts"""
        results = {}
        sessions_to_use = []
        if session_names:
            sessions_to_use = session_names
        else:
            # Получаем все сессии, назначенные для реакций
            reaction_sessions = await self.get_sessions_for_task('reactions')
            sessions_to_use = [s.alias for s in reaction_sessions]

        if not sessions_to_use:
            logger.warning("[REACTIONS] Нет сессий, назначенных для постановки реакций.")
            return {"status": "warning", "message": "No sessions assigned for reactions"}

        try:
            numeric_chat_id = int(chat_id)
        except (ValueError, TypeError):
            logger.error(f"[REACTIONS] Неверный формат chat_id: '{chat_id}'. ID должен быть числовым.")
            results["error"] = "Invalid chat_id format"
            return results

        for alias in sessions_to_use:
            client = await self.get_client(alias)
            if client:
                try:
                    if not client.is_connected:
                        await client.start()

                    # Проверяем, может ли сессия получить доступ к каналу
                    try:
                        chat_info = await client.get_chat(numeric_chat_id)
                        logger.info(f"Сессия {alias} имеет доступ к каналу {numeric_chat_id}")
                    except Exception as access_error:
                        logger.warning(f"Сессия {alias} не имеет доступа к каналу {numeric_chat_id}: {access_error}")
                        results[alias] = f"no_access: {str(access_error)}"
                        continue

                    await client.send_reaction(
                        chat_id=numeric_chat_id,
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
    
    async def get_assignments(self) -> Dict[str, Any]:
        """Возвращает assignments: task -> [session_alias]"""
        return await self.db.get_assignments()
    
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

    async def confirm_code(self, alias: str, phone: str, code: str, phone_code_hash: str) -> dict:
        logger.info(f"[CONFIRM_CODE] alias={alias}, phone={phone}, code={code}, phone_code_hash={phone_code_hash}")
        client = self.clients.get(alias)
        if not client:
            # Попробовать создать клиента из БД
            session = await self.db.get_session_by_alias(alias)
            if session:
                session_dir_abs = os.path.abspath(self.session_dir)
                client = Client(
                    name=session.session_path,
                    api_id=session.api_id,
                    api_hash=session.api_hash,
                    workdir=session_dir_abs,
                    phone_number=session.phone
                )
                self.clients[alias] = client
                logger.info(f"[CONFIRM_CODE] Created client for alias '{alias}' from DB")
            else:
                logger.error(f"[CONFIRM_CODE] No session in DB for alias '{alias}'")
                return {"success": False, "error": "Session not found"}
        try:
            await client.connect()
            logger.info(f"[CONFIRM_CODE] Connected client for alias '{alias}'")
            # В Pyrogram 2.0+ используем правильную сигнатуру с именованными аргументами
            try:
                # Логируем параметры для отладки
                logger.info(f"[CONFIRM_CODE] Parameters: phone='{phone}', code='{code}', phone_code_hash='{phone_code_hash}'")
                logger.info(f"[CONFIRM_CODE] Code type: {type(code)}, length: {len(code) if code else 0}")
                
                # Пробуем с именованными аргументами
                await client.sign_in(
                    phone_number=phone,
                    phone_code=code,
                    phone_code_hash=phone_code_hash
                )
                logger.info(f"[CONFIRM_CODE] sign_in completed")
            except Exception as e:
                logger.error(f"[CONFIRM_CODE] sign_in error: {e}")
                # Проверяем тип ошибки
                if "PHONE_CODE_EXPIRED" in str(e):
                    return {"success": False, "error": "Код подтверждения истек. Запросите новый код.", "code_expired": True}
                elif "PHONE_CODE_INVALID" in str(e):
                    return {"success": False, "error": "Неверный код подтверждения. Проверьте код и попробуйте снова.", "invalid_code": True}
                else:
                    raise e
            await client.disconnect()
            return {"success": True, "result": "Code confirmed successfully"}
        except Exception as e:
            logger.error(f"[CONFIRM_CODE] Error: {e}", exc_info=True)
            return {"success": False, "error": str(e)} 

    async def get_sessions_for_task(self, task: str) -> list:
        return await self.db.get_sessions_for_task(task)

    async def update_session_user_ids(self):
        """Обновить user_id для всех сессий, получив их из Telegram API"""
        sessions = await self.db.get_all_sessions()
        updated_count = 0
        
        for session in sessions:
            if session.user_id is None:  # Обновляем только если еще не установлен
                try:
                    client = await self.get_client(session.alias)
                    if client:
                        if not client.is_connected:
                            await client.start()
                        
                        me = await client.get_me()
                        if me and me.id:
                            await self.db.update_session(session.id, user_id=me.id)
                            logger.info(f"[SESSION_MANAGER] Обновлен user_id для сессии {session.alias}: {me.id}")
                            updated_count += 1
                        
                        if client.is_connected:
                            await client.stop()
                except Exception as e:
                    logger.error(f"[SESSION_MANAGER] Ошибка обновления user_id для сессии {session.alias}: {e}")
        
        logger.info(f"[SESSION_MANAGER] Обновлено user_id для {updated_count} сессий")
        return updated_count

    async def group_sessions_by_user_id(self, sessions: List[SessionMeta]) -> Dict[int, List[SessionMeta]]:
        """Группирует сессии по user_id для определения дублирующихся аккаунтов
        
        Args:
            sessions: Список сессий для группировки
            
        Returns:
            Словарь {user_id: [список сессий]}
        """
        groups = {}
        
        for session in sessions:
            if session.user_id is None:
                # Если user_id еще не установлен, пробуем получить его
                try:
                    client = await self.get_client(session.alias)
                    if client:
                        if not client.is_connected:
                            await client.start()
                        
                        me = await client.get_me()
                        if me and me.id:
                            session.user_id = me.id
                            await self.db.update_session(session.id, user_id=me.id)
                            logger.info(f"[SESSION_MANAGER] Получен user_id для сессии {session.alias}: {me.id}")
                        
                        if client.is_connected:
                            await client.stop()
                except Exception as e:
                    logger.error(f"[SESSION_MANAGER] Ошибка получения user_id для сессии {session.alias}: {e}")
            
            if session.user_id:
                if session.user_id not in groups:
                    groups[session.user_id] = []
                groups[session.user_id].append(session)
            else:
                # Сессии без user_id группируем отдельно (каждая в своей группе)
                groups[f"unknown_{session.id}"] = [session]
        
        return groups

    async def get_next_parsing_session(self, current_session_alias: str = None) -> Optional[Client]:
        """
        Получить следующую доступную сессию для парсинга.
        Если текущая сессия указана, возвращает следующую за ней.
        Если текущая не указана, возвращает первую доступную.
        """
        logger.debug(f"[SESSION_MANAGER][get_next_parsing_session] Запрашиваем сессию для парсинга, текущая: {current_session_alias}")

        parsing_sessions = await self.get_sessions_for_task("parsing")
        logger.debug(f"[SESSION_MANAGER][get_next_parsing_session] Найдено сессий для парсинга: {len(parsing_sessions)}")

        if not parsing_sessions:
            logger.warning("[SESSION_MANAGER][get_next_parsing_session] Нет сессий, назначенных для парсинга")
            logger.warning("[SESSION_MANAGER][get_next_parsing_session] Используйте assign_session для назначения сессий задаче 'parsing'")
            return None

        # Логируем все доступные сессии
        session_info = [f"{s.alias}(id={s.id},active={s.is_active})" for s in parsing_sessions]
        logger.debug(f"[SESSION_MANAGER][get_next_parsing_session] Доступные сессии: {session_info}")

        # Если текущая сессия не указана, возвращаем первую
        if current_session_alias is None:
            session = parsing_sessions[0]
            logger.info(f"[SESSION_MANAGER][get_next_parsing_session] Выбрана первая сессия для парсинга: {session.alias}")
            logger.debug(f"[SESSION_MANAGER][get_next_parsing_session] Детали сессии: id={session.id}, api_id={session.api_id}, phone={session.phone}")

            client = await self.get_client(session.alias)
            if client:
                logger.info(f"[SESSION_MANAGER][get_next_parsing_session] ✅ Успешно получен клиент для первой сессии: {session.alias}")
                return client
            else:
                logger.error(f"[SESSION_MANAGER][get_next_parsing_session] ❌ Не удалось получить клиент для сессии: {session.alias}")
                return None

        # Находим индекс текущей сессии
        current_index = -1
        for i, session in enumerate(parsing_sessions):
            if session.alias == current_session_alias:
                current_index = i
                break

        if current_index == -1:
            logger.warning(f"[SESSION_MANAGER][get_next_parsing_session] Текущая сессия {current_session_alias} не найдена в списке парсинга")
            logger.warning(f"[SESSION_MANAGER][get_next_parsing_session] Доступные сессии: {[s.alias for s in parsing_sessions]}")
            return None

        # Пробуем следующую сессию (по кругу)
        next_index = (current_index + 1) % len(parsing_sessions)
        session = parsing_sessions[next_index]

        logger.info(f"[SESSION_MANAGER][get_next_parsing_session] Переключение на следующую сессию для парсинга: {session.alias} (была {current_session_alias})")
        logger.debug(f"[SESSION_MANAGER][get_next_parsing_session] Детали новой сессии: id={session.id}, api_id={session.api_id}, phone={session.phone}")

        client = await self.get_client(session.alias)
        if client:
            logger.info(f"[SESSION_MANAGER][get_next_parsing_session] ✅ Успешно переключено на сессию: {session.alias}")
            return client

        # Если следующая не доступна, пробуем остальные
        logger.warning(f"[SESSION_MANAGER][get_next_parsing_session] Следующая сессия {session.alias} недоступна, пробуем другие")

        for i in range(len(parsing_sessions)):
            if i == current_index:
                continue
            session = parsing_sessions[i]
            logger.debug(f"[SESSION_MANAGER][get_next_parsing_session] Пробуем сессию: {session.alias}")

            client = await self.get_client(session.alias)
            if client:
                logger.info(f"[SESSION_MANAGER][get_next_parsing_session] ✅ Найдена доступная сессия для парсинга: {session.alias} (была {current_session_alias})")
                return client

        logger.error("[SESSION_MANAGER][get_next_parsing_session] ❌ Не найдено доступных сессий для парсинга")
        logger.error("[SESSION_MANAGER][get_next_parsing_session] Проверьте статус сессий и логи выше для диагностики")
        return None 