#!/usr/bin/env python3
"""
Массовое обновление ссылок в постах канала
Заменяет ссылку "Приватный канал / Подписаться" на новую, оставляя _TSSH_Fans_ нетронутой
"""

import asyncio
import re
import logging
import sys
import os
from typing import List, Optional
from pyrogram import Client, filters
from pyrogram.types import Message

# Загружаем переменные окружения из .env файла
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class BulkLinkUpdater:
    def __init__(self, session_path: str = None):
        """
        Инициализация обновлятора ссылок
        
        Args:
            session_path: Путь к сессии (по умолчанию использует sessions/egor_ka.session)
        """
        if session_path is None:
            # Используем сессию из корневой папки проекта
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            session_path = os.path.join(project_root, "sessions", "egor_ka.session")
        
        self.session_path = session_path
        self.client = None
        
        # Настройки замены ссылок для "Приватный канал / Подписаться"
        self.old_link = "https://t.me/+ArzlAnjllm5iNmI6"
        self.new_link = "https://t.me/+ybzXQhwkAio4ZGYy"
        self.target_text = "Приватный канал / Подписаться"
        
        # Настройки для "🌐 _TSSH_Fans_"
        self.tsh_fans_text = "🌐 _TSSH_Fans_"
        self.tsh_fans_link = "https://t.me/TESAMSH/4026"
        
        logger.info(f"[BULK_UPDATER] Инициализация с сессией: {session_path}")
        logger.info(f"[BULK_UPDATER] Старая ссылка: {self.old_link}")
        logger.info(f"[BULK_UPDATER] Новая ссылка: {self.new_link}")
        logger.info(f"[BULK_UPDATER] Целевой текст: {self.target_text}")
        logger.info(f"[BULK_UPDATER] TSSH Fans текст: {self.tsh_fans_text}")
        logger.info(f"[BULK_UPDATER] TSSH Fans ссылка: {self.tsh_fans_link}")
    
    async def start_client(self):
        """Запуск клиента Pyrogram"""
        try:
            # Получаем API_ID и API_HASH из переменных окружения
            api_id = os.getenv("API_ID")
            api_hash = os.getenv("API_HASH")
            
            if not api_id or not api_hash:
                logger.error("[BULK_UPDATER] ❌ API_ID или API_HASH не найдены в переменных окружения")
                return False
            
            # Создаем клиент с правильными параметрами
            self.client = Client(
                name="egor_ka",  # Имя сессии
                api_id=int(api_id),
                api_hash=api_hash,
                workdir=os.path.dirname(self.session_path)  # Рабочая директория для сессии
            )
            
            await self.client.start()
            logger.info("[BULK_UPDATER] ✅ Клиент успешно запущен")
            return True
        except Exception as e:
            logger.error(f"[BULK_UPDATER] ❌ Ошибка запуска клиента: {e}")
            return False
    
    async def stop_client(self):
        """Остановка клиента"""
        if self.client:
            try:
                await self.client.stop()
                logger.info("[BULK_UPDATER] Клиент остановлен")
            except Exception as e:
                logger.warning(f"[BULK_UPDATER] Ошибка при остановке клиента: {e}")
    
    def should_update_message(self, text: str, message=None) -> bool:
        """
        Проверяет, нужно ли обновлять сообщение
        
        Args:
            text: Текст сообщения
            message: Объект сообщения для проверки сущностей
            
        Returns:
            True если сообщение содержит "Приватный канал / Подписаться" со старой ссылкой
        """
        if not text:
            return False
        
        # Проверяем наличие целевого текста "Приватный канал / Подписаться"
        has_target_text = self.target_text in text
        
        # Проверяем наличие старой ссылки в обычном тексте
        has_old_link = self.old_link in text
        
        # Проверяем наличие старой ссылки в HTML-формате
        html_pattern = f'href="{self.old_link}"'
        has_html_link = html_pattern in text
        
        # Проверяем наличие старой ссылки в скобках после целевого текста
        target_with_old_link_pattern = rf'{re.escape(self.target_text)}\s*\({re.escape(self.old_link)}\)'
        has_target_with_old_link_pattern = bool(re.search(target_with_old_link_pattern, text))
        
        # Проверяем сущности сообщения для поиска ссылок
        has_old_link_in_entities = False
        if message:
            # Проверяем entities
            if hasattr(message, 'entities') and message.entities:
                for entity in message.entities:
                    if hasattr(entity, 'url') and entity.url == self.old_link:
                        has_old_link_in_entities = True
                        break
            
            # Проверяем caption_entities
            if hasattr(message, 'caption_entities') and message.caption_entities:
                for entity in message.caption_entities:
                    if hasattr(entity, 'url') and entity.url == self.old_link:
                        has_old_link_in_entities = True
                        break
        
        # Проверяем, есть ли уже правильная ссылка для "Приватный канал / Подписаться"
        has_correct_target_link = f'<a href="{self.new_link}">{self.target_text}</a>' in text
        
        # Проверяем, есть ли целевой текст со старой ссылкой (это то, что нам нужно обновить)
        has_target_with_old_link = has_target_text and (has_old_link or has_html_link or has_target_with_old_link_pattern or has_old_link_in_entities) and not has_correct_target_link
        
        # Логируем детали только если есть целевой текст или старая ссылка
        if has_target_text or has_old_link or has_html_link or has_target_with_old_link_pattern or has_old_link_in_entities:
            logger.info(f"[BULK_UPDATER] 🔍 Анализ текста:")
            logger.info(f"[BULK_UPDATER]   Целевой текст: {has_target_text}")
            logger.info(f"[BULK_UPDATER]   Обычная ссылка: {has_old_link}")
            logger.info(f"[BULK_UPDATER]   HTML ссылка: {has_html_link}")
            logger.info(f"[BULK_UPDATER]   Целевой текст со старой ссылкой в скобках: {has_target_with_old_link_pattern}")
            logger.info(f"[BULK_UPDATER]   Старая ссылка в сущностях: {has_old_link_in_entities}")
            logger.info(f"[BULK_UPDATER]   Правильная ссылка для цели: {has_correct_target_link}")
            logger.info(f"[BULK_UPDATER]   Целевой текст со старой ссылкой: {has_target_with_old_link}")
            logger.info(f"[BULK_UPDATER]   Текст: {text[:200]}...")
        
        return has_target_with_old_link
    
    def update_message_text(self, text: str) -> str:
        """
        Обновляет текст сообщения, заменяя старую ссылку на новую для "Приватный канал / Подписаться"
        и добавляя ссылку к "🌐 _TSSH_Fans_" если её нет
        
        Args:
            text: Исходный текст
            
        Returns:
            Обновленный текст
        """
        if not text:
            return text
        
        updated_text = text
        
        # Заменяем обычную ссылку в скобках для "Приватный канал / Подписаться"
        pattern = rf'({re.escape(self.target_text)})\s*\({re.escape(self.old_link)}\)'
        replacement = rf'\1({self.new_link})'
        updated_text = re.sub(pattern, replacement, updated_text)
        
        # Заменяем HTML-ссылку для старой ссылки
        html_pattern = f'href="{self.old_link}"'
        html_replacement = f'href="{self.new_link}"'
        updated_text = updated_text.replace(html_pattern, html_replacement)
        
        # Заменяем обычную ссылку в тексте
        updated_text = updated_text.replace(self.old_link, self.new_link)
        
        # Добавляем HTML-гиперссылку к тексту "Приватный канал / Подписаться" без ссылки
        # Проверяем, что правильная ссылка еще не существует
        correct_target_link = f'<a href="{self.new_link}">{self.target_text}</a>'
        if self.target_text in updated_text and correct_target_link not in updated_text:
            # Проверяем, есть ли уже гиперссылка для этого текста
            if f'<a href="' in updated_text and self.target_text in updated_text:
                # Если есть другие гиперссылки, добавляем новую только к целевому тексту
                # Используем более простой паттерн без look-behind
                pattern = rf'({re.escape(self.target_text)})'
                replacement = rf'<a href="{self.new_link}">\1</a>'
                updated_text = re.sub(pattern, replacement, updated_text)
            else:
                # Если нет других гиперссылок, просто заменяем
                pattern = rf'({re.escape(self.target_text)})'
                replacement = rf'<a href="{self.new_link}">\1</a>'
                updated_text = re.sub(pattern, replacement, updated_text)
        
        # Добавляем HTML-гиперссылку к тексту "🌐 _TSSH_Fans_" без ссылки
        # Проверяем, что правильная ссылка еще не существует
        correct_tsh_fans_link = f'<a href="{self.tsh_fans_link}">{self.tsh_fans_text}</a>'
        if self.tsh_fans_text in updated_text and correct_tsh_fans_link not in updated_text:
            # Проверяем, есть ли уже гиперссылка для этого текста
            if f'<a href="' in updated_text and self.tsh_fans_text in updated_text:
                # Если есть другие гиперссылки, добавляем новую только к целевому тексту
                # Используем более простой паттерн без look-behind
                pattern = rf'({re.escape(self.tsh_fans_text)})'
                replacement = rf'<a href="{self.tsh_fans_link}">\1</a>'
                updated_text = re.sub(pattern, replacement, updated_text)
            else:
                # Если нет других гиперссылок, просто заменяем
                pattern = rf'({re.escape(self.tsh_fans_text)})'
                replacement = rf'<a href="{self.tsh_fans_link}">\1</a>'
                updated_text = re.sub(pattern, replacement, updated_text)
        
        if updated_text != text:
            logger.info(f"[BULK_UPDATER] 🔄 Текст обновлен:")
            logger.info(f"[BULK_UPDATER]   Было: {text}")
            logger.info(f"[BULK_UPDATER]   Стало: {updated_text}")
        
        return updated_text
    
    async def get_channel_messages(self, channel_id: str, limit: int = 1000) -> List[Message]:
        """
        Получает сообщения из канала
        
        Args:
            channel_id: ID канала
            limit: Максимальное количество сообщений
            
        Returns:
            Список сообщений
        """
        try:
            messages = []
            async for message in self.client.get_chat_history(channel_id, limit=limit):
                if message.text or message.caption:
                    messages.append(message)
            
            logger.info(f"[BULK_UPDATER] 📊 Получено {len(messages)} сообщений из канала {channel_id}")
            return messages
        except Exception as e:
            logger.error(f"[BULK_UPDATER] ❌ Ошибка получения сообщений: {e}")
            return []
    
    async def update_channel_messages(self, channel_id: str, dry_run: bool = True) -> dict:
        """
        Обновляет ссылки в сообщениях канала
        
        Args:
            channel_id: ID канала
            dry_run: Если True, только показывает что будет изменено, не редактирует
            
        Returns:
            Статистика обновлений
        """
        logger.info(f"[BULK_UPDATER] 🚀 Начинаем обновление канала {channel_id}")
        if dry_run:
            logger.info("[BULK_UPDATER] 🔍 РЕЖИМ ПРЕДПРОСМОТРА - изменения не будут применены")
        
        # Получаем сообщения
        messages = await self.get_channel_messages(channel_id)
        if not messages:
            logger.warning(f"[BULK_UPDATER] ⚠️ Не найдено сообщений в канале {channel_id}")
            return {"total": 0, "updated": 0, "errors": 0}
        
        stats = {
            "total": len(messages),
            "updated": 0,
            "errors": 0,
            "skipped": 0
        }
        
        for message in messages:
            try:
                # Проверяем текст сообщения
                text = message.text or message.caption or ""
                
                # Добавляем подробное логирование для первых 5 сообщений
                if stats["total"] <= 5 or stats["skipped"] < 5:
                    logger.info(f"[BULK_UPDATER] 🔍 Анализ сообщения {message.id}: {text}")
                    # Ищем ссылки в тексте
                    import re
                    links = re.findall(r'https://t\.me/[^\s\)]+', text)
                    if links:
                        logger.info(f"[BULK_UPDATER] 🔗 Найденные ссылки: {links}")
                
                if not self.should_update_message(text, message):
                    stats["skipped"] += 1
                    continue
                
                # Обновляем текст
                updated_text = self.update_message_text(text)
                
                if updated_text != text:
                    stats["updated"] += 1
                    
                    if not dry_run:
                        # Редактируем сообщение
                        try:
                            if message.text:
                                await self.client.edit_message_text(
                                    chat_id=channel_id,
                                    message_id=message.id,
                                    text=updated_text
                                )
                            elif message.caption:
                                # Для медиа-сообщений редактируем caption
                                await self.client.edit_message_caption(
                                    chat_id=channel_id,
                                    message_id=message.id,
                                    caption=updated_text
                                )
                            logger.info(f"[BULK_UPDATER] ✅ Сообщение {message.id} обновлено")
                        except Exception as e:
                            logger.error(f"[BULK_UPDATER] ❌ Ошибка редактирования сообщения {message.id}: {e}")
                            stats["errors"] += 1
                    else:
                        logger.info(f"[BULK_UPDATER] 🔍 Будет обновлено сообщение {message.id}")
                
            except Exception as e:
                logger.error(f"[BULK_UPDATER] ❌ Ошибка обработки сообщения {message.id}: {e}")
                stats["errors"] += 1
        
        logger.info(f"[BULK_UPDATER] 📊 Статистика обновлений:")
        logger.info(f"[BULK_UPDATER]   Всего сообщений: {stats['total']}")
        logger.info(f"[BULK_UPDATER]   Обновлено: {stats['updated']}")
        logger.info(f"[BULK_UPDATER]   Пропущено: {stats['skipped']}")
        logger.info(f"[BULK_UPDATER]   Ошибок: {stats['errors']}")
        
        return stats

