import logging
import asyncio
from typing import Dict, List, Optional
from pyrogram import Client
from pyrogram.types import Message
from .database import Database
from .config import config
from shared.models import SessionMeta

logger = logging.getLogger(__name__)

def is_paid_post(post_messages):
    """
    Проверяет, является ли пост платным (требующим оплаты звезд)
    post_messages: List[Message] - сообщения поста (одиночное или медиагруппа)
    return: bool - True если пост платный
    """
    # Проверяем первое сообщение поста
    first_msg = post_messages[0]

    # Проверяем различные атрибуты, указывающие на платный контент
    if hasattr(first_msg, 'paid_content') and first_msg.paid_content:
        return True

    # Проверяем наличие цены в звездах
    if hasattr(first_msg, 'paid_content_price') and first_msg.paid_content_price is not None and first_msg.paid_content_price > 0:
        return True

    # Проверяем, есть ли текст о платном контенте
    if hasattr(first_msg, 'text') and first_msg.text:
        text_lower = first_msg.text.lower()
        if any(keyword in text_lower for keyword in ['платный контент', 'paid content', 'unlock with stars', 'разблокировать звездами']):
            return True

    # Проверяем подпись (caption) для медиа
    if hasattr(first_msg, 'caption') and first_msg.caption:
        caption_lower = first_msg.caption.lower()
        if any(keyword in caption_lower for keyword in ['платный контент', 'paid content', 'unlock with stars', 'разблокировать звездами']):
            return True

    return False

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
        
    async def start_forwarding(self, source_channel: str, target_groups: List[str],
                             user_id: int, settings: Dict) -> Dict:
        """Запуск пересылки в публичные группы"""
        # Используем хэш от списка групп для стабильного ID
        import hashlib
        target_groups_str = "".join(sorted(target_groups))
        target_groups_hash = hashlib.md5(target_groups_str.encode()).hexdigest()[:8]
        task_id = f"public_{source_channel}_{target_groups_hash}_{user_id}"
        
        # Проверяем, есть ли уже запущенная задача
        if task_id in self.active_tasks:
            existing_status = self.active_tasks[task_id].get("status")
            # Разрешаем запуск только если предыдущая задача завершена или остановлена
            if existing_status == "running":
                return {"status": "error", "message": "Задача уже запущена"}
            else:
                # Удаляем старую задачу, чтобы запустить новую
                logger.info(f"[PUBLIC_FORWARDER] Удаляем завершенную задачу {task_id} со статусом {existing_status}")
                del self.active_tasks[task_id]
        
        # Получаем сессию для задачи public_groups
        if not self.session_manager:
            return {"status": "error", "message": "Session manager не доступен"}
        
        # Проверяем, выбрана ли конкретная сессия пользователем
        session_name = settings.get("session_name")
        
        if session_name:
            # Используем выбранную сессию
            logger.info(f"[PUBLIC_FORWARDER] Используем выбранную пользователем сессию: {session_name}")
            userbot = await self.session_manager.get_client(session_name)
            if not userbot:
                return {"status": "error", "message": f"Не удалось получить клиент для выбранной сессии {session_name}"}
            session_alias = session_name
        else:
            # Автоматический выбор из назначенных сессий
            sessions = await self.session_manager.get_sessions_for_task("public_groups")
            if not sessions:
                return {"status": "error", "message": "Нет доступных сессий для задачи 'public_groups'. Назначьте сессию через менеджер сессий."}
            
            # Берем первую доступную сессию
            session_alias = sessions[0].alias
            logger.info(f"[PUBLIC_FORWARDER] Автоматически выбрана сессия: {session_alias}")
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
            "target_groups": target_groups,
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
        logger.info(f"[PUBLIC_FORWARDER] Попытка остановить задачу: {task_id}")
        logger.info(f"[PUBLIC_FORWARDER] Активные задачи: {list(self.active_tasks.keys())}")
        
        if task_id not in self.active_tasks:
            logger.error(f"[PUBLIC_FORWARDER] Задача {task_id} не найдена в active_tasks")
            return {"status": "error", "message": "Задача не найдена"}
        
        self.active_tasks[task_id]["status"] = "stopped"
        
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
        # Копируем task_info без userbot объекта для безопасности
        tasks = []
        for task_id, task_info in self.active_tasks.items():
            task_copy = {
                "task_id": task_id,
                "source_channel": task_info.get("source_channel"),
                "target_groups": task_info.get("target_groups"),
                "user_id": task_info.get("user_id"),
                "settings": task_info.get("settings"),
                "status": task_info.get("status"),
                "forwarded_count": task_info.get("forwarded_count", 0),
                "session_alias": task_info.get("session_alias")
            }
            tasks.append(task_copy)
        
        return {
            "status": "success",
            "tasks": tasks
        }
    
    async def _forwarding_worker(self, task_id: str):
        task_info = self.active_tasks[task_id]
        source_channel = task_info["source_channel"]
        target_groups = task_info["target_groups"]
        settings = task_info["settings"]
        userbot = task_info["userbot"]
        session_alias = task_info["session_alias"]
        posts_count = int(settings.get("posts_count", 20))
        views_limit = int(settings.get("views_limit", 50))
        delay_seconds = int(settings.get("delay_seconds", 0))
        forward_one_from_group = bool(settings.get("forward_one_from_group", False))
        include_paid_posts = bool(settings.get("include_paid_posts", True))
        # already_forwarded = set()  # больше не нужен
        try:
            if not hasattr(userbot, 'is_connected') or not userbot.is_connected:
                await userbot.start()
                logger.info(f"[PUBLIC_FORWARDER] Запущена сессия {session_alias}")
            try:
                # Проверяем доступ к первой группе для быстрой валидации
                if target_groups:
                    await userbot.get_chat(target_groups[0])
            except Exception as e:
                logger.warning(f"[PUBLIC_FORWARDER] Не удалось получить чат {target_groups[0]}: {e}")
            while self.active_tasks[task_id]["status"] == "running":
                # Проверяем статус перед началом каждой итерации
                if self.active_tasks[task_id]["status"] != "running":
                    logger.info(f"[PUBLIC_FORWARDER] Задача {task_id} остановлена пользователем")
                    break
                    
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
                # Фильтруем по просмотрам с проверкой типов
                candidates = []
                for post in last_posts:
                    views = getattr(post[0], 'views', None)
                    if views is None:
                        views = 0
                    try:
                        if int(views) < views_limit:
                            candidates.append(post)
                    except (ValueError, TypeError):
                        logger.warning(f"[PUBLIC_FORWARDER] Невозможно сравнить просмотры для поста {post[0].id}: views={views}, type={type(views)}")
                        continue

                # Фильтруем платные посты, если настройка отключена
                if not include_paid_posts:
                    paid_posts_count = 0
                    filtered_candidates = []
                    for post in candidates:
                        try:
                            if not is_paid_post(post):
                                filtered_candidates.append(post)
                            else:
                                paid_posts_count += 1
                        except Exception as e:
                            logger.warning(f"[PUBLIC_FORWARDER] Ошибка при проверке платного поста {post[0].id}: {e}")
                            filtered_candidates.append(post)  # В случае ошибки включаем пост
                    candidates = filtered_candidates
                    logger.info(f"[PUBLIC_FORWARDER] Отфильтровано платных постов: {paid_posts_count}")
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
                        
                # Проверяем статус перед пересылкой
                if self.active_tasks[task_id]["status"] != "running":
                    logger.info(f"[PUBLIC_FORWARDER] Задача {task_id} остановлена перед пересылкой")
                    break
                    
                logger.info(f"[PUBLIC_FORWARDER] К пересылке выбран post message_id={min_post[0].id} с views={min_views}")
                try:
                    for target_group in target_groups:
                        # Проверяем статус перед каждой пересылкой
                        if self.active_tasks[task_id]["status"] != "running":
                            logger.info(f"[PUBLIC_FORWARDER] Задача {task_id} остановлена перед пересылкой в {target_group}")
                            break
                        
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
                    
                    if self.active_tasks[task_id]["status"] == "running":
                        self.active_tasks[task_id]["forwarded_count"] += 1
                        logger.info(f"[PUBLIC_FORWARDER] Жду {delay_seconds} сек перед следующей итерацией...")
                except Exception as e:
                    logger.error(f"[PUBLIC_FORWARDER] Ошибка пересылки поста {min_post[0].id}: {e}")
                    
                # Проверяем статус во время задержки
                if delay_seconds > 0:
                    for _ in range(delay_seconds):
                        if self.active_tasks[task_id]["status"] != "running":
                            logger.info(f"[PUBLIC_FORWARDER] Задача {task_id} остановлена во время задержки")
                            break
                        await asyncio.sleep(1)
            self.active_tasks[task_id]["status"] = "completed"
            logger.info(f"[PUBLIC_FORWARDER] Задача {task_id} завершена")
        except Exception as e:
            logger.error(f"[PUBLIC_FORWARDER] Ошибка в задаче {task_id}: {e}")
            if task_id in self.active_tasks:
                self.active_tasks[task_id]["status"] = "error"
            if task_id in self.active_tasks:
                self.active_tasks[task_id]["error"] = str(e)
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