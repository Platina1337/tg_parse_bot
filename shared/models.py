from pydantic import BaseModel
from typing import List, Optional, Literal, Dict, Union
from datetime import datetime
from enum import Enum

class MediaGroup(BaseModel):
    id: int
    messages: List[int]  # ID сообщений в группе
    text: Optional[str] = None  # Текст группы, если есть

class Message(BaseModel):
    id: int
    chat_id: int
    text: Optional[str] = None
    type: Literal[
        "text_only",           # Только текст
        "photo_only",          # Только фото без текста
        "photo_with_text",     # Фото с текстом
        "video_only",          # Только видео без текста
        "video_with_text",     # Видео с текстом
        "document_only",       # Только документ без текста
        "document_with_text",  # Документ с текстом
        "media_group_only",    # Медиагруппа без текста
        "media_group_with_text" # Медиагруппа с текстом
    ]
    media_group_id: Optional[int] = None
    local_file_path: Optional[str] = None  # Путь к скачанному медиа
    parsed: bool = False
    forwarded_to: Optional[str] = None  # ID канала, куда было переслано

    @staticmethod
    def from_pyrogram(message) -> 'Message':
        message_type = "text_only"
        file_id = None

        if message.media:
            if message.photo:
                message_type = "photo_only"
                file_id = message.photo.file_id
                if message.text: message_type = "photo_with_text"
            elif message.video:
                message_type = "video_only"
                file_id = message.video.file_id
                if message.text: message_type = "video_with_text"
            elif message.document:
                message_type = "document_only"
                file_id = message.document.file_id
                if message.text: message_type = "document_with_text"
            elif message.media_group_id:
                message_type = "media_group_only"
                if message.text: message_type = "media_group_with_text"
        elif message.text:
            message_type = "text_only"

        return Message(
            id=message.id,
            chat_id=message.chat.id,
            text=message.text or getattr(message, 'caption', None),
            type=message_type,
            media_group_id=message.media_group_id if hasattr(message, 'media_group_id') else None,
            local_file_path=file_id
        )

class Channel(BaseModel):
    id: int
    title: str
    username: Optional[str] = None
    description: Optional[str] = None
    members_count: Optional[int] = None

class ParseResult(BaseModel):
    messages: List[Message]
    total_messages: int
    parsed_at: datetime

class ParseMode(str, Enum):
    ALL = "all"
    TEXT = "text"
    MEDIA = "media"
    LAST_N = "last_n"
    SINCE_DATE = "since_date"
    KEYWORDS = "keywords"

class MessageType(str, Enum):
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    MEDIA_GROUP = "media_group"

class ParseConfig(BaseModel):
    channel_id: int
    mode: ParseMode
    settings: Dict
    is_active: bool = True
    created_at: datetime = datetime.now()
    last_parsed_at: Optional[datetime] = None
    parse_direction: str = "backward"  # "forward" (от старых к новым) или "backward" (от новых к старым)

