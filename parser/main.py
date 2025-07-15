import os
import sys
import json
import re
import asyncio
import traceback
from typing import List, Optional
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI, HTTPException, BackgroundTasks, Body
from pydantic import BaseModel
import logging
from dotenv import load_dotenv
from pyrogram.errors import PeerIdInvalid
from fastapi.responses import JSONResponse

from parser.database import Database
from shared.models import ForwardingConfigRequest, ParseConfig
from parser.config import config
from parser.forwarder import TelegramForwarder
import aiosqlite
from parser.navigation_api import router as navigation_router
from fastapi.middleware.cors import CORSMiddleware

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Telegram Parser API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(navigation_router)

# Модели для запросов
class MonitorRequest(BaseModel):
    channel_link: str
    config: ParseConfig

# Модели для запросов пересылки
class ForwardingRequest(BaseModel):
    source_channel: str
    target_channel: str
    config: dict

class ForwardingConfigRequest(BaseModel):
    user_id: int
    source_channel_id: int
    target_channel_id: str
    parse_mode: str = "all"
    hashtag_filter: Optional[str] = None
    delay_seconds: int = 0
    footer_text: str = ""
    # Поля для гиперссылки в приписке
    footer_link: Optional[str] = None  # URL для гиперссылки
    footer_link_text: Optional[str] = None  # Текст, который будет гиперссылкой
    footer_full_link: bool = False  # Превращать ли всю приписку в ссылку
    text_mode: str = "hashtags_only"
    max_posts: Optional[int] = None
    hide_sender: bool = True
    paid_content_stars: Optional[int] = 0  # Новое поле для стоимости платного контента

# --- Pydantic модели для запросов ---

class ChannelRequest(BaseModel):
    channel_id: str
    channel_title: str

class TargetChannelRequest(BaseModel):
    channel_id: str
    channel_title: str

class MonitoringRequest(BaseModel):
    channel_id: str
    target_channel: str

class PostingTemplateRequest(BaseModel):
    name: str
    settings: dict

class NavigationMessageRequest(BaseModel):
    message_id: int

# Инициализация базы данных
db = Database()

# Инициализация форвардера (будет запущен при необходимости)
forwarder = None

@app.on_event("startup")
async def startup_event():
    """Действия при запуске сервиса"""
    await db.init()
    # Сбросить все мониторинги в неактивные (на случай падения/рестарта)
    async with aiosqlite.connect('parser.db') as conn:
        await conn.execute("UPDATE user_monitorings SET is_active = FALSE")
        await conn.execute("UPDATE parse_configs SET is_active = FALSE")
        await conn.commit()
    logger.info("Parser service started")
    # Восстанавливаем активные мониторинги (если нужно)
    # active_monitors = await db.get_active_configs()
    # for monitor in active_monitors:
    #     await parser.start_monitoring(monitor.channel_id, monitor)

@app.on_event("shutdown")
async def shutdown_event():
    """Действия при остановке сервиса"""

    if forwarder:
        await forwarder.stop()  # Останавливаем форвардер только если он был запущен
    await db.close()

def get_or_create_forwarder():
    """Получить существующий forwarder или создать новый"""
    global forwarder
    if forwarder is None:
        forwarder = TelegramForwarder(db_instance=db)
    return forwarder

@app.post("/monitor/start")
async def start_monitoring(request: MonitorRequest):
    """Запуск мониторинга канала"""
    try:
        # Проверяем, что указан целевой канал
        target_channel = request.config.settings.get("target_channel")
        if not target_channel:
            raise HTTPException(status_code=400, detail="Не указан целевой канал для публикации!")
        await parser.start_monitoring(
            request.channel_link,
            request.config
        )
        return {"status": "success", "message": f"Monitoring started. Target: {target_channel}"}
    except Exception as e:
        logger.error(f"Error starting monitoring: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/monitor/stop/{channel_id}")
async def stop_monitoring(channel_id: int):
    """Остановка мониторинга канала"""
    try:
        await parser.stop_monitoring(channel_id)
        return {"status": "success", "message": "Monitoring stopped"}
    except Exception as e:
        logger.error(f"Error stopping monitoring: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/channel/last-message/{channel_id}")
