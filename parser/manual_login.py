import os
import sys
from pyrogram import Client
from parser.database import Database
from shared.models import SessionMeta
import asyncio
from datetime import datetime

if __name__ == "__main__":
    session_name = input("Введите название сессии (alias): ").strip()
    api_id = int(input("Введите API ID: ").strip())
    api_hash = input("Введите API HASH: ").strip()
    phone = input("Введите номер телефона (в международном формате): ").strip()

    # Путь к папке сессий
    sessions_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sessions"))
    os.makedirs(sessions_dir, exist_ok=True)
    session_path = os.path.join(sessions_dir, session_name)

    app = Client(session_path, api_id=api_id, api_hash=api_hash, phone_number=phone)
    print(f"\n--- Запуск Pyrogram для авторизации сессии '{session_name}' ---\n")
    app.start()
    print("\nСессия успешно создана!")
    app.stop()

    # Добавляем запись в БД
    async def add_to_db():
        db = Database()
        await db.init()
        session = SessionMeta(
            id=0,
            alias=session_name,
            api_id=api_id,
            api_hash=api_hash,
            phone=phone,
            session_path=session_path,
            is_active=True,
            created_at=datetime.now(),
            last_used_at=None,
            assigned_task=None,
            notes=None
        )
        await db.create_session(session)
        await db.close()
        print(f"\nСессия '{session_name}' добавлена в БД!\n")
    asyncio.run(add_to_db()) 