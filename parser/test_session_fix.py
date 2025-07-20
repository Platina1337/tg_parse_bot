import asyncio
import os
import sys

# Добавляем путь к корню проекта
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import Database
from session_manager import SessionManager

async def test_sessions():
    print("=== Тест работы с сессиями ===")
    
    # Инициализируем базу данных
    db = Database()
    await db.init()
    print("✅ База данных инициализирована")
    
    # Проверяем путь к сессиям
    sessions_dir = os.path.abspath("../sessions")
    print(f"📁 Путь к сессиям: {sessions_dir}")
    print(f"📁 Папка существует: {os.path.exists(sessions_dir)}")
    
    if os.path.exists(sessions_dir):
        files = [f for f in os.listdir(sessions_dir) if f.endswith('.session')]
        print(f"📄 Найдено .session файлов: {len(files)}")
        for file in files:
            print(f"   - {file}")
    
    # Инициализируем session manager
    session_manager = SessionManager(db=db, session_dir=sessions_dir)
    print("✅ SessionManager создан")
    
    # Импортируем сессии из файлов
    imported = await session_manager.import_sessions_from_files()
    print(f"📥 Импортировано сессий: {imported}")
    
    # Загружаем клиентов
    await session_manager.load_clients()
    print("✅ Клиенты загружены")
    
    # Получаем все сессии
    sessions = await session_manager.get_all_sessions()
    print(f"📋 Всего сессий в БД: {len(sessions)}")
    for session in sessions:
        print(f"   - {session.alias} (активна: {session.is_active})")
    
    # Назначаем сессии на задачи
    if sessions:
        # Назначаем первую сессию на parsing
        result = await session_manager.assign_task(sessions[0].alias, "parsing")
        print(f"📝 Назначение на parsing: {result}")
        
        # Назначаем первую сессию на monitoring
        result = await session_manager.assign_task(sessions[0].alias, "monitoring")
        print(f"📝 Назначение на monitoring: {result}")
    
    # Получаем сессии для задач
    parsing_sessions = await session_manager.get_sessions_for_task("parsing")
    print(f"🔍 Сессий для parsing: {len(parsing_sessions)}")
    
    monitoring_sessions = await session_manager.get_sessions_for_task("monitoring")
    print(f"🔍 Сессий для monitoring: {len(monitoring_sessions)}")
    
    # Тестируем получение клиента
    if sessions:
        client = await session_manager.get_client(sessions[0].alias)
        print(f"🔌 Клиент получен: {client is not None}")
        if client:
            print(f"   - Имя клиента: {getattr(client, 'name', 'N/A')}")
    
    await db.close()
    print("✅ Тест завершен")

if __name__ == "__main__":
    asyncio.run(test_sessions()) 