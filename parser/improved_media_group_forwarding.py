import asyncio
import sys
import os
from typing import List, Optional, Dict, Any

# Добавляем путь к корню проекта
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyrogram import Client
from pyrogram.types import Message, InputMediaPhoto, InputMediaVideo, InputMediaDocument

class MediaGroupForwarder:
    """Улучшенный класс для пересылки медиагрупп"""
    
    def __init__(self, userbot: Client):
        self.userbot = userbot
    
    async def forward_media_group_method1(self, source_chat: str, target_chat: str, media_group_id: str) -> bool:
        """
        Способ 1: Пересылка всех сообщений медиагруппы по одному
        Рекомендуется для небольших медиагрупп
        """
        try:
            # Получаем все сообщения медиагруппы
            media_group_messages = []
            async for message in self.userbot.get_chat_history(source_chat, limit=200):
                if getattr(message, 'media_group_id', None) == media_group_id:
                    media_group_messages.append(message)
            
            if not media_group_messages:
                print(f"❌ Медиагруппа {media_group_id} не найдена")
                return False
            
            # Сортируем по ID для правильного порядка
            media_group_messages.sort(key=lambda m: m.id)
            
            # Пересылаем каждое сообщение отдельно
            for message in media_group_messages:
                await self.userbot.forward_messages(
                    chat_id=target_chat,
                    from_chat_id=source_chat,
                    message_ids=[message.id]
                )
            
            print(f"✅ Медиагруппа {media_group_id} переслана ({len(media_group_messages)} сообщений)")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка пересылки медиагруппы {media_group_id}: {e}")
            return False
    
    async def forward_media_group_method2(self, source_chat: str, target_chat: str, media_group_id: str) -> bool:
        """
        Способ 2: Пересылка всех сообщений одним вызовом
        Рекомендуется для больших медиагрупп
        """
        try:
            # Получаем все сообщения медиагруппы
            media_group_messages = []
            async for message in self.userbot.get_chat_history(source_chat, limit=200):
                if getattr(message, 'media_group_id', None) == media_group_id:
                    media_group_messages.append(message)
            
            if not media_group_messages:
                print(f"❌ Медиагруппа {media_group_id} не найдена")
                return False
            
            # Собираем ID всех сообщений
            message_ids = [msg.id for msg in sorted(media_group_messages, key=lambda m: m.id)]
            
            # Пересылаем все сразу
            await self.userbot.forward_messages(
                chat_id=target_chat,
                from_chat_id=source_chat,
                message_ids=message_ids
            )
            
            print(f"✅ Медиагруппа {media_group_id} переслана одним вызовом ({len(message_ids)} сообщений)")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка пересылки медиагруппы {media_group_id}: {e}")
            return False
    
    async def forward_media_group_method3(self, source_chat: str, target_chat: str, media_group_id: str) -> bool:
        """
        Способ 3: Пересылка только первого сообщения медиагруппы
        Для превью или когда нужен только один файл
        """
        try:
            # Получаем все сообщения медиагруппы
            media_group_messages = []
            async for message in self.userbot.get_chat_history(source_chat, limit=200):
                if getattr(message, 'media_group_id', None) == media_group_id:
                    media_group_messages.append(message)
            
            if not media_group_messages:
                print(f"❌ Медиагруппа {media_group_id} не найдена")
                return False
            
            # Берем первое сообщение
            first_message = min(media_group_messages, key=lambda m: m.id)
            
            # Пересылаем только его
            await self.userbot.forward_messages(
                chat_id=target_chat,
                from_chat_id=source_chat,
                message_ids=[first_message.id]
            )
            
            print(f"✅ Первое сообщение медиагруппы {media_group_id} переслано")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка пересылки медиагруппы {media_group_id}: {e}")
            return False
    
    async def copy_media_group(self, source_chat: str, target_chat: str, media_group_id: str) -> bool:
        """
        Способ 4: Копирование медиагруппы (не пересылка)
        Создает новый контент без ссылки на источник
        """
        try:
            # Получаем все сообщения медиагруппы
            media_group_messages = []
            async for message in self.userbot.get_chat_history(source_chat, limit=200):
                if getattr(message, 'media_group_id', None) == media_group_id:
                    media_group_messages.append(message)
            
            if not media_group_messages:
                print(f"❌ Медиагруппа {media_group_id} не найдена")
                return False
            
            # Сортируем по ID
            media_group_messages.sort(key=lambda m: m.id)
            
            # Собираем медиа объекты
            media_objects = []
            caption = None
            
            for message in media_group_messages:
                if message.photo:
                    media_objects.append(InputMediaPhoto(message.photo.file_id))
                elif message.video:
                    media_objects.append(InputMediaVideo(message.video.file_id))
                elif message.document:
                    media_objects.append(InputMediaDocument(message.document.file_id))
                
                # Берем caption из первого сообщения с caption
                if not caption and getattr(message, 'caption', None):
                    caption = message.caption
            
            if not media_objects:
                print(f"❌ В медиагруппе {media_group_id} нет поддерживаемых файлов")
                return False
            
            # Добавляем caption к первому медиа объекту
            if caption and media_objects:
                media_objects[0].caption = caption
            
            # Отправляем как новую медиагруппу
            await self.userbot.send_media_group(
                chat_id=target_chat,
                media=media_objects
            )
            
            print(f"✅ Медиагруппа {media_group_id} скопирована ({len(media_objects)} файлов)")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка копирования медиагруппы {media_group_id}: {e}")
            return False
    
    async def forward_media_group_smart(self, source_chat: str, target_chat: str, media_group_id: str, 
                                      strategy: str = "auto") -> bool:
        """
        Умная пересылка с выбором стратегии
        """
        try:
            # Получаем все сообщения медиагруппы
            media_group_messages = []
            async for message in self.userbot.get_chat_history(source_chat, limit=200):
                if getattr(message, 'media_group_id', None) == media_group_id:
                    media_group_messages.append(message)
            
            if not media_group_messages:
                print(f"❌ Медиагруппа {media_group_id} не найдена")
                return False
            
            # Автоматический выбор стратегии
            if strategy == "auto":
                if len(media_group_messages) <= 3:
                    strategy = "method1"  # Небольшие группы - по одному
                elif len(media_group_messages) <= 10:
                    strategy = "method2"  # Средние группы - одним вызовом
                else:
                    strategy = "method3"  # Большие группы - только первое
            
            # Выполняем выбранную стратегию
            if strategy == "method1":
                return await self.forward_media_group_method1(source_chat, target_chat, media_group_id)
            elif strategy == "method2":
                return await self.forward_media_group_method2(source_chat, target_chat, media_group_id)
            elif strategy == "method3":
                return await self.forward_media_group_method3(source_chat, target_chat, media_group_id)
            elif strategy == "copy":
                return await self.copy_media_group(source_chat, target_chat, media_group_id)
            else:
                print(f"❌ Неизвестная стратегия: {strategy}")
                return False
                
        except Exception as e:
            print(f"❌ Ошибка умной пересылки медиагруппы {media_group_id}: {e}")
            return False
    
    async def forward_media_group_with_retry(self, source_chat: str, target_chat: str, media_group_id: str, 
                                           max_retries: int = 3, strategy: str = "method1") -> bool:
        """
        Пересылка с повторными попытками
        """
        for attempt in range(max_retries):
            try:
                print(f"🔄 Попытка {attempt + 1}/{max_retries} для медиагруппы {media_group_id}")
                
                if strategy == "method1":
                    success = await self.forward_media_group_method1(source_chat, target_chat, media_group_id)
                elif strategy == "method2":
                    success = await self.forward_media_group_method2(source_chat, target_chat, media_group_id)
                elif strategy == "method3":
                    success = await self.forward_media_group_method3(source_chat, target_chat, media_group_id)
                elif strategy == "copy":
                    success = await self.copy_media_group(source_chat, target_chat, media_group_id)
                else:
                    success = await self.forward_media_group_smart(source_chat, target_chat, media_group_id, strategy)
                
                if success:
                    print(f"✅ Медиагруппа {media_group_id} успешно переслана с попытки {attempt + 1}")
                    return True
                else:
                    print(f"❌ Попытка {attempt + 1} неудачна")
                    
            except Exception as e:
                print(f"❌ Ошибка на попытке {attempt + 1}: {e}")
            
            # Ждем перед следующей попыткой (экспоненциальная задержка)
            if attempt < max_retries - 1:
                delay = 2 ** attempt
                print(f"⏳ Ждем {delay} секунд перед следующей попыткой...")
                await asyncio.sleep(delay)
        
        print(f"❌ Все {max_retries} попыток исчерпаны для медиагруппы {media_group_id}")
        return False

