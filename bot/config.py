from pydantic_settings import BaseSettings
from dotenv import load_dotenv
import os
from typing import ClassVar, List

# Определяем, какой .env файл использовать
env_file = os.getenv("ENV_FILE", ".env")
# Загружаем .env файл только если он существует
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    # Если .env файл не найден, загружаем переменные окружения из системы
    pass

class BotConfig(BaseSettings):
    # Telegram API credentials
    API_ID: int
    API_HASH: str
    BOT_TOKEN: str
    
    # Parser service URL
    PARSER_SERVICE_URL: str = "http://127.0.0.1:8000"  # Изменил дефолт на локальный
    
    class Config:
        env_file = ".env"
        extra = "ignore"

# List of Telegram user IDs who are considered admins
ADMIN_IDS: List[int] = [8185973411, 7657388967, 7879908185]  # TODO: Replace with your real Telegram user IDs

config = BotConfig() 