async def main():
    """Главная функция для запуска обновления"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Массовое обновление ссылок в канале")
    parser.add_argument("channel_id", help="ID канала для обновления")
    parser.add_argument("--session", help="Путь к файлу сессии")
    parser.add_argument("--dry-run", action="store_true", help="Только предпросмотр, без изменений")
    parser.add_argument("--limit", type=int, default=1000, help="Максимальное количество сообщений")
    
    args = parser.parse_args()
    
    # Создаем обновлятор
    updater = BulkLinkUpdater(session_path=args.session)
    
    try:
        # Запускаем клиент
        if not await updater.start_client():
            logger.error("[BULK_UPDATER] ❌ Не удалось запустить клиент")
            return
        
        # Обновляем сообщения
        stats = await updater.update_channel_messages(args.channel_id, dry_run=args.dry_run)
        
        if args.dry_run:
            logger.info("[BULK_UPDATER] 🔍 РЕЖИМ ПРЕДПРОСМОТРА ЗАВЕРШЕН")
            logger.info("[BULK_UPDATER] Для применения изменений запустите без --dry-run")
        else:
            logger.info("[BULK_UPDATER] ✅ ОБНОВЛЕНИЕ ЗАВЕРШЕНО")
    
    except KeyboardInterrupt:
        logger.info("[BULK_UPDATER] ⚠️ Операция прервана пользователем")
    except Exception as e:
        logger.error(f"[BULK_UPDATER] ❌ Критическая ошибка: {e}")
    finally:
        await updater.stop_client()

if __name__ == "__main__":
    asyncio.run(main()) 