async def get_channel_last_message(channel_id: str):
    """Получить информацию о последнем сообщении в канале"""
    logger.info(f"[API] === НАЧАЛО ОБРАБОТКИ /channel/last-message/{channel_id} ===")
    try:
        # Пробуем сначала int, если не получилось — используем строку (username)
        try:
            channel_id_typed = int(channel_id)
            id_type = 'id'
            logger.info(f"[API] channel_id_typed={channel_id_typed}, id_type={id_type}")
        except (ValueError, TypeError):
            channel_id_typed = channel_id
            id_type = 'username'
            logger.info(f"[API] channel_id_typed={channel_id_typed}, id_type={id_type}")
        
        # Получаем forwarder
        forwarder = get_or_create_forwarder()
        
        # Запускаем userbot если не запущен
        if not hasattr(forwarder.userbot, 'is_connected') or not forwarder.userbot.is_connected:
            logger.info(f"[API] Userbot не запущен, запускаем...")
            await forwarder.userbot.start()
            logger.info(f"[API] Userbot успешно запущен")
        
        # Получаем последнее сообщение из канала
        logger.info(f"[API] Делаем запрос к Telegram API для получения последнего сообщения из канала {channel_id_typed}")
        try:
            # Получаем историю сообщений (только 1 сообщение, самое новое)
            messages = []
            async for message in forwarder.userbot.get_chat_history(channel_id_typed, limit=1):
                messages.append(message)
                break  # Берем только первое (самое новое)
            
            if not messages:
                raise HTTPException(status_code=404, detail="В канале нет сообщений")
            
            last_message = messages[0]
            
            result = {
                "channel_id": str(channel_id_typed),
                "last_message_id": last_message.id,
                "last_message_date": last_message.date.isoformat() if hasattr(last_message, 'date') else None,
                "has_media": last_message.media is not None,
                "media_type": last_message.media.value if last_message.media else None,
                "text_length": len(last_message.text or last_message.caption or "") if hasattr(last_message, 'text') or hasattr(last_message, 'caption') else 0
            }
            
            logger.info(f"[API] === КОНЕЦ ОБРАБОТКИ /channel/last-message/{channel_id} ===")
            return result
            
        except PeerIdInvalid as e:
            logger.error(f"[API] Канал {channel_id_typed} недоступен: {e}")
            raise HTTPException(status_code=404, detail=f"Канал {channel_id_typed} недоступен или не существует")
        except Exception as e:
            logger.error(f"[API] Ошибка при получении последнего сообщения: {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка при получении последнего сообщения: {str(e)}")
            
    except Exception as e:
        logger.error(f"[API] Неожиданная ошибка в get_channel_last_message: {e}")
        logger.error(f"[API] Полная ошибка: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/channel/stats/{channel_id}")
