import logging
import asyncio
from typing import Dict, List, Optional
from pyrogram import Client
from pyrogram.types import Message
from parser.database import Database
from parser.config import config

logger = logging.getLogger(__name__)

class PublicGroupsForwarder:
    """Класс для пересылки сообщений в публичные группы"""
    
    def __init__(self, userbot: Client, database: Database):
        self.userbot = userbot
        self.db = database
        self.active_tasks = {}  # {task_id: task_info}
        
    async def start_forwarding(self, source_channel: str, target_group: str, 
                             user_id: int, settings: Dict) -> Dict:
        """Запуск пересылки в публичную группу"""
        task_id = f"public_{source_channel}_{target_group}_{user_id}"
        
        if task_id in self.active_tasks:
            return {"status": "error", "message": "Задача уже запущена"}
        
        # Создаем задачу
        task_info = {
            "source_channel": source_channel,
            "target_group": target_group,
            "user_id": user_id,
            "settings": settings,
            "status": "running",
            "forwarded_count": 0
        }
        
        self.active_tasks[task_id] = task_info
        
        # Запускаем асинхронную задачу
        asyncio.create_task(self._forwarding_worker(task_id))
        
        logger.info(f"[PUBLIC_FORWARDER] Запущена пересылка {task_id}")
        return {"status": "success", "task_id": task_id}
    
    async def stop_forwarding(self, task_id: str) -> Dict:
        """Остановка пересылки"""
        if task_id not in self.active_tasks:
            return {"status": "error", "message": "Задача не найдена"}
        
        task_info = self.active_tasks[task_id]
        task_info["status"] = "stopped"
        
        logger.info(f"[PUBLIC_FORWARDER] Остановлена пересылка {task_id}")
        return {"status": "success", "message": "Пересылка остановлена"}
    
    async def get_status(self, task_id: str) -> Dict:
        """Получить статус задачи"""
        if task_id not in self.active_tasks:
            return {"status": "error", "message": "Задача не найдена"}
        
        task_info = self.active_tasks[task_id]
        return {
            "status": "success",
            "task_info": task_info
        }
    
    async def get_all_tasks(self) -> Dict:
        """Получить все активные задачи"""
        return {
            "status": "success",
            "tasks": list(self.active_tasks.values())
        }
    
    async def _forwarding_worker(self, task_id: str):
        """Рабочий процесс пересылки"""
        task_info = self.active_tasks[task_id]
        source_channel = task_info["source_channel"]
        target_group = task_info["target_group"]
        settings = task_info["settings"]
        
        try:
            # Получаем последние сообщения из исходного канала
            messages = await self._get_messages_from_channel(source_channel, settings)
            
            for message in messages:
                if task_info["status"] == "stopped":
                    break
                
                # Пересылаем сообщение в публичную группу
                success = await self._forward_message_to_group(message, target_group, settings)
                
                if success:
                    task_info["forwarded_count"] += 1
                
                # Задержка между пересылками
                delay = settings.get("delay_seconds", 0)
                if delay > 0:
                    await asyncio.sleep(delay)
            
            task_info["status"] = "completed"
            logger.info(f"[PUBLIC_FORWARDER] Задача {task_id} завершена")
            
        except Exception as e:
            logger.error(f"[PUBLIC_FORWARDER] Ошибка в задаче {task_id}: {e}")
            task_info["status"] = "error"
            task_info["error"] = str(e)
    
    async def _get_messages_from_channel(self, channel: str, settings: Dict) -> List[Message]:
        """Получить сообщения из канала"""
        try:
            # Получаем последние сообщения
            limit = settings.get("max_posts", 10)
            messages = []
            
            async for message in self.userbot.get_chat_history(channel, limit=limit):
                messages.append(message)
            
            return messages
            
        except Exception as e:
            logger.error(f"[PUBLIC_FORWARDER] Ошибка получения сообщений из {channel}: {e}")
            return []
    
    async def _forward_message_to_group(self, message: Message, target_group: str, 
                                      settings: Dict) -> bool:
        """Переслать сообщение в группу"""
        try:
            # Проверяем фильтры
            if not self._check_message_filters(message, settings):
                return False
            
            # Подготавливаем текст с припиской
            caption = self._prepare_caption(message, settings)
            
            # Пересылаем сообщение
            if message.photo:
                await self.userbot.send_photo(
                    chat_id=target_group,
                    photo=message.photo.file_id,
                    caption=caption,
                    parse_mode="html" if caption else None
                )
            elif message.video:
                await self.userbot.send_video(
                    chat_id=target_group,
                    video=message.video.file_id,
                    caption=caption,
                    parse_mode="html" if caption else None
                )
            elif message.document:
                await self.userbot.send_document(
                    chat_id=target_group,
                    document=message.document.file_id,
                    caption=caption,
                    parse_mode="html" if caption else None
                )
            else:
                # Текстовое сообщение
                await self.userbot.send_message(
                    chat_id=target_group,
                    text=caption or message.text,
                    parse_mode="html" if caption else None
                )
            
            logger.info(f"[PUBLIC_FORWARDER] Переслано сообщение {message.id} в {target_group}")
            return True
            
        except Exception as e:
            logger.error(f"[PUBLIC_FORWARDER] Ошибка пересылки в {target_group}: {e}")
            return False
    
    def _check_message_filters(self, message: Message, settings: Dict) -> bool:
        """Проверить фильтры сообщения"""
        # Фильтр по медиа
        media_filter = settings.get("media_filter", "all")
        if media_filter == "media_only":
            if not (message.photo or message.video or message.document):
                return False
        
        # Фильтр по хэштегам
        hashtag_filter = settings.get("hashtag_filter")
        if hashtag_filter and message.text:
            if hashtag_filter.lower() not in message.text.lower():
                return False
        
        return True
    
    def _prepare_caption(self, message: Message, settings: Dict) -> Optional[str]:
        """Подготовить подпись с припиской"""
        original_text = message.text or message.caption or ""
        footer_text = settings.get("footer_text", "")
        
        if not footer_text:
            return original_text
        
        # Добавляем приписку
        if original_text:
            return f"{original_text}\n\n{footer_text}"
        else:
            return footer_text 