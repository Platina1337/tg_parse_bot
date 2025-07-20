import os

print("=== ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ===")
api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")

print(f"API_ID: {api_id}")
print(f"API_HASH: {api_hash}")

if not api_id or not api_hash:
    print("\n❌ ПРОБЛЕМА: Переменные окружения не установлены!")
    print("Нужно создать файл .env в корне проекта")
    print("Содержимое .env файла:")
    print("API_ID=ваш_api_id")
    print("API_HASH=ваш_api_hash")
    print("PHONE_NUMBER=ваш_номер_телефона")
else:
    print("\n✅ Переменные окружения установлены")
    print(f"API_ID: {api_id}")
    print(f"API_HASH: {api_hash[:10]}...") 