import os
import sqlite3
import shutil

def check_session_file():
    session_path = '../sessions/egor_ka.session'
    print(f"=== ДИАГНОСТИКА ФАЙЛА СЕССИИ ===")
    print(f"Файл: {session_path}")
    print(f"Абсолютный путь: {os.path.abspath(session_path)}")
    print(f"Существует: {os.path.exists(session_path)}")
    
    if not os.path.exists(session_path):
        print("❌ Файл не существует!")
        return False
    
    print(f"Размер: {os.path.getsize(session_path)} байт")
    print(f"Доступен для чтения: {os.access(session_path, os.R_OK)}")
    print(f"Доступен для записи: {os.access(session_path, os.W_OK)}")
    
    # Проверяем, не заблокирован ли файл
    try:
        with open(session_path, 'rb') as f:
            f.read(1)
        print("✅ Файл можно открыть для чтения")
    except Exception as e:
        print(f"❌ Ошибка при открытии файла: {e}")
        return False
    
    # Проверяем SQLite структуру
    try:
        conn = sqlite3.connect(session_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        print(f"✅ SQLite таблицы: {tables}")
        
        # Проверяем содержимое таблицы sessions
        if ('sessions',) in tables:
            cursor.execute("SELECT * FROM sessions LIMIT 1")
            session_data = cursor.fetchone()
            print(f"✅ Данные сессии: {session_data}")
        
        conn.close()
    except Exception as e:
        print(f"❌ Ошибка при работе с SQLite: {e}")
        return False
    
    return True

def test_pyrogram_with_session():
    print(f"\n=== ТЕСТИРОВАНИЕ PYROGRAM ===")
    try:
        from pyrogram import Client
        import asyncio
        
        # Создаем клиент с правильным путем
        client = Client(
            name='../sessions/egor_ka',  # Без расширения .session
            api_id=os.getenv("API_ID"),
            api_hash=os.getenv("API_HASH")
        )
        print("✅ Клиент создан успешно")
        
        async def test_start():
            try:
                await client.start()
                print("✅ Клиент запущен успешно")
                me = await client.get_me()
                print(f"✅ Авторизован как: {me.first_name}")
                await client.stop()
                print("✅ Клиент остановлен")
                return True
            except Exception as e:
                print(f"❌ Ошибка при запуске: {e}")
                return False
        
        result = asyncio.run(test_start())
        return result
        
    except Exception as e:
        print(f"❌ Ошибка при создании клиента: {e}")
        return False

def check_environment():
    print(f"\n=== ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ===")
    api_id = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    
    print(f"API_ID: {'✅ Установлен' if api_id else '❌ НЕ УСТАНОВЛЕН'}")
    print(f"API_HASH: {'✅ Установлен' if api_hash else '❌ НЕ УСТАНОВЛЕН'}")
    
    if api_id and api_hash:
        print(f"API_ID значение: {api_id}")
        print(f"API_HASH начало: {api_hash[:10]}...")
        return True
    else:
        print("❌ Переменные окружения не установлены!")
        return False

if __name__ == "__main__":
    print("Начинаем диагностику...")
    
    env_ok = check_environment()
    if not env_ok:
        print("\n❌ ПРОБЛЕМА: Переменные окружения не установлены!")
        print("Нужно создать файл .env в корне проекта с API_ID и API_HASH")
        exit(1)
    
    file_ok = check_session_file()
    if not file_ok:
        print("\n❌ ПРОБЛЕМА: Файл сессии поврежден или недоступен!")
        exit(1)
    
    pyrogram_ok = test_pyrogram_with_session()
    if not pyrogram_ok:
        print("\n❌ ПРОБЛЕМА: Pyrogram не может использовать файл сессии!")
        print("Возможные причины:")
        print("1. Файл сессии поврежден")
        print("2. Неправильные API_ID/API_HASH")
        print("3. Файл заблокирован другим процессом")
        exit(1)
    
    print("\n✅ ВСЕ ПРОВЕРКИ ПРОШЛИ УСПЕШНО!")
    print("Файл сессии должен работать корректно") 