from pydantic_settings import BaseSettings
from dotenv import load_dotenv
import os

# Определяем, какой .env файл использовать
env_file = os.getenv("ENV_FILE", ".env")
load_dotenv(env_file)

class BotConfig(BaseSettings):
    # Telegram API credentials
    API_ID: int
    API_HASH: str
    BOT_TOKEN: str
    
    # Parser service URL
    PARSER_SERVICE_URL: str = "http://127.0.0.1:8000"  # Изменил дефолт на локальный
    
    class Config:
        env_file = env_file  # Используем выбранный .env файл
        extra = "ignore"

config = BotConfig() 