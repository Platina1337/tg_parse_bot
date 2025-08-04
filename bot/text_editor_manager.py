"""
Менеджер для управления режимом редактирования текста постов
Интерфейс между ботом и функциональностью редактирования текста
"""

import logging
from typing import Optional, Dict, Any, List
import httpx
from bot.config import config

logger = logging.getLogger(__name__)


class TextEditorManager:
    """Менеджер для работы с редактированием текста постов"""
    
    def __init__(self):
        self.base_url = config.PARSER_SERVICE_URL
        
    async def start_text_editing(self, channel_id: int, link_text: str, link_url: str, max_posts: int = 100) -> Dict[str, Any]:
        """
        Запуск редактирования текста постов
        
        Args:
            channel_id: ID канала
            link_text: Текст для гиперссылки
            link_url: URL для гиперссылки  
            max_posts: Максимальное количество постов
            
        Returns:
            dict: Результат запуска редактирования
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/text-editor/start",
                    json={
                        "channel_id": channel_id,
                        "link_text": link_text,
                        "link_url": link_url,
                        "max_posts": max_posts
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"[TEXT_EDITOR_MANAGER] Редактирование запущено: {result}")
                    return result
                else:
                    error_msg = f"Ошибка запуска редактирования: HTTP {response.status_code}"
                    logger.error(f"[TEXT_EDITOR_MANAGER] {error_msg}")
                    return {"status": "error", "message": error_msg}
                    
        except Exception as e:
            error_msg = f"Ошибка при запуске редактирования: {e}"
            logger.error(f"[TEXT_EDITOR_MANAGER] {error_msg}")
            return {"status": "error", "message": error_msg}
    
    async def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """
        Получение статуса задачи редактирования
        
        Args:
            task_id: ID задачи
            
        Returns:
            dict: Статус задачи
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.base_url}/text-editor/status/{task_id}")
                
                if response.status_code == 200:
                    return response.json()
                else:
                    return {"status": "error", "message": f"HTTP {response.status_code}"}
                    
        except Exception as e:
            logger.error(f"[TEXT_EDITOR_MANAGER] Ошибка получения статуса: {e}")
            return {"status": "error", "message": str(e)}
    
    async def get_all_tasks(self) -> Dict[str, Any]:
        """
        Получение всех задач редактирования
        
        Returns:
            dict: Список всех задач
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.base_url}/text-editor/tasks")
                
                if response.status_code == 200:
                    return response.json()
                else:
                    return {"status": "error", "message": f"HTTP {response.status_code}"}
                    
        except Exception as e:
            logger.error(f"[TEXT_EDITOR_MANAGER] Ошибка получения задач: {e}")
            return {"status": "error", "message": str(e)}
    
    async def stop_task(self, task_id: str) -> Dict[str, Any]:
        """
        Остановка задачи редактирования
        
        Args:
            task_id: ID задачи
            
        Returns:
            dict: Результат остановки
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(f"{self.base_url}/text-editor/stop/{task_id}")
                
                if response.status_code == 200:
                    return response.json()
                else:
                    return {"status": "error", "message": f"HTTP {response.status_code}"}
                    
        except Exception as e:
            logger.error(f"[TEXT_EDITOR_MANAGER] Ошибка остановки задачи: {e}")
            return {"status": "error", "message": str(e)}
    
    def format_task_status_message(self, task_info: Dict[str, Any]) -> str:
        """
        Форматирование сообщения со статусом задачи
        
        Args:
            task_info: Информация о задаче
            
        Returns:
            str: Отформатированное сообщение
        """
        if task_info.get("status") == "error":
            return f"❌ Ошибка: {task_info.get('message', 'Неизвестная ошибка')}"
        
        task_id = task_info.get('task_id', 'Неизвестно')
        status = task_info.get('status', 'Неизвестно')
        processed = task_info.get('processed_count', 0)
        modified = task_info.get('modified_count', 0)
        channel_id = task_info.get('channel_id', 'Неизвестно')
        link_text = task_info.get('link_text', 'Неизвестно')
        max_posts = task_info.get('max_posts', 0)
        
        status_emoji = {
            'running': '🔄',
            'completed': '✅',
            'stopped': '⏹️',
            'error': '❌'
        }.get(status, '❓')
        
        message = f"{status_emoji} **Редактирование текста**\n\n"
        message += f"📋 ID задачи: `{task_id}`\n"
        message += f"📺 Канал: `{channel_id}`\n"
        message += f"🔗 Добавляемый текст: `{link_text}`\n"
        message += f"📊 Лимит постов: {max_posts}\n"
        message += f"📈 Обработано: {processed}\n"
        message += f"✏️ Изменено: {modified}\n"
        message += f"📍 Статус: {status}"
        
        if task_info.get('error'):
            message += f"\n❌ Ошибка: {task_info['error']}"
            
        return message
    
    def format_all_tasks_message(self, tasks_data: Dict[str, Any]) -> str:
        """
        Форматирование сообщения со всеми задачами
        
        Args:
            tasks_data: Данные всех задач
            
        Returns:
            str: Отформатированное сообщение
        """
        if tasks_data.get("status") == "error":
            return f"❌ Ошибка получения задач: {tasks_data.get('message', 'Неизвестная ошибка')}"
        
        tasks = tasks_data.get('tasks', [])
        
        if not tasks:
            return "📝 **Задачи редактирования текста**\n\nНет активных задач"
        
        message = f"📝 **Задачи редактирования текста** ({len(tasks)})\n\n"
        
        for task in tasks:
            status = task.get('status', 'Неизвестно')
            status_emoji = {
                'running': '🔄',
                'completed': '✅',
                'stopped': '⏹️',
                'error': '❌'
            }.get(status, '❓')
            
            task_id = task.get('task_id', 'Неизвестно')
            channel_id = task.get('channel_id', 'Неизвестно')
            processed = task.get('processed_count', 0)
            modified = task.get('modified_count', 0)
            
            message += f"{status_emoji} `{task_id}`\n"
            message += f"   📺 Канал: `{channel_id}`\n"
            message += f"   📊 {processed}→{modified}\n\n"
        
        return message