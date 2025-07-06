# Telegram Parser Bot

Микросервисное приложение для парсинга сообщений из Telegram-каналов.

## Структура проекта

- `bot/` - Telegram бот на Pyrogram для взаимодействия с пользователем
- `parser/` - Микросервис на FastAPI для парсинга сообщений
- `shared/` - Общие компоненты и модели данных

## Установка и запуск

1. Установите зависимости:
```bash
pip install -r requirements.txt
```

2. Настройте конфигурацию в файлах:
- `bot/config.py`
- `parser/config.py`
- `shared/config.py`

3. Запустите сервисы:
```bash
docker-compose up
```

## Требования

- Python 3.8+
- Docker и Docker Compose
- Telegram API credentials 

cd parser
venv/scripts/activate
cd ..
uvicorn parser.main:app --reload

cd bot
venv/scripts/activate
cd ..
python -m bot.bot_main

cd bot
venv/scripts/activate
cd ..
python -m bot.main_new

python parser/main.py