import json
import os
from typing import Dict, Optional, List
from shared.models import ParseConfig, PostingSettings
from bot.api_client import api_client
from dotenv import load_dotenv

load_dotenv()

# Путь к файлу с настройками
SETTINGS_FILE = "user_settings.json"
DB_PATH = os.path.abspath(os.getenv("POSTING_TEMPLATES_DB", "parser.db"))

def load_settings() -> Dict:
    """Загрузка настроек из файла"""
    if not os.path.exists(SETTINGS_FILE):
        return {}
    
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка при загрузке настроек: {e}")
        return {}

def save_settings(settings: Dict):
    """Сохранение настроек в файл"""
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

def get_user_settings(user_id: int) -> Dict:
    """Получение настроек пользователя"""
    settings = load_settings()
    return settings.get(str(user_id), {})

def update_user_settings(user_id: int, **kwargs):
    """Обновление настроек пользователя"""
    settings = load_settings()
    user_settings = settings.get(str(user_id), {})
    user_settings.update(kwargs)
    settings[str(user_id)] = user_settings
    save_settings(settings)

def clear_user_settings(user_id: int):
    """Очистка настроек пользователя"""
    settings = load_settings()
    if str(user_id) in settings:
        del settings[str(user_id)]
        save_settings(settings)

async def ensure_templates_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS posting_templates (
                user_id INTEGER,
                name TEXT,
                settings_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, name)
            )
        ''')
        await db.commit()

async def get_user_templates(user_id: int) -> List[Dict]:
    print(f"[TEMPLATE][DEBUG] get_user_templates: DB_PATH={DB_PATH}")
    await ensure_templates_table()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT name, settings_json FROM posting_templates WHERE user_id = ?", (user_id,)) as cursor:
                templates = []
                async for row in cursor:
                    templates.append({
                        "name": row[0],
                        "settings": json.loads(row[1])
                    })
                print(f"[TEMPLATE][DEBUG] get_user_templates: найдено {len(templates)} шаблонов для user_id={user_id}")
                return templates
    except Exception as e:
        print(f"[TEMPLATE][ERROR] get_user_templates: {e}")
        return []

async def save_user_template(user_id: int, name: str, settings: dict):
    print(f"[TEMPLATE][DEBUG] save_user_template: DB_PATH={DB_PATH}, user_id={user_id}, name={name}, settings={settings}")
    await ensure_templates_table()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            print(f"[TEMPLATE][DEBUG] TRY INSERT INTO posting_templates (user_id, name, settings_json)")
            await db.execute(
                "INSERT OR REPLACE INTO posting_templates (user_id, name, settings_json) VALUES (?, ?, ?)",
                (user_id, name, json.dumps(settings, ensure_ascii=False))
            )
            await db.commit()
            print(f"[TEMPLATE][DEBUG] save_user_template: шаблон '{name}' сохранён для user_id={user_id}")
    except Exception as e:
        print(f"[TEMPLATE][ERROR] save_user_template: {e}")

async def delete_user_template(user_id: int, name: str):
    await ensure_templates_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM posting_templates WHERE user_id = ? AND name = ?",
            (user_id, name)
        )
        await db.commit()

async def update_user_template(user_id: int, name: str, new_settings: dict):
    await save_user_template(user_id, name, new_settings)

async def save_posting_template(user_id: int, name: str, settings: dict):
    """Сохранить шаблон публикации"""
    try:
        await api_client.save_user_posting_template(user_id, name, settings)
        return True
    except Exception as e:
        print(f"[ERROR] Ошибка сохранения шаблона публикации: {e}")
        return False

async def get_posting_templates(user_id: int):
    """Получить шаблоны публикации пользователя"""
    try:
        return await api_client.get_user_posting_templates(user_id)
    except Exception as e:
        print(f"[ERROR] Ошибка получения шаблонов публикации: {e}")
        return []

async def delete_posting_template(user_id: int, name: str):
    """Удалить шаблон публикации"""
    try:
        await api_client.delete_user_posting_template(user_id, name)
        return True
    except Exception as e:
        print(f"[ERROR] Ошибка удаления шаблона публикации: {e}")
        return False 