async def get_channel_stats(channel_id: str):
    logger.info(f"[API] === НАЧАЛО ОБРАБОТКИ /channel/stats/{channel_id} ===")
    try:
        # Пробуем сначала int, если не получилось — используем строку (username)
        try:
            channel_id_typed = int(channel_id)
            id_type = 'id'
            logger.info(f"[API] channel_id_typed={channel_id_typed}, id_type={id_type}")
        except (ValueError, TypeError):
            channel_id_typed = channel_id
            id_type = 'username'
            logger.info(f"[API] channel_id_typed={channel_id_typed}, id_type={id_type}")
        
        # Сначала проверяем БД на наличие данных о канале
        logger.info(f"[API] Проверяем БД на наличие данных о канале {channel_id_typed}")
        db_stats = {
            'parsed_count': 0,
            'parsed_media_groups': 0,
            'parsed_singles': 0,
            'min_id': None,
            'max_id': None,
            'last_parsed_id': None,
            'last_parsed_date': None
        }
        
        try:
            db_stats = await db.get_channel_stats(channel_id_typed)
            logger.info(f"[API] Получена статистика из БД: {db_stats}")
        except Exception as e:
            logger.warning(f"[API] Не удалось получить статистику из БД: {e}")
        
        # Проверяем, есть ли в БД информация о канале (не только статистика парсинга)
        logger.info(f"[API] Проверяем таблицу channel_info для канала {channel_id_typed}")
        channel_info_from_db = None
        last_message_id = None
        try:
            # Проверяем, есть ли запись о канале в БД
            async with db.conn.execute(
                "SELECT channel_title, username, total_posts, is_public, last_updated, last_message_id FROM channel_info WHERE channel_id = ?",
                (str(channel_id_typed),)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    channel_info_from_db = {
                        'channel_title': row[0],
                        'username': row[1],
                        'total_posts': row[2],
                        'is_public': bool(row[3]),
                        'last_updated': row[4],
                        'last_message_id': row[5] if len(row) > 5 else None
                    }
                    last_message_id = channel_info_from_db.get('last_message_id')
                    logger.info(f"[API] Найдена информация о канале в БД: {channel_info_from_db}")
                else:
                    logger.info(f"[API] Информация о канале {channel_id_typed} в БД не найдена")
        except Exception as e:
            logger.warning(f"[API] Не удалось получить информацию о канале из БД: {e}")
        
        # Если в БД есть актуальная информация (не старше 1 часа), используем её
        if channel_info_from_db:
            last_updated = datetime.fromisoformat(channel_info_from_db['last_updated'])
            time_diff = datetime.now() - last_updated
            logger.info(f"[API] Время с последнего обновления: {time_diff}")
            if time_diff < timedelta(hours=1):
                logger.info(f"[API] Используем данные из БД (актуальны)")
                return {
                    "status": "success",
                    "channel_id": channel_id_typed,
                    "channel_title": channel_info_from_db['channel_title'],
                    "username": channel_info_from_db['username'],
                    "total_posts": channel_info_from_db['total_posts'],
                    "parsed_posts": db_stats['parsed_count'],
                    "parsed_media_groups": db_stats['parsed_media_groups'],
                    "parsed_singles": db_stats['parsed_singles'],
                    "min_id": db_stats['min_id'],
                    "max_id": db_stats['max_id'],
                    "last_parsed_id": db_stats['last_parsed_id'],
                    "last_parsed_date": db_stats['last_parsed_date'],
                    "last_message_id": last_message_id,
                    "is_member": True,  # Если есть в БД, значит был доступ
                    "is_public": channel_info_from_db['is_public'],
                    "accessible": True,
                    "source": "database"
                }
            else:
                logger.info(f"[API] Данные в БД устарели (старше 1 часа)")
        else:
            logger.info(f"[API] Данных в БД нет, нужно запросить из Telegram API")
        
        # Если данных нет или они устарели, делаем запрос к Telegram API
        logger.info(f"[API] Делаем запрос к Telegram API для канала {channel_id_typed}")
        
        # Получаем forwarder
        forwarder = get_or_create_forwarder()
        
        # Запускаем userbot если не запущен
        if not hasattr(forwarder.userbot, 'is_connected') or not forwarder.userbot.is_connected:
            logger.info(f"[API] Userbot не запущен, запускаем...")
            await forwarder.userbot.start()
            logger.info(f"[API] Userbot успешно запущен")
        
        # Получаем информацию о канале
        try:
            chat = await forwarder.userbot.get_chat(channel_id_typed)
            if not chat:
                logger.warning(f"[API] Канал {channel_id_typed} не найден")
                return {
                    "status": "error",
                    "message": f"Канал {channel_id_typed} не найден или недоступен",
                    "parsed_posts": db_stats['parsed_count'],
                    "parsed_media_groups": db_stats['parsed_media_groups'],
                    "parsed_singles": db_stats['parsed_singles'],
                    "min_id": db_stats['min_id'],
                    "max_id": db_stats['max_id'],
                    "last_parsed_id": db_stats['last_parsed_id'],
                    "last_parsed_date": db_stats['last_parsed_date'],
                    "source": "database_only"
                }
        except Exception as e:
            logger.error(f"[API] Ошибка при получении информации о канале: {e}")
            return {
                "status": "error",
                "message": f"Ошибка при получении информации о канале: {str(e)}",
                "parsed_posts": db_stats['parsed_count'],
                "parsed_media_groups": db_stats['parsed_media_groups'],
                "parsed_singles": db_stats['parsed_singles'],
                "min_id": db_stats['min_id'],
                "max_id": db_stats['max_id'],
                "last_parsed_id": db_stats['last_parsed_id'],
                "last_parsed_date": db_stats['last_parsed_date'],
                "source": "database_only"
            }
        
        # Попробуем получить последнее сообщение
        try:
            # Получаем историю сообщений (только 1 сообщение, самое новое)
            messages = []
            async for message in forwarder.userbot.get_chat_history(channel_id_typed, limit=1):
                messages.append(message)
                break  # Берем только первое (самое новое)
            
            if messages:
                last_message = messages[0]
                last_message_id = last_message.id
                logger.info(f"[API] Получено последнее сообщение ID: {last_message_id}")
            else:
                logger.info(f"[API] В канале нет сообщений")
                last_message_id = None
        except Exception as e:
            logger.error(f"[API] Ошибка при получении последнего сообщения: {e}")
            last_message_id = None
        
        # Теперь получаем общее количество сообщений
        total_posts = getattr(chat, 'message_count', 0) or 0
        logger.info(f"[API] Общее количество постов: {total_posts}")
        
        # Извлекаем остальные данные из объекта chat
        channel_title = getattr(chat, 'title', None) or getattr(chat, 'username', None) or str(channel_id_typed)
        username = getattr(chat, 'username', None)
        is_member = not getattr(chat, 'left', False)
        is_public = bool(username)
        description = getattr(chat, 'description', None)
        created_at = getattr(chat, 'date', None)
        members_count = getattr(chat, 'members_count', None)
        logger.info(f"[API] Извлечённые данные: title={channel_title}, username={username}, total_posts={total_posts}, is_member={is_member}, is_public={is_public}, description={description}, created_at={created_at}, members_count={members_count}, last_message_id={last_message_id}")
        
        # Сохраняем информацию о канале в БД
        logger.info(f"[API] Сохраняем информацию о канале в БД")
        try:
            await db.conn.execute(
                """INSERT OR REPLACE INTO channel_info 
                   (channel_id, channel_title, username, total_posts, is_public, last_updated, last_message_id) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(channel_id_typed), channel_title, username, total_posts, is_public, datetime.now().isoformat(), last_message_id)
            )
            await db.conn.commit()
            logger.info(f"[API] Сохранена информация о канале в БД")
        except Exception as e:
            logger.warning(f"[API] Не удалось сохранить информацию о канале в БД: {e}")
        
        logger.info(f"[API] Возвращаем результат")
        
        return {
            "status": "success",
            "channel_id": channel_id_typed,
            "channel_title": channel_title,
            "username": username,
            "total_posts": total_posts,
            "parsed_posts": db_stats['parsed_count'],
            "parsed_media_groups": db_stats['parsed_media_groups'],
            "parsed_singles": db_stats['parsed_singles'],
            "min_id": db_stats['min_id'],
            "max_id": db_stats['max_id'],
            "last_parsed_id": db_stats['last_parsed_id'],
            "last_parsed_date": db_stats['last_parsed_date'],
            "last_message_id": last_message_id,
            "is_member": is_member,
            "is_public": is_public,
            "accessible": True,
            "members_count": members_count,
            "description": description,
            "created_at": created_at.isoformat() if created_at else None,
            "source": "telegram_api"
        }
    except Exception as e:
        logger.error(f"[API] Непредвиденная ошибка: {e}")
        return {
            "status": "error",
            "message": str(e),
            "source": "error"
        }
    finally:
        logger.info(f"[API] === КОНЕЦ ОБРАБОТКИ /channel/stats/{channel_id} ===")

@app.get("/monitor/status/{channel_id}")
async def monitor_status(channel_id: str):
    try:
        config = await db.get_parse_config(int(channel_id))
        if config and config.is_active:
            return {"is_active": True, "started_at": config.created_at}
        else:
            return {"is_active": False}
    except Exception as e:
        logger.error(f"Error getting monitor status: {e}")
        return {"is_active": False, "error": str(e)}

# --- Вспомогательная функция для извлечения хэштегов ---
def extract_hashtags(text):
    if not text:
        return ""
    hashtags = re.findall(r"#\w+", text)
    return " ".join(hashtags)

@app.post("/forwarding/start")
async def start_forwarding(request: dict):
    """Запуск пересылки сообщений"""
    try:
        logger.info(f"[API] 🔄 ЗАПУСК МОНИТОРИНГА (НЕ ПАРСИНГА!)")
        logger.info(f"[API] Запрос: {request}")
        user_id = request.get('user_id')
        source_channel_id = request.get('source_channel_id')
        target_channel_id = request.get('target_channel_id')
        settings = request.get('settings')
        if settings:
            config = {
                'hide_sender': settings.get('hide_sender', True),
                'footer_text': settings.get('footer_text', ''),
                'max_posts': settings.get('max_posts', 0) if settings.get('max_posts') is not None else 0,
                'forward_mode': 'copy',
                'parse_mode': settings.get('parse_mode', 'all'),
                'hashtag_filter': settings.get('hashtag_filter', ''),
                'text_mode': settings.get('text_mode', 'hashtags_only'),
                'delay_seconds': settings.get('delay_seconds', 0),
                'paid_content_mode': settings.get('paid_content_mode', 'off'),
                'paid_content_stars': settings.get('paid_content_stars', 0),
                'paid_content_hashtag': settings.get('paid_content_hashtag'),
                'paid_content_every': settings.get('paid_content_every'),
                'paid_content_chance': settings.get('paid_content_chance'),
                # Добавляем настройки гиперссылки
                'footer_link': settings.get('footer_link'),
                'footer_link_text': settings.get('footer_link_text'),
                'footer_full_link': settings.get('footer_full_link', False),
            }
            logger.info(f"[API] Итоговый config для форвардера (из settings): {config}")
        else:
            config = {
                'hide_sender': request.get('hide_sender', True),
                'footer_text': request.get('footer_text', ''),
                'max_posts': request.get('max_posts', 0) if request.get('max_posts') is not None else 0,
                'forward_mode': 'copy',
                'parse_mode': request.get('parse_mode', 'all'),
                'hashtag_filter': request.get('hashtag_filter', ''),
                'text_mode': request.get('text_mode', 'hashtags_only'),
                'delay_seconds': request.get('delay_seconds', 0),
                'paid_content_mode': request.get('paid_content_mode', 'off'),
                'paid_content_stars': request.get('paid_content_stars', 0),
                'paid_content_hashtag': request.get('paid_content_hashtag'),
                'paid_content_every': request.get('paid_content_every'),
                'paid_content_chance': request.get('paid_content_chance'),
                # Добавляем настройки гиперссылки
                'footer_link': request.get('footer_link'),
                'footer_link_text': request.get('footer_link_text'),
                'footer_full_link': request.get('footer_full_link', False),
            }
            logger.info(f"[API] Итоговый config для форвардера (из request): {config}")
        await forward_messages(user_id, source_channel_id, target_channel_id, config)
        logger.info(f"[API] ✅ Мониторинг запущен для пользователя {user_id}")
        return {"status": "success", "message": "Пересылка запущена"}
    except Exception as e:
        logger.error(f"[API] ❌ Ошибка запуска мониторинга: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/forwarding/parse")
async def start_forwarding_parsing(request: ForwardingRequest):
    """Запуск парсинга и пересылки существующих сообщений"""
    try:
        logger.info(f"[API] 🚀 ЗАПУСК ПАРСИНГА + ПЕРЕСЫЛКИ (НЕ МОНИТОРИНГА!)")
        logger.info(f"[API] Источник: {request.source_channel} -> Цель: {request.target_channel}")
        logger.info(f"[API] Конфигурация: {request.config}")
        
        # Подробное логирование конфигурации
        paid_stars = request.config.get('paid_content_stars', 0)
        logger.info(f"[API] 🔍 ПЛАТНЫЕ ЗВЕЗДЫ: {paid_stars} (тип: {type(paid_stars)})")
        logger.info(f"[API] 🔍 Все ключи конфигурации: {list(request.config.keys())}")
        
        forwarder = get_or_create_forwarder()
        result = await forwarder.start_forwarding_parsing(
            source_channel=request.source_channel,
            target_channel=request.target_channel,
            config=request.config
        )
        logger.info(f"[API] ✅ Парсинг + пересылка завершены: {result}")
        return {"status": "success", "message": "Parsing and forwarding completed", "result": result}
    except Exception as e:
        logger.error(f"[API] ❌ Ошибка запуска парсинга + пересылки: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/forwarding/stop")
async def stop_forwarding(request: dict):
    """
    Остановить пересылку/мониторинг для канала и цели (если указана).
    """
    channel_id = request.get("channel_id")
    target_channel_id = request.get("target_channel_id")
    logger.info(f"[API] /forwarding/stop: channel_id={channel_id}, target_channel_id={target_channel_id}")
    forwarder = get_or_create_forwarder()
    if channel_id is not None:
        try:
            channel_id_int = int(channel_id)
        except Exception:
            channel_id_int = channel_id
        await forwarder.stop_forwarding(channel_id_int, target_channel_id)
        logger.info(f"[API] Остановлен мониторинг: {channel_id} -> {target_channel_id}")
        return {"status": "stopped", "channel_id": channel_id, "target_channel_id": target_channel_id}
    return JSONResponse(status_code=400, content={"error": "channel_id обязателен"})

@app.get("/forwarding/stats/{channel_id}")
async def get_forwarding_stats(channel_id: int):
    """Получить статистику пересылки для канала"""
    try:
        forwarder = get_or_create_forwarder()
        stats = await forwarder.get_forwarding_status(channel_id)
        return stats
    except Exception as e:
        logger.error(f"Error getting forwarding stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/forwarding/config")
async def save_forwarding_config(config: ForwardingConfigRequest):
    """Сохранение конфигурации пересылки"""
    try:
        # Преобразуем username в id, если нужно
        global forwarder
        if forwarder is None:
            forwarder = TelegramForwarder(db_instance=db)
        userbot = forwarder.userbot
        new_config = config.dict()
        for field in ["source_channel_id", "target_channel_id"]:
            val = new_config[field]
            if isinstance(val, str) and not val.startswith("-100") and not val.isdigit():
                try:
                    # Добавляем обработку FloodWait и других ошибок
                    try:
                        chat = await userbot.get_chat(val)
                    except Exception as chat_error:
                        if "FLOOD_WAIT" in str(chat_error):
                            logger.warning(f"[CONFIG] FloodWait: ожидаем {wait_time} секунд")
                            await asyncio.sleep(wait_time)
                            chat = await userbot.get_chat(val)
                        elif "Peer id invalid" in str(chat_error) or "ID not found" in str(chat_error):
                            logger.error(f"[CONFIG] Канал {val} недоступен или не существует: {chat_error}")
                            return {"status": "error", "message": f"Канал {val} недоступен или не существует"}, 400
                        else:
                            raise chat_error
                    new_config[field] = chat.id
                except Exception as e:
                    return {"status": "error", "message": f"Не удалось получить id для {field}: {val} ({e})"}, 400
            elif isinstance(val, str) and val.isdigit():
                new_config[field] = int(val)
        # Сохраняем конфигурацию в базу данных
        await db.add_forwarding_config(ForwardingConfigRequest(**new_config))
        return {"status": "success", "message": "Конфигурация сохранена"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/forwarding/clear_history")
async def clear_forwarding_history(request: dict):
    """Очистить историю пересланных постов"""
    try:
        channel_id = request.get("channel_id")
        target_channel = request.get("target_channel")
        
        # Если channel_id передан как строка, конвертируем в int
        if channel_id and isinstance(channel_id, str):
            try:
                channel_id = int(channel_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="channel_id должен быть числом")
        
        success = await db.clear_forwarding_history(channel_id, target_channel)
        
        if success:
            # Очищаем кэш в памяти форвардера
            global forwarder
            if forwarder is not None:
                await forwarder.clear_cache(channel_id)
            
            # Получаем статистику после очистки
            stats = await db.get_forwarding_history_stats(channel_id, target_channel)
            
            if channel_id and target_channel:
                message = f"История пересылки канала {channel_id} в {target_channel} очищена"
            elif channel_id:
                message = f"История пересылки канала {channel_id} очищена"
            elif target_channel:
                message = f"История пересылки в {target_channel} очищена"
            else:
                message = "Вся история пересылки очищена"
            
            return {
                "status": "success", 
                "message": message,
                "remaining_stats": stats
            }
        else:
            raise HTTPException(status_code=500, detail="Ошибка при очистке истории")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error clearing forwarding history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/forwarding/history_stats")
async def get_forwarding_history_stats(channel_id: int = None, target_channel: str = None):
    """Получить статистику истории пересылки"""
    try:
        # Если channel_id передан как строка, конвертируем в int
        if channel_id and isinstance(channel_id, str):
            try:
                channel_id = int(channel_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="channel_id должен быть числом")
        
        stats = await db.get_forwarding_history_stats(channel_id, target_channel)
        
        if "error" in stats:
            raise HTTPException(status_code=500, detail=stats["error"])
        
        return {
            "status": "success",
            "stats": stats
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting forwarding history stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def forward_messages(user_id: int, source_channel_id: int, target_channel_id: int, config: dict = None):
    """Запуск мониторинга пересылки сообщений"""
    try:
        logger.info(f"[FORWARD_MESSAGES] 🔄 ЗАПУСК МОНИТОРИНГА (НЕ ПАРСИНГА!)")
        logger.info(f"[FORWARD_MESSAGES] Пользователь: {user_id}, Источник: {source_channel_id}, Цель: {target_channel_id}")
        if config is None:
            config = await db.get_forwarding_config(user_id, source_channel_id)
        logger.info(f"[FORWARD_MESSAGES] ⚙️ Конфигурация: {config}")
        global forwarder
        if forwarder is None:
            forwarder = TelegramForwarder(db_instance=db)
            logger.info("Forwarder initialized with existing userbot")
        try:
            logger.info(f"[FORWARD_MESSAGES] Вызов start_forwarding для пересылки новых сообщений (handler)")
            result = await forwarder.start_forwarding(
                source_channel=str(source_channel_id),
                target_channel=str(target_channel_id),
                config=config
            )
            logger.info(f"[FORWARD_MESSAGES] ✅ Мониторинг запущен: {result}")
        except Exception as e:
            logger.error(f"[FORWARD_MESSAGES] ❌ Ошибка в процессе запуска мониторинга: {e}")
            import traceback
            logger.error(f"[FORWARD_MESSAGES] Полная ошибка: {traceback.format_exc()}")
    except Exception as e:
        logger.error(f"[FORWARD_MESSAGES] ❌ Ошибка запуска мониторинга: {e}")
        import traceback
        logger.error(f"[FORWARD_MESSAGES] Полная ошибка: {traceback.format_exc()}")

# --- Новые эндпоинты для работы с пользовательскими данными ---

@app.get("/health")
async def health_check():
    """Проверка состояния сервиса"""
    try:
        # Проверяем подключение к базе данных
        await db.conn.execute("SELECT 1")
        return {"status": "healthy", "database": "connected", "timestamp": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Service unhealthy: {e}")

@app.get("/user/channels/{user_id}")
async def get_user_channels(user_id: int):
    """Получить историю каналов пользователя"""
    try:
        logger.info(f"[API] Getting user channels for user_id={user_id}")
        channels = await db.get_user_channels(user_id)
        logger.info(f"[API] Successfully got {len(channels)} channels for user_id={user_id}")
        return {"status": "success", "channels": channels}
    except Exception as e:
        logger.error(f"[API] Error getting user channels for user_id={user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/user/channels/{user_id}")
async def add_user_channel(user_id: int, request: ChannelRequest):
    """Добавить канал в историю пользователя (только если канал существует)"""
    try:
        forwarder = get_or_create_forwarder()
        try:
            chat = await forwarder.userbot.get_chat(request.channel_id)
            channel_title = getattr(chat, 'title', None) or request.channel_title
        except Exception as e:
            logger.warning(f"[API][add_user_channel] Не удалось получить канал {request.channel_id}: {e}")
            raise HTTPException(status_code=400, detail="Канал не найден или недоступен")
        await db.add_user_channel(user_id, str(chat.id), channel_title)
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding user channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/user/channels/{user_id}/{channel_id}")
async def update_user_channel_last_used(user_id: int, channel_id: str):
    """Обновить время последнего использования канала"""
    try:
        await db.update_user_channel_last_used(user_id, channel_id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error updating user channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/user/channels/{user_id}/{channel_id}")
async def remove_user_channel(user_id: int, channel_id: str):
    """Удалить канал из истории пользователя"""
    try:
        await db.remove_user_channel(user_id, channel_id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error removing user channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/target-channels/{user_id}")
async def get_user_target_channels(user_id: int):
    """Получить историю целевых каналов пользователя"""
    try:
        channels = await db.get_user_target_channels(user_id)
        return {"status": "success", "channels": channels}
    except Exception as e:
        logger.error(f"Error getting user target channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/user/target-channels/{user_id}")
async def add_user_target_channel(user_id: int, request: TargetChannelRequest):
    """Добавить целевой канал в историю пользователя (только если канал существует)"""
    try:
        forwarder = get_or_create_forwarder()
        try:
            chat = await forwarder.userbot.get_chat(request.channel_id)
            channel_title = getattr(chat, 'title', None) or request.channel_title
        except Exception as e:
            logger.warning(f"[API][add_user_target_channel] Не удалось получить канал {request.channel_id}: {e}")
            raise HTTPException(status_code=400, detail="Канал не найден или недоступен")
        await db.add_user_target_channel(user_id, str(chat.id), channel_title)
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding user target channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/user/target-channels/{user_id}/{channel_id}")
async def update_user_target_channel_last_used(user_id: int, channel_id: str):
    """Обновить время последнего использования целевого канала"""
    try:
        await db.update_user_target_channel_last_used(user_id, channel_id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error updating user target channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/user/target-channels/{user_id}/{channel_id}")
async def remove_user_target_channel(user_id: int, channel_id: str):
    """Удалить целевой канал из истории пользователя"""
    try:
        await db.remove_user_target_channel(user_id, channel_id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error removing user target channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/monitorings/{user_id}")
async def get_user_monitorings(user_id: int):
    """Получить активные мониторинги пользователя"""
    try:
        monitorings = await db.get_active_monitorings_by_user(user_id)
        return {"status": "success", "monitorings": monitorings}
    except Exception as e:
        logger.error(f"Error getting user monitorings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/user/monitorings/{user_id}")
async def add_user_monitoring(user_id: int, request: MonitoringRequest):
    """Добавить мониторинг пользователя"""
    try:
        await db.add_user_monitoring(user_id, request.channel_id, request.target_channel)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error adding user monitoring: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/user/monitorings/{user_id}")
async def deactivate_user_monitoring(user_id: int, request: MonitoringRequest):
    """Деактивировать мониторинг пользователя"""
    try:
        await db.deactivate_user_monitoring(user_id, request.channel_id, request.target_channel)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error deactivating user monitoring: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/posting-templates/{user_id}")
async def get_user_posting_templates(user_id: int):
    """Получить шаблоны публикации пользователя"""
    try:
        async with db.conn.execute(
            "SELECT name, settings_json FROM posting_templates WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            templates = []
            async for row in cursor:
                templates.append({
                    "name": row[0],
                    "settings": json.loads(row[1]) if row[1] else {}
                })
        return {"status": "success", "templates": templates}
    except Exception as e:
        logger.error(f"Error getting user posting templates: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/user/posting-templates/{user_id}")
async def save_user_posting_template(user_id: int, request: PostingTemplateRequest):
    """Сохранить шаблон публикации пользователя"""
    try:
        await db.conn.execute(
            "INSERT OR REPLACE INTO posting_templates (user_id, name, settings_json) VALUES (?, ?, ?)",
            (user_id, request.name, json.dumps(request.settings))
        )
        await db.conn.commit()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error saving user posting template: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/user/posting-templates/{user_id}/{name}")
async def delete_user_posting_template(user_id: int, name: str):
    """Удалить шаблон публикации пользователя"""
    try:
        await db.conn.execute(
            "DELETE FROM posting_templates WHERE user_id = ? AND name = ?",
            (user_id, name)
        )
        await db.conn.commit()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error deleting user posting template: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Эндпоинты для навигации ---

@app.get("/navigation/message/{channel_id}")
async def get_navigation_message_id(channel_id: str):
    """Получить ID навигационного сообщения для канала"""
    try:
        async with db.conn.execute(
            "SELECT message_id FROM navigation_messages WHERE channel_id = ?",
            (str(channel_id),)
        ) as cursor:
            row = await cursor.fetchone()
            return {"status": "success", "message_id": row[0] if row else None}
    except Exception as e:
        logger.error(f"Error getting navigation message ID: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/navigation/message/{channel_id}")
async def save_navigation_message_id(channel_id: str, request: NavigationMessageRequest):
    """Сохранить ID навигационного сообщения для канала"""
    try:
        await db.conn.execute(
            "INSERT OR REPLACE INTO navigation_messages (channel_id, message_id) VALUES (?, ?)",
            (str(channel_id), request.message_id)
        )
        await db.conn.commit()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error saving navigation message ID: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/navigation/message/{channel_id}")
async def delete_navigation_message_id(channel_id: str):
    """Удалить ID навигационного сообщения для канала"""
    try:
        await db.conn.execute(
            "DELETE FROM navigation_messages WHERE channel_id = ?",
            (str(channel_id),)
        )
        await db.conn.commit()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error deleting navigation message ID: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/monitor/stats/{channel_id}/{target_channel}")
async def get_monitor_stats(channel_id: int, target_channel: str):
    """Получить статистику мониторинга"""
    try:
        # Здесь можно добавить логику получения статистики мониторинга
        # Пока возвращаем базовую статистику
        return {
            "status": "success",
            "stats": {
                "processed": 0,
                "forwarded": 0,
                "skipped": 0,
                "last_activity": "Нет"
            }
        }
    except Exception as e:
        logger.error(f"Error getting monitor stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Эндпоинты для пересылки ---

@app.post("/forwarding/start/{user_id}")
async def start_forwarding(user_id: int):
    """Запустить пересылку для пользователя"""
    try:
        # Здесь можно добавить логику запуска пересылки
        return {"status": "success", "message": "Пересылка запущена"}
    except Exception as e:
        logger.error(f"Error starting forwarding: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/forwarding/stop/{user_id}")
async def stop_forwarding(user_id: int):
    """Остановить пересылку для пользователя"""
    try:
        # Здесь можно добавить логику остановки пересылки
        return {"status": "success", "message": "Пересылка остановлена"}
    except Exception as e:
        logger.error(f"Error stopping forwarding: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/forwarding/stats/{user_id}")
async def get_forwarding_stats(user_id: int):
    """Получить статистику пересылки для пользователя"""
    try:
        # Здесь можно добавить логику получения статистики пересылки
        return {
            "status": "success",
            "stats": {
                "total_forwarded": 0,
                "today_forwarded": 0,
                "hashtag_matches": 0,
                "errors_count": 0,
                "last_activity": "Нет"
            }
        }
    except Exception as e:
        logger.error(f"Error getting forwarding stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/forwarding/config/{user_id}")
async def save_forwarding_config(user_id: int, config: dict):
    """Сохранить конфигурацию пересылки для пользователя"""
    try:
        # Здесь можно добавить логику сохранения конфигурации пересылки
        return {"status": "success", "message": "Конфигурация сохранена"}
    except Exception as e:
        logger.error(f"Error saving forwarding config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/forwarding/parse-and-forward/{user_id}")
async def start_forwarding_parsing(user_id: int):
    """Запустить парсинг и пересылку для пользователя"""
    try:
        # Здесь можно добавить логику запуска парсинга и пересылки
        return {"status": "success", "message": "Парсинг и пересылка запущены"}
    except Exception as e:
        logger.error(f"Error starting forwarding parsing: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/forwarding/history")
async def clear_forwarding_history(channel_id: Optional[int] = None, target_channel: Optional[str] = None):
    """Очистить историю пересылки"""
    try:
        # Здесь можно добавить логику очистки истории пересылки
        result = await db.clear_forwarding_history(channel_id, target_channel)
        return {"status": "success", "result": result}
    except Exception as e:
        logger.error(f"Error clearing forwarding history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/forwarding/history/stats")
async def get_forwarding_history_stats(channel_id: Optional[int] = None, target_channel: Optional[str] = None):
    """Получить статистику истории пересылки"""
    try:
        # Здесь можно добавить логику получения статистики истории пересылки
        stats = await db.get_forwarding_history_stats(channel_id, target_channel)
        return {"status": "success", "stats": stats}
    except Exception as e:
        logger.error(f"Error getting forwarding history stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/forwarding/monitoring_status")
async def get_forwarding_monitoring_status():
    """
    Получить список всех активных мониторингов с полным config каждого.
    Теперь для каждого мониторинга возвращается также target_channel (цель мониторинга).
    """
    forwarder = get_or_create_forwarder()
    status = forwarder.get_all_monitoring_status()
    return JSONResponse(content={"monitorings": status})

@app.post("/forwarding/start_parsing_background")
async def start_forwarding_parsing_background(request: dict):
    """Запустить парсинг+пересылку в фоновом режиме (возвращает task_id сразу)."""
    try:
        source_channel = request.get("source_channel")
        target_channel = request.get("target_channel")
        config = request.get("config", {})
        
        if not source_channel or not target_channel:
            return JSONResponse(
                status_code=400,
                content={"error": "source_channel и target_channel обязательны"}
            )
        
        forwarder = get_or_create_forwarder()
        task_id = await forwarder.start_forwarding_parsing(source_channel, target_channel, config)
        
        return JSONResponse(content={
            "status": "started",
            "task_id": task_id,
            "message": "Парсинг+пересылка запущены в фоновом режиме"
        })
        
    except Exception as e:
        logger.error(f"Ошибка при запуске парсинг+пересылки в фоне: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

@app.get("/forwarding/task_status/{task_id}")
async def get_parse_forward_task_status(task_id: str):
    """Получить статус задачи парсинг+пересылки по task_id."""
    try:
        forwarder = get_or_create_forwarder()
        status = forwarder.get_parse_forward_task_status(task_id)
        
        if "error" in status:
            return JSONResponse(
                status_code=404,
                content=status
            )
        
        return JSONResponse(content=status)
        
    except Exception as e:
        logger.error(f"Ошибка при получении статуса задачи {task_id}: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

@app.post("/forwarding/stop_task/{task_id}")
async def stop_parse_forward_task(task_id: str):
    """Остановить задачу парсинг+пересылки по task_id."""
    try:
        forwarder = get_or_create_forwarder()
        success = forwarder.stop_parse_forward_task(task_id)
        
        if success:
            return JSONResponse(content={
                "status": "stopped",
                "task_id": task_id,
                "message": "Задача остановлена"
            })
        else:
            return JSONResponse(
                status_code=404,
                content={"error": "Задача не найдена или уже завершена"}
            )
        
    except Exception as e:
        logger.error(f"Ошибка при остановке задачи {task_id}: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

@app.get("/forwarding/all_tasks")
async def get_all_parse_forward_tasks():
    """Получить список всех задач парсинг+пересылки."""
    try:
        forwarder = get_or_create_forwarder()
        tasks = forwarder.get_all_parse_forward_tasks()
        
        return JSONResponse(content={
            "tasks": tasks,
            "count": len(tasks)
        })
        
    except Exception as e:
        logger.error(f"Ошибка при получении списка задач: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)