class PostingSettings(BaseModel):
    target_channel_id: int  # ID канала, куда будут поститься сообщения
    text_mode: Optional[str] = None  # Режим текста ("с текстом", "только хэштеги")
    order: Optional[str] = None      # Порядок публикации ("old_to_new", "new_to_old", "random", ...)
    mode: Optional[str] = None       # Режим публикации ("все", "media", "text")
    footer: Optional[str] = None     # Приписка
    max_posts: Optional[int] = None  # Лимит сообщений
    parse_mode: str = "HTML"  # Режим форматирования текста
    disable_web_page_preview: bool = False
    disable_notification: bool = False
    protect_content: bool = False
    add_source_link: bool = True  # Добавлять ссылку на источник
    add_hashtags: bool = True     # Добавлять хэштеги
    custom_hashtags: List[str] = []  # Пользовательские хэштеги
    watermark_text: Optional[str] = None  # Текст водяного знака
    max_message_length: int = 4096  # Максимальная длина сообщения
    truncate_long_messages: bool = True  # Обрезать длинные сообщения
    add_footer: bool = True  # Добавлять подпись
    footer_text: Optional[str] = None  # Текст подписи
    # --- Новые поля для гиперссылки в приписке ---
    footer_link: Optional[str] = None  # URL для гиперссылки
    footer_link_text: Optional[str] = None  # Текст, который будет гиперссылкой
    footer_full_link: bool = False  # Превращать ли всю приписку в ссылку
    add_header: bool = True  # Добавлять заголовок
    header_text: Optional[str] = None  # Текст заголовка
    filter_words: List[str] = []  # Слова для фильтрации
    replace_words: Dict[str, str] = {}  # Слова для замены
    add_timestamp: bool = True  # Добавлять временную метку
    timestamp_format: str = "%Y-%m-%d %H:%M:%S"  # Формат временной метки
    timezone: str = "UTC"  # Часовой пояс
    posting_delay: int = 0  # Задержка постинга в секундах
    max_posts_per_day: Optional[int] = None  # Максимум постов в день
    min_posts_per_day: Optional[int] = None  # Минимум постов в день
    posting_interval: Optional[int] = None  # Интервал между постами
    # --- Новые поля для платных постов ---
    paid_content_mode: str = "off"  # off | hashtag | random | hashtag_random | hashtag_select
    paid_content_stars: int = 0
    paid_content_hashtag: Optional[str] = None  # Хэштег для платных постов (если режим hashtag, hashtag_random, hashtag_select)
    paid_content_chance: Optional[int] = None  # Шанс (1..10) для рандомного режима

class PostingConfig(BaseModel):
    target_channel_id: int  # ID канала, куда будут поститься сообщения
    bot_token: str         # Токен бота, который будет постить
    text_mode: Optional[str] = None
    order: Optional[str] = None
    mode: Optional[str] = None
    footer: Optional[str] = None
    max_posts: Optional[int] = None
    parse_mode: str = "HTML"  # Режим форматирования текста
    disable_web_page_preview: bool = False
    disable_notification: bool = False
    protect_content: bool = False
    reply_to_message_id: Optional[int] = None
    allow_forwarding: bool = True  # Разрешить пересылку сообщений
    allow_media: bool = True      # Разрешить медиафайлы
    allow_text: bool = True       # Разрешить текстовые сообщения
    allowed_media_types: List[str] = ["photo", "video", "document"]  # Разрешенные типы медиа
    message_template: Optional[str] = None  # Шаблон для форматирования сообщений
    add_source_link: bool = True  # Добавлять ссылку на источник
    add_hashtags: bool = True     # Добавлять хэштеги
    custom_hashtags: List[str] = []  # Пользовательские хэштеги
    watermark_text: Optional[str] = None  # Текст водяного знака
    watermark_position: str = "bottom_right"  # Позиция водяного знака
    max_message_length: int = 4096  # Максимальная длина сообщения
    truncate_long_messages: bool = True  # Обрезать длинные сообщения
    add_footer: bool = True  # Добавлять подпись
    footer_text: Optional[str] = None  # Текст подписи
    add_header: bool = True  # Добавлять заголовок
    header_text: Optional[str] = None  # Текст заголовка
    replace_links: bool = False  # Заменять ссылки
    link_replacement: Optional[str] = None  # Текст замены для ссылок
    filter_words: List[str] = []  # Слова для фильтрации
    replace_words: Dict[str, str] = {}  # Слова для замены
    add_timestamp: bool = True  # Добавлять временную метку
    timestamp_format: str = "%Y-%m-%d %H:%M:%S"  # Формат временной метки
    timezone: str = "UTC"  # Часовой пояс
    language: str = "ru"  # Язык
    translate: bool = False  # Включить перевод
    target_language: Optional[str] = None  # Целевой язык для перевода
    add_emoji: bool = True  # Добавлять эмодзи
    emoji_map: Dict[str, str] = {}  # Карта эмодзи
    add_reactions: bool = False  # Добавлять реакции
    default_reactions: List[str] = []  # Реакции по умолчанию
    schedule_posting: bool = False  # Включить отложенный постинг
    posting_delay: int = 0  # Задержка постинга в секундах
    posting_time: Optional[str] = None  # Время постинга
    posting_days: List[str] = []  # Дни постинга
    posting_hours: List[int] = []  # Часы постинга
    max_posts_per_day: Optional[int] = None  # Максимум постов в день
    min_posts_per_day: Optional[int] = None  # Минимум постов в день
    randomize_posting: bool = False  # Рандомизировать время постинга
    posting_interval: Optional[int] = None  # Интервал между постами
    retry_on_failure: bool = True  # Повторять при ошибке
    max_retries: int = 3  # Максимум попыток
    retry_delay: int = 60  # Задержка между попытками
    notify_on_error: bool = True  # Уведомлять об ошибках
    error_notification_chat_id: Optional[int] = None  # Чат для уведомлений об ошибках
    save_to_database: bool = True  # Сохранять в базу данных
    database_table: str = "posted_messages"  # Таблица в базе данных
    track_statistics: bool = True  # Отслеживать статистику
    statistics_metrics: List[str] = ["views", "forwards", "reactions"]  # Метрики статистики
    generate_report: bool = False  # Генерировать отчет
    report_frequency: str = "daily"  # Частота отчетов
    report_chat_id: Optional[int] = None  # Чат для отчетов
    report_format: str = "text"  # Формат отчета
    report_include_media: bool = True  # Включать медиа в отчет
    report_include_text: bool = True  # Включать текст в отчет
    report_include_statistics: bool = True  # Включать статистику в отчет
    report_include_errors: bool = True  # Включать ошибки в отчет
    report_include_warnings: bool = True  # Включать предупреждения в отчет
    report_include_suggestions: bool = True  # Включать предложения в отчет
    report_include_improvements: bool = True  # Включать улучшения в отчет
    report_include_future_plans: bool = True  # Включать планы на будущее в отчет
    report_include_resources: bool = True  # Включать ресурсы в отчет
    report_include_credits: bool = True  # Включать кредиты в отчет
    report_include_license: bool = True  # Включать лицензию в отчет
    report_include_contact: bool = True  # Включать контакты в отчет
    report_include_links: bool = True  # Включать ссылки в отчет
    report_include_hashtags: bool = True  # Включать хэштеги в отчет
    report_include_mentions: bool = True  # Включать упоминания в отчет
    report_include_reactions: bool = True  # Включать реакции в отчет
    report_include_forwards: bool = True  # Включать пересылки в отчет
    report_include_views: bool = True  # Включать просмотры в отчет
    report_include_comments: bool = True  # Включать комментарии в отчет
    report_include_ratings: bool = True  # Включать рейтинги в отчет
    report_include_feedback: bool = True  # Включать обратную связь в отчет

