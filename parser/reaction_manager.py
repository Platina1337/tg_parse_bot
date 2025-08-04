import logging
from typing import Dict, List, Optional, Any
from .session_manager import SessionManager

logger = logging.getLogger(__name__)

class ReactionManager:
    """Manager for adding reactions to messages using multiple accounts"""
    
    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager
    
    async def add_reaction(self, chat_id: str, message_id: int, reaction: str, 
                          session_names: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Add a reaction to a message using multiple accounts
        """
        try:
            # Если session_names не указаны, берем все сессии, назначенные на reactions
            if session_names is None:
                sessions = await self.session_manager.get_sessions_for_task('reactions')
                session_names = [s.alias for s in sessions]
            results = await self.session_manager.add_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=reaction,
                session_names=session_names
            )
            # Count successes and failures
            success_count = sum(1 for status in results.values() if status == "success")
            error_count = len(results) - success_count
            return {
                "success": True,
                "results": results,
                "summary": {
                    "total": len(results),
                    "success": success_count,
                    "error": error_count
                }
            }
        except Exception as e:
            logger.error(f"Error adding reactions: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def add_reaction_to_multiple_messages(self, chat_id: str, message_ids: List[int], 
                                              reaction: str, session_names: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Add reactions to multiple messages using multiple accounts
        
        Args:
            chat_id: ID of the chat containing the messages
            message_ids: List of message IDs to react to
            reaction: Emoji reaction to add
            session_names: List of session names to use (if None, use all reaction sessions)
            
        Returns:
            Dictionary with results for each message and session
        """
        results = {}
        success_count = 0
        error_count = 0
        
        for message_id in message_ids:
            try:
                message_result = await self.add_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    reaction=reaction,
                    session_names=session_names
                )
                
                results[str(message_id)] = message_result
                
                if message_result.get("success", False):
                    success_count += 1
                else:
                    error_count += 1
            except Exception as e:
                logger.error(f"Error adding reactions to message {message_id}: {e}")
                results[str(message_id)] = {"success": False, "error": str(e)}
                error_count += 1
        
        return {
            "success": error_count == 0,
            "results": results,
            "summary": {
                "total_messages": len(message_ids),
                "success": success_count,
                "error": error_count
            }
        }
    
    async def group_messages_by_media_group(self, messages: List) -> Dict[str, List]:
        """
        Группирует сообщения по медиагруппам
        
        Args:
            messages: Список сообщений из get_chat_history
            
        Returns:
            Словарь {media_group_id: [messages]}, где media_group_id может быть None для одиночных сообщений
        """
        media_groups = {}
        
        for message in messages:
            # Получаем media_group_id из сообщения
            media_group_id = getattr(message, 'media_group_id', None)
            
            if media_group_id is not None:
                # Это часть медиагруппы
                if media_group_id not in media_groups:
                    media_groups[media_group_id] = []
                media_groups[media_group_id].append(message)
            else:
                # Это одиночное сообщение
                if None not in media_groups:
                    media_groups[None] = []
                media_groups[None].append(message)
        
        return media_groups
    
    async def get_main_message_from_media_group(self, messages: List) -> Optional:
        """
        Выбирает основное сообщение из медиагруппы для постановки реакции
        
        Приоритет:
        1. Сообщение с текстом (caption или text)
        2. Первое сообщение в группе
        
        Args:
            messages: Список сообщений медиагруппы
            
        Returns:
            Основное сообщение для реакции
        """
        if not messages:
            return None
        
        # Сортируем по ID (обычно первое сообщение имеет меньший ID)
        sorted_messages = sorted(messages, key=lambda x: x.id)
        
        # Ищем сообщение с текстом
        for message in sorted_messages:
            if hasattr(message, 'text') and message.text:
                return message
            if hasattr(message, 'caption') and message.caption:
                return message
        
        # Если нет сообщений с текстом, возвращаем первое
        return sorted_messages[0]
    
    async def add_reaction_to_posts(self, chat_id: str, messages: List, reaction: str,
                                  session_names: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Добавляет реакции к постам (медиагруппам), а не к отдельным сообщениям
        
        Args:
            chat_id: ID чата
            messages: Список сообщений из get_chat_history
            reaction: Эмодзи для реакции
            session_names: Список сессий для использования
            
        Returns:
            Результат операции
        """
        try:
            # Группируем сообщения по медиагруппам
            media_groups = await self.group_messages_by_media_group(messages)
            
            # Получаем сессии для реакций
            if session_names is None:
                sessions = await self.session_manager.get_sessions_for_task('reactions')
                session_names = [s.alias for s in sessions]
            
            results = {}
            total_posts = len(media_groups)
            success_count = 0
            error_count = 0
            
            logger.info(f"[REACTION_MANAGER] Обрабатываем {total_posts} постов (медиагрупп)")
            
            for media_group_id, group_messages in media_groups.items():
                try:
                    # Выбираем основное сообщение для реакции
                    main_message = await self.get_main_message_from_media_group(group_messages)
                    
                    if main_message is None:
                        logger.warning(f"[REACTION_MANAGER] Не удалось найти основное сообщение для медиагруппы {media_group_id}")
                        continue
                    
                    # Добавляем реакцию к основному сообщению
                    post_result = await self.session_manager.add_reaction(
                        chat_id=chat_id,
                        message_id=main_message.id,
                        reaction=reaction,
                        session_names=session_names
                    )
                    
                    # Проверяем успешность
                    post_success = all(status == "success" for status in post_result.values())
                    
                    results[str(media_group_id or f"single_{main_message.id}")] = {
                        "main_message_id": main_message.id,
                        "total_messages_in_group": len(group_messages),
                        "success": post_success,
                        "session_results": post_result
                    }
                    
                    if post_success:
                        success_count += 1
                        logger.info(f"[REACTION_MANAGER] Реакция поставлена на пост {media_group_id or main_message.id} (сообщение {main_message.id})")
                    else:
                        error_count += 1
                        logger.error(f"[REACTION_MANAGER] Ошибка при постановке реакции на пост {media_group_id or main_message.id}")
                        
                except Exception as e:
                    logger.error(f"[REACTION_MANAGER] Ошибка при обработке медиагруппы {media_group_id}: {e}")
                    results[str(media_group_id or "unknown")] = {
                        "success": False,
                        "error": str(e)
                    }
                    error_count += 1
            
            return {
                "success": error_count == 0,
                "results": results,
                "summary": {
                    "total_posts": total_posts,
                    "success": success_count,
                    "error": error_count
                }
            }
            
        except Exception as e:
            logger.error(f"[REACTION_MANAGER] Ошибка при добавлении реакций к постам: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_available_reactions(self) -> List[str]:
        """
        Get list of available reaction emojis
        
        Returns:
            List of available reaction emojis
        """
        # Common reaction emojis in Telegram
        return [
            "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", 
            "🤯", "😱", "🤬", "😢", "🎉", "🤩", "🤮", "💩", 
            "🙏", "👌", "🕊", "🤡", "🥱", "🥴", "��", "🐳"
        ] 