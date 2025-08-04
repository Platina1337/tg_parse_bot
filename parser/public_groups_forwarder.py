import logging
import asyncio
from typing import Dict, List, Optional
from pyrogram import Client
from pyrogram.types import Message
from .database import Database
from .config import config
from shared.models import SessionMeta

logger = logging.getLogger(__name__)

def group_messages_to_posts(messages):
    """
    messages: List[Message]
    return: List[List[Message]] — каждый элемент это пост (одиночное сообщение или медиагруппа)
    """
    posts = []
    media_groups = {}
    singles = []
    for msg in messages:
        if getattr(msg, 'media_group_id', None):
            media_groups.setdefault(msg.media_group_id, []).append(msg)
        else:
            singles.append([msg])
    # Добавить медиагруппы как посты
    for group in media_groups.values():
        posts.append(sorted(group, key=lambda m: m.id))
    posts.extend(singles)
    # Отсортировать посты по дате/ID (по первому сообщению)
    posts.sort(key=lambda post: post[0].date or post[0].id)
    return posts

class PublicGroupsForwarder:
    """Класс для пересылки сообщений в публичные группы"""
    
    def __init__(self, database: Database, session_manager=None):
        self.db = database
        self.session_manager = session_manager
        self.active_tasks = {}  # {task_id: task_info}
        
    async def start_forwarding(self, source_channel: str, target_group: str, 
                             user_id: int, settings: Dict) -> Dict:
        """Запуск пересылки в публичную группу"""
        task_id = f"public_{source_channel}_{target_group}_{user_id}"
        
        if task_id in self.active_tasks:
            return {"status": "error", "message": "Задача уже запущена"}
        
        # Получаем сессию для задачи public_groups
        if not self.session_manager:
            return {"status": "error", "message": "Session manager не доступен"}
        
        sessions = await self.session_manager.get_sessions_for_task("public_groups")
        if not sessions:
            return {"status": "error", "message": "Нет доступных сессий для задачи 'public_groups'. Назначьте сессию через менеджер сессий."}
        
        # Берем первую доступную сессию
        session_alias = sessions[0].alias
        userbot = await self.session_manager.get_client(session_alias)
        if not userbot:
            return {"status": "error", "message": f"Не удалось получить клиент для сессии {session_alias}"}
        
        # Значения по умолчанию
        posts_count = settings.get("posts_count", 20)
        views_limit = settings.get("views_limit", 50)
        delay_seconds = settings.get("delay_seconds", 0)
        settings["posts_count"] = posts_count
        settings["views_limit"] = views_limit
        settings["delay_seconds"] = delay_seconds
        
        # Создаем задачу
        task_info = {
            "source_channel": source_channel,
            "target_group": target_group,
            "user_id": user_id,
            "settings": settings,
            "status": "running",
            "forwarded_count": 0,
            "session_alias": session_alias,
            "userbot": userbot
        }
        
        self.active_tasks[task_id] = task_info
        
        # Запускаем асинхронную задачу
        asyncio.create_task(self._forwarding_worker(task_id))
        
        logger.info(f"[PUBLIC_FORWARDER] Запущена пересылка {task_id} с сессией {session_alias}")
        return {"status": "success", "task_id": task_id, "session_alias": session_alias}
    
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
        task_info = self.active_tasks[task_id]
        source_channel = task_info["source_channel"]
        target_group = task_info["target_group"]
        settings = task_info["settings"]
        userbot = task_info["userbot"]
        session_alias = task_info["session_alias"]
        posts_count = settings.get("posts_count", 20)
        views_limit = settings.get("views_limit", 50)
        delay_seconds = settings.get("delay_seconds", 0)
        forward_one_from_group = settings.get("forward_one_from_group", False)
        # already_forwarded = set()  # больше не нужен
        try:
            if not hasattr(userbot, 'is_connected') or not userbot.is_connected:
                await userbot.start()
                logger.info(f"[PUBLIC_FORWARDER] Запущена сессия {session_alias}")
            try:
                await userbot.get_chat(target_group)
            except Exception as e:
                logger.warning(f"[PUBLIC_FORWARDER] Не удалось получить чат {target_group}: {e}")
            while task_info["status"] == "running":
                messages = []
                async for message in userbot.get_chat_history(source_channel, limit=200):
                    messages.append(message)
                posts = group_messages_to_posts(messages)
                if not posts:
                    logger.info(f"[PUBLIC_FORWARDER] Нет постов для анализа, завершаем задачу {task_id}")
                    break
                logical_posts = []
                for post in posts[-posts_count:]:
                    logical_posts.append(post)
                last_posts = logical_posts
                logger.info(f"[PUBLIC_FORWARDER] Анализируем {len(last_posts)} постов (медиагрупп/одиночных) в диапазоне:")
                for idx, post in enumerate(last_posts):
                    views = getattr(post[0], 'views', None)
                    logger.info(f"  {idx+1}. message_id={post[0].id}, views={views}")
                candidates = [post for post in last_posts if getattr(post[0], 'views', 0) < views_limit]
                if not candidates:
                    logger.info(f"[PUBLIC_FORWARDER] У всех постов просмотры >= лимита {views_limit}, задача завершена")
                    break
                min_views = None
                min_post = None
                for post in candidates:
                    views = getattr(post[0], 'views', None)
                    if views is None:
                        continue
                    if min_views is None or views < min_views:
                        min_views = views
                        min_post = post
                logger.info(f"[PUBLIC_FORWARDER] К пересылке выбран post message_id={min_post[0].id} с views={min_views}")
                try:
                    if forward_one_from_group or len(min_post) == 1:
                        # Одиночное сообщение - пересылаем как есть
                        m = min_post[0]
                        await userbot.forward_messages(
                            chat_id=target_group,
                            from_chat_id=source_channel,
                            message_ids=[m.id]
                        )
                        logger.info(f"[PUBLIC_FORWARDER] Переслан одиночный пост {m.id} в {target_group}")
                    else:
                        # Медиагруппа - пересылаем все сообщения одним вызовом
                        message_ids = [m.id for m in sorted(min_post, key=lambda x: x.id)]
                        await userbot.forward_messages(
                            chat_id=target_group,
                            from_chat_id=source_channel,
                            message_ids=message_ids
                        )
                        logger.info(f"[PUBLIC_FORWARDER] Переслана медиагруппа одним вызовом: {message_ids} в {target_group}")
                    task_info["forwarded_count"] += 1
                    logger.info(f"[PUBLIC_FORWARDER] Жду {delay_seconds} сек перед следующей итерацией...")
                except Exception as e:
                    logger.error(f"[PUBLIC_FORWARDER] Ошибка пересылки поста {min_post[0].id}: {e}")
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
            task_info["status"] = "completed"
            logger.info(f"[PUBLIC_FORWARDER] Задача {task_id} завершена")
        except Exception as e:
            logger.error(f"[PUBLIC_FORWARDER] Ошибка в задаче {task_id}: {e}")
            task_info["status"] = "error"
            task_info["error"] = str(e)
        finally:
            try:
                if userbot.is_connected:
                    await userbot.stop()
                    logger.info(f"[PUBLIC_FORWARDER] Сессия {session_alias} остановлена")
            except Exception as e:
                logger.error(f"[PUBLIC_FORWARDER] Ошибка при остановке сессии {session_alias}: {e}")
    
    async def _get_messages_from_channel(self, channel: str, posts_count: int, userbot: Client) -> List[Message]:
        """Получить N последних сообщений из канала"""
        try:
            messages = []
            async for message in userbot.get_chat_history(channel, limit=posts_count):
                messages.append(message)
            return messages
        except Exception as e:
            logger.error(f"[PUBLIC_FORWARDER] Ошибка получения сообщений из {channel}: {e}")
            return []
    
    async def _forward_message_to_group(self, message: Message, target_group: str, 
                                      settings: Dict, userbot: Client) -> bool:
        """Переслать сообщение в группу"""
        try:
            # Проверяем фильтры
            if not self._check_message_filters(message, settings):
                return False
            
            # Подготавливаем текст с припиской
            caption = self._prepare_caption(message, settings)
            
            # Пересылаем сообщение
            if message.photo:
                await userbot.send_photo(
                    chat_id=target_group,
                    photo=message.photo.file_id,
                    caption=caption,
                    parse_mode="html" if caption else None
                )
            elif message.video:
                await userbot.send_video(
                    chat_id=target_group,
                    video=message.video.file_id,
                    caption=caption,
                    parse_mode="html" if caption else None
                )
            elif message.document:
                await userbot.send_document(
                    chat_id=target_group,
                    document=message.document.file_id,
                    caption=caption,
                    parse_mode="html" if caption else None
                )
            else:
                # Текстовое сообщение
                await userbot.send_message(
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