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
            logger.debug(f"[DB][init] Подключаемся к БД: {self.db_path}")
            self.conn = await aiosqlite.connect(self.db_path)
            await self.conn.execute("PRAGMA journal_mode=WAL")
            logger.debug("[DB][init] Установлен режим WAL")

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
                        username TEXT,
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
                        username TEXT,
                        last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, channel_id)
                    )
                """)
                
                # Таблица для хранения истории групп пользователя
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_groups (
                        user_id INTEGER,
                        group_id TEXT,
                        group_title TEXT,
                        username TEXT,
                        last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, group_id)
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
                
                # Таблица для хранения конфигураций пересылки
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS forwarding_configs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        source_channel_id TEXT NOT NULL,
                        target_channel_id TEXT NOT NULL,
                        parse_mode TEXT DEFAULT 'all',
                        hashtag_filter TEXT,
                        delay_seconds INTEGER DEFAULT 0,
                        footer_text TEXT,
                        text_mode TEXT DEFAULT 'hashtags_only',
                        max_posts INTEGER,
                        hide_sender BOOLEAN DEFAULT TRUE,
                        paid_content_stars INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user_id, source_channel_id)
                    )
                """)
                
                # Таблица для настроек водяных знаков
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS watermark_settings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        channel_id TEXT NOT NULL,
                        watermark_enabled BOOLEAN DEFAULT FALSE,
                        watermark_mode TEXT DEFAULT 'all',
                        watermark_chance INTEGER DEFAULT 100,
                        watermark_hashtag TEXT,
                        watermark_image_path TEXT,
                        watermark_position TEXT DEFAULT 'bottom_right',
                        watermark_opacity INTEGER DEFAULT 128,
                        watermark_scale REAL DEFAULT 0.3,
                        watermark_text TEXT,
                        UNIQUE(user_id, channel_id)
                    )
                """)

                # Таблица для привязки watermark к каналам
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS channel_watermarks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        channel_id TEXT NOT NULL,
                        watermark_image_id INTEGER,
                        watermark_text TEXT,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (watermark_image_id) REFERENCES user_watermark_images(id) ON DELETE SET NULL,
                        UNIQUE(user_id, channel_id)
                    )
                """)
                
                # Таблица для хранения загруженных пользователем изображений watermark
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_watermark_images (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        file_path TEXT NOT NULL,
                        file_name TEXT NOT NULL,
                        file_size INTEGER,
                        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_active BOOLEAN DEFAULT TRUE
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
                        user_id INTEGER,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_used_at TIMESTAMP,
                        assigned_task TEXT,
                        notes TEXT
                    )
                ''')
                
                # Таблица для множественных назначений задач на одну сессию
                await cursor.execute('''
                    CREATE TABLE IF NOT EXISTS session_assignments (
                        session_id INTEGER,
                        task TEXT,
                        PRIMARY KEY (session_id, task),
                        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                    )
                ''')
                
                # Индексы для оптимизации запросов
                await cursor.execute("CREATE INDEX IF NOT EXISTS idx_parsed_messages_channel ON parsed_messages(channel_id)")
                await cursor.execute("CREATE INDEX IF NOT EXISTS idx_parsed_messages_forwarded ON parsed_messages(forwarded_to)")
                await cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_groups_channel ON media_groups(channel_id)")
                await cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_channels_user ON user_channels(user_id)")
                await cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_target_channels_user ON user_target_channels(user_id)")
                await cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_groups_user ON user_groups(user_id)")


            # Проверяем, нужно ли добавить поле last_message_id в таблицу channel_info
            async with self.conn.execute("PRAGMA table_info(channel_info)") as cursor:
                columns = [row[1] async for row in cursor]
                if "last_message_id" not in columns:
                    logger.info("Добавляем столбец last_message_id в таблицу channel_info")
                    await self.conn.execute("ALTER TABLE channel_info ADD COLUMN last_message_id INTEGER")
                    await self.conn.commit()

            # Добавляем поле user_id в таблицу sessions если его нет
            async with self.conn.execute("PRAGMA table_info(sessions)") as cursor:
                columns = [row[1] async for row in cursor]
                if "user_id" not in columns:
                    logger.info("Добавляем столбец user_id в таблицу sessions")
                    await self.conn.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER")
                    await self.conn.commit()

            # Обновляем структуру таблицы channel_info для новых полей
            async with self.conn.execute("PRAGMA table_info(channel_info)") as cursor:
                columns = [row[1] async for row in cursor]
                
                # Переименовываем channel_id в id для консистентности
                if "channel_id" in columns and "id" not in columns:
                    logger.info("Обновляем структуру таблицы channel_info")
                    await self.conn.execute("""
                        CREATE TABLE IF NOT EXISTS channel_info_new (
                            id TEXT PRIMARY KEY,
                            title TEXT,
                            username TEXT,
                            description TEXT,
                            members_count INTEGER DEFAULT 0,
                            type TEXT DEFAULT 'unknown',
                            total_posts INTEGER DEFAULT 0,
                            is_public BOOLEAN DEFAULT FALSE,
                            last_message_id INTEGER,
                            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    
                    # Копируем данные из старой таблицы
                    await self.conn.execute("""
                        INSERT INTO channel_info_new (id, title, username, total_posts, is_public, last_message_id, last_updated)
                        SELECT channel_id, channel_title, username, total_posts, is_public, last_message_id, last_updated
                        FROM channel_info
                    """)
                    
                    # Удаляем старую таблицу и переименовываем новую
                    await self.conn.execute("DROP TABLE channel_info")
                    await self.conn.execute("ALTER TABLE channel_info_new RENAME TO channel_info")
                    await self.conn.commit()
                    logger.info("Структура таблицы channel_info обновлена")
                
                # Добавляем недостающие поля в существующую таблицу
                else:
                    if "description" not in columns:
                        logger.info("Добавляем столбец description в таблицу channel_info")
                        await self.conn.execute("ALTER TABLE channel_info ADD COLUMN description TEXT")
                    
                    if "members_count" not in columns:
                        logger.info("Добавляем столбец members_count в таблицу channel_info")
                        await self.conn.execute("ALTER TABLE channel_info ADD COLUMN members_count INTEGER DEFAULT 0")
                    
                    if "type" not in columns:
                        logger.info("Добавляем столбец type в таблицу channel_info")
                        await self.conn.execute("ALTER TABLE channel_info ADD COLUMN type TEXT DEFAULT 'unknown'")
                    
                    await self.conn.commit()

            # Проверяем, нужно ли добавить поле username в таблицу user_channels
            async with self.conn.execute("PRAGMA table_info(user_channels)") as cursor:
                columns = [row[1] async for row in cursor]
                if "username" not in columns:
                    logger.info("Добавляем столбец username в таблицу user_channels")
                    await self.conn.execute("ALTER TABLE user_channels ADD COLUMN username TEXT")
                    await self.conn.commit()

            # Проверяем, нужно ли добавить поле username в таблицу user_target_channels
            async with self.conn.execute("PRAGMA table_info(user_target_channels)") as cursor:
                columns = [row[1] async for row in cursor]
                if "username" not in columns:
                    logger.info("Добавляем столбец username в таблицу user_target_channels")
                    await self.conn.execute("ALTER TABLE user_target_channels ADD COLUMN username TEXT")
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
                logger.debug("[DB][close] Закрываю соединение с БД")
                await self.conn.close()
                logger.debug("[DB][close] Соединение закрыто")
            except Exception as e:
                logger.error(f"[DB][close] Ошибка при закрытии: {e}")
            finally:
                self.conn = None

    async def diagnose_db_state(self) -> Dict[str, Any]:
        """Диагностика состояния базы данных для отладки ошибок блокировки"""
        diagnosis = {
            "db_path": self.db_path,
            "connection_active": self.conn is not None,
            "file_exists": os.path.exists(self.db_path),
            "file_size": None,
            "file_readable": False,
            "db_accessible": False,
            "active_transactions": None,
            "recommendations": []
        }

        try:
            # Проверяем файл
            if os.path.exists(self.db_path):
                diagnosis["file_size"] = os.path.getsize(self.db_path)

                try:
                    with open(self.db_path, 'rb') as f:
                        f.read(1)
                    diagnosis["file_readable"] = True
                except Exception as e:
                    diagnosis["file_readable"] = False
                    diagnosis["file_error"] = str(e)

            # Проверяем соединение
            if self.conn:
                try:
                    await self.conn.execute("SELECT 1")
                    diagnosis["db_accessible"] = True

                    # Проверяем активные транзакции
                    async with self.conn.cursor() as cursor:
                        await cursor.execute("PRAGMA wal_checkpoint")
                        checkpoint_result = await cursor.fetchone()
                        diagnosis["wal_checkpoint"] = checkpoint_result

                except Exception as e:
                    diagnosis["db_accessible"] = False
                    diagnosis["db_error"] = str(e)

                    if "database is locked" in str(e).lower():
                        diagnosis["recommendations"].extend([
                            "База данных заблокирована другой транзакцией",
                            "Проверьте, нет ли других процессов, использующих БД",
                            "Попробуйте перезапустить все сервисы",
                            "Проверьте права доступа к файлу БД"
                        ])

            # Общие рекомендации
            if not diagnosis["file_exists"]:
                diagnosis["recommendations"].append("Файл базы данных не существует")

            if diagnosis["file_exists"] and not diagnosis["file_readable"]:
                diagnosis["recommendations"].append("Файл базы данных недоступен для чтения")

            if diagnosis["connection_active"] and not diagnosis["db_accessible"]:
                diagnosis["recommendations"].append("Соединение с БД активно, но запросы не выполняются")

        except Exception as e:
            diagnosis["diagnosis_error"] = str(e)

        return diagnosis

    async def execute_with_retry(self, query: str, params: tuple = None, max_retries: int = 3, retry_delay: float = 0.1) -> Any:
        """Выполнить SQL запрос с повторными попытками при ошибке блокировки БД"""
        last_error = None

        for attempt in range(max_retries):
            try:
                if params:
                    result = await self.conn.execute(query, params)
                else:
                    result = await self.conn.execute(query)

                if "SELECT" in query.upper():
                    return await result.fetchall()
                else:
                    await self.conn.commit()
                    return result

            except Exception as e:
                last_error = e
                error_msg = str(e).lower()

                if "database is locked" in error_msg:
                    if attempt < max_retries - 1:
                        logger.warning(f"[DB][execute_with_retry] Попытка {attempt + 1}/{max_retries}: БД заблокирована, ждем {retry_delay}с")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # Экспоненциальная задержка
                        continue

                    # Последняя попытка - логируем подробную диагностику
                    logger.error(f"[DB][execute_with_retry] ❌ Все попытки исчерпаны, БД заблокирована")
                    diagnosis = await self.diagnose_db_state()

                    logger.error(f"[DB][execute_with_retry] 🔍 Диагностика состояния БД:")
                    for key, value in diagnosis.items():
                        if key != "recommendations":
                            logger.error(f"[DB][execute_with_retry]   {key}: {value}")

                    logger.error(f"[DB][execute_with_retry] 💡 Рекомендации:")
                    for rec in diagnosis.get("recommendations", []):
                        logger.error(f"[DB][execute_with_retry]   - {rec}")

                else:
                    # Другая ошибка - не повторяем
                    break

        # Если все попытки исчерпаны
        logger.error(f"[DB][execute_with_retry] Запрос не выполнен после {max_retries} попыток: {query}")
        raise last_error

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
        """Получить историю каналов пользователя (список каналов с id, title и username, отсортировано по последнему использованию)"""
        logger.debug(f"[get_user_channels] user_id={user_id}")
        try:
            if self.conn is None:
                await self.init()
            async with self.conn.execute(
                "SELECT channel_id, channel_title, username FROM user_channels WHERE user_id = ? ORDER BY last_used DESC",
                (user_id,)
            ) as cursor:
                result = [
                    {"id": row[0], "title": row[1], "username": row[2]} async for row in cursor
                ]
                logger.debug(f"[get_user_channels] result={result}")
                return result
        except Exception as e:
            logger.error(f"[get_user_channels] Ошибка: {e}")
            raise

    async def add_user_channel(self, user_id: int, channel_id: str, channel_title: str, username: str = None):
        """Добавить канал в историю пользователя или обновить его название, username и время использования"""
        logger.debug(f"[add_user_channel] user_id={user_id}, channel_id={channel_id}, channel_title={channel_title}, username={username}")
        try:
            await self.conn.execute(
                "INSERT INTO user_channels (user_id, channel_id, channel_title, username, last_used) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(user_id, channel_id) DO UPDATE SET channel_title=excluded.channel_title, username=excluded.username, last_used=CURRENT_TIMESTAMP",
                (user_id, channel_id, channel_title, username)
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
        Получить историю целевых каналов пользователя (список каналов с id, title и username, отсортировано по последнему использованию)
        """
        if self.conn is None:
            await self.init()
        async with self.conn.execute(
            "SELECT channel_id, channel_title, username FROM user_target_channels WHERE user_id = ? ORDER BY last_used DESC",
            (user_id,)
        ) as cursor:
            return [
                {"id": row[0], "title": row[1], "username": row[2]} async for row in cursor
            ]

    async def add_user_target_channel(self, user_id: int, channel_id: str, channel_title: str, username: str = None):
        """
        Добавить целевой канал в историю пользователя или обновить его название, username и время использования
        """
        await self.conn.execute(
            "INSERT INTO user_target_channels (user_id, channel_id, channel_title, username, last_used) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, channel_id) DO UPDATE SET channel_title=excluded.channel_title, username=excluded.username, last_used=CURRENT_TIMESTAMP",
            (user_id, channel_id, channel_title, username)
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

    # Методы для работы с группами пользователей
    async def get_user_groups(self, user_id: int) -> list:
        """Получение списка групп пользователя"""
        try:
            async with self.conn.execute(
                "SELECT group_id, group_title, username, last_used FROM user_groups WHERE user_id = ? ORDER BY last_used DESC",
                (user_id,)
            ) as cursor:
                groups = []
                async for row in cursor:
                    groups.append({
                        'group_id': row[0],
                        'group_title': row[1],
                        'username': row[2],
                        'last_used': row[3]
                    })
                return groups
        except Exception as e:
            logger.error(f"[DB] Ошибка при получении групп пользователя: {e}")
            return []

    async def add_user_group(self, user_id: int, group_id: str, group_title: str, username: str = None):
        """Добавление группы пользователя"""
        try:
            await self.conn.execute("""
                INSERT OR REPLACE INTO user_groups (user_id, group_id, group_title, username, last_used)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (user_id, group_id, group_title, username))
            await self.conn.commit()
            logger.info(f"[DB] Добавлена группа {group_id} ({group_title}) для пользователя {user_id}")
        except Exception as e:
            logger.error(f"[DB] Ошибка при добавлении группы: {e}")
            raise

    async def update_user_group_last_used(self, user_id: int, group_id: str):
        """Обновление времени последнего использования группы"""
        try:
            await self.conn.execute(
                "UPDATE user_groups SET last_used = CURRENT_TIMESTAMP WHERE user_id = ? AND group_id = ?",
                (user_id, group_id)
            )
            await self.conn.commit()
        except Exception as e:
            logger.error(f"[DB] Ошибка при обновлении времени использования группы: {e}")

    async def remove_user_group(self, user_id: int, group_id: str):
        """Удаление группы пользователя"""
        try:
            await self.conn.execute(
                "DELETE FROM user_groups WHERE user_id = ? AND group_id = ?",
                (user_id, group_id)
            )
            await self.conn.commit()
            logger.info(f"[DB] Удалена группа {group_id} для пользователя {user_id}")
        except Exception as e:
            logger.error(f"[DB] Ошибка при удалении группы: {e}")
            raise





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
            
            # Получаем watermark настройки из config если они есть
            watermark_enabled = getattr(config, 'watermark_enabled', False)
            watermark_mode = getattr(config, 'watermark_mode', 'all')
            watermark_chance = getattr(config, 'watermark_chance', 100)
            watermark_hashtag = getattr(config, 'watermark_hashtag', None)
            watermark_image_path = getattr(config, 'watermark_image_path', None)
            watermark_position = getattr(config, 'watermark_position', 'bottom_right')
            watermark_opacity = getattr(config, 'watermark_opacity', 128)
            watermark_scale = getattr(config, 'watermark_scale', 0.3)
            watermark_text = getattr(config, 'watermark_text', None)
            
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
            INSERT INTO sessions (alias, api_id, api_hash, phone, session_path, user_id, is_active, created_at, last_used_at, assigned_task, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.alias,
                session.api_id,
                session.api_hash,
                session.phone,
                session.session_path,
                session.user_id,
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
        try:
            rows = await self.execute_with_retry("SELECT * FROM sessions WHERE alias = ?", (alias,))
            if rows:
                # Для SELECT возвращается список строк
                # user_id добавлен в конец через ALTER TABLE, поэтому он последний
                columns = ['id', 'alias', 'api_id', 'api_hash', 'phone', 'session_path', 'is_active', 'created_at', 'last_used_at', 'assigned_task', 'notes', 'user_id']
                return SessionMeta(**dict(zip(columns, rows[0])))
            return None
        except Exception as e:
            logger.error(f"[DB][get_session_by_alias] Ошибка при получении сессии {alias}: {e}")
            raise

    async def get_all_sessions(self) -> list:
        try:
            rows = await self.execute_with_retry("SELECT * FROM sessions")
            if rows:
                # user_id добавлен в конец через ALTER TABLE, поэтому он последний
                columns = ['id', 'alias', 'api_id', 'api_hash', 'phone', 'session_path', 'is_active', 'created_at', 'last_used_at', 'assigned_task', 'notes', 'user_id']
                return [SessionMeta(**dict(zip(columns, row))) for row in rows]
            return []
        except Exception as e:
            logger.error(f"[DB][get_all_sessions] Ошибка при получении всех сессий: {e}")
            raise

    async def update_session(self, session_id: int, **kwargs) -> None:
        try:
            fields = ', '.join([f"{k} = ?" for k in kwargs.keys()])
            values = list(kwargs.values())
            values.append(session_id)
            await self.execute_with_retry(f"UPDATE sessions SET {fields} WHERE id = ?", tuple(values))
        except Exception as e:
            logger.error(f"[DB][update_session] Ошибка при обновлении сессии {session_id}: {e}")
            raise

    async def delete_session(self, session_id: int) -> None:
        try:
            await self.execute_with_retry("DELETE FROM sessions WHERE id = ?", (session_id,))
        except Exception as e:
            logger.error(f"[DB][delete_session] Ошибка при удалении сессии {session_id}: {e}")
            raise

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

    # --- Методы для работы с назначениями задач на сессиями ---
    async def add_session_assignment(self, session_id: int, task: str):
        try:
            await self.execute_with_retry(
                "INSERT OR IGNORE INTO session_assignments (session_id, task) VALUES (?, ?)",
                (session_id, task)
            )
        except Exception as e:
            logger.error(f"[DB][add_session_assignment] Ошибка при добавлении назначения сессии {session_id} задаче {task}: {e}")
            raise

    async def remove_session_assignment(self, session_id: int, task: str):
        try:
            await self.execute_with_retry(
                "DELETE FROM session_assignments WHERE session_id = ? AND task = ?",
                (session_id, task)
            )
        except Exception as e:
            logger.error(f"[DB][remove_session_assignment] Ошибка при удалении назначения сессии {session_id} задаче {task}: {e}")
            raise

    async def get_assignments(self) -> Dict[str, list]:
        """Возвращает assignments: task -> [session_alias]"""
        try:
            rows = await self.execute_with_retry(
                "SELECT sa.task, s.alias FROM session_assignments sa JOIN sessions s ON sa.session_id = s.id"
            )
            assignments = {}
            for task, alias in rows:
                assignments.setdefault(task, []).append(alias)
            return assignments
        except Exception as e:
            logger.error(f"[DB][get_assignments] Ошибка при получении назначений: {e}")
            raise

    async def get_session_tasks(self, session_id: int) -> list:
        try:
            rows = await self.execute_with_retry(
                "SELECT task FROM session_assignments WHERE session_id = ?",
                (session_id,)
            )
            return [row[0] for row in rows] if rows else []
        except Exception as e:
            logger.error(f"[DB][get_session_tasks] Ошибка при получении задач сессии {session_id}: {e}")
            raise

    async def get_sessions_for_task(self, task: str) -> list:
        try:
            rows = await self.execute_with_retry(
                "SELECT s.* FROM session_assignments sa JOIN sessions s ON sa.session_id = s.id WHERE sa.task = ?",
                (task,)
            )
            if rows:
                # user_id добавлен в конец через ALTER TABLE, поэтому он последний
                columns = ['id', 'alias', 'api_id', 'api_hash', 'phone', 'session_path', 'is_active', 'created_at', 'last_used_at', 'assigned_task', 'notes', 'user_id']
                return [SessionMeta(**dict(zip(columns, row))) for row in rows]
            return []
        except Exception as e:
            logger.error(f"[DB][get_sessions_for_task] Ошибка при получении сессий для задачи {task}: {e}")
            raise

    async def get_channel_info(self, channel_id) -> Optional[Dict]:
        """Получить информацию о канале из БД"""
        try:
            async with self.conn.execute(
                "SELECT id, title, username, description, members_count, type FROM channel_info WHERE id = ?",
                (str(channel_id),)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        'id': int(row[0]) if row[0].lstrip('-').isdigit() else row[0],
                        'title': row[1],
                        'username': row[2] or '',
                        'description': row[3] or '',
                        'members_count': row[4] or 0,
                        'type': row[5] or 'unknown'
                    }
                return None
        except Exception as e:
            logger.error(f"Ошибка получения информации о канале {channel_id}: {e}")
            return None

    async def save_channel_info(self, channel_data: Dict):
        """Сохранить информацию о канале в БД"""
        try:
            await self.conn.execute(
                """INSERT OR REPLACE INTO channel_info 
                   (id, title, username, description, members_count, type, last_updated) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(channel_data['id']),
                    channel_data.get('title', ''),
                    channel_data.get('username', ''),
                    channel_data.get('description', ''),
                    channel_data.get('members_count', 0),
                    channel_data.get('type', 'unknown'),
                    datetime.now().isoformat()
                )
            )
            await self.conn.commit()
            logger.info(f"Сохранена информация о канале {channel_data['id']} в БД")
        except Exception as e:
            logger.error(f"Ошибка сохранения информации о канале: {e}")

    # --- Методы для работы с watermark ---
    
    async def save_watermark_image(self, user_id: int, file_path: str, file_name: str, file_size: int) -> int:
        """Сохранить информацию о загруженном watermark изображении"""
        try:
            async with self.conn.execute("""
                INSERT INTO user_watermark_images (user_id, file_path, file_name, file_size)
                VALUES (?, ?, ?, ?)
            """, (user_id, file_path, file_name, file_size)) as cursor:
                await self.conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"Ошибка сохранения watermark изображения: {e}")
            raise
    
    async def get_user_watermark_images(self, user_id: int) -> List[Dict]:
        """Получить список watermark изображений пользователя"""
        try:
            async with self.conn.execute("""
                SELECT id, file_path, file_name, file_size, uploaded_at, is_active
                FROM user_watermark_images
                WHERE user_id = ? AND is_active = TRUE
                ORDER BY uploaded_at DESC
            """, (user_id,)) as cursor:
                rows = await cursor.fetchall()
                return [{
                    'id': row[0],
                    'file_path': row[1],
                    'file_name': row[2],
                    'file_size': row[3],
                    'uploaded_at': row[4],
                    'is_active': row[5]
                } for row in rows]
        except Exception as e:
            logger.error(f"Ошибка получения watermark изображений: {e}")
            return []
    
    async def delete_watermark_image(self, image_id: int, user_id: int) -> bool:
        """Удалить (деактивировать) watermark изображение"""
        try:
            await self.conn.execute("""
                UPDATE user_watermark_images
                SET is_active = FALSE
                WHERE id = ? AND user_id = ?
            """, (image_id, user_id))
            await self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка удаления watermark изображения: {e}")
            return False
    
    async def get_watermark_image(self, image_id: int) -> Optional[Dict]:
        """Получить информацию о watermark изображении"""
        try:
            async with self.conn.execute("""
                SELECT id, user_id, file_path, file_name, file_size, uploaded_at
                FROM user_watermark_images
                WHERE id = ? AND is_active = TRUE
            """, (image_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'user_id': row[1],
                        'file_path': row[2],
                        'file_name': row[3],
                        'file_size': row[4],
                        'uploaded_at': row[5]
                    }
                return None
        except Exception as e:
            logger.error(f"Ошибка получения watermark изображения: {e}")
            return None
    
    async def set_channel_watermark(self, user_id: int, channel_id: str, watermark_image_id: Optional[int] = None, watermark_text: Optional[str] = None):
        """Установить watermark для канала"""
        try:
            await self.conn.execute("""
                INSERT INTO channel_watermarks (user_id, channel_id, watermark_image_id, watermark_text)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, channel_id) DO UPDATE SET
                    watermark_image_id = excluded.watermark_image_id,
                    watermark_text = excluded.watermark_text
            """, (user_id, channel_id, watermark_image_id, watermark_text))
            await self.conn.commit()
        except Exception as e:
            logger.error(f"Ошибка установки watermark для канала: {e}")
            raise
    
    async def get_channel_watermark(self, user_id: int, channel_id: str) -> Optional[Dict]:
        """Получить watermark для канала"""
        try:
            async with self.conn.execute("""
                SELECT cw.id, cw.watermark_image_id, cw.watermark_text, uwi.file_path
                FROM channel_watermarks cw
                LEFT JOIN user_watermark_images uwi ON cw.watermark_image_id = uwi.id
                WHERE cw.user_id = ? AND cw.channel_id = ? AND cw.is_active = TRUE
            """, (user_id, channel_id)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'watermark_image_id': row[1],
                        'watermark_text': row[2],
                        'watermark_image_path': row[3]
                    }
                return None
        except Exception as e:
            logger.error(f"Ошибка получения watermark для канала: {e}")
            return None

    async def save_watermark_settings(self, user_id: int, channel_id: str, settings: dict):
        """Сохранить или обновить настройки водяного знака для канала."""
        try:
            await self.conn.execute("""
                INSERT INTO watermark_settings (
                    user_id, channel_id, watermark_enabled, watermark_mode, watermark_chance,
                    watermark_hashtag, watermark_image_path, watermark_position, watermark_opacity,
                    watermark_scale, watermark_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, channel_id) DO UPDATE SET
                    watermark_enabled = excluded.watermark_enabled,
                    watermark_mode = excluded.watermark_mode,
                    watermark_chance = excluded.watermark_chance,
                    watermark_hashtag = excluded.watermark_hashtag,
                    watermark_image_path = excluded.watermark_image_path,
                    watermark_position = excluded.watermark_position,
                    watermark_opacity = excluded.watermark_opacity,
                    watermark_scale = excluded.watermark_scale,
                    watermark_text = excluded.watermark_text
            """, (
                user_id, channel_id,
                settings.get('watermark_enabled', False),
                settings.get('watermark_mode', 'all'),
                settings.get('watermark_chance', 100),
                settings.get('watermark_hashtag'),
                settings.get('watermark_image_path'),
                settings.get('watermark_position', 'bottom_right'),
                settings.get('watermark_opacity', 128),
                settings.get('watermark_scale', 0.3),
                settings.get('watermark_text')
            ))
            await self.conn.commit()
            logger.info(f"Настройки водяного знака для канала {channel_id} сохранены.")
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек водяного знака: {e}")
            raise

    async def get_watermark_settings(self, user_id: int, channel_id: str) -> Optional[Dict]:
        """Получить настройки водяного знака для канала."""
        try:
            async with self.conn.execute("""
                SELECT watermark_enabled, watermark_mode, watermark_chance, watermark_hashtag,
                       watermark_image_path, watermark_position, watermark_opacity, watermark_scale,
                       watermark_text
                FROM watermark_settings
                WHERE user_id = ? AND channel_id = ?
            """, (user_id, channel_id)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        'watermark_enabled': row[0],
                        'watermark_mode': row[1],
                        'watermark_chance': row[2],
                        'watermark_hashtag': row[3],
                        'watermark_image_path': row[4],
                        'watermark_position': row[5],
                        'watermark_opacity': row[6],
                        'watermark_scale': row[7],
                        'watermark_text': row[8]
                    }
                return None
        except Exception as e:
            logger.error(f"Ошибка получения настроек водяного знака: {e}")
            return None