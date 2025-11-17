"""
Обработчики callback для водяных знаков
Этот файл нужно интегрировать в bot/handlers.py
"""
import logging
import os
from bot.states import (
    user_states,
    FSM_WATERMARK_CHANCE, FSM_WATERMARK_HASHTAG, FSM_WATERMARK_OPACITY,
    FSM_WATERMARK_SCALE, FSM_WATERMARK_TEXT_INPUT, FSM_WATERMARK_IMAGE_UPLOAD,
    get_watermark_menu_keyboard, get_watermark_type_keyboard,
    get_watermark_mode_keyboard, get_watermark_position_keyboard
)
from bot.watermark_manager import watermark_manager
from typing import Optional

logger = logging.getLogger(__name__)


def get_current_watermark_channel(user_id: int) -> Optional[str]:
    """Получить текущий канал для настроек watermark из user_state."""
    user_state = user_states.get(user_id, {})
    
    # 1. Приоритет: Явно установленный ID канала для настроек watermark
    channel_id = user_state.get('current_watermark_channel_id')
    if channel_id:
        return str(channel_id)
        
    # 2. Фоллбэк: Первый из выбранных целевых каналов
    target_channels = user_state.get('forward_target_channels')
    if target_channels and isinstance(target_channels, list) and len(target_channels) > 0:
        return str(target_channels[0]['id'])
        
    # 3. Если ничего не найдено
    return None


# ==================== ОБРАБОТЧИКИ WATERMARK CALLBACK ====================

async def handle_watermark_settings(client, callback_query):
    """Обработчик кнопки настроек watermark. Гарантирует, что channel_id установлен."""
    user_id = callback_query.from_user.id
    
    # Определяем ID канала, для которого открываются настройки
    channel_id = get_current_watermark_channel(user_id)
    
    if not channel_id:
        logger.error(f"[WATERMARK] Не удалось определить ID канала для пользователя {user_id}")
        await callback_query.answer("❌ Не выбран целевой канал для настройки водяного знака!", show_alert=True)
        return

    # Сохраняем ID канала в состояние для последующих обработчиков
    user_states.setdefault(user_id, {})['current_watermark_channel_id'] = channel_id

    logger.info(f"[WATERMARK] Пользователь {user_id} открыл настройки watermark для канала {channel_id}")

    try:
        text = await watermark_manager.format_watermark_settings(user_id, channel_id)
        keyboard = get_watermark_menu_keyboard()

        await client.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.id,
            text=text,
            reply_markup=keyboard
        )
        await callback_query.answer()

    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка показа настроек: {e}")
        await callback_query.answer("❌ Ошибка загрузки настроек", show_alert=True)


