import asyncio
import sys
import os
from typing import List

# Добавляем путь к корню проекта
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyrogram import Client
from pyrogram.types import Message

async def demonstrate_media_group_forwarding():
    """Демонстрация различных способов пересылки медиагрупп"""
    
    print("=== Способы пересылки медиагрупп ===\n")
    
    print("🎯 **Способ 1: Пересылка всех сообщений медиагруппы (рекомендуемый)**")
    print("""
    # 1. Находим все сообщения медиагруппы
    media_group_messages = [msg for msg in messages if msg.media_group_id == target_group_id]
    
    # 2. Сортируем по ID для правильного порядка
    media_group_messages.sort(key=lambda m: m.id)
    
    # 3. Пересылаем каждое сообщение отдельно
    for message in media_group_messages:
        await userbot.forward_messages(
            chat_id=target_chat,
            from_chat_id=source_chat,
            message_ids=[message.id]
        )
    """)
    
    print("\n🎯 **Способ 2: Пересылка всех сообщений одним вызовом**")
    print("""
    # 1. Собираем ID всех сообщений медиагруппы
    media_group_messages = [msg for msg in messages if msg.media_group_id == target_group_id]
    message_ids = [msg.id for msg in sorted(media_group_messages, key=lambda m: m.id)]
    
    # 2. Пересылаем все сразу
    await userbot.forward_messages(
        chat_id=target_chat,
        from_chat_id=source_chat,
        message_ids=message_ids  # Список всех ID
    )
    """)
    
    print("\n🎯 **Способ 3: Пересылка только первого сообщения медиагруппы**")
    print("""
    # 1. Находим первое сообщение медиагруппы
    media_group_messages = [msg for msg in messages if msg.media_group_id == target_group_id]
    if media_group_messages:
        first_message = min(media_group_messages, key=lambda m: m.id)
        
        # 2. Пересылаем только его
        await userbot.forward_messages(
            chat_id=target_chat,
            from_chat_id=source_chat,
            message_ids=[first_message.id]
        )
    """)
    
    print("\n🎯 **Способ 4: Копирование медиагруппы (не пересылка)**")
    print("""
    # 1. Собираем медиа объекты
    media_group_messages = [msg for msg in messages if msg.media_group_id == target_group_id]
    media_objects = []
    
    for msg in sorted(media_group_messages, key=lambda m: m.id):
        if msg.photo:
            media_objects.append(InputMediaPhoto(msg.photo.file_id))
        elif msg.video:
            media_objects.append(InputMediaVideo(msg.video.file_id))
        # ... другие типы медиа
    
    # 2. Отправляем как новую медиагруппу
    await userbot.send_media_group(
        chat_id=target_chat,
        media=media_objects
    )
    """)
    
    print("\n🎯 **Способ 5: Умная пересылка с проверкой**")
    print("""
    async def forward_media_group_smart(userbot, source_chat, target_chat, media_group_id):
        # 1. Получаем все сообщения медиагруппы
        media_group_messages = []
        async for message in userbot.get_chat_history(source_chat, limit=100):
            if getattr(message, 'media_group_id', None) == media_group_id:
                media_group_messages.append(message)
        
        if not media_group_messages:
            print(f"Медиагруппа {media_group_id} не найдена")
            return False
        
        # 2. Сортируем по ID
        media_group_messages.sort(key=lambda m: m.id)
        
        # 3. Пересылаем все сообщения
        try:
            for message in media_group_messages:
                await userbot.forward_messages(
                    chat_id=target_chat,
                    from_chat_id=source_chat,
                    message_ids=[message.id]
                )
            print(f"Переслана медиагруппа {media_group_id} ({len(media_group_messages)} сообщений)")
            return True
        except Exception as e:
            print(f"Ошибка пересылки медиагруппы {media_group_id}: {e}")
            return False
    """)
    
    print("\n🎯 **Способ 6: Пересылка с обработкой ошибок**")
    print("""
    async def forward_media_group_with_retry(userbot, source_chat, target_chat, media_group_id, max_retries=3):
        for attempt in range(max_retries):
            try:
                # Получаем сообщения медиагруппы
                media_group_messages = []
                async for message in userbot.get_chat_history(source_chat, limit=200):
                    if getattr(message, 'media_group_id', None) == media_group_id:
                        media_group_messages.append(message)
                
                if not media_group_messages:
                    print(f"Медиагруппа {media_group_id} не найдена")
                    return False
                
                # Сортируем и пересылаем
                media_group_messages.sort(key=lambda m: m.id)
                
                for message in media_group_messages:
                    await userbot.forward_messages(
                        chat_id=target_chat,
                        from_chat_id=source_chat,
                        message_ids=[message.id]
                    )
                
                print(f"✅ Медиагруппа {media_group_id} успешно переслана")
                return True
                
            except Exception as e:
                print(f"❌ Попытка {attempt + 1} неудачна: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Экспоненциальная задержка
                else:
                    print(f"❌ Все попытки исчерпаны для медиагруппы {media_group_id}")
                    return False
    """)
    
    print("\n📋 **Сравнение способов:**")
    print("""
    | Способ | Преимущества | Недостатки | Использование |
    |--------|-------------|------------|---------------|
    | 1 (цикл) | Простота, контроль | Много запросов | Для небольших групп |
    | 2 (массив) | Один запрос | Сложнее обработка ошибок | Для надежных соединений |
    | 3 (первое) | Быстрота | Теряются остальные файлы | Для превью |
    | 4 (копирование) | Новый контент | Теряется источник | Для анонимности |
    | 5 (умная) | Надежность | Сложность | Для продакшена |
    | 6 (с повторами) | Максимальная надежность | Медленность | Для критичных данных |
    """)
    
    print("\n🎯 **Рекомендации:**")
    print("""
    ✅ Для большинства случаев: Способ 1 (цикл по сообщениям)
    ✅ Для больших медиагрупп: Способ 2 (массив ID)
    ✅ Для продакшена: Способ 5 (умная пересылка)
    ✅ Для критичных данных: Способ 6 (с повторами)
    """)

if __name__ == "__main__":
    asyncio.run(demonstrate_media_group_forwarding()) 