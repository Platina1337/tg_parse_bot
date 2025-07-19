from pydantic_settings import BaseSettings
from dotenv import load_dotenv
import os

# Загружаем .env файл только если он существует
env_file = ".env"
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    # Если .env файл не найден, загружаем переменные окружения из системы
    pass

class ParserConfig(BaseSettings):
    # Telegram API credentials для userbot
    API_ID: int
    API_HASH: str
    PHONE_NUMBER: str
    SESSIONS_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), '../sessions'))
    
    # Настройки сервиса
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DB_PATH: str = "../parser.db"  # относительный путь к корню проекта
    
    class Config:
        env_file = ".env"
        extra = "ignore"

config = ParserConfig() 