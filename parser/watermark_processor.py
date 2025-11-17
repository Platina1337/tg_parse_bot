"""
Модуль для обработки изображений и наложения водяных знаков
"""
import os
import logging
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import tempfile

logger = logging.getLogger(__name__)


class WatermarkProcessor:
    """Класс для обработки изображений и наложения водяных знаков"""
    
    # Поддерживаемые форматы изображений
    SUPPORTED_FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF'}
    
    # Максимальный размер изображения для обработки (в пикселях)
    MAX_IMAGE_SIZE = 4096
    
    def __init__(self):
        self.temp_dir = tempfile.gettempdir()
        logger.info("[WatermarkProcessor] Инициализация процессора водяных знаков")
    
    def _get_watermark_position(
        self,
        image_size: Tuple[int, int],
        watermark_size: Tuple[int, int],
        position: str
    ) -> Tuple[int, int]:
        """
        Вычислить позицию водяного знака на изображении
        
        Args:
            image_size: Размер основного изображения (ширина, высота)
            watermark_size: Размер водяного знака (ширина, высота)
            position: Позиция ("center", "bottom_right", "bottom_left", "top_right", "top_left")
        
        Returns:
            Координаты (x, y) для размещения водяного знака
        """
        img_width, img_height = image_size
        wm_width, wm_height = watermark_size
        
        # Отступы от краев (5% от размера изображения)
        margin_x = int(img_width * 0.05)
        margin_y = int(img_height * 0.05)
        
        positions = {
            'center': (
                (img_width - wm_width) // 2,
                (img_height - wm_height) // 2
            ),
            'bottom_right': (
                img_width - wm_width - margin_x,
                img_height - wm_height - margin_y
            ),
            'bottom_left': (
                margin_x,
                img_height - wm_height - margin_y
            ),
            'top_right': (
                img_width - wm_width - margin_x,
                margin_y
            ),
            'top_left': (
                margin_x,
                margin_y
            )
        }
        
        return positions.get(position, positions['bottom_right'])
    
    def _create_text_watermark(
        self,
        text: str,
        font_size: int = 48,
        color: Tuple[int, int, int, int] = (255, 255, 255, 128)
    ) -> Image.Image:
        """
        Создать изображение с текстовым водяным знаком
        
        Args:
            text: Текст водяного знака
            font_size: Размер шрифта
            color: Цвет в формате RGBA
        
        Returns:
            PIL Image с текстом
        """
        try:
            # Пытаемся использовать системный шрифт
            font = ImageFont.truetype("arial.ttf", font_size)
        except IOError:
            try:
                # Для Linux
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
            except IOError:
                # Используем дефолтный шрифт PIL
                font = ImageFont.load_default()
                logger.warning("[WatermarkProcessor] Использование дефолтного шрифта")
        
        # Создаем временное изображение для определения размера текста
        dummy_img = Image.new('RGBA', (1, 1))
        draw = ImageDraw.Draw(dummy_img)
        
        # Получаем размер текста
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # Добавляем padding
        padding = 20
        img_width = text_width + padding * 2
        img_height = text_height + padding * 2
        
        # Создаем изображение с текстом
        text_img = Image.new('RGBA', (img_width, img_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(text_img)
        
        # Рисуем текст
        draw.text((padding, padding), text, font=font, fill=color)
        
        return text_img
    
    def _resize_watermark(
        self,
        watermark: Image.Image,
        target_size: Tuple[int, int],
        scale: float
    ) -> Image.Image:
        """
        Изменить размер водяного знака относительно целевого изображения
        
        Args:
            watermark: Изображение водяного знака
            target_size: Размер целевого изображения
            scale: Масштаб (0.1-1.0)
        
        Returns:
            Измененное изображение водяного знака
        """
        target_width, target_height = target_size
        
        # Вычисляем новый размер водяного знака
        # Берем меньшую сторону целевого изображения как базу
        base_size = min(target_width, target_height)
        max_wm_size = int(base_size * scale)
        
        # Сохраняем пропорции водяного знака
        wm_width, wm_height = watermark.size
        aspect_ratio = wm_width / wm_height
        
        if wm_width > wm_height:
            new_width = max_wm_size
            new_height = int(new_width / aspect_ratio)
        else:
            new_height = max_wm_size
            new_width = int(new_height * aspect_ratio)
        
        # Изменяем размер с сохранением качества
        return watermark.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    def _adjust_opacity(self, image: Image.Image, opacity: int) -> Image.Image:
        """
        Изменить прозрачность изображения
        
        Args:
            image: Изображение
            opacity: Прозрачность (0-255)
        
        Returns:
            Изображение с измененной прозрачностью
        """
        # Убеждаемся, что изображение в режиме RGBA
        if image.mode != 'RGBA':
            image = image.convert('RGBA')
        
        # Создаем копию альфа-канала
        alpha = image.split()[3]
        
        # Применяем прозрачность
        alpha = ImageEnhance.Brightness(alpha).enhance(opacity / 255.0)
        
        # Применяем измененный альфа-канал
        image.putalpha(alpha)
        
        return image
    
    def apply_watermark(
        self,
        image_path: str,
        watermark_config: dict,
        output_path: Optional[str] = None
    ) -> str:
        """
        Наложить водяной знак на изображение
        
        Args:
            image_path: Путь к исходному изображению
            watermark_config: Конфигурация водяного знака:
                - watermark_text: Текстовый водяной знак (опционально)
                - watermark_image_path: Путь к изображению водяного знака (опционально)
                - watermark_position: Позиция ("center", "bottom_right", и т.д.)
                - watermark_opacity: Прозрачность (0-255)
                - watermark_scale: Масштаб (0.1-1.0)
            output_path: Путь для сохранения результата (опционально)
        
        Returns:
            Путь к обработанному изображению
        """
        logger.info(f"[WatermarkProcessor] Применение watermark к {image_path}")
        
        try:
            # Открываем исходное изображение
            with Image.open(image_path) as base_image:
                # Проверяем размер
                if max(base_image.size) > self.MAX_IMAGE_SIZE:
                    logger.warning(f"[WatermarkProcessor] Изображение слишком большое: {base_image.size}")
                    # Масштабируем
                    base_image.thumbnail((self.MAX_IMAGE_SIZE, self.MAX_IMAGE_SIZE), Image.Resampling.LANCZOS)
                
                # Конвертируем в RGBA для поддержки прозрачности
                if base_image.mode != 'RGBA':
                    base_image = base_image.convert('RGBA')
                
                # Создаем водяной знак
                watermark = None
                
                # Приоритет: изображение > текст
                if watermark_config.get('watermark_image_path'):
                    watermark_img_path = watermark_config['watermark_image_path']
                    if os.path.exists(watermark_img_path):
                        try:
                            watermark = Image.open(watermark_img_path)
                            if watermark.mode != 'RGBA':
                                watermark = watermark.convert('RGBA')
                        except Exception as e:
                            logger.error(f"[WatermarkProcessor] Ошибка загрузки watermark изображения: {e}")
                
                elif watermark_config.get('watermark_text'):
                    # Создаем текстовый водяной знак
                    watermark = self._create_text_watermark(
                        watermark_config['watermark_text']
                    )
                
                if not watermark:
                    logger.warning("[WatermarkProcessor] Водяной знак не создан")
                    return image_path
                
                # Изменяем размер водяного знака
                scale = watermark_config.get('watermark_scale', 0.3)
                watermark = self._resize_watermark(watermark, base_image.size, scale)
                
                # Применяем прозрачность
                opacity = watermark_config.get('watermark_opacity', 128)
                watermark = self._adjust_opacity(watermark, opacity)
                
                # Вычисляем позицию
                position_name = watermark_config.get('watermark_position', 'bottom_right')
                position = self._get_watermark_position(
                    base_image.size,
                    watermark.size,
                    position_name
                )
                
                # Накладываем водяной знак
                base_image.paste(watermark, position, watermark)
                
                # Определяем путь для сохранения
                if not output_path:
                    name, ext = os.path.splitext(os.path.basename(image_path))
                    output_path = os.path.join(
                        self.temp_dir,
                        f"{name}_watermarked{ext}"
                    )
                
                # Сохраняем результат
                # Конвертируем обратно в RGB для JPEG
                if output_path.lower().endswith('.jpg') or output_path.lower().endswith('.jpeg'):
                    base_image = base_image.convert('RGB')
                
                base_image.save(output_path, quality=95)
                logger.info(f"[WatermarkProcessor] Watermark применен, сохранено в {output_path}")
                
                return output_path
                
        except Exception as e:
            logger.error(f"[WatermarkProcessor] Ошибка при применении watermark: {e}")
            logger.exception(e)
            # В случае ошибки возвращаем оригинальный файл
            return image_path
    
    def cleanup_temp_files(self, *file_paths: str):
        """
        Удалить временные файлы
        
        Args:
            file_paths: Пути к файлам для удаления
        """
        for file_path in file_paths:
            try:
                if os.path.exists(file_path) and file_path.startswith(self.temp_dir):
                    os.remove(file_path)
                    logger.debug(f"[WatermarkProcessor] Удален временный файл: {file_path}")
            except Exception as e:
                logger.warning(f"[WatermarkProcessor] Не удалось удалить {file_path}: {e}")


# Singleton instance
watermark_processor = WatermarkProcessor()