class ForwardingConfigRequest(BaseModel):
    user_id: int
    source_channel_id: Union[int, str]  # Можно передавать id (int) или username (str)
    target_channel_id: Union[int, str]  # Можно передавать id (int) или username (str)
    parse_mode: str = "all"  # "all" или "hashtags"
    hashtag_filter: Optional[str] = None
    delay_seconds: int = 0
    footer_text: str = ""
    # --- Новые поля для гиперссылки в приписке ---
    footer_link: Optional[str] = None  # URL для гиперссылки
    footer_link_text: Optional[str] = None  # Текст, который будет гиперссылкой
    footer_full_link: bool = False  # Превращать ли всю приписку в ссылку
    text_mode: str = "hashtags_only"  # "remove", "as_is", "hashtags_only"
    max_posts: Optional[int] = None
    hide_sender: bool = True
    # --- Новые поля для платных постов ---
    paid_content_mode: str = "off"  # off | hashtag | random | hashtag_random | hashtag_select
    paid_content_stars: int = 0  # Количество звездочек для платного контента (0 = отключено)
    paid_content_hashtag: Optional[str] = None  # Хэштег для платных постов (если режим hashtag, hashtag_random, hashtag_select)
    paid_content_chance: Optional[int] = None  # Шанс (1..10) для рандомного режима
    # --- Новые поля для режимов парсинга ---
    parse_direction: str = "forward"  # "forward" (от старых к новым) или "backward" (от новых к старым)
    media_filter: str = "all"  # "all" (все сообщения) или "media_only" (только с медиа)
    range_mode: str = "all"  # "all" (все сообщения) или "range" (по диапазону)
    range_start_id: Optional[int] = None  # ID сообщения для начала диапазона
    range_end_id: Optional[int] = None  # ID сообщения для конца диапазона 