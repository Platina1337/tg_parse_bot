from pydantic_settings import BaseSettings
from dotenv import load_dotenv
import os

load_dotenv()

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