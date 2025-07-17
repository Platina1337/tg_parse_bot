import logging
from typing import Dict, List, Optional, Any
from parser.session_manager import SessionManager

logger = logging.getLogger(__name__)

class ReactionManager:
    """Manager for adding reactions to messages using multiple accounts"""
    
    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager
    
    async def add_reaction(self, chat_id: str, message_id: int, reaction: str, 
                          session_names: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Add a reaction to a message using multiple accounts
        
        Args:
            chat_id: ID of the chat containing the message
            message_id: ID of the message to react to
            reaction: Emoji reaction to add
            session_names: List of session names to use (if None, use all reaction sessions)
            
        Returns:
            Dictionary with results for each session
        """
        try:
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