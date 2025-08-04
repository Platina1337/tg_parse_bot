import os
import sys
import json
import re
import asyncio
import traceback
import time
from typing import List, Optional
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI, HTTPException, BackgroundTasks, Body, APIRouter, Request, Form
from pydantic import BaseModel
import logging
from dotenv import load_dotenv
from pyrogram.errors import PeerIdInvalid
from fastapi.responses import JSONResponse

from .database import Database
from shared.models import ForwardingConfigRequest, ParseConfig
from .config import config
from .forwarder import TelegramForwarder
import aiosqlite
from .navigation_api import router as navigation_router
from fastapi.middleware.cors import CORSMiddleware
from .session_manager import SessionManager
from .reaction_manager import ReactionManager
from .text_editor import TextEditor
from pydantic import BaseModel, Field
from pyrogram import Client
from .bulk_link_updater import BulkLinkUpdater

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
    username: Optional[str] = None

class TargetChannelRequest(BaseModel):
    channel_id: str
    channel_title: str
    username: Optional[str] = None



class PostingTemplateRequest(BaseModel):
    name: str
    settings: dict

class NavigationMessageRequest(BaseModel):
    message_id: int

# --- Модели для редактирования текста ---
class TextEditRequest(BaseModel):
    channel_id: int
    link_text: str
    link_url: str
    max_posts: int = 100

# Инициализация базы данных
db = Database()

# Инициализация форвардера (будет запущен при необходимости)
forwarder = None

# Инициализация редактора текста (будет запущен при необходимости)
text_editor = None

# Initialize session manager and reaction manager
session_manager = None
reaction_manager = None

# Хранение задач реакций
reaction_tasks = {}

