import httpx
import logging
from typing import List, Dict, Optional
from bot.config import config

logger = logging.getLogger(__name__)

class ParserAPIClient:
    """Клиент для взаимодействия с API парсера"""
    
    def __init__(self):
        self.base_url = config.PARSER_SERVICE_URL
        self.timeout = 60.0
    
    async def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict:
        """Выполнить HTTP запрос к API парсера"""
        url = f"{self.base_url}{endpoint}"
        logger.info(f"[API] Making {method} request to {url}")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                logger.info(f"[API] Successfully got response from {url}")
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"[API] Resource not found: {url}")
                # Для 404 ошибок возвращаем пустой результат вместо исключения
                if "channel/stats" in url:
                    return {"status": "not_found", "message": "Канал не найден или недоступен"}
                elif "user/channels" in url:
                    return {"status": "success", "channels": []}
                elif "user/target-channels" in url:
                    return {"status": "success", "channels": []}
                elif "user/monitorings" in url:
                    return {"status": "success", "monitorings": []}
                elif "user/posting-templates" in url:
                    return {"status": "success", "templates": []}
                else:
                    return {"status": "not_found", "message": "Ресурс не найден"}
            else:
                logger.error(f"[API] HTTP error {e.response.status_code}: {e.response.text}")
                raise
        except httpx.ReadTimeout as e:
            logger.error(f"[API] Read timeout for {url}: {e}")
            raise
        except httpx.ConnectError as e:
            logger.error(f"[API] Connection error for {url}: {e}")
            # Вместо вызова исключения возвращаем пустой результат с сообщением об ошибке
            if "channel/stats" in url:
                return {"status": "error", "message": "Сервис парсера недоступен", "title": "Канал недоступен", "username": "", "members_count": "N/A", "last_message_id": "N/A", "parsed_posts": "0", "description": ""}
            elif "user/channels" in url:
                return {"status": "error", "message": "Сервис парсера недоступен", "channels": []}
            elif "user/target-channels" in url:
                return {"status": "error", "message": "Сервис парсера недоступен", "channels": []}
            elif "user/monitorings" in url:
                return {"status": "error", "message": "Сервис парсера недоступен", "monitorings": []}
            elif "user/posting-templates" in url:
                return {"status": "error", "message": "Сервис парсера недоступен", "templates": []}
            elif "forwarding/start" in url or "forwarding/stop" in url or "forwarding/config" in url:
                return {"status": "error", "message": "Сервис парсера недоступен. Проверьте, запущен ли сервис парсера (uvicorn parser.main:app)."}
            else:
                return {"status": "error", "message": "Сервис парсера недоступен. Проверьте, запущен ли сервис парсера (uvicorn parser.main:app)."}
        except Exception as e:
            logger.error(f"[API] Request error for {url}: {e}")
            raise
    
    # --- Методы для работы с каналами пользователя ---
    
    async def get_user_channels(self, user_id: int) -> List[Dict]:
        """Получить историю каналов пользователя"""
        response = await self._make_request("GET", f"/user/channels/{user_id}")
        return response.get("channels", [])
    
    async def add_user_channel(self, user_id: int, channel_id: str, channel_title: str, username: str = None) -> bool:
        """Добавить канал в историю пользователя"""
        data = {"channel_id": channel_id, "channel_title": channel_title, "username": username}
        await self._make_request("POST", f"/user/channels/{user_id}", json=data)
        return True
    
    async def update_user_channel_last_used(self, user_id: int, channel_id: str) -> bool:
        """Обновить время последнего использования канала"""
        await self._make_request("PUT", f"/user/channels/{user_id}/{channel_id}")
        return True
    
    async def remove_user_channel(self, user_id: int, channel_id: str) -> bool:
        """Удалить канал из истории пользователя"""
        await self._make_request("DELETE", f"/user/channels/{user_id}/{channel_id}")
        return True
    
    # --- Методы для работы с целевыми каналами ---
    
    async def get_user_target_channels(self, user_id: int) -> List[Dict]:
        """Получить историю целевых каналов пользователя"""
        response = await self._make_request("GET", f"/user/target-channels/{user_id}")
        return response.get("channels", [])
    
    async def add_user_target_channel(self, user_id: int, channel_id: str, channel_title: str, username: str = None) -> bool:
        """Добавить целевой канал в историю пользователя"""
        data = {"channel_id": channel_id, "channel_title": channel_title, "username": username}
        await self._make_request("POST", f"/user/target-channels/{user_id}", json=data)
        return True
    
    async def update_user_target_channel_last_used(self, user_id: int, channel_id: str) -> bool:
        """Обновить время последнего использования целевого канала"""
        await self._make_request("PUT", f"/user/target-channels/{user_id}/{channel_id}")
        return True
    
    async def remove_user_target_channel(self, user_id: int, channel_id: str) -> bool:
        """Удалить целевой канал из истории пользователя"""
        await self._make_request("DELETE", f"/user/target-channels/{user_id}/{channel_id}")
        return True
    
    # --- Методы для работы с мониторингами ---
    

    
    # --- Методы для работы с шаблонами публикации ---
    
    async def get_user_posting_templates(self, user_id: int) -> List[Dict]:
        """Получить шаблоны публикации пользователя"""
        response = await self._make_request("GET", f"/user/posting-templates/{user_id}")
        return response.get("templates", [])
    
    async def save_user_posting_template(self, user_id: int, name: str, settings: Dict) -> bool:
        """Сохранить шаблон публикации пользователя"""
        data = {"name": name, "settings": settings}
        await self._make_request("POST", f"/user/posting-templates/{user_id}", json=data)
        return True
    
    async def delete_user_posting_template(self, user_id: int, name: str) -> bool:
        """Удалить шаблон публикации пользователя"""
        await self._make_request("DELETE", f"/user/posting-templates/{user_id}/{name}")
        return True
    
    # --- Методы для работы с навигацией ---
    
    async def get_navigation_message_id(self, channel_id: str) -> Optional[int]:
        """Получить ID навигационного сообщения для канала"""
        response = await self._make_request("GET", f"/navigation/message/{channel_id}")
        return response.get("message_id")
    
    async def save_navigation_message_id(self, channel_id: str, message_id: int) -> bool:
        """Сохранить ID навигационного сообщения для канала"""
        data = {"message_id": message_id}
        await self._make_request("POST", f"/navigation/message/{channel_id}", json=data)
        return True
    
    async def delete_navigation_message_id(self, channel_id: str) -> bool:
        """Удалить ID навигационного сообщения для канала"""
        await self._make_request("DELETE", f"/navigation/message/{channel_id}")
        return True
    
    # --- Методы для работы с пересылкой ---
    
    async def start_forwarding(self, user_id: int) -> bool:
        """Запустить пересылку для пользователя"""
        response = await self._make_request("POST", f"/forwarding/start/{user_id}")
        return response.get("status") == "success"
    
    async def stop_forwarding(self, user_id: int) -> bool:
        """Остановить пересылку для пользователя"""
        response = await self._make_request("POST", f"/forwarding/stop/{user_id}")
        return response.get("status") == "success"
    
    async def get_forwarding_stats(self, user_id: int) -> Dict:
        """Получить статистику пересылки для пользователя"""
        response = await self._make_request("GET", f"/forwarding/stats/{user_id}")
        return response.get("stats", {})
    
    async def save_forwarding_config(self, user_id: int, config: Dict) -> bool:
        """Сохранить конфигурацию пересылки для пользователя"""
        response = await self._make_request("POST", f"/forwarding/config/{user_id}", json=config)
        return response.get("status") == "success"
    
    async def start_forwarding_parsing(self, user_id: int) -> bool:
        """Запустить парсинг и пересылку для пользователя"""
        response = await self._make_request("POST", f"/forwarding/parse-and-forward/{user_id}")
        return response.get("status") == "success"
    
    async def clear_forwarding_history(self, channel_id: Optional[int] = None, target_channel: Optional[str] = None) -> Dict:
        """Очистить историю пересылки"""
        params = {}
        if channel_id:
            params["channel_id"] = channel_id
        if target_channel:
            params["target_channel"] = target_channel
        response = await self._make_request("DELETE", "/forwarding/history", params=params)
        return response.get("result", {})
    
    async def get_forwarding_history_stats(self, channel_id: Optional[int] = None, target_channel: Optional[str] = None) -> Dict:
        """Получить статистику истории пересылки"""
        params = {}
        if channel_id:
            params["channel_id"] = channel_id
        if target_channel:
            params["target_channel"] = target_channel
        response = await self._make_request("GET", "/forwarding/history/stats", params=params)
        return response.get("stats", {})
    
    # --- Методы для работы с каналами ---
    
    async def get_channel_stats(self, channel_id: str) -> Dict:
        """Получить статистику канала"""
        try:
            response = await self._make_request("GET", f"/channel/stats/{channel_id}")
            logger.info(f"[get_channel_stats] Ответ от parser: {response}")
            logger.info(f"[get_channel_stats] Тип ответа: {type(response)}")
            logger.info(f"[get_channel_stats] Ключи в ответе: {list(response.keys()) if isinstance(response, dict) else 'не словарь'}")
            
            # Если в ответе нет необходимых полей, создаем их с дефолтными значениями
            if not isinstance(response, dict):
                logger.error(f"[get_channel_stats] Неверный формат ответа: {response}")
                response = {}
                
            # Обеспечиваем наличие всех необходимых полей
            result = {
                "id": response.get("channel_id", channel_id),  # Используем channel_id из ответа
                "title": response.get("channel_title", f"Канал {channel_id}"),
                "username": response.get("username", ""),  # Исправлено: было channel_username
                "members_count": response.get("members_count", "N/A"),
                "last_message_id": response.get("last_message_id", "N/A"),
                "parsed_posts": response.get("parsed_posts", "0"),
                "description": response.get("description", "")
            }
            logger.info(f"[get_channel_stats] Возвращаем: {result}")
            logger.info(f"[get_channel_stats] last_message_id из API: {response.get('last_message_id')}")
            logger.info(f"[get_channel_stats] last_message_id в результате: {result.get('last_message_id')}")
            return result
        except Exception as e:
            logger.error(f"[get_channel_stats] Ошибка: {e}")
            return {
                "id": channel_id,
                "title": f"Канал {channel_id}",
                "username": "",
                "members_count": "N/A",
                "last_message_id": "N/A",
                "parsed_posts": "0",
                "description": ""
            }
    
    async def get_channel_hashtags(self, channel_id: str) -> List[str]:
        """Получить хэштеги канала"""
        response = await self._make_request("GET", f"/channel/hashtags/{channel_id}")
        return response.get("hashtags", [])
    


    async def get_monitoring_status(self) -> dict:
        """
        Получить статус всех активных мониторингов.
        Возвращает список monitorings, где для каждого теперь есть:
        - channel_id
        - config
        - active
        - task_running
        - target_channel (новое поле, цель мониторинга)
        """
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}/forwarding/monitoring_status")
            response.raise_for_status()
            return response.json()

    async def start_parsing_background(self, source_channel: str, target_channel: str, config: dict) -> dict:
        """Запустить парсинг+пересылку в фоновом режиме."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/forwarding/start_parsing_background",
                json={
                    "source_channel": source_channel,
                    "target_channel": target_channel,
                    "config": config
                }
            )
            response.raise_for_status()
            return response.json()

    async def get_task_status(self, task_id: str) -> dict:
        """Получить статус задачи парсинг+пересылки."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}/forwarding/task_status/{task_id}")
            response.raise_for_status()
            return response.json()

    async def stop_task(self, task_id: str) -> dict:
        """Остановить задачу парсинг+пересылки."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.base_url}/forwarding/stop_task/{task_id}")
            response.raise_for_status()
            return response.json()

    async def get_all_tasks(self) -> dict:
        """Получить список всех задач парсинг+пересылки."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}/forwarding/all_tasks")
            response.raise_for_status()
            return response.json()
            


    # --- Methods for working with sessions ---
    
    async def add_session(self, session_name: str, api_id: str = None, api_hash: str = None, phone: str = None) -> dict:
        """Add a new session"""
        # Используем значения из переменных окружения, если не переданы
        import os
        data = {
            "session_name": session_name,
            "api_id": api_id or os.getenv("API_ID"),
            "api_hash": api_hash or os.getenv("API_HASH"),
            "phone": phone
        }
        return await self._make_request("POST", "/sessions/add", json=data)
    
    async def send_code(self, session_name: str, phone: str) -> dict:
        """Send authentication code"""
        data = {
            "session_name": session_name,
            "phone": phone
        }
        return await self._make_request("POST", "/sessions/send_code", json=data)
    
    async def sign_in(self, session_name: str, phone: str, code: str, phone_code_hash: str) -> dict:
        """Sign in with code"""
        data = {
            "session_name": session_name,
            "phone": phone,
            "code": code,
            "phone_code_hash": phone_code_hash
        }
        return await self._make_request("POST", "/sessions/sign_in", json=data)
    
    async def sign_in_with_password(self, session_name: str, password: str) -> dict:
        """Sign in with 2FA password"""
        data = {
            "session_name": session_name,
            "password": password
        }
        return await self._make_request("POST", "/sessions/sign_in_with_password", json=data)
    
    async def list_sessions(self) -> dict:
        """Get list of all sessions"""
        return await self._make_request("GET", "/sessions/list")
    
    async def assign_session(self, task: str, session_name: str) -> dict:
        """Assign a session to a task"""
        data = {
            "task": task,
            "session_name": session_name
        }
        return await self._make_request("POST", "/sessions/assign", json=data)
    
    async def remove_assignment(self, task: str, session_name: str = None) -> dict:
        """Remove a session assignment"""
        data = {
            "task": task,
            "session_name": session_name
        }
        return await self._make_request("POST", "/sessions/remove_assignment", json=data)
    
    async def get_session_status(self, session_name: str) -> dict:
        """Check session status"""
        return await self._make_request("GET", f"/sessions/status/{session_name}")
    
    async def delete_session(self, session_name: str) -> dict:
        """Delete a session"""
        return await self._make_request("DELETE", f"/sessions/{session_name}")
    
    async def confirm_code(self, session_name, phone, code, phone_code_hash):
        # Логируем данные перед отправкой
        logger.info(f"[API_CONFIRM_CODE] Preparing data: session_name='{session_name}', phone='{phone}', code='{code}', phone_code_hash='{phone_code_hash}'")
        logger.info(f"[API_CONFIRM_CODE] Code type: {type(code)}, length: {len(code) if code else 0}")
        
        data = {
            "session_name": session_name,
            "phone": phone,
            "code": code,
            "phone_code_hash": phone_code_hash
        }
        
        logger.info(f"[API_CONFIRM_CODE] Sending data: {data}")
        return await self._make_request("POST", "/sessions/confirm_code", json=data)
    
    async def init_session(self, session_name: str):
        try:
            return await self._make_request("POST", "/sessions/init", json={"session_name": session_name})
        except Exception as e:
            logging.error(f"[API] Error in init_session: {e}")
            return {"success": False, "error": str(e)}
    
    # --- Methods for working with reactions ---
    
    async def add_reaction(self, chat_id: str, message_id: int, reaction: str) -> dict:
        """Поставить реакцию на сообщение всеми userbot-ами, назначенными на reactions"""
        data = {"chat_id": chat_id, "message_id": message_id, "reaction": reaction}
        return await self._make_request("POST", "/reactions/add", json=data)
    
    async def add_multiple_reactions(self, chat_id: str, message_ids: list, reaction: str, session_names: list = None) -> dict:
        """Add reactions to multiple messages"""
        data = {
            "chat_id": chat_id,
            "message_ids": message_ids,
            "reaction": reaction,
            "session_names": session_names
        }
        return await self._make_request("POST", "/reactions/add_multiple", json=data)
    
    async def get_available_reactions(self) -> dict:
        """Get list of available reactions"""
        return await self._make_request("GET", "/reactions/available")

    async def start_mass_reactions(self, channel_id, settings):
        """Запуск массовых реакций через API парсера"""
        url = f"{self.base_url}/reactions/mass_add"
        payload = {"chat_id": channel_id, **settings}
        # Явно добавляем delay, если есть
        if "delay" in settings:
            payload["delay"] = settings["delay"]
        logger.info(f"[API][MASS_REACTIONS] Отправляем payload: {payload}")
        
        # Увеличиваем timeout для массовых реакций (5 минут)
        timeout = httpx.Timeout(300.0)  # 5 минут
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(url, json=payload)
                result = resp.json()
                logger.info(f"[API][MASS_REACTIONS] Получен ответ: {result}")
                return result
            except httpx.TimeoutException:
                logger.error(f"[API][MASS_REACTIONS] Timeout - операция заняла слишком много времени")
                return {"success": False, "error": "Timeout - операция заняла слишком много времени"}
            except Exception as e:
                logger.error(f"[API][MASS_REACTIONS] Ошибка: {e}")
                return {"success": False, "error": str(e)}

    async def get_reaction_task_status(self, task_id: str) -> dict:
        """Получить статус задачи реакций."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}/reactions/task_status/{task_id}")
            response.raise_for_status()
            return response.json()

    async def stop_reaction_task(self, task_id: str) -> dict:
        """Остановить задачу реакций."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.base_url}/reactions/stop_task/{task_id}")
            response.raise_for_status()
            return response.json()

    async def get_all_reaction_tasks(self) -> dict:
        """Получить список всех задач реакций."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}/reactions/all_tasks")
            response.raise_for_status()
            return response.json()

# Глобальный экземпляр API клиента
api_client = ParserAPIClient() 