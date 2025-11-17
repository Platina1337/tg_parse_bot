"""
Модуль управления водяными знаками через интерфейс бота
"""
import logging
import os
from bot.api_client import api_client
from bot.states import (
    user_states,
    FSM_WATERMARK_MENU, FSM_WATERMARK_TYPE, FSM_WATERMARK_TEXT_INPUT,
    FSM_WATERMARK_IMAGE_UPLOAD, FSM_WATERMARK_MODE, FSM_WATERMARK_CHANCE,
    FSM_WATERMARK_HASHTAG, FSM_WATERMARK_POSITION, FSM_WATERMARK_OPACITY,
    FSM_WATERMARK_SCALE,
    get_watermark_menu_keyboard, get_watermark_type_keyboard,
    get_watermark_mode_keyboard, get_watermark_position_keyboard
)

logger = logging.getLogger(__name__)


class WatermarkManager:
    """Менеджер для управления водяными знаками"""
    
    # Максимальный размер файла watermark (5 МБ)
    MAX_FILE_SIZE = 5 * 1024 * 1024
    
    # Поддерживаемые форматы
    SUPPORTED_FORMATS = ['.jpg', '.jpeg', '.png', '.webp', '.gif']
    
    # Директория для хранения watermark файлов
    WATERMARKS_DIR = "watermarks"
    
    def __init__(self):
        # Создаем директорию для watermark если её нет
        os.makedirs(self.WATERMARKS_DIR, exist_ok=True)
        logger.info(f"[WatermarkManager] Инициализирован, директория: {self.WATERMARKS_DIR}")
    
    def _get_user_watermark_dir(self, user_id: int) -> str:
        """Получить директорию для watermark пользователя"""
        user_dir = os.path.join(self.WATERMARKS_DIR, f"user_{user_id}")
        os.makedirs(user_dir, exist_ok=True)
        return user_dir
    
    async def _get_watermark_state(self, user_id: int, channel_id: str) -> dict:
        """Получить текущее состояние watermark настроек пользователя для канала из API."""
        user_state = user_states.get(user_id, {})
        if not channel_id:
            channel_id = user_state.get('current_watermark_channel_id') or \
                         (user_state.get('forward_target_channels') and user_state['forward_target_channels'][0]['id'])

        if not channel_id:
            logger.warning(f"Не удалось определить channel_id для пользователя {user_id}")
            # Возвращаем дефолтные настройки, если канал не найден
            return {
                'watermark_enabled': False, 'watermark_mode': 'all', 'watermark_chance': 100,
                'watermark_hashtag': None, 'watermark_text': None, 'watermark_image_path': None,
                'watermark_position': 'bottom_right', 'watermark_opacity': 128, 'watermark_scale': 0.3
            }

        settings = await api_client.get_watermark_settings(user_id, channel_id)
        
        # Сохраняем настройки в user_states для консистентности
        if 'watermark_channels' not in user_state:
            user_state['watermark_channels'] = {}
        user_state['watermark_channels'][str(channel_id)] = settings
        user_states[user_id] = user_state
        
        return settings

    async def _save_watermark_state(self, user_id: int, settings: dict, channel_id: str):
        """Сохранить состояние watermark настроек пользователя для канала через API."""
        user_state = user_states.get(user_id, {})
        if not channel_id:
            channel_id = user_state.get('current_watermark_channel_id') or \
                         (user_state.get('forward_target_channels') and user_state['forward_target_channels'][0]['id'])

        if not channel_id:
            logger.error(f"Не удалось определить channel_id для сохранения настроек пользователя {user_id}")
            return

        # Обновляем локальное состояние
        if 'watermark_channels' not in user_state:
            user_state['watermark_channels'] = {}
        user_state['watermark_channels'][str(channel_id)] = settings
        user_states[user_id] = user_state

        # Отправляем на сервер
        await api_client.save_watermark_settings(user_id, channel_id, settings)
    
    async def format_watermark_settings(self, user_id: int, channel_id: str = None) -> str:
        """Форматировать текущие настройки watermark для отображения"""
        settings = await self._get_watermark_state(user_id, channel_id)
        
        status = "✅ Включен" if settings.get('watermark_enabled') else "❌ Выключен"
        
        wm_type = "📝 Текст" if settings.get('watermark_text') else "🖼️ Изображение"
        if settings.get('watermark_text'):
            wm_value = f"\n  └─ Текст: {settings.get('watermark_text')}"
        elif settings.get('watermark_image_path'):
            wm_value = f"\n  └─ Файл: {os.path.basename(settings.get('watermark_image_path'))}"
        else:
            wm_value = "\n  └─ Не настроено"
        
        mode_map = {
            'all': '✅ Все посты',
            'random': f"🎲 Случайно ({settings.get('watermark_chance', 100)}%)",
            'hashtag': f"#️⃣ По хэштегу: {settings.get('watermark_hashtag') or 'не указан'}",
            'manual': '✋ Вручную'
        }
        mode = mode_map.get(settings.get('watermark_mode', 'all'), 'Неизвестно')
        
        position_map = {
            'center': '🎯 Центр',
            'bottom_right': '⬇️ Низ справа',
            'bottom_left': '⬇️ Низ слева',
            'top_right': '⬆️ Верх справа',
            'top_left': '⬆️ Верх слева'
        }
        position = position_map.get(settings.get('watermark_position', 'bottom_right'), 'Неизвестно')
        
        opacity_percent = int((settings.get('watermark_opacity', 128) / 255) * 100)
        scale_percent = int(settings.get('watermark_scale', 0.3) * 100)
        
        text = f"""
🎨 **Настройки водяного знака**

**Статус**: {status}
**Тип**: {wm_type}{wm_value}
**Режим**: {mode}
**Позиция**: {position}
**Прозрачность**: {opacity_percent}% (значение: {settings.get('watermark_opacity', 128)}/255)
**Масштаб**: {scale_percent}%

Выберите параметр для изменения:
        """.strip()
        
        return text
    
    async def show_watermark_menu(self, client, message, user_id: int, channel_id: str = None):
        """Показать главное меню watermark"""
        logger.info(f"[WatermarkManager] Показываем меню watermark для пользователя {user_id}, канал {channel_id}")

        text = await self.format_watermark_settings(user_id, channel_id)
        keyboard = get_watermark_menu_keyboard()

        try:
            await client.send_message(
                chat_id=message.chat.id,
                text=text,
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"[WatermarkManager] Ошибка показа меню watermark: {e}")

    async def toggle_watermark(self, user_id: int, channel_id: str = None) -> str:
        """Включить/выключить watermark"""
        settings = await self._get_watermark_state(user_id, channel_id)
        settings['watermark_enabled'] = not settings.get('watermark_enabled', False)
        await self._save_watermark_state(user_id, settings, channel_id)

        status = "включен ✅" if settings['watermark_enabled'] else "выключен ❌"
        return f"Водяной знак {status}"

    async def set_watermark_type_text(self, user_id: int, channel_id: str = None):
        """Установить тип watermark - текст"""
        settings = await self._get_watermark_state(user_id, channel_id)
        settings['watermark_image_path'] = None
        await self._save_watermark_state(user_id, settings, channel_id)
        user_states[user_id]['state'] = FSM_WATERMARK_TEXT_INPUT
        return "Введите текст для водяного знака:"

    async def set_watermark_text(self, user_id: int, text: str, channel_id: str = None):
        """Сохранить текст водяного знака"""
        settings = await self._get_watermark_state(user_id, channel_id)
        settings['watermark_text'] = text
        settings['watermark_image_path'] = None
        await self._save_watermark_state(user_id, settings, channel_id)
        return f"✅ Текстовый водяной знак установлен: {text}"

    async def set_watermark_type_image(self, user_id: int, channel_id: str = None):
        """Установить тип watermark - изображение"""
        settings = await self._get_watermark_state(user_id, channel_id)
        settings['watermark_text'] = None
        await self._save_watermark_state(user_id, settings, channel_id)
        user_states[user_id]['state'] = FSM_WATERMARK_IMAGE_UPLOAD
        return "Отправьте изображение для водяного знака (PNG, JPEG, WEBP, GIF, макс. 5 МБ):"
    
    async def save_watermark_image(self, user_id: int, file_path: str, file_name: str, file_size: int, channel_id: str = None):
        """Сохранить изображение водяного знака"""
        try:
            logger.info(f"[WatermarkManager] Saving image for user {user_id}, channel {channel_id}: {file_name}, size: {file_size}")

            # Проверка размера
            if file_size > self.MAX_FILE_SIZE:
                return "❌ Файл слишком большой. Максимальный размер: 5 МБ"

            # Проверка формата
            file_ext = os.path.splitext(file_name)[1].lower()
            if file_ext not in self.SUPPORTED_FORMATS:
                return f"❌ Неподдерживаемый формат. Поддерживаются: {', '.join(self.SUPPORTED_FORMATS)}"

            # Получаем директорию пользователя
            user_dir = self._get_user_watermark_dir(user_id)

            # Путь для сохранения файла
            new_file_path = os.path.join(user_dir, file_name)

            # Перемещаем файл в директорию пользователя
            if os.path.exists(file_path) and file_path != new_file_path:
                import shutil
                shutil.move(file_path, new_file_path)
                logger.info(f"[WatermarkManager] File moved to: {new_file_path}")
            else:
                logger.warning(f"[WatermarkManager] File path issue: {file_path} -> {new_file_path}")

            # Сохраняем информацию в БД через API
            # TODO: Добавить API endpoint для сохранения watermark изображения
            # await api_client.save_watermark_image(user_id, new_file_path, file_name, file_size)

            # Сохраняем путь в состоянии
            settings = await self._get_watermark_state(user_id, channel_id)
            settings['watermark_image_path'] = new_file_path
            settings['watermark_text'] = None
            await self._save_watermark_state(user_id, settings, channel_id)

            logger.info(f"[WatermarkManager] Image saved successfully for user {user_id}, channel {channel_id}: {new_file_path}")
            return f"✅ Изображение водяного знака сохранено: {file_name}"

        except Exception as e:
            logger.error(f"[WatermarkManager] Ошибка сохранения изображения: {e}")
            return f"❌ Ошибка сохранения изображения: {str(e)}"
    
    async def set_watermark_mode(self, user_id: int, mode: str, channel_id: str = None, **kwargs):
        """Установить режим применения watermark"""
        settings = await self._get_watermark_state(user_id, channel_id)
        settings['watermark_mode'] = mode

        if mode == 'random' and 'chance' in kwargs:
            settings['watermark_chance'] = kwargs['chance']
        elif mode == 'hashtag' and 'hashtag' in kwargs:
            settings['watermark_hashtag'] = kwargs['hashtag']

        await self._save_watermark_state(user_id, settings, channel_id)

        mode_names = {
            'all': 'Все посты',
            'random': f"Случайно ({settings.get('watermark_chance', 100)}%)",
            'hashtag': f"По хэштегу: {settings.get('watermark_hashtag')}",
            'manual': 'Вручную'
        }
        return f"✅ Режим установлен: {mode_names.get(mode, mode)}"

    async def set_watermark_position(self, user_id: int, position: str, channel_id: str = None):
        """Установить позицию watermark"""
        settings = await self._get_watermark_state(user_id, channel_id)
        settings['watermark_position'] = position
        await self._save_watermark_state(user_id, settings, channel_id)

        position_names = {
            'center': 'Центр',
            'bottom_right': 'Низ справа',
            'bottom_left': 'Низ слева',
            'top_right': 'Верх справа',
            'top_left': 'Верх слева'
        }
        return f"✅ Позиция установлена: {position_names.get(position, position)}"

    async def set_watermark_opacity(self, user_id: int, opacity: int, channel_id: str = None):
        """Установить прозрачность watermark"""
        # Проверяем диапазон
        opacity = max(0, min(255, opacity))

        settings = await self._get_watermark_state(user_id, channel_id)
        settings['watermark_opacity'] = opacity
        await self._save_watermark_state(user_id, settings, channel_id)

        percent = int((opacity / 255) * 100)
        return f"✅ Прозрачность установлена: {percent}% ({opacity}/255)"

    async def set_watermark_scale(self, user_id: int, scale: float, channel_id: str = None):
        """Установить масштаб watermark"""
        # Проверяем диапазон
        scale = max(0.1, min(1.0, scale))

        settings = await self._get_watermark_state(user_id, channel_id)
        settings['watermark_scale'] = scale
        await self._save_watermark_state(user_id, settings, channel_id)

        percent = int(scale * 100)
        return f"✅ Масштаб установлен: {percent}%"
    
    async def save_watermark_config(self, user_id: int, channel_id: str = None) -> str:
        """Сохранить конфигурацию watermark в настройках пересылки"""
        try:
            settings = await self._get_watermark_state(user_id, channel_id)
            await self._save_watermark_state(user_id, settings, channel_id) # Explicitly save
            logger.info(f"[WatermarkManager] Настройки watermark сохранены для пользователя {user_id}, канал {channel_id}")
            return "✅ Настройки водяного знака сохранены"

        except Exception as e:
            logger.error(f"[WatermarkManager] Ошибка сохранения конфигурации: {e}")
            return f"❌ Ошибка сохранения: {str(e)}"

    async def get_channel_watermark_settings(self, user_id: int, channel_id: str) -> dict:
        """Получить настройки watermark для конкретного канала"""
        return await self._get_watermark_state(user_id, channel_id)

    async def apply_channel_watermark(self, user_id: int, channel_id: str):
        """Применить настройки watermark канала к текущему состоянию"""
        channel_settings = await self._get_watermark_state(user_id, channel_id)

        # Копируем настройки канала в глобальное состояние для совместимости
        user_state = user_states.get(user_id, {})
        user_state.update({
            'watermark_enabled': channel_settings['watermark_enabled'],
            'watermark_mode': channel_settings['watermark_mode'],
            'watermark_chance': channel_settings['watermark_chance'],
            'watermark_hashtag': channel_settings['watermark_hashtag'],
            'watermark_text': channel_settings['watermark_text'],
            'watermark_image_path': channel_settings['watermark_image_path'],
            'watermark_position': channel_settings['watermark_position'],
            'watermark_opacity': channel_settings['watermark_opacity'],
            'watermark_scale': channel_settings['watermark_scale']
        })
        user_states[user_id] = user_state

        logger.info(f"[WatermarkManager] Applied watermark settings for channel {channel_id} to user {user_id}")


# Singleton instance
watermark_manager = WatermarkManager()