@app.on_event("startup")
async def startup_event():
    """Действия при запуске сервиса"""
    global forwarder, session_manager, reaction_manager
    
    await db.init()
    # Сбросить все мониторинги в неактивные (на случай падения/рестарта)
    async with aiosqlite.connect('parser.db') as conn:

        await conn.execute("UPDATE parse_configs SET is_active = FALSE")
        await conn.commit()
    logger.info("Parser service started")
    
    # Initialize session manager (DB-backed)
    session_manager = SessionManager(db=db, session_dir=os.path.abspath("sessions"))
    await session_manager.import_sessions_from_files()
    await session_manager.load_clients()
    
    # Initialize reaction manager
    reaction_manager = ReactionManager(session_manager=session_manager)

    # --- Новое: инициализация userbot при старте ---
    forwarder = get_or_create_forwarder()
    try:
        userbot = await forwarder.get_userbot()
        logger.info(f"[STARTUP] Проверка userbot: {userbot}")
        if not hasattr(userbot, 'is_connected') or not userbot.is_connected:
            logger.info(f"[STARTUP] Userbot не запущен, запускаем...")
            await userbot.start()
            logger.info(f"[STARTUP] Userbot успешно запущен")
    except Exception as e:
        logger.error(f"[STARTUP] Ошибка при инициализации userbot: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """Действия при остановке сервиса"""
    global forwarder, session_manager

    if forwarder:
        await forwarder.stop()  # Останавливаем форвардер только если он был запущен
    
    # Stop all sessions
    if session_manager:
        await session_manager.stop_all()
    
    await db.close()

def get_or_create_forwarder():
    """Получить существующий forwarder или создать новый"""
    global forwarder, session_manager
    if forwarder is None:
        forwarder = TelegramForwarder(db_instance=db, session_manager=session_manager)
    return forwarder

def get_or_create_text_editor():
    """Получить существующий text_editor или создать новый"""
    global text_editor, session_manager
    if text_editor is None:
        if session_manager is None:
            logger.error("[get_or_create_text_editor] session_manager не инициализирован!")
            raise Exception("Session manager не инициализирован")
        logger.info(f"[get_or_create_text_editor] Создаем TextEditor с session_manager")
        text_editor = TextEditor(session_manager=session_manager)
    return text_editor

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
            async for message in userbot.get_chat_history(channel_id_typed, limit=1):
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
    """Получить статистику канала"""
    try:
        logger.info(f"[API] === НАЧАЛО ОБРАБОТКИ /channel/stats/{channel_id} ===")
        
        # Определяем тип идентификатора (ID или username)
        if channel_id.startswith("-100") or channel_id.isdigit():
            channel_id_typed = int(channel_id)
            id_type = "id"
        else:
            channel_id_typed = channel_id
            id_type = "username"
        
        logger.info(f"[API] channel_id_typed={channel_id_typed}, id_type={id_type}")
        
        # Сначала проверяем в БД
        logger.info(f"[API] Проверяем БД на наличие данных о канале {channel_id_typed}")
        parsed_stats = await db.get_channel_stats(channel_id_typed)
        logger.info(f"[API] Получена статистика из БД: {parsed_stats}")
        
        # Проверяем таблицу channel_info
        logger.info(f"[API] Проверяем таблицу channel_info для канала {channel_id_typed}")
        channel_info = await db.get_channel_info(channel_id_typed)
        
        if channel_info:
            logger.info(f"[API] Информация о канале {channel_id_typed} найдена в БД: {channel_info}")
            # Объединяем статистику и информацию о канале
            stats = {**parsed_stats, **channel_info}
        else:
            logger.info(f"[API] Информация о канале {channel_id_typed} в БД не найдена")
            
            # Если данных нет, пытаемся получить из Telegram API
            if parsed_stats['parsed_count'] == 0:
                logger.info(f"[API] Данных в БД нет, нужно запросить из Telegram API")
                logger.info(f"[API] Делаем запрос к Telegram API для канала {channel_id_typed}")
                
                logger.info(f"[API][DEBUG] Перед get_or_create_forwarder")
                forwarder = get_or_create_forwarder()
                logger.info(f"[API][DEBUG] Получен forwarder: {forwarder}")
                
                userbot = await forwarder.get_userbot(task="parsing")
                logger.info(f"[API][DEBUG] userbot: {userbot}")
                
                try:
                    # ДОБАВЛЕНО: Попытка разрешения peer ID
                    if id_type == "id":
                        logger.info(f"[API] Пытаемся разрешить peer ID {channel_id_typed}")
                        try:
                            # Метод 1: Прямое получение чата
                            chat = await userbot.get_chat(channel_id_typed)
                            logger.info(f"[API] ✅ Успешно получили чат: {chat.title} (@{chat.username})")
                        except Exception as direct_error:
                            logger.warning(f"[API] Прямое получение чата не удалось: {direct_error}")
                            
                            # Метод 2: Попытка получения через invite link (если доступен)
                            try:
                                logger.info(f"[API] Пытаемся получить invite link для канала")
                                invite_link = await userbot.export_chat_invite_link(channel_id_typed)
                                logger.info(f"[API] Получили invite link: {invite_link}")
                                chat = await userbot.get_chat(channel_id_typed)
                                logger.info(f"[API] ✅ Успешно получили чат через invite link: {chat.title}")
                            except Exception as invite_error:
                                logger.warning(f"[API] Получение через invite link не удалось: {invite_error}")
                                
                                # Метод 2.5: Попытка вступления по invite link (если известен)
                                try:
                                    # Проверяем, есть ли invite link в конфигурации или БД
                                    # Пока что пропускаем этот метод, так как нет функции для получения invite link
                                    raise Exception("Invite link не найден")
                                except Exception as join_error:
                                    logger.warning(f"[API] Вступление по invite link не удалось: {join_error}")
                                    
                                    # Метод 3: Поиск канала по части названия или описания
                                    try:
                                        logger.info(f"[API] Пытаемся найти канал через поиск")
                                        # Если userbot подписан на канал, он должен быть в диалогах
                                        async for dialog in userbot.get_dialogs():
                                            if dialog.chat.id == channel_id_typed:
                                                chat = dialog.chat
                                                logger.info(f"[API] ✅ Найден канал в диалогах: {chat.title} (@{chat.username})")
                                                break
                                        else:
                                            raise Exception(f"Канал {channel_id_typed} не найден в диалогах userbot")
                                    except Exception as search_error:
                                        logger.error(f"[API] Поиск канала не удался: {search_error}")
                                        raise Exception(f"Не удалось разрешить peer ID {channel_id_typed}. Убедитесь, что userbot подписан на канал или имеет к нему доступ.")
                    else:
                        # Для username обычная логика
                        chat = await userbot.get_chat(channel_id_typed)
                    
                    # Сохраняем информацию о канале в БД
                    channel_data = {
                        'id': chat.id,
                        'title': chat.title,
                        'username': chat.username or '',
                        'description': getattr(chat, 'description', '') or '',
                        'members_count': getattr(chat, 'members_count', 0) or 0,
                        'type': str(chat.type) if hasattr(chat, 'type') else 'unknown'
                    }
                    
                    await db.save_channel_info(channel_data)
                    logger.info(f"[API] Сохранена информация о канале в БД: {channel_data}")
                    
                    # Объединяем статистику и информацию о канале
                    stats = {**parsed_stats, **channel_data}
                    
                except Exception as e:
                    logger.error(f"[API] Ошибка при получении информации о канале: {e}")
                    # Возвращаем базовую статистику даже при ошибке
                    stats = {
                        **parsed_stats,
                        'id': channel_id_typed,
                        'title': f"Канал {channel_id}",
                        'username': '',
                        'description': '',
                        'members_count': 0,
                        'type': 'unknown',
                        'error': str(e)
                    }
            else:
                # Если есть статистика парсинга, но нет информации о канале
                stats = {
                    **parsed_stats,
                    'id': channel_id_typed,
                    'title': f"Канал {channel_id}",
                    'username': '',
                    'description': '',
                    'members_count': 0,
                    'type': 'unknown'
                }
        
        logger.info(f"[API] === КОНЕЦ ОБРАБОТКИ /channel/stats/{channel_id} ===")
        return stats
        
    except Exception as e:
        logger.error(f"[API] Ошибка при получении статистики канала {channel_id}: {e}")
        return {
            'parsed_count': 0,
            'min_id': None,
            'max_id': None,
            'parsed_media_groups': 0,
            'parsed_singles': 0,
            'last_parsed_id': None,
            'last_parsed_date': None,
            'id': channel_id,
            'title': f"Канал {channel_id}",
            'username': '',
            'description': '',
            'members_count': 0,
            'type': 'unknown',
            'error': str(e)
        }

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
        userbot = await forwarder.get_userbot()
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
            userbot = await forwarder.get_userbot()
            chat = await userbot.get_chat(request.channel_id)
            channel_title = getattr(chat, 'title', None) or request.channel_title
            username = getattr(chat, 'username', None)
        except Exception as e:
            logger.warning(f"[API][add_user_channel] Не удалось получить канал {request.channel_id}: {e}")
            raise HTTPException(status_code=400, detail="Канал не найден или недоступен")
        
        # Определяем, что передал клиент: username или ID
        is_username = not request.channel_id.startswith("-100") and not request.channel_id.isdigit()
        
        if is_username:
            # Клиент передал username, сохраняем username в поле username, а ID в поле channel_id
            await db.add_user_channel(user_id, str(chat.id), channel_title, request.channel_id)
        else:
            # Клиент передал ID, сохраняем ID в поле channel_id, а username в поле username
            await db.add_user_channel(user_id, str(chat.id), channel_title, username)
        
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
            userbot = await forwarder.get_userbot()
            chat = await userbot.get_chat(request.channel_id)
            channel_title = getattr(chat, 'title', None) or request.channel_title
            username = getattr(chat, 'username', None)
        except Exception as e:
            logger.warning(f"[API][add_user_target_channel] Не удалось получить канал {request.channel_id}: {e}")
            raise HTTPException(status_code=400, detail="Канал не найден или недоступен")
        
        # Определяем, что передал клиент: username или ID
        is_username = not request.channel_id.startswith("-100") and not request.channel_id.isdigit()
        
        if is_username:
            # Клиент передал username, сохраняем username в поле username, а ID в поле channel_id
            await db.add_user_target_channel(user_id, str(chat.id), channel_title, request.channel_id)
        else:
            # Клиент передал ID, сохраняем ID в поле channel_id, а username в поле username
            await db.add_user_target_channel(user_id, str(chat.id), channel_title, username)
        
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

# --- Эндпоинты для групп пользователей ---

class GroupRequest(BaseModel):
    group_id: str
    group_title: str
    username: Optional[str] = None

@app.get("/user/groups/{user_id}")
async def get_user_groups(user_id: int):
    """Получить историю групп пользователя"""
    try:
        groups = await db.get_user_groups(user_id)
        return {"status": "success", "groups": groups}
    except Exception as e:
        logger.error(f"Error getting user groups: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/user/groups/{user_id}")
async def add_user_group(user_id: int, request: GroupRequest):
    """Добавить группу в историю пользователя (только если группа существует)"""
    try:
        forwarder = get_or_create_forwarder()
        try:
            userbot = await forwarder.get_userbot()
            chat = await userbot.get_chat(request.group_id)
            group_title = getattr(chat, 'title', None) or request.group_title
            username = getattr(chat, 'username', None)
        except Exception as e:
            logger.warning(f"[API][add_user_group] Не удалось получить группу {request.group_id}: {e}")
            raise HTTPException(status_code=400, detail="Группа не найдена или недоступна")
        
        # Определяем, что передал клиент: username или ID
        is_username = not request.group_id.startswith("-100") and not request.group_id.isdigit()
        
        if is_username:
            # Клиент передал username, сохраняем username в поле username, а ID в поле group_id
            await db.add_user_group(user_id, str(chat.id), group_title, request.group_id)
        else:
            # Клиент передал ID, сохраняем ID в поле group_id, а username в поле username
            await db.add_user_group(user_id, str(chat.id), group_title, username)
        
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding user group: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/user/groups/{user_id}/{group_id}")
async def update_user_group_last_used(user_id: int, group_id: str):
    """Обновить время последнего использования группы"""
    try:
        await db.update_user_group_last_used(user_id, group_id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error updating user group: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/user/groups/{user_id}/{group_id}")
async def remove_user_group(user_id: int, group_id: str):
    """Удалить группу из истории пользователя"""
    try:
        await db.remove_user_group(user_id, group_id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error removing user group: {e}")
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

# --- Session Management API Endpoints ---

class SessionRequest(BaseModel):
    session_name: str
    api_id: Optional[str] = None
    api_hash: Optional[str] = None
    phone: Optional[str] = None

class CodeRequest(BaseModel):
    session_name: str
    phone: str

class SignInRequest(BaseModel):
    session_name: str
    phone: str
    code: str
    phone_code_hash: str

class PasswordRequest(BaseModel):
    session_name: str
    password: str

class AssignSessionRequest(BaseModel):
    task: str
    session_name: str

class RemoveAssignmentRequest(BaseModel):
    task: str
    session_name: Optional[str] = None

class ConfirmCodeRequest(BaseModel):
    session_name: str
    phone: str
    code: str
    phone_code_hash: str

@app.post("/sessions/add")
async def add_session(request: SessionRequest):
    """Add a new session"""
    global session_manager
    import logging
    logger = logging.getLogger("sessions.add")
    logger.info(f"[API] /sessions/add: session_name='{request.session_name}' api_id={request.api_id} api_hash={request.api_hash} phone='{request.phone}'")
    if not session_manager:
        logger.error("Session manager not initialized")
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    api_id = request.api_id or os.getenv("API_ID")
    api_hash = request.api_hash or os.getenv("API_HASH")
    logger.info(f"[API] /sessions/add: Using api_id={api_id} api_hash={api_hash[:10] if api_hash else None}...")
    if not api_id or not api_hash:
        logger.error("API credentials are required")
        raise HTTPException(status_code=400, detail="API credentials are required")
    # Проверка на уникальность alias
    existing = await session_manager.db.get_session_by_alias(request.session_name)
    if existing:
        logger.warning(f"Session with alias '{request.session_name}' already exists")
        return JSONResponse(status_code=400, content={"success": False, "error": "Session with this name already exists"})
    try:
        result = await session_manager.add_account(
            alias=request.session_name,
            api_id=int(api_id),
            api_hash=api_hash,
            phone=request.phone or ""
        )
        logger.info(f"Session add result: {result}")
        if not result.get("success", False):
            logger.error(f"Session add error: {result.get('error', 'Unknown error')}")
            return JSONResponse(status_code=500, content={"success": False, "error": result.get("error", "Unknown error")})
        return result
    except Exception as e:
        logger.error(f"Exception in /sessions/add: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.post("/sessions/send_code")
async def send_code(request: CodeRequest):
    """Send authentication code"""
    global session_manager
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    result = await session_manager.send_code(
        alias=request.session_name,
        phone=request.phone
    )
    if not result.get("success", False):
        raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    return result

@app.post("/sessions/sign_in")
async def sign_in(request: SignInRequest):
    """Sign in with code"""
    global session_manager
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    result = await session_manager.sign_in(
        alias=request.session_name,
        phone=request.phone,
        code=request.code,
        phone_code_hash=request.phone_code_hash
    )
    if not result.get("success", False):
        if result.get("needs_password", False):
            return JSONResponse(status_code=202, content={"needs_password": True})
        raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    return result

@app.post("/sessions/sign_in_with_password")
async def sign_in_with_password(request: PasswordRequest):
    """Sign in with 2FA password"""
    global session_manager
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    result = await session_manager.sign_in_with_password(
        alias=request.session_name,
        password=request.password
    )
    if not result.get("success", False):
        raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    return result

@app.post("/sessions/confirm_code")
async def confirm_code(request: ConfirmCodeRequest):
    global session_manager
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    
    # Логируем входящие данные
    logger.info(f"[API_ENDPOINT] confirm_code received: session_name='{request.session_name}', phone='{request.phone}', code='{request.code}', phone_code_hash='{request.phone_code_hash}'")
    logger.info(f"[API_ENDPOINT] Code type: {type(request.code)}, length: {len(request.code) if request.code else 0}")
    
    result = await session_manager.confirm_code(
        alias=request.session_name,
        phone=request.phone,
        code=request.code,
        phone_code_hash=request.phone_code_hash
    )
    if not result.get("success", False):
        raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
    return result

@app.get("/sessions/list")
async def list_sessions():
    """Get list of all sessions"""
    global session_manager
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    sessions = await session_manager.get_all_sessions()
    assignments = await session_manager.get_assignments()
    return {
        "success": True,
        "sessions": [s.dict() for s in sessions],
        "assignments": assignments
    }

@app.post("/sessions/assign")
async def assign_session(request: AssignSessionRequest):
    """Assign a session to a task"""
    global session_manager
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    result = await session_manager.assign_task(
        alias=request.session_name,
        task=request.task
    )
    if not result.get("success", False):
        raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    return result

@app.post("/sessions/remove_assignment")
async def remove_assignment(request: RemoveAssignmentRequest):
    """Remove a session assignment"""
    global session_manager
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    result = await session_manager.remove_assignment(
        alias=request.session_name,
        task=request.task
    )
    if not result.get("success", False):
        raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    return result

@app.get("/sessions/status/{session_name}")
async def get_session_status(session_name: str):
    """Check session status"""
    global session_manager
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    result = await session_manager.check_session_status(session_name)
    if not result.get("success", False):
        raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    return result

@app.delete("/sessions/{session_name}")
async def delete_session(session_name: str):
    """Delete a session"""
    global session_manager
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    result = await session_manager.delete_session(session_name)
    if not result.get("success", False):
        raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    return result

@app.post("/sessions/init")
async def init_session(request: dict, background_tasks: BackgroundTasks):
    """Инициализация новой сессии с заданным именем: интерактивный ввод api_id, api_hash, телефона и кода в терминале."""
    session_name = request.get("session_name")
    if not session_name:
        raise HTTPException(status_code=400, detail="session_name обязателен")
    def run_pyrogram_interactive():
        print(f"[SESSIONS/INIT] Запуск интерактивной авторизации для сессии: {session_name}")
        app = Client(session_name)
        app.start()
        print(f"[SESSIONS/INIT] Сессия {session_name} успешно создана!")
        app.stop()
    background_tasks.add_task(run_pyrogram_interactive)
    logger.info(f"[API] /sessions/init: инициирована интерактивная авторизация для {session_name}")
    return {"success": True, "message": f"Интерактивная авторизация для {session_name} запущена в терминале парсера."}

# --- Reaction API Endpoints ---

class ReactionRequest(BaseModel):
    chat_id: str
    message_id: int
    reaction: str
    session_names: Optional[List[str]] = None

class MultipleReactionsRequest(BaseModel):
    chat_id: str
    message_ids: List[int]
    reaction: str
    session_names: Optional[List[str]] = None

@app.post("/reactions/add")
async def add_reaction(request: ReactionRequest):
    """Add reaction to a message"""
    global reaction_manager
    
    if not reaction_manager:
        raise HTTPException(status_code=500, detail="Reaction manager not initialized")
    
    result = await reaction_manager.add_reaction(
        chat_id=request.chat_id,
        message_id=request.message_id,
        reaction=request.reaction,
        session_names=request.session_names
    )
    
    if not result.get("success", False):
        raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    
    return result

@app.post("/reactions/add_multiple")
async def add_multiple_reactions(request: MultipleReactionsRequest):
    """Add reactions to multiple messages"""
    global reaction_manager
    
    if not reaction_manager:
        raise HTTPException(status_code=500, detail="Reaction manager not initialized")
    
    result = await reaction_manager.add_reaction_to_multiple_messages(
        chat_id=request.chat_id,
        message_ids=request.message_ids,
        reaction=request.reaction,
        session_names=request.session_names
    )
    
    if not result.get("success", False):
        raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    
    return result

@app.post("/reactions/add_to_posts")
async def add_reactions_to_posts(request: Request):
    """Add reactions to posts (media groups) instead of individual messages"""
    global reaction_manager, session_manager
    
    if not reaction_manager:
        raise HTTPException(status_code=500, detail="Reaction manager not initialized")
    
    try:
        data = await request.json()
        chat_id = data.get("chat_id")
        reaction = data.get("reaction")
        session_names = data.get("session_names")
        limit = data.get("limit", 50)  # Количество постов для обработки
        
        if not chat_id or not reaction:
            raise HTTPException(status_code=400, detail="chat_id и reaction обязательны")
        
        # Получаем сообщения из канала
        sessions = await session_manager.get_sessions_for_task("reactions")
        if not sessions:
            raise HTTPException(status_code=400, detail="Нет сессий для реакций")
        
        # Используем первую сессию для получения сообщений
        userbot = await session_manager.get_client(sessions[0].alias)
        if not userbot.is_connected:
            await userbot.start()
        
        messages = []
        async for msg in userbot.get_chat_history(chat_id, limit=limit * 10):  # Берем больше сообщений для группировки
            messages.append(msg)
        
        # Добавляем реакции к постам
        result = await reaction_manager.add_reaction_to_posts(
            chat_id=chat_id,
            messages=messages,
            reaction=reaction,
            session_names=session_names
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Error adding reactions to posts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/reactions/available")
async def get_available_reactions():
    """Get list of available reactions"""
    global reaction_manager
    
    if not reaction_manager:
        raise HTTPException(status_code=500, detail="Reaction manager not initialized")
    
    reactions = await reaction_manager.get_available_reactions()
    
    return {
        "success": True,
        "reactions": reactions
    }

@app.post("/reactions/mass_add")
async def mass_add_reactions(request: Request, background_tasks: BackgroundTasks):
    import logging
    global session_manager, reaction_manager, reaction_tasks
    sessions = await session_manager.get_sessions_for_task("reactions")
    if not sessions:
        logging.error("[MASS_REACTIONS] Нет ни одной сессии, назначенной на reactions")
        return JSONResponse({"success": False, "error": "Нет ни одной сессии, назначенной на reactions"})
    
    data = await request.json()
    chat_id = data.get("chat_id")
    emojis = data.get("emojis", [])
    mode = data.get("mode")
    count = data.get("count")
    date = data.get("date")
    date_from = data.get("date_from")
    date_to = data.get("date_to")
    hashtag = data.get("hashtag")
    delay = data.get("delay", 1)
    
    # Дополнительное логирование для отладки
    logging.info(f"[MASS_REACTIONS] === НАЧАЛО ОБРАБОТКИ ===")
    logging.info(f"[MASS_REACTIONS] Получены данные: chat_id={chat_id}, emojis={emojis}, mode={mode}, count={count}, delay={delay}")
    logging.info(f"[MASS_REACTIONS] Тип emojis: {type(emojis)}, длина: {len(emojis) if emojis else 0}")
    
    if not emojis:
        return JSONResponse({"success": False, "error": "Не указаны эмодзи для реакций"})
    
    # Создаем task_id
    task_id = f"mass_reactions_{chat_id}_{int(time.time())}"
    
    # Сохраняем информацию о задаче
    reaction_tasks[task_id] = {
        "task_id": task_id,
        "chat_id": chat_id,
        "emojis": emojis,
        "mode": mode,
        "count": count,
        "date": date,
        "date_from": date_from,
        "date_to": date_to,
        "hashtag": hashtag,
        "delay": delay,
        "status": "running",
        "started_at": datetime.now().isoformat(),
        "completed_at": None,
        "error": None,
        "results": []
    }
    
    # Запускаем массовые реакции в фоне
    background_tasks.add_task(execute_mass_reactions, task_id, chat_id, emojis, mode, count, date, date_from, date_to, hashtag, delay)
    
    return JSONResponse({
        "success": True,
        "message": "Массовые реакции запущены в фоновом режиме",
        "task_id": task_id
    })


async def execute_mass_reactions(task_id, chat_id, emojis, mode, count, date, date_from, date_to, hashtag, delay):
    """Выполнение массовых реакций в фоновом режиме"""
    import logging
    import random
    global session_manager, reaction_manager, reaction_tasks
    
    sessions = await session_manager.get_sessions_for_task("reactions")
    if not sessions:
        logging.error("[MASS_REACTIONS] Нет ни одной сессии, назначенной на reactions")
        # Обновляем статус задачи при ошибке
        if task_id in reaction_tasks:
            reaction_tasks[task_id]["status"] = "error"
            reaction_tasks[task_id]["error"] = "Нет ни одной сессии, назначенной на reactions"
            reaction_tasks[task_id]["completed_at"] = datetime.now().isoformat()
        return
    
    all_results = []
    
    for session in sessions:
        alias = session.alias
        userbot = await session_manager.get_client(alias)
        logging.info(f"[MASS_REACTIONS] Используется userbot: alias={alias}, name={getattr(userbot, 'name', None)}, phone={getattr(userbot, 'phone_number', None)}")
        
        errors = []
        total_posts = 0
        reacted_posts = 0
        
        try:
            if not userbot.is_connected:
                await userbot.start()
            
            # Проверяем premium
            is_premium = False
            try:
                me = await userbot.get_me()
                logging.info(f"[MASS_REACTIONS] Аккаунт: {me.first_name} (@{me.username})")
            except Exception as e:
                logging.warning(f"[MASS_REACTIONS] Не удалось определить premium для {alias}: {e}")
            
            # Получаем сообщения
            messages = []
            logging.info(f"[MASS_REACTIONS] chat_id={chat_id}, mode={mode}, count={count}, emojis={emojis}, delay={delay}, is_premium={is_premium}")
            
            # Берем больше сообщений для поиска постов
            limit_for_search = max(count * 3, 100) if count else 2000  # Берем минимум в 3 раза больше или 100 сообщений
            
            if mode == "from_last":
                async for msg in userbot.get_chat_history(chat_id, limit=limit_for_search):
                    messages.append(msg)
            elif mode == "last_n":
                async for msg in userbot.get_chat_history(chat_id, limit=limit_for_search):
                    messages.append(msg)
            else:
                async for msg in userbot.get_chat_history(chat_id, limit=limit_for_search):
                    messages.append(msg)
            
            logging.info(f"[MASS_REACTIONS] Найдено сообщений: {len(messages)}")
            if not messages:
                logging.warning(f"[MASS_REACTIONS] В канале {chat_id} нет сообщений!")
                all_results.append({
                    "alias": alias,
                    "phone": getattr(userbot, 'phone_number', None),
                    "total_posts": 0,
                    "reacted_posts": 0,
                    "errors": ["Нет сообщений в канале"],
                })
                continue
            
            # Подсчитываем количество постов заранее для информации
            media_groups_found = set()
            individual_posts_found = 0
            
            for msg in messages:
                if hasattr(msg, 'media_group_id') and msg.media_group_id is not None:
                    media_groups_found.add(msg.media_group_id)
                else:
                    individual_posts_found += 1
            
            total_posts_available = len(media_groups_found) + individual_posts_found
            logging.info(f"[MASS_REACTIONS] Найдено постов: {total_posts_available} (медиагрупп: {len(media_groups_found)}, отдельных: {individual_posts_found})")
            logging.info(f"[MASS_REACTIONS] Цель: обработать {count or 'все'} постов")
            
            # НЕ ограничиваем количество сообщений заранее - будем искать посты среди всех сообщений
            logging.info(f"[MASS_REACTIONS] Будем искать посты среди {len(messages)} сообщений")
            
            # Простая логика: идем по сообщениям и обрабатываем каждый пост
            processed_media_groups = set()  # Уже обработанные медиагруппы
            reacted_posts = 0
            
            logging.info(f"[MASS_REACTIONS] Начинаем обработку {len(messages)} сообщений")
            
            for i, message in enumerate(messages):
                if count and reacted_posts >= count:
                    logging.info(f"[MASS_REACTIONS] Достигнут лимит {count} постов - останавливаемся")
                    break
                
                # Логируем прогресс каждые 10 сообщений
                if i % 10 == 0 and i > 0:
                    logging.info(f"[MASS_REACTIONS] Просмотрено {i}/{len(messages)} сообщений, найдено {reacted_posts} постов")
                    
                try:
                    # Проверяем, является ли сообщение частью медиагруппы
                    if hasattr(message, 'media_group_id') and message.media_group_id is not None:
                        # Это медиагруппа
                        if message.media_group_id in processed_media_groups:
                            logging.info(f"[MASS_REACTIONS] Пропускаем сообщение {message.id} - медиагруппа {message.media_group_id} уже обработана")
                            continue
                        
                        # Обрабатываем медиагруппу
                        logging.info(f"[MASS_REACTIONS] Обрабатываем пост {reacted_posts + 1}/{count or '∞'}: медиагруппа {message.media_group_id} (сообщение {message.id})")
                        emoji = random.choice(emojis)
                        result = await userbot.send_reaction(chat_id, message.id, emoji)
                        logging.info(f"[MASS_REACTIONS] Поставлена реакция {emoji} на медиагруппу {message.media_group_id} (сообщение {message.id})")
                        processed_media_groups.add(message.media_group_id)
                        reacted_posts += 1
                        
                    else:
                        # Это отдельное сообщение
                        logging.info(f"[MASS_REACTIONS] Обрабатываем пост {reacted_posts + 1}/{count or '∞'}: отдельное сообщение {message.id}")
                        emoji = random.choice(emojis)
                        result = await userbot.send_reaction(chat_id, message.id, emoji)
                        logging.info(f"[MASS_REACTIONS] Поставлена реакция {emoji} на сообщение {message.id}")
                        reacted_posts += 1
                    
                    logging.info(f"[MASS_REACTIONS] Прогресс: {reacted_posts} постов обработано")
                    
                    # Задержка между постами
                    if delay > 0 and i < len(messages) - 1:
                        logging.info(f"[MASS_REACTIONS] Задержка {delay} сек перед следующим постом")
                        await asyncio.sleep(delay)
                        
                except Exception as e:
                    logging.error(f"[MASS_REACTIONS] Ошибка при обработке сообщения {message.id}: {e}")
                    errors.append(str(e))
            
            total_posts = reacted_posts
            
            if count and reacted_posts < count:
                logging.warning(f"[MASS_REACTIONS] Найдено только {reacted_posts} постов из запрошенных {count}")
                logging.info(f"[MASS_REACTIONS] Просмотрено {len(messages)} сообщений, но постов недостаточно")
            else:
                logging.info(f"[MASS_REACTIONS] Успешно обработано {reacted_posts} постов")
            
            logging.info(f"[MASS_REACTIONS] Завершена обработка сессии {alias}: {reacted_posts}/{total_posts} постов обработано")
            
        except Exception as e:
            logging.error(f"[MASS_REACTIONS] Глобальная ошибка для сессии {alias}: {e}")
            errors.append(str(e))
        finally:
            if userbot.is_connected:
                await userbot.stop()
                logging.info(f"[MASS_REACTIONS] Сессия {alias} остановлена после массовой реакции")
        
        all_results.append({
            "alias": alias,
            "phone": getattr(userbot, 'phone_number', None),
            "total_posts": total_posts,
            "reacted_posts": reacted_posts,
            "errors": errors,
        })
    
    logging.info(f"[MASS_REACTIONS] === ЗАВЕРШЕНО === Результаты: {all_results}")
    
    # Обновляем статус задачи
    if task_id in reaction_tasks:
        # Проверяем, есть ли ошибки во всех сессиях
        all_errors = []
        for result in all_results:
            if result.get("errors"):
                all_errors.extend(result["errors"])
        
        if all_errors and len(all_errors) == len(all_results):
            # Все сессии завершились с ошибками
            reaction_tasks[task_id]["status"] = "error"
            reaction_tasks[task_id]["error"] = "; ".join(all_errors[:3])  # Берем первые 3 ошибки
        else:
            reaction_tasks[task_id]["status"] = "completed"
        
        reaction_tasks[task_id]["completed_at"] = datetime.now().isoformat()
        reaction_tasks[task_id]["results"] = all_results
        logging.info(f"[MASS_REACTIONS] Статус задачи {task_id} обновлен на '{reaction_tasks[task_id]['status']}'")

# API эндпоинты для работы с задачами реакций
@app.get("/reactions/task_status/{task_id}")
async def get_reaction_task_status(task_id: str):
    """Получить статус задачи реакций"""
    global reaction_tasks
    if task_id not in reaction_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task_info = reaction_tasks[task_id]
    return {
        "success": True,
        "task": task_info
    }

@app.post("/reactions/stop_task/{task_id}")
async def stop_reaction_task(task_id: str):
    """Остановить задачу реакций"""
    global reaction_tasks
    if task_id not in reaction_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task_info = reaction_tasks[task_id]
    if task_info["status"] == "running":
        task_info["status"] = "stopped"
        task_info["completed_at"] = datetime.now().isoformat()
        return {
            "success": True,
            "message": "Task stopped successfully",
            "task_id": task_id
        }
    else:
        return {
            "success": False,
            "message": "Task is not running",
            "task_id": task_id
        }

@app.get("/reactions/all_tasks")
async def get_all_reaction_tasks():
    """Получить список всех задач реакций"""
    global reaction_tasks
    return {
        "success": True,
        "tasks": list(reaction_tasks.values())
    }

# --- API для публичных групп ---

class PublicGroupsRequest(BaseModel):
    source_channel: str
    target_group: str
    user_id: int
    settings: dict

@app.post("/public_groups/start")
async def start_public_groups_forwarding(request: PublicGroupsRequest):
    """Запуск пересылки в публичные группы"""
    try:
        from parser.public_groups_forwarder import PublicGroupsForwarder
        public_forwarder = PublicGroupsForwarder(db, session_manager)
        
        result = await public_forwarder.start_forwarding(
            request.source_channel,
            request.target_group,
            request.user_id,
            request.settings
        )
        
        return result
    except Exception as e:
        logger.error(f"Error starting public groups forwarding: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/public_groups/stop")
async def stop_public_groups_forwarding(request: dict):
    """Остановка пересылки в публичные группы"""
    try:
        task_id = request.get("task_id")
        if not task_id:
            raise HTTPException(status_code=400, detail="task_id is required")
        
        from parser.public_groups_forwarder import PublicGroupsForwarder
        public_forwarder = PublicGroupsForwarder(db, session_manager)
        
        result = await public_forwarder.stop_forwarding(task_id)
        return result
    except Exception as e:
        logger.error(f"Error stopping public groups forwarding: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/public_groups/status/{task_id}")
async def get_public_groups_status(task_id: str):
    """Получить статус задачи пересылки в публичные группы"""
    try:
        from parser.public_groups_forwarder import PublicGroupsForwarder
        public_forwarder = PublicGroupsForwarder(db, session_manager)
        
        result = await public_forwarder.get_status(task_id)
        return result
    except Exception as e:
        logger.error(f"Error getting public groups status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/public_groups/all_tasks")
async def get_all_public_groups_tasks():
    """Получить все задачи пересылки в публичные группы"""
    try:
        from parser.public_groups_forwarder import PublicGroupsForwarder
        public_forwarder = PublicGroupsForwarder(db, session_manager)
        
        result = await public_forwarder.get_all_tasks()
        return result
    except Exception as e:
        logger.error(f"Error getting public groups tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === TEXT EDITOR ENDPOINTS ===

@app.post("/text-editor/start")
async def start_text_editing(request: TextEditRequest):
    """Запуск редактирования текста постов в канале"""
    try:
        logger.info(f"[API] Запуск редактирования текста для канала {request.channel_id}")
        logger.info(f"[API] Параметры: текст='{request.link_text}', ссылка='{request.link_url}', лимит={request.max_posts}")
        
        # Валидация входных данных
        if not request.link_text or not request.link_url:
            error_msg = "Текст и ссылка обязательны для редактирования"
            logger.error(f"[API] {error_msg}")
            raise HTTPException(status_code=400, detail=error_msg)
        
        if request.max_posts <= 0:
            error_msg = "Количество постов должно быть больше 0"
            logger.error(f"[API] {error_msg}")
            raise HTTPException(status_code=400, detail=error_msg)
        
        logger.info(f"[API] Получаем text_editor...")
        text_editor = get_or_create_text_editor()
        
        logger.info(f"[API] Запускаем задачу редактирования...")
        task_id = await text_editor.start_text_editing(
            channel_id=request.channel_id,
            link_text=request.link_text,
            link_url=request.link_url,
            max_posts=request.max_posts
        )
        
        logger.info(f"[API] Редактирование запущено с ID: {task_id}")
        return {
            "status": "success",
            "task_id": task_id,
            "message": "Редактирование текста запущено"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Ошибка запуска редактирования: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/text-editor/status/{task_id}")
async def get_text_editing_status(task_id: str):
    """Получение статуса задачи редактирования"""
    try:
        text_editor = get_or_create_text_editor()
        task_info = text_editor.get_task_status(task_id)
        
        if task_info is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
            
        return {
            "status": "success",
            "task": task_info
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Ошибка получения статуса: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/text-editor/tasks")
async def get_all_text_editing_tasks():
    """Получение всех задач редактирования"""
    try:
        text_editor = get_or_create_text_editor()
        tasks = text_editor.get_all_tasks()
        
        return {
            "status": "success",
            "tasks": tasks
        }
        
    except Exception as e:
        logger.error(f"[API] Ошибка получения задач: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/text-editor/stop/{task_id}")
async def stop_text_editing(task_id: str):
    """Остановка задачи редактирования"""
    try:
        text_editor = get_or_create_text_editor()
        success = text_editor.stop_task(task_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Задача не найдена или уже завершена")
            
        return {
            "status": "success",
            "message": "Задача остановлена"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Ошибка остановки задачи: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/text-editor/assign-session")
async def assign_text_editing_session(request: dict):
    """Назначить сессию для редактирования текста"""
    try:
        session_alias = request.get("session_alias")
        if not session_alias:
            raise HTTPException(status_code=400, detail="session_alias обязателен")
        
        global session_manager
        if not session_manager:
            raise HTTPException(status_code=500, detail="Session manager не инициализирован")
        
        result = await session_manager.assign_task(session_alias, "text_editing")
        
        if result.get("success"):
            logger.info(f"[API] Сессия {session_alias} назначена для text_editing")
            return {
                "status": "success",
                "message": f"Сессия {session_alias} назначена для редактирования текста"
            }
        else:
            error_msg = result.get("error", "Неизвестная ошибка")
            logger.error(f"[API] Ошибка назначения сессии: {error_msg}")
            raise HTTPException(status_code=400, detail=error_msg)
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Ошибка назначения сессии для text_editing: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/text-editor/sessions")
async def get_text_editing_sessions():
    """Получить список сессий для редактирования текста"""
    try:
        global session_manager
        if not session_manager:
            raise HTTPException(status_code=500, detail="Session manager не инициализирован")
        
        # Получаем сессии для text_editing
        assigned_sessions = await session_manager.get_sessions_for_task("text_editing")
        
        # Получаем все активные сессии
        all_sessions = await session_manager.get_all_sessions()
        active_sessions = [s for s in all_sessions if s.is_active]
        
        return {
            "status": "success",
            "assigned_sessions": [
                {
                    "alias": s.alias if hasattr(s, 'alias') else s.session_path,
                    "phone": s.phone if hasattr(s, 'phone') else None
                } for s in assigned_sessions
            ],
            "available_sessions": [
                {
                    "alias": s.alias if hasattr(s, 'alias') else s.session_path,
                    "phone": s.phone if hasattr(s, 'phone') else None
                } for s in active_sessions
            ]
        }
        
    except Exception as e:
        logger.error(f"[API] Ошибка получения сессий для text_editing: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/bulk-update-links")
async def bulk_update_links(
    channel_id: str = Form(..., description="ID канала для обновления"),
    dry_run: bool = Form(True, description="Только предпросмотр, без изменений"),
    limit: int = Form(1000, description="Максимальное количество сообщений")
):
    """
    Массовое обновление ссылок в постах канала
    Заменяет ссылку "Приватный канал / Подписаться" на новую
    """
    try:
        from bulk_link_updater import BulkLinkUpdater
        
        logger.info(f"[API] 🚀 Запрос на массовое обновление ссылок в канале {channel_id}")
        logger.info(f"[API] Параметры: dry_run={dry_run}, limit={limit}")
        
        # Создаем обновлятор
        updater = BulkLinkUpdater()
        
        # Запускаем клиент
        if not await updater.start_client():
            return {"success": False, "error": "Не удалось запустить клиент"}
        
        try:
            # Обновляем сообщения
            stats = await updater.update_channel_messages(channel_id, dry_run=dry_run)
            
            result = {
                "success": True,
                "dry_run": dry_run,
                "stats": stats,
                "message": "Предпросмотр завершен" if dry_run else "Обновление завершено"
            }
            
            logger.info(f"[API] ✅ Массовое обновление ссылок завершено: {stats}")
            return result
            
        finally:
            await updater.stop_client()
            
    except Exception as e:
        logger.error(f"[API] ❌ Ошибка массового обновления ссылок: {e}")
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)