async def handle_wm_toggle(client, callback_query):
    """Переключение вкл/выкл watermark"""
    user_id = callback_query.from_user.id
    channel_id = get_current_watermark_channel(user_id)

    try:
        result = await watermark_manager.toggle_watermark(user_id, channel_id)
        await callback_query.answer(result)

        text = await watermark_manager.format_watermark_settings(user_id, channel_id)
        keyboard = get_watermark_menu_keyboard()
        await client.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.id,
            text=text,
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка переключения: {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


async def handle_wm_type(client, callback_query):
    """Обработчик выбора типа watermark"""
    keyboard = get_watermark_type_keyboard()
    
    try:
        await client.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.id,
            text="Выберите тип водяного знака:",
            reply_markup=keyboard
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка выбора типа: {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


async def handle_wm_type_text(client, callback_query):
    """Обработчик выбора текстового watermark"""
    user_id = callback_query.from_user.id
    channel_id = get_current_watermark_channel(user_id)
    
    try:
        text = await watermark_manager.set_watermark_type_text(user_id, channel_id)
        await callback_query.message.reply(text)
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка установки текстового типа: {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


async def handle_wm_type_image(client, callback_query):
    """Обработчик выбора изображения watermark"""
    user_id = callback_query.from_user.id
    channel_id = get_current_watermark_channel(user_id)
    
    try:
        text = await watermark_manager.set_watermark_type_image(user_id, channel_id)
        await callback_query.message.reply(text)
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка установки типа изображения: {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


async def handle_wm_mode(client, callback_query):
    """Обработчик выбора режима watermark"""
    keyboard = get_watermark_mode_keyboard()
    
    try:
        await client.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.id,
            text="Выберите режим применения водяного знака:",
            reply_markup=keyboard
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка выбора режима: {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


async def handle_wm_mode_all(client, callback_query):
    """Установить режим 'все посты'"""
    user_id = callback_query.from_user.id
    channel_id = get_current_watermark_channel(user_id)
    
    try:
        result = await watermark_manager.set_watermark_mode(user_id, 'all', channel_id=channel_id)
        await callback_query.answer(result)
        
        text = await watermark_manager.format_watermark_settings(user_id, channel_id)
        keyboard = get_watermark_menu_keyboard()
        await client.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.id,
            text=text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка установки режима 'все': {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


async def handle_wm_mode_random(client, callback_query):
    """Установить режим 'случайно'"""
    user_id = callback_query.from_user.id
    user_states[user_id]['state'] = FSM_WATERMARK_CHANCE
    
    try:
        await callback_query.message.reply("Введите вероятность применения watermark (0-100%):")
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка установки режима 'случайно': {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


async def handle_wm_mode_hashtag(client, callback_query):
    """Установить режим 'по хэштегу'"""
    user_id = callback_query.from_user.id
    user_states[user_id]['state'] = FSM_WATERMARK_HASHTAG
    
    try:
        await callback_query.message.reply("Введите хэштег для применения watermark (например, #watermark):")
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка установки режима 'по хэштегу': {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


async def handle_wm_mode_manual(client, callback_query):
    """Установить режим 'вручную'"""
    user_id = callback_query.from_user.id
    channel_id = get_current_watermark_channel(user_id)
    
    try:
        result = await watermark_manager.set_watermark_mode(user_id, 'manual', channel_id=channel_id)
        await callback_query.answer(result + " (В разработке)")
        
        text = await watermark_manager.format_watermark_settings(user_id, channel_id)
        keyboard = get_watermark_menu_keyboard()
        await client.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.id,
            text=text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка установки режима 'вручную': {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


async def handle_wm_position(client, callback_query):
    """Обработчик выбора позиции watermark"""
    keyboard = get_watermark_position_keyboard()
    
    try:
        await client.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.id,
            text="Выберите позицию водяного знака:",
            reply_markup=keyboard
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка выбора позиции: {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


async def handle_wm_position_set(client, callback_query, position: str):
    """Установить позицию watermark"""
    user_id = callback_query.from_user.id
    channel_id = get_current_watermark_channel(user_id)
    
    try:
        result = await watermark_manager.set_watermark_position(user_id, position, channel_id=channel_id)
        await callback_query.answer(result)
        
        text = await watermark_manager.format_watermark_settings(user_id, channel_id)
        keyboard = get_watermark_menu_keyboard()
        await client.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.id,
            text=text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка установки позиции: {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


async def handle_wm_opacity(client, callback_query):
    """Обработчик настройки прозрачности"""
    user_id = callback_query.from_user.id
    user_states[user_id]['state'] = FSM_WATERMARK_OPACITY
    
    try:
        await callback_query.message.reply(
            "Введите прозрачность водяного знака:\n"
            "- Число от 0 до 255 (0 = полностью прозрачный, 255 = непрозрачный)\n"
            "- Или процент от 0% до 100%"
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка настройки прозрачности: {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


async def handle_wm_scale(client, callback_query):
    """Обработчик настройки масштаба"""
    user_id = callback_query.from_user.id
    user_states[user_id]['state'] = FSM_WATERMARK_SCALE
    
    try:
        await callback_query.message.reply(
            "Введите масштаб водяного знака:\n"
            "- Число от 0.1 до 1.0 (например, 0.3 = 30% от размера изображения)\n"
            "- Или процент от 10% до 100%"
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка настройки масштаба: {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


async def handle_wm_save(client, callback_query):
    """Сохранить настройки watermark"""
    user_id = callback_query.from_user.id
    channel_id = get_current_watermark_channel(user_id)
    
    try:
        result = await watermark_manager.save_watermark_config(user_id, channel_id)
        await callback_query.answer(result)
        
        # Возвращаемся в меню настроек пересылки
        from bot.handlers import show_forwarding_settings
        await show_forwarding_settings(client, callback_query.message, user_id)
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка сохранения настроек: {e}")
        await callback_query.answer("❌ Ошибка сохранения", show_alert=True)


async def handle_wm_menu(client, callback_query):
    """Вернуться в главное меню watermark"""
    user_id = callback_query.from_user.id
    channel_id = get_current_watermark_channel(user_id)
    
    try:
        text = await watermark_manager.format_watermark_settings(user_id, channel_id)
        keyboard = get_watermark_menu_keyboard()
        await client.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.id,
            text=text,
            reply_markup=keyboard
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"[WATERMARK] Ошибка возврата в меню: {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


# ==================== ОБРАБОТЧИКИ ТЕКСТОВОГО ВВОДА И ЗАГРУЗКИ ФОТО ====================

async def handle_watermark_text_input(client, message):
    """Обработчик ввода текста для watermark"""
    user_id = message.from_user.id
    channel_id = get_current_watermark_channel(user_id)
    
    if user_states.get(user_id, {}).get('state') == FSM_WATERMARK_TEXT_INPUT:
        try:
            result = await watermark_manager.set_watermark_text(user_id, message.text, channel_id=channel_id)
            await message.reply(result)
            await watermark_manager.show_watermark_menu(client, message, user_id, channel_id)
            
        except Exception as e:
            logger.error(f"[WATERMARK] Ошибка сохранения текста: {e}")
            await message.reply(f"❌ Ошибка: {str(e)}")


async def handle_watermark_chance_input(client, message):
    """Обработчик ввода вероятности для режима random"""
    user_id = message.from_user.id
    channel_id = get_current_watermark_channel(user_id)
    
    if user_states.get(user_id, {}).get('state') == FSM_WATERMARK_CHANCE:
        try:
            # Парсим процент
            chance_text = message.text.strip().replace('%', '')
            chance = int(chance_text)
            
            if chance < 0 or chance > 100:
                await message.reply("❌ Вероятность должна быть от 0 до 100%")
                return
            
            result = await watermark_manager.set_watermark_mode(user_id, 'random', chance=chance, channel_id=channel_id)
            await message.reply(result)
            await watermark_manager.show_watermark_menu(client, message, user_id, channel_id)
            
        except ValueError:
            await message.reply("❌ Введите число от 0 до 100")
        except Exception as e:
            logger.error(f"[WATERMARK] Ошибка сохранения вероятности: {e}")
            await message.reply(f"❌ Ошибка: {str(e)}")


async def handle_watermark_hashtag_input(client, message):
    """Обработчик ввода хэштега для режима hashtag"""
    user_id = message.from_user.id
    channel_id = get_current_watermark_channel(user_id)
    
    if user_states.get(user_id, {}).get('state') == FSM_WATERMARK_HASHTAG:
        try:
            hashtag = message.text.strip()
            result = await watermark_manager.set_watermark_mode(user_id, 'hashtag', hashtag=hashtag, channel_id=channel_id)
            await message.reply(result)
            await watermark_manager.show_watermark_menu(client, message, user_id, channel_id)
            
        except Exception as e:
            logger.error(f"[WATERMARK] Ошибка сохранения хэштега: {e}")
            await message.reply(f"❌ Ошибка: {str(e)}")


async def handle_watermark_opacity_input(client, message):
    """Обработчик ввода прозрачности"""
    user_id = message.from_user.id
    channel_id = get_current_watermark_channel(user_id)
    
    if user_states.get(user_id, {}).get('state') == FSM_WATERMARK_OPACITY:
        try:
            opacity_text = message.text.strip().replace('%', '')
            opacity = int(opacity_text)
            
            # Если введен процент (0-100), конвертируем в 0-255
            if opacity <= 100:
                opacity = int((opacity / 100) * 255)
            
            result = await watermark_manager.set_watermark_opacity(user_id, opacity, channel_id=channel_id)
            await message.reply(result)
            await watermark_manager.show_watermark_menu(client, message, user_id, channel_id)
            
        except ValueError:
            await message.reply("❌ Введите число от 0 до 255 или процент от 0 до 100%")
        except Exception as e:
            logger.error(f"[WATERMARK] Ошибка сохранения прозрачности: {e}")
            await message.reply(f"❌ Ошибка: {str(e)}")


async def handle_watermark_scale_input(client, message):
    """Обработчик ввода масштаба"""
    user_id = message.from_user.id
    channel_id = get_current_watermark_channel(user_id)
    
    if user_states.get(user_id, {}).get('state') == FSM_WATERMARK_SCALE:
        try:
            scale_text = message.text.strip().replace('%', '')
            scale = float(scale_text)
            
            # Если введен процент (10-100), конвертируем в 0.1-1.0
            if scale >= 10:
                scale = scale / 100
            
            result = await watermark_manager.set_watermark_scale(user_id, scale, channel_id=channel_id)
            await message.reply(result)
            await watermark_manager.show_watermark_menu(client, message, user_id, channel_id)
            
        except ValueError:
            await message.reply("❌ Введите число от 0.1 до 1.0 или процент от 10 до 100%")
        except Exception as e:
            logger.error(f"[WATERMARK] Ошибка сохранения масштаба: {e}")
            await message.reply(f"❌ Ошибка: {str(e)}")


async def handle_watermark_image_upload(client, message):
    """Обработчик загрузки изображения для watermark (работает с фото и документами)"""
    user_id = message.from_user.id
    channel_id = get_current_watermark_channel(user_id)

    logger.info(f"[WATERMARK] handle_watermark_image_upload called for user {user_id}")
    logger.info(f"[WATERMARK] Current state: {user_states.get(user_id, {}).get('state')}")

    if user_states.get(user_id, {}).get('state') == FSM_WATERMARK_IMAGE_UPLOAD:
        logger.info(f"[WATERMARK] State matches, processing upload for user {user_id}")
        try:
            logger.info(f"[WATERMARK] Starting image upload for user {user_id}")

            # Определяем, с чем работаем - с фото или документом
            if hasattr(message, 'photo') and message.photo:
                # Это фото
                media = message.photo
                media_type = "photo"
                file_name_attr = None
            elif hasattr(message, 'document') and message.document:
                # Это документ
                media = message.document
                media_type = "document"
                file_name_attr = getattr(message.document, 'file_name', None)
            else:
                logger.error(f"[WATERMARK] No photo or document in message for user {user_id}")
                await message.reply("❌ Не найдено изображение в сообщении")
                return

            logger.info(f"[WATERMARK] Processing {media_type} media")

            # Определяем формат файла из MIME типа или имени файла
            file_ext = '.jpg'  # По умолчанию
            mime_type = getattr(media, 'mime_type', '')

            if mime_type:
                mime_to_ext = {
                    'image/jpeg': '.jpg',
                    'image/png': '.png',
                    'image/webp': '.webp',
                    'image/gif': '.gif'
                }
                file_ext = mime_to_ext.get(mime_type, '.jpg')
                logger.info(f"[WATERMARK] Detected MIME type: {mime_type}, extension: {file_ext}")
            elif file_name_attr:
                # Определяем расширение из имени файла
                _, ext = os.path.splitext(file_name_attr)
                if ext.lower() in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
                    file_ext = ext.lower()
                    if file_ext == '.jpeg':
                        file_ext = '.jpg'
                logger.info(f"[WATERMARK] Detected extension from filename: {file_ext}")
            else:
                logger.info(f"[WATERMARK] No MIME type or filename, using default extension: {file_ext}")

            # Генерируем имя файла с правильным расширением
            file_name = f"watermark_{user_id}_{media.file_unique_id}{file_ext}"
            file_size = getattr(media, 'file_size', 0)

            logger.info(f"[WATERMARK] Generated file name: {file_name}, size: {file_size}")

            # Скачиваем файл с указанным именем
            logger.info(f"[WATERMARK] Calling message.download({file_name})")
            file_path = await message.download(file_name)
            logger.info(f"[WATERMARK] Download completed, file_path: {file_path}")

            # Проверяем, что файл действительно скачался
            if not file_path or not os.path.exists(file_path):
                logger.error(f"[WATERMARK] File download failed, path: {file_path}")
                await message.reply("❌ Ошибка скачивания файла")
                return

            logger.info(f"[WATERMARK] File downloaded successfully to: {file_path}")

            result = await watermark_manager.save_watermark_image(user_id, file_path, file_name, file_size, channel_id=channel_id)
            logger.info(f"[WATERMARK] Save result: {result}")
            await message.reply(result)
            await watermark_manager.show_watermark_menu(client, message, user_id, channel_id)

        except Exception as e:
            logger.error(f"[WATERMARK] Ошибка загрузки изображения: {e}")
            import traceback
            logger.error(f"[WATERMARK] Traceback: {traceback.format_exc()}")
            await message.reply(f"❌ Ошибка загрузки: {str(e)}")
    else:
        logger.info(f"[WATERMARK] State does not match for user {user_id}")
        await message.reply("❌ Сначала выберите тип watermark - изображение")


# РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ (добавить в bot_main.py при регистрации приложения):
"""
# В forwarding_callback_handler добавить обработку watermark callback
elif data.startswith('watermark') or data.startswith('wm_'):
    if data == 'watermark_settings':
        from bot.watermark_handlers import handle_watermark_settings
        await handle_watermark_settings(client, callback_query)
    elif data == 'wm_toggle':
        from bot.watermark_handlers import handle_wm_toggle
        await handle_wm_toggle(client, callback_query)
    elif data == 'wm_type':
        from bot.watermark_handlers import handle_wm_type
        await handle_wm_type(client, callback_query)
    elif data == 'wm_type_text':
        from bot.watermark_handlers import handle_wm_type_text
        await handle_wm_type_text(client, callback_query)
    elif data == 'wm_type_image':
        from bot.watermark_handlers import handle_wm_type_image
        await handle_wm_type_image(client, callback_query)
    elif data == 'wm_mode':
        from bot.watermark_handlers import handle_wm_mode
        await handle_wm_mode(client, callback_query)
    elif data == 'wm_mode_all':
        from bot.watermark_handlers import handle_wm_mode_all
        await handle_wm_mode_all(client, callback_query)
    elif data == 'wm_mode_random':
        from bot.watermark_handlers import handle_wm_mode_random
        await handle_wm_mode_random(client, callback_query)
    elif data == 'wm_mode_hashtag':
        from bot.watermark_handlers import handle_wm_mode_hashtag
        await handle_wm_mode_hashtag(client, callback_query)
    elif data == 'wm_mode_manual':
        from bot.watermark_handlers import handle_wm_mode_manual
        await handle_wm_mode_manual(client, callback_query)
    elif data == 'wm_position':
        from bot.watermark_handlers import handle_wm_position
        await handle_wm_position(client, callback_query)
    elif data.startswith('wm_pos_'):
        position = data.replace('wm_pos_', '')
        from bot.watermark_handlers import handle_wm_position_set
        await handle_wm_position_set(client, callback_query, position)
    elif data == 'wm_opacity':
        from bot.watermark_handlers import handle_wm_opacity
        await handle_wm_opacity(client, callback_query)
    elif data == 'wm_scale':
        from bot.watermark_handlers import handle_wm_scale
        await handle_wm_scale(client, callback_query)
    elif data == 'wm_save':
        from bot.watermark_handlers import handle_wm_save
        await handle_wm_save(client, callback_query)
    elif data == 'wm_menu':
        from bot.watermark_handlers import handle_wm_menu
        await handle_wm_menu(client, callback_query)
    return

# В обработчике текстовых сообщений добавить:
from bot.watermark_handlers import (
    handle_watermark_text_input,
    handle_watermark_chance_input,
    handle_watermark_hashtag_input,
    handle_watermark_opacity_input,
    handle_watermark_scale_input
)

# Проверяем водяные знаки states
await handle_watermark_text_input(client, message)
await handle_watermark_chance_input(client, message)
await handle_watermark_hashtag_input(client, message)
await handle_watermark_opacity_input(client, message)
await handle_watermark_scale_input(client, message)

# В обработчике фото добавить:
from bot.watermark_handlers import handle_watermark_image_upload
await handle_watermark_image_upload(client, message)
"""

