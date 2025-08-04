import re
from fastapi import APIRouter, HTTPException
from .config import config
import os
import logging
from pyrogram.errors import PeerIdInvalid
from pyrogram import Client

NAVIGATION_TEMPLATE = (
    "Для вашего удобства сделали очень удобный навигационный лист по хештегам\n"
    "Поиск стал намного удобнее:\n\n"
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Создаём отдельный userbot для navigation_api
SESSION_PATH = os.path.join(os.path.dirname(__file__), "sessions", "userbot")
userbot = Client(
    "userbot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    workdir=SESSION_PATH
)

@router.get("/channel/hashtags/{channel_id}")
async def get_channel_hashtags(channel_id: str) -> dict:
    try:
        # Пробуем сначала int, если не получилось — используем строку (username)
        tried_username = False
        chat = None
        id_type = 'id'
        channel_id_int = None
        # 1. Пробуем получить по id
        try:
            channel_id_int = int(channel_id)
            chat = await userbot.get_chat(channel_id_int)
            used_type = 'id'
        except (ValueError, TypeError):
            # channel_id не int, значит username
            id_type = 'username'
        except Exception as e:
            logger.warning(f"[HASHTAGS] get_chat by id failed: {e}")
            # Если не получилось по id, пробуем получить username через get_chat(id)
            try:
                chat_by_id = await userbot.get_chat(int(channel_id))
                username = getattr(chat_by_id, 'username', None)
                if username:
                    chat = await userbot.get_chat(username)
                    used_type = 'username'
                    tried_username = True
                    logger.info(f"[HASHTAGS] get_chat by username succeeded: {username}")
                else:
                    logger.error(f"[HASHTAGS] get_chat by id failed, username not found")
                    raise HTTPException(status_code=400, detail=f"Peer id invalid: {channel_id}, username not found")
            except Exception as e2:
                logger.error(f"[HASHTAGS] get_chat by id failed, cannot get username: {e2}")
                raise HTTPException(status_code=400, detail=f"Peer id invalid: {channel_id}")
        # 2. Если chat всё ещё None, пробуем по username
        if chat is None:
            chat = await userbot.get_chat(channel_id)
            used_type = 'username'
        is_member = not getattr(chat, "left", False)
        is_public = bool(getattr(chat, "username", None))
        logger.info(f"[HASHTAGS] channel_id={channel_id} | used_as={getattr(chat, 'username', channel_id)} ({'username' if tried_username else id_type}) | title={getattr(chat, 'title', None)} | username={getattr(chat, 'username', None)} | is_member={is_member} | is_public={is_public} | type={getattr(chat, 'type', None)}")
        # Проверяем, что это канал
        if not (hasattr(chat, "type") and (chat.type == ChatType.CHANNEL or getattr(chat, "is_channel", False))):
            logger.warning(f"[HASHTAGS] Not a channel: channel_id={channel_id}")
            raise HTTPException(status_code=400, detail="Это не канал. Укажите @username или ID канала.")
        if getattr(chat, "left", False):
            logger.warning(f"[HASHTAGS] Userbot not a member: channel_id={channel_id}")
            raise HTTPException(status_code=400, detail="Userbot не подписан на канал. Добавьте userbot в канал и попробуйте снова.")
        hashtags = []
        used = set()
        try:
            async for msg in userbot.get_chat_history(chat.id, limit=1000):
                text = msg.text or msg.caption or ""
                tags = re.findall(r"#\w+", text)
                if tags:
                    tag = tags[0].lower()
                    if tag not in used:
                        hashtags.append(tags[0])
                        used.add(tag)
        except Exception as e:
            logger.error(f"[HASHTAGS] Error in get_chat_history: {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка получения истории сообщений: {e}")
        logger.info(f"[HASHTAGS] channel_id={channel_id} | hashtags_count={len(hashtags)} | used_type={'username' if tried_username else id_type}")
        return {"hashtags": hashtags, "is_member": is_member, "is_public": is_public, "used_type": ('username' if tried_username else id_type)}
    except PeerIdInvalid:
        logger.error(f"[navigation_api] PeerIdInvalid: userbot не подписан на канал или канал не существует: {channel_id}")
        raise HTTPException(status_code=400, detail="Userbot не подписан на канал или канал не существует. Для публичных каналов используйте @username, для приватных — подпишите userbot на канал.")
    except ValueError as e:
        logger.error(f"[navigation_api] ValueError: {e}")
        raise HTTPException(status_code=400, detail=f"Ошибка: {e}")
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"[navigation_api] Ошибка получения хэштегов: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка userbot: {e}")

@router.post("/channel/navigation_message/{channel_id}")
async def create_or_update_navigation_message(channel_id: str):
    try:
        chat = await userbot.get_chat(channel_id)
        if not (hasattr(chat, "type") and (chat.type == ChatType.CHANNEL or getattr(chat, "is_channel", False))):
            raise HTTPException(status_code=400, detail="Это не канал. Укажите @username или ID канала.")
        if getattr(chat, "left", False):
            raise HTTPException(status_code=400, detail="Userbot не подписан на канал. Добавьте userbot в канал и попробуйте снова.")
        hashtags = []
        used = set()
        async for msg in userbot.get_chat_history(chat.id, limit=1000):
            text = msg.text or msg.caption or ""
            tags = re.findall(r"#\w+", text)
            if tags:
                tag = tags[0].lower()
                if tag not in used:
                    hashtags.append(tags[0])
                    used.add(tag)
        text_nav = NAVIGATION_TEMPLATE + ("\n".join(hashtags) if hashtags else "ℹ️ Нет хэштегов для отображения.")
        sent = await userbot.send_message(chat.id, text_nav)
        return {"message_id": sent.id, "hashtags": hashtags}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"[navigation_api] Ошибка публикации навигации: {e}")
        if "PEER_ID_INVALID" in str(e):
            raise HTTPException(status_code=400, detail="Userbot не подписан на канал или канал не существует. Для публичных каналов используйте @username, для приватных — подпишите userbot на канал.")
        raise HTTPException(status_code=500, detail=f"Ошибка userbot: {e}") 