# Пример использования
async def example_usage():
    """Пример использования MediaGroupForwarder"""
    
    print("=== Пример использования MediaGroupForwarder ===\n")
    
    # Создаем экземпляр (нужно будет настроить userbot)
    # userbot = Client("session_name", api_id=API_ID, api_hash=API_HASH)
    # forwarder = MediaGroupForwarder(userbot)
    
    print("📋 Доступные методы:")
    print("""
    1. forward_media_group_method1() - Пересылка по одному сообщению
    2. forward_media_group_method2() - Пересылка одним вызовом
    3. forward_media_group_method3() - Пересылка только первого
    4. copy_media_group() - Копирование (не пересылка)
    5. forward_media_group_smart() - Умная пересылка
    6. forward_media_group_with_retry() - С повторными попытками
    """)
    
    print("🎯 Пример кода:")
    print("""
    # Создание
    forwarder = MediaGroupForwarder(userbot)
    
    # Простая пересылка
    success = await forwarder.forward_media_group_method1(
        source_chat="@source_channel",
        target_chat="@target_channel", 
        media_group_id="123456789"
    )
    
    # Умная пересылка
    success = await forwarder.forward_media_group_smart(
        source_chat="@source_channel",
        target_chat="@target_channel",
        media_group_id="123456789",
        strategy="auto"  # или "method1", "method2", "method3", "copy"
    )
    
    # С повторными попытками
    success = await forwarder.forward_media_group_with_retry(
        source_chat="@source_channel",
        target_chat="@target_channel",
        media_group_id="123456789",
        max_retries=3,
        strategy="method1"
    )
    """)

if __name__ == "__main__":
    asyncio.run(example_usage()) 