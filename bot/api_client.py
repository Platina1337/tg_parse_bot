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
            raise
        except Exception as e:
            logger.error(f"[API] Request error for {url}: {e}")
            raise
    
    # --- Методы для работы с каналами пользователя ---
    
    async def get_user_channels(self, user_id: int) -> List[Dict]:
        """Получить историю каналов пользователя"""
        response = await self._make_request("GET", f"/user/channels/{user_id}")
        return response.get("channels", [])
    
    async def add_user_channel(self, user_id: int, channel_id: str, channel_title: str) -> bool:
        """Добавить канал в историю пользователя"""
        data = {"channel_id": channel_id, "channel_title": channel_title}
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
    
    async def add_user_target_channel(self, user_id: int, channel_id: str, channel_title: str) -> bool:
        """Добавить целевой канал в историю пользователя"""
        data = {"channel_id": channel_id, "channel_title": channel_title}
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
    
    async def get_user_monitorings(self, user_id: int) -> List[Dict]:
        """Получить активные мониторинги пользователя"""
        response = await self._make_request("GET", f"/user/monitorings/{user_id}")
        return response.get("monitorings", [])
    
    async def add_user_monitoring(self, user_id: int, channel_id: str, target_channel: str) -> bool:
        """Добавить мониторинг пользователя"""
        data = {"channel_id": channel_id, "target_channel": target_channel}
        await self._make_request("POST", f"/user/monitorings/{user_id}", json=data)
        return True
    
    async def deactivate_user_monitoring(self, user_id: int, channel_id: str, target_channel: str) -> bool:
        """Деактивировать мониторинг пользователя"""
        data = {"channel_id": channel_id, "target_channel": target_channel}
        await self._make_request("DELETE", f"/user/monitorings/{user_id}", json=data)
        return True
    
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
        response = await self._make_request("GET", f"/channel/stats/{channel_id}")
        return response
    
    async def get_channel_hashtags(self, channel_id: str) -> List[str]:
        """Получить хэштеги канала"""
        response = await self._make_request("GET", f"/channel/hashtags/{channel_id}")
        return response.get("hashtags", [])
    
    async def get_monitor_stats(self, channel_id: int, target_channel: str) -> Dict:
        """Получить статистику мониторинга"""
        response = await self._make_request("GET", f"/monitor/stats/{channel_id}/{target_channel}")
        return response.get("stats", {})

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

# Глобальный экземпляр API клиента
api_client = ParserAPIClient() 