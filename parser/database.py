import sqlite3
import aiosqlite
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple, Set, Any
from shared.models import Message, ParseConfig, ParseMode, SessionMeta
from functools import lru_cache
import asyncio
from collections import defaultdict
import os

logger = logging.getLogger("parser.database")
logger.setLevel(logging.DEBUG)

class Database:
    def __init__(self):
        # Всегда использовать относительный путь для Docker
        self.db_path = "parser.db"
        self.conn: Optional[aiosqlite.Connection] = None  # Будет хранить асинхронное соединение
        self._message_cache: Dict[int, Set[int]] = defaultdict(set)  # channel_id -> set of message_ids
        self._media_group_cache: Dict[int, Dict[int, List[int]]] = defaultdict(dict)  # channel_id -> {group_id -> message_ids}
        self._lock = asyncio.Lock()

    async def init(self):
        """Инициализация базы данных"""
        try:
            self.conn = await aiosqlite.connect(self.db_path)
            await self.conn.execute("PRAGMA journal_mode=WAL")
            async with self.conn.cursor() as cursor:
                # Таблица для хранения информации о спарсенных сообщениях из канала
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS parsed_messages (
                        channel_id INTEGER,
                        message_id INTEGER,
                        text TEXT,
                        has_media BOOLEAN,
                        media_group_id INTEGER,
                        local_file_path TEXT,
                        forwarded_to TEXT,
                        type TEXT,
                        published BOOLEAN DEFAULT FALSE,
                        parsed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (channel_id, message_id)
                    )
                """)
                
                # Таблица для хранения информации о медиагруппах (для оптимизации парсинга)
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS media_groups (
                        channel_id INTEGER,
                        group_id INTEGER,
                        message_ids TEXT, -- список ID сообщений в группе (JSON)
                        parsed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (channel_id, group_id)
                    )
                """)
                
                # Таблица для хранения истории просмотренных пользователем каналов
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_channels (
                        user_id INTEGER,
                        channel_id TEXT,
                        channel_title TEXT,
                        last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, channel_id)
                    )
                """)
                
                # Таблица для хранения истории целевых каналов пользователя
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_target_channels (
                        user_id INTEGER,
                        channel_id TEXT,
                        channel_title TEXT,
                        last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, channel_id)
                    )
                """)
                
                # Таблица для хранения активных мониторингов
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_monitorings (
                        user_id INTEGER,
                        channel_id TEXT,
                        target_channel TEXT,
                        config TEXT, -- JSON с настройками
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_active BOOLEAN DEFAULT TRUE,
                        PRIMARY KEY (user_id, channel_id, target_channel)
                    )
                """)
                
                # Таблица для хранения опубликованных постов
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS published_messages (
                        source_channel_id INTEGER,
                        source_message_id INTEGER,
                        target_channel_id TEXT,
                        target_message_id INTEGER,
                        published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (source_channel_id, source_message_id, target_channel_id)
                    )
                """)
                
                # Таблица для сохранения ID сообщений с навигацией по хэштегам
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS navigation_messages (
                        channel_id TEXT PRIMARY KEY,
                        message_id INTEGER,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Таблица для хранения шаблонов парсинга пользователей
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_posting_templates (
                        user_id INTEGER,
                        name TEXT,
                        settings TEXT,  -- JSON с настройками
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, name)
                    )
                """)
                
                # Таблица для кэширования информации о каналах из Telegram API
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS channel_info (
                        channel_id TEXT PRIMARY KEY,
                        channel_title TEXT,
                        username TEXT,
                        total_posts INTEGER,
                        is_public BOOLEAN,
                        last_message_id INTEGER,
                        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Таблица для хранения метаданных о сессиях Telegram-аккаунтов
                await cursor.execute('''
                    CREATE TABLE IF NOT EXISTS sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        alias TEXT UNIQUE NOT NULL,
                        api_id INTEGER NOT NULL,
                        api_hash TEXT NOT NULL,
                        phone TEXT NOT NULL,
                        session_path TEXT NOT NULL,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_used_at TIMESTAMP,
                        assigned_task TEXT,
                        notes TEXT
                    )
                ''')
                
                # Индексы для оптимизации запросов
                await cursor.execute("CREATE INDEX IF NOT EXISTS idx_parsed_messages_channel ON parsed_messages(channel_id)")
                await cursor.execute("CREATE INDEX IF NOT EXISTS idx_parsed_messages_forwarded ON parsed_messages(forwarded_to)")
                await cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_groups_channel ON media_groups(channel_id)")
                await cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_channels_user ON user_channels(user_id)")
                await cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_target_channels_user ON user_target_channels(user_id)")
                await cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_monitorings_user ON user_monitorings(user_id)")

            # Проверяем, нужно ли добавить поле last_message_id в таблицу channel_info
            async with self.conn.execute("PRAGMA table_info(channel_info)") as cursor:
                columns = [row[1] async for row in cursor]
                if "last_message_id" not in columns:
                    logger.info("Добавляем столбец last_message_id в таблицу channel_info")
                    await self.conn.execute("ALTER TABLE channel_info ADD COLUMN last_message_id INTEGER")
                    await self.conn.commit()

            # Предзаполняем кэш для часто используемых каналов
            # await self._preload_cache()
            await self.conn.commit()
            logger.info("База данных инициализирована")
        except Exception as e:
            logger.error(f"Ошибка инициализации базы данных: {e}")
            raise

    async def close(self):
        """Закрытие соединения с базой данных"""
        if self.conn:
            try:
                logger.debug("[close] Закрываю соединение с БД")
                await self.conn.close()
                logger.debug("[close] Соединение закрыто")
            except Exception as e:
                logger.error(f"[close] Ошибка при закрытии: {e}")
            finally:
                self.conn = None

    async def _load_cache(self, channel_id: int):
        """Загрузка кэша для канала"""
        try:
            async with self.conn.execute(
                "SELECT message_id FROM parsed_messages WHERE channel_id = ?",
                (channel_id,)
            ) as cursor:
                self._message_cache[channel_id] = {
                    row[0] async for row in cursor
                }
            
            # Загружаем медиагруппы
            async with self.conn.execute(
                "SELECT group_id, message_ids FROM media_groups WHERE channel_id = ?",
                (channel_id,)
            ) as cursor:
                self._media_group_cache[channel_id] = {
                    row[0]: eval(row[1]) async for row in cursor
                }
        except Exception as e:
            raise

    # @lru_cache(maxsize=1000)
    async def is_message_parsed(self, channel_id: int, message_id: int) -> bool:
        """Проверка, было ли сообщение уже спарсено в конкретном канале"""
        if channel_id not in self._message_cache:
            await self._load_cache(channel_id)
        result = message_id in self._message_cache[channel_id]
        return result

    async def mark_message_as_parsed(self, message_data: dict, channel_id: int, forwarded_to: Optional[str] = None, published: bool = False):
        message_id = message_data.get('message_id') or message_data.get('id')
        try:
            await self.conn.execute("""
                INSERT INTO parsed_messages (channel_id, message_id, text, has_media, media_group_id, local_file_path, forwarded_to, type, published)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, message_id) DO UPDATE SET
                    text=excluded.text,
                    has_media=excluded.has_media,
                    media_group_id=excluded.media_group_id,
                    local_file_path=excluded.local_file_path,
                    forwarded_to=excluded.forwarded_to,
                    type=excluded.type,
                    published=excluded.published
            """, (
                channel_id,
                message_id,
                message_data.get('text'),
                message_data.get('has_media'),
                message_data.get('media_group_id'),
                message_data.get('local_file_path'),
                forwarded_to,
                message_data.get('type', 'text_only'),
                published
            ))
            await self.conn.commit()
            # Обновляем кэш
            self._message_cache[channel_id].add(message_id)
        except Exception as e:
            logger.error(f"Error marking message as parsed: {e}")
            raise

    async def get_forward_target(self, channel_id: int, message_id: int) -> Optional[str]:
        """Получает информацию о пересылке сообщения"""
        try:
            async with self.conn.execute("""
                SELECT forwarded_to FROM parsed_messages
                WHERE channel_id = ? AND message_id = ?
            """, (channel_id, message_id)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"Error getting forward target: {e}")
            return None

    async def save_media_group(
        self,
        channel_id: int,
        group_id: int,
        message_ids: List[int],
        text: Optional[str] = None
    ):
        """Сохранение информации о медиагруппе"""
        async with self._lock:
            await self.conn.execute("""
                INSERT OR REPLACE INTO media_groups 
                (channel_id, group_id, message_ids, text, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                channel_id,
                group_id,
                str(message_ids),
                text,
                datetime.now()
            ))
            await self.conn.commit()
            
            # Обновляем кэш
            self._media_group_cache[channel_id][group_id] = message_ids

    async def get_media_group_messages(
        self,
        channel_id: int,
        group_id: int
    ) -> Optional[List[int]]:
        """Получение списка сообщений в медиагруппе"""
        if channel_id not in self._media_group_cache or group_id not in self._media_group_cache[channel_id]:
            # Если кэш не загружен или группа не найдена, загружаем из БД
            async with self.conn.execute(
                "SELECT message_ids FROM media_groups WHERE channel_id = ? AND group_id = ?",
                (channel_id, group_id)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    message_ids = eval(row[0])
                    self._media_group_cache[channel_id][group_id] = message_ids # Обновляем кэш
                    return message_ids
            return None
        return self._media_group_cache[channel_id].get(group_id)

    async def get_channel_parsed_messages(self, channel_id: int) -> List[Tuple[int, str]]:
        """Получить список всех спарсенных сообщений канала"""
        logger.debug(f"[get_channel_parsed_messages] channel_id={channel_id}")
        try:
            async with self.conn.execute("""
                SELECT message_id, text 
                FROM parsed_messages 
                WHERE channel_id = ?
            """, (channel_id,)) as cursor:
                result = [(row[0], row[1]) async for row in cursor]
                logger.debug(f"[get_channel_parsed_messages] result={result}")
                return result
        except Exception as e:
            logger.error(f"[get_channel_parsed_messages] Ошибка: {e}")
            raise

    async def get_forwarded_messages(self, channel_id: int) -> List[Tuple[int, str]]:
        """Получить список пересланных сообщений канала"""
        async with self.conn.execute("""
            SELECT message_id, forwarded_to 
            FROM parsed_messages 
            WHERE channel_id = ? AND forwarded_to IS NOT NULL
        """, (channel_id,)) as cursor:
            return [(row[0], row[1]) async for row in cursor]

    async def save_parse_config(self, config: ParseConfig):
        """Сохранение конфигурации парсинга"""
        try:
            await self.conn.execute("""
                INSERT OR REPLACE INTO parse_configs 
                (channel_id, mode, settings, is_active, created_at, last_parsed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                config.channel_id,
                config.mode.value,
                str(config.settings),
                config.is_active,
                config.created_at,
                config.last_parsed_at
            ))
            await self.conn.commit()
        except Exception as e:
            logger.error(f"Error saving parse config: {e}")
            raise

    async def get_parse_config(self, channel_id: int) -> Optional[ParseConfig]:
        """Получение конфигурации парсинга для канала"""
        try:
            async with self.conn.execute(
                "SELECT * FROM parse_configs WHERE channel_id = ?",
                (channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return ParseConfig(
                        channel_id=row[0],
                        mode=ParseMode(row[1]),
                        settings=eval(row[2]),
                        is_active=row[3],
                        created_at=row[4],
                        last_parsed_at=row[5]
                    )
                return None
        except Exception as e:
            logger.error(f"Error getting parse config: {e}")
            return None

    async def get_active_configs(self) -> List[ParseConfig]:
        """Получение всех активных конфигураций"""
        try:
            configs = []
            async with self.conn.execute(
                "SELECT * FROM parse_configs WHERE is_active = TRUE"
            ) as cursor:
                async for row in cursor:
                    configs.append(ParseConfig(
                        channel_id=row[0],
                        mode=ParseMode(row[1]),
                        settings=eval(row[2]),
                        is_active=row[3],
                        created_at=row[4],
                        last_parsed_at=row[5]
                    ))
            return configs
        except Exception as e:
            logger.error(f"Error getting active configs: {e}")
            return []

    async def save_messages(self, messages):
        for msg in messages:
            # msg может быть либо объектом Message, либо dict
            if hasattr(msg, 'to_dict'):
                data = msg.to_dict()
            elif hasattr(msg, 'dict'):
                data = msg.dict()
            elif isinstance(msg, dict):
                data = msg
            else:
                continue
            await self.mark_message_as_parsed(data, data.get('chat_id') or data.get('channel_id')) 

    async def get_channel_messages(self, channel_id: int):
        """Получить все сообщения канала из базы данных."""
        logger.debug(f"[get_channel_messages] channel_id={channel_id}")
        try:
            async with self.conn.execute(
                "SELECT message_id, channel_id, text, has_media, media_group_id, local_file_path, forwarded_to, type FROM parsed_messages WHERE channel_id = ?",
                (channel_id,)
            ) as cursor:
                messages = []
                async for row in cursor:
                    messages.append({
                        "id": row[0],
                        "chat_id": row[1],  # channel_id из базы
                        "text": row[2],
                        "has_media": row[3],
                        "media_group_id": row[4],
                        "local_file_path": row[5],
                        "forwarded_to": row[6],
                        "type": row[7] or "text_only",  # Если type не указан, считаем текстовым
                    })
                logger.debug(f"[get_channel_messages] result={messages}")
                return messages
        except Exception as e:
            logger.error(f"[get_channel_messages] Ошибка: {e}")
            raise

    async def get_user_channels(self, user_id: int) -> list:
        """Получить историю каналов пользователя (список каналов с id и title, отсортировано по последнему использованию)"""
        logger.debug(f"[get_user_channels] user_id={user_id}")
        try:
            if self.conn is None:
                await self.init()
            async with self.conn.execute(
                "SELECT channel_id, channel_title FROM user_channels WHERE user_id = ? ORDER BY last_used DESC",
                (user_id,)
            ) as cursor:
                result = [
                    {"id": row[0], "title": row[1]} async for row in cursor
                ]
                logger.debug(f"[get_user_channels] result={result}")
                return result
        except Exception as e:
            logger.error(f"[get_user_channels] Ошибка: {e}")
            raise

    async def add_user_channel(self, user_id: int, channel_id: str, channel_title: str):
        """Добавить канал в историю пользователя или обновить его название и время использования"""
        logger.debug(f"[add_user_channel] user_id={user_id}, channel_id={channel_id}, channel_title={channel_title}")
        try:
            await self.conn.execute(
                "INSERT INTO user_channels (user_id, channel_id, channel_title, last_used) VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(user_id, channel_id) DO UPDATE SET channel_title=excluded.channel_title, last_used=CURRENT_TIMESTAMP",
                (user_id, channel_id, channel_title)
            )
            await self.conn.commit()
            logger.debug("[add_user_channel] Коммит выполнен")
        except Exception as e:
            logger.error(f"[add_user_channel] Ошибка: {e}")
            raise

    async def update_user_channel_last_used(self, user_id: int, channel_id: str):
        """Обновить время последнего использования канала пользователем"""
        await self.conn.execute(
            "UPDATE user_channels SET last_used = CURRENT_TIMESTAMP WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id)
        )
        await self.conn.commit()

    async def remove_user_channel(self, user_id: int, channel_id: str):
        """Удалить канал из истории пользователя"""
        await self.conn.execute(
            "DELETE FROM user_channels WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id)
        )
        await self.conn.commit()

    async def mark_message_as_published(self, source_channel_id: int, message_id: int, target_channel_id: int):
        """Отметить сообщение как опубликованное"""
        try:
            await self.conn.execute("""
                INSERT INTO published_messages (source_channel_id, message_id, target_channel_id)
                VALUES (?, ?, ?)
                ON CONFLICT(source_channel_id, message_id, target_channel_id) DO NOTHING
            """, (source_channel_id, message_id, target_channel_id))
            await self.conn.commit()
        except Exception as e:
            logger.error(f"Error marking message as published: {e}")

    async def get_user_target_channels(self, user_id: int) -> list:
        """
        Получить историю целевых каналов пользователя (список каналов с id и title, отсортировано по последнему использованию)
        """
        if self.conn is None:
            await self.init()
        async with self.conn.execute(
            "SELECT channel_id, channel_title FROM user_target_channels WHERE user_id = ? ORDER BY last_used DESC",
            (user_id,)
        ) as cursor:
            return [
                {"id": row[0], "title": row[1]} async for row in cursor
            ]

    async def add_user_target_channel(self, user_id: int, channel_id: str, channel_title: str):
        """
        Добавить целевой канал в историю пользователя или обновить его название и время использования
        """
        await self.conn.execute(
            "INSERT INTO user_target_channels (user_id, channel_id, channel_title, last_used) VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, channel_id) DO UPDATE SET channel_title=excluded.channel_title, last_used=CURRENT_TIMESTAMP",
            (user_id, channel_id, channel_title)
        )
        await self.conn.commit()

    async def update_user_target_channel_last_used(self, user_id: int, channel_id: str):
        """
        Обновить время последнего использования целевого канала пользователем
        """
        await self.conn.execute(
            "UPDATE user_target_channels SET last_used = CURRENT_TIMESTAMP WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id)
        )
        await self.conn.commit()

    async def remove_user_target_channel(self, user_id: int, channel_id: str):
        """
        Удалить целевой канал из истории пользователя
        """
        await self.conn.execute(
            "DELETE FROM user_target_channels WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id)
        )
        await self.conn.commit()

    async def add_user_monitoring(self, user_id: int, channel_id: str, target_channel: str):
        """
        Добавить мониторинг пользователя (если такого нет или если был неактивен — активировать заново)
        """
        await self.conn.execute(
            "INSERT INTO user_monitorings (user_id, channel_id, target_channel, is_active, created_at) VALUES (?, ?, ?, TRUE, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, channel_id, target_channel) DO UPDATE SET is_active=TRUE, created_at=CURRENT_TIMESTAMP",
            (user_id, channel_id, target_channel)
        )
        await self.conn.commit()

    async def deactivate_user_monitoring(self, user_id: int, channel_id: str, target_channel: str):
        """
        Деактивировать мониторинг пользователя
        """
        await self.conn.execute(
            "UPDATE user_monitorings SET is_active = FALSE WHERE user_id = ? AND channel_id = ? AND target_channel = ?",
            (user_id, channel_id, target_channel)
        )
        await self.conn.commit()

    async def is_monitoring_exists(self, user_id: int, channel_id: str, target_channel: str) -> bool:
        """
        Проверить, существует ли уже активный мониторинг для пары (user_id, channel_id, target_channel)
        """
        async with self.conn.execute(
            "SELECT 1 FROM user_monitorings WHERE user_id = ? AND channel_id = ? AND target_channel = ? AND is_active = TRUE",
            (user_id, channel_id, target_channel)
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row)

    async def get_active_monitorings_by_user(self, user_id: int) -> list:
        """
        Получить список активных мониторингов пользователя
        """
        if self.conn is None:
            await self.init()
        async with self.conn.execute(
            "SELECT channel_id, target_channel, created_at FROM user_monitorings WHERE user_id = ? AND is_active = TRUE",
            (user_id,)
        ) as cursor:
            return [
                {"channel_id": row[0], "target_channel": row[1], "created_at": row[2]} async for row in cursor
            ]

    async def is_message_published(self, source_channel_id: int, message_id: int, target_channel_id: int) -> bool:
        """Проверить, публиковалось ли сообщение в указанный канал"""
        async with self.conn.execute(
            "SELECT 1 FROM published_messages WHERE source_channel_id = ? AND message_id = ? AND target_channel_id = ?",
            (source_channel_id, message_id, target_channel_id)
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row)

    async def mark_message_as_forwarded(self, channel_id: int, message_id: int, target_channel: str):
        """Отметить сообщение как пересланное в целевой канал"""
        try:
            # Сначала получаем текущие целевые каналы
            async with self.conn.execute("""
                SELECT forwarded_to FROM parsed_messages 
                WHERE channel_id = ? AND message_id = ?
            """, (channel_id, message_id)) as cursor:
                row = await cursor.fetchone()
                current_targets = row[0] if row and row[0] else ""
            
            # Добавляем новый целевой канал
            if current_targets:
                if target_channel not in current_targets.split(','):
                    new_targets = f"{current_targets},{target_channel}"
                else:
                    new_targets = current_targets
            else:
                new_targets = target_channel
            
            await self.conn.execute("""
                UPDATE parsed_messages 
                SET forwarded_to = ? 
                WHERE channel_id = ? AND message_id = ?
            """, (new_targets, channel_id, message_id))
            await self.conn.commit()
        except Exception as e:
            logger.error(f"Ошибка при отметке сообщения как пересланного: {e}")

    async def is_message_forwarded(self, channel_id: int, message_id: int, target_channel: str) -> bool:
        """Проверить, было ли сообщение уже переслано в целевой канал"""
        try:
            async with self.conn.execute("""
                SELECT forwarded_to FROM parsed_messages 
                WHERE channel_id = ? AND message_id = ?
            """, (channel_id, message_id)) as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    return target_channel in row[0].split(',')
                return False
        except Exception as e:
            logger.error(f"Ошибка при проверке пересылки сообщения: {e}")
            return False

    async def clear_forwarding_history(self, channel_id: int = None, target_channel: str = None):
        """Очистить историю пересланных постов
        
        Args:
            channel_id: ID канала для очистки (если None - очистить все каналы)
            target_channel: ID целевого канала для очистки (если None - очистить все целевые каналы)
        """
        try:
            if channel_id and target_channel:
                # Очистить пересылку конкретного канала в конкретный целевой канал
                await self.conn.execute("""
                    UPDATE parsed_messages 
                    SET forwarded_to = CASE 
                        WHEN forwarded_to = ? THEN NULL
                        WHEN forwarded_to LIKE ? THEN REPLACE(forwarded_to, ? || ',', '')
                        WHEN forwarded_to LIKE ? THEN REPLACE(forwarded_to, ',' || ?, '')
                        WHEN forwarded_to LIKE ? THEN REPLACE(REPLACE(forwarded_to, ? || ',', ''), ',' || ?, '')
                        ELSE forwarded_to
                    END
                    WHERE channel_id = ? AND forwarded_to IS NOT NULL
                """, (
                    target_channel,
                    f"{target_channel},%",
                    f"{target_channel},",
                    f"%,{target_channel}",
                    f",{target_channel}",
                    f"%,{target_channel},%",
                    f"{target_channel},",
                    f",{target_channel}",
                    channel_id
                ))
                
                # Очищаем медиагруппы для этого канала
                await self.conn.execute("""
                    DELETE FROM media_groups 
                    WHERE channel_id = ?
                """, (channel_id,))
                
            elif channel_id:
                # Очистить всю историю пересылки конкретного канала
                await self.conn.execute("""
                    UPDATE parsed_messages 
                    SET forwarded_to = NULL
                    WHERE channel_id = ?
                """, (channel_id,))
                
                # Очищаем медиагруппы для этого канала
                await self.conn.execute("""
                    DELETE FROM media_groups 
                    WHERE channel_id = ?
                """, (channel_id,))
                
            elif target_channel:
                # Очистить всю историю пересылки в конкретный целевой канал
                await self.conn.execute("""
                    UPDATE parsed_messages 
                    SET forwarded_to = CASE 
                        WHEN forwarded_to = ? THEN NULL
                        WHEN forwarded_to LIKE ? THEN REPLACE(forwarded_to, ? || ',', '')
                        WHEN forwarded_to LIKE ? THEN REPLACE(forwarded_to, ',' || ?, '')
                        WHEN forwarded_to LIKE ? THEN REPLACE(REPLACE(forwarded_to, ? || ',', ''), ',' || ?, '')
                        ELSE forwarded_to
                    END
                    WHERE forwarded_to IS NOT NULL
                """, (
                    target_channel,
                    f"{target_channel},%",
                    f"{target_channel},",
                    f"%,{target_channel}",
                    f",{target_channel}",
                    f"%,{target_channel},%",
                    f"{target_channel},",
                    f",{target_channel}"
                ))
                
                # Очищаем все медиагруппы (так как очищаем всю историю пересылки в целевой канал)
                await self.conn.execute("DELETE FROM media_groups")
            
            else:
                # Очистить всю историю пересылки
                await self.conn.execute("""
                    UPDATE parsed_messages 
                    SET forwarded_to = NULL
                    WHERE forwarded_to IS NOT NULL
                """)
                
                # Очищаем все медиагруппы
                await self.conn.execute("DELETE FROM media_groups")
            
            await self.conn.commit()
            
            # Очищаем кэш
            self._message_cache.clear()
            self._media_group_cache.clear()
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка при очистке истории пересылки: {e}")
            return False

    async def get_forwarding_history_stats(self, channel_id: int = None, target_channel: str = None) -> dict:
        """Получить статистику истории пересылки
        
        Returns:
            dict: Статистика с ключами total_forwarded, channels_count, target_channels_count
        """
        logger.debug(f"[get_forwarding_history_stats] channel_id={channel_id}, target_channel={target_channel}")
        try:
            query = "SELECT COUNT(*) FROM published_messages WHERE 1=1"
            params = []
            if channel_id:
                query += " AND source_channel_id = ?"
                params.append(channel_id)
            if target_channel:
                query += " AND target_channel_id = ?"
                params.append(target_channel)
            async with self.conn.execute(query, tuple(params)) as cursor:
                count = (await cursor.fetchone())[0]
                logger.debug(f"[get_forwarding_history_stats] count={count}")
                return {"count": count}
        except Exception as e:
            logger.error(f"[get_forwarding_history_stats] Ошибка: {e}")
            raise

    async def add_forwarding_config(self, config):
        """Добавление или обновление конфигурации пересылки"""
        try:
            # Проверяем, существует ли уже конфигурация
            async with self.conn.execute(
                "SELECT id FROM forwarding_configs WHERE user_id = ? AND source_channel_id = ?",
                (config.user_id, config.source_channel_id)
            ) as cursor:
                existing = await cursor.fetchone()
            
            if existing:
                # Обновляем существующую конфигурацию
                await self.conn.execute("""
                    UPDATE forwarding_configs SET
                        target_channel_id = ?,
                        parse_mode = ?,
                        hashtag_filter = ?,
                        delay_seconds = ?,
                        footer_text = ?,
                        text_mode = ?,
                        max_posts = ?,
                        hide_sender = ?,
                        paid_content_stars = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND source_channel_id = ?
                """, (
                    config.target_channel_id,
                    config.parse_mode,
                    config.hashtag_filter,
                    config.delay_seconds,
                    config.footer_text,
                    config.text_mode,
                    config.max_posts,
                    config.hide_sender,
                    config.paid_content_stars,
                    config.user_id,
                    config.source_channel_id
                ))
            else:
                # Создаем новую конфигурацию
                await self.conn.execute("""
                    INSERT INTO forwarding_configs (
                        user_id, source_channel_id, target_channel_id, parse_mode,
                        hashtag_filter, delay_seconds, footer_text, text_mode,
                        max_posts, hide_sender, paid_content_stars
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    config.user_id,
                    config.source_channel_id,
                    config.target_channel_id,
                    config.parse_mode,
                    config.hashtag_filter,
                    config.delay_seconds,
                    config.footer_text,
                    config.text_mode,
                    config.max_posts,
                    config.hide_sender,
                    config.paid_content_stars
                ))
            
            await self.conn.commit()
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении конфигурации пересылки: {e}")
            raise

    async def get_forwarding_config(self, user_id: int, source_channel_id: int) -> dict:
        """Получение конфигурации пересылки"""
        logger.debug(f"[get_forwarding_config] user_id={user_id}, source_channel_id={source_channel_id}")
        try:
            async with self.conn.execute(
                "SELECT * FROM forwarding_configs WHERE user_id = ? AND source_channel_id = ? ORDER BY updated_at DESC LIMIT 1",
                (user_id, source_channel_id)
            ) as cursor:
                row = await cursor.fetchone()
                logger.debug(f"[get_forwarding_config] row={row}")
                return dict(row) if row else {}
        except Exception as e:
            logger.error(f"[get_forwarding_config] Ошибка: {e}")
            raise

    async def get_channel_stats(self, channel_id):
        logger.debug(f"[get_channel_stats] channel_id={channel_id}")
        async with self._lock:
            stats = {
                'parsed_count': 0,
                'min_id': None,
                'max_id': None,
                'parsed_media_groups': 0,
                'parsed_singles': 0,
                'last_parsed_id': None,
                'last_parsed_date': None
            }
            try:
                async with self.conn.execute(
                    "SELECT COUNT(*), MIN(message_id), MAX(message_id) FROM parsed_messages WHERE channel_id = ?",
                    (channel_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    stats['parsed_count'] = row[0] if row else 0
                    stats['min_id'] = row[1] if row else None
                    stats['max_id'] = row[2] if row else None
                logger.debug(f"[get_channel_stats] parsed_count={stats['parsed_count']}, min_id={stats['min_id']}, max_id={stats['max_id']}")
                async with self.conn.execute(
                    "SELECT COUNT(DISTINCT media_group_id) FROM parsed_messages WHERE channel_id = ? AND media_group_id IS NOT NULL",
                    (channel_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    stats['parsed_media_groups'] = row[0] if row else 0
                logger.debug(f"[get_channel_stats] parsed_media_groups={stats['parsed_media_groups']}")
                async with self.conn.execute(
                    "SELECT COUNT(*) FROM parsed_messages WHERE channel_id = ? AND media_group_id IS NULL",
                    (channel_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    stats['parsed_singles'] = row[0] if row else 0
                logger.debug(f"[get_channel_stats] parsed_singles={stats['parsed_singles']}")
                async with self.conn.execute(
                    "SELECT message_id, parsed_at FROM parsed_messages WHERE channel_id = ? ORDER BY message_id DESC LIMIT 1",
                    (channel_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    stats['last_parsed_id'] = row[0] if row else None
                    stats['last_parsed_date'] = row[1] if row else None
                logger.debug(f"[get_channel_stats] last_parsed_id={stats['last_parsed_id']}, last_parsed_date={stats['last_parsed_date']}")
            except Exception as e:
                logger.error(f"[get_channel_stats] Ошибка: {e}")
                raise
            return stats

    # --- Методы для работы сессиями Telegram-аккаунтов ---
    async def create_session(self, session: SessionMeta) -> int:
        async with self.conn.execute(
            """
            INSERT INTO sessions (alias, api_id, api_hash, phone, session_path, is_active, created_at, last_used_at, assigned_task, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.alias,
                session.api_id,
                session.api_hash,
                session.phone,
                session.session_path,
                session.is_active,
                session.created_at,
                session.last_used_at,
                session.assigned_task,
                session.notes
            )
        ) as cursor:
            await self.conn.commit()
            return cursor.lastrowid

    async def get_session_by_id(self, session_id: int) -> SessionMeta:
        async with self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return SessionMeta(**dict(zip([column[0] for column in cursor.description], row)))
            return None

    async def get_session_by_alias(self, alias: str) -> SessionMeta:
        async with self.conn.execute("SELECT * FROM sessions WHERE alias = ?", (alias,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return SessionMeta(**dict(zip([column[0] for column in cursor.description], row)))
            return None

    async def get_all_sessions(self) -> list:
        async with self.conn.execute("SELECT * FROM sessions") as cursor:
            rows = await cursor.fetchall()
            columns = [column[0] for column in cursor.description]
            return [SessionMeta(**dict(zip(columns, row))) for row in rows]

    async def update_session(self, session_id: int, **kwargs) -> None:
        fields = ', '.join([f"{k} = ?" for k in kwargs.keys()])
        values = list(kwargs.values())
        values.append(session_id)
        await self.conn.execute(f"UPDATE sessions SET {fields} WHERE id = ?", values)
        await self.conn.commit()

    async def delete_session(self, session_id: int) -> None:
        await self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await self.conn.commit()

    async def import_existing_sessions(self, sessions_dir: str = "sessions/") -> int:
        """Импортирует все .session файлы, которых нет в БД, с минимальными данными."""
        imported = 0
        if not os.path.exists(sessions_dir):
            return 0
        files = [f for f in os.listdir(sessions_dir) if f.endswith('.session')]
        for file in files:
            session_path = os.path.join(sessions_dir, file)
            alias = os.path.splitext(file)[0]
            # Проверяем, есть ли уже такая сессия в БД
            existing = await self.get_session_by_alias(alias)
            if not existing:
                # Минимальные данные, остальное пользователь может заполнить позже
                session = SessionMeta(
                    id=0,
                    alias=alias,
                    api_id=0,
                    api_hash='',
                    phone='',
                    session_path=session_path,
                    is_active=True
                )
                await self.create_session(session)
                imported += 1
        return imported