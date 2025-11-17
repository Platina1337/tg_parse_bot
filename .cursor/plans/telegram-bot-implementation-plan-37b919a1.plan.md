<!-- 37b919a1-7ce6-4603-916c-db5815680851 6c26ba7d-a657-4c97-9a72-09a86fd73559 -->
# План реализации новых функций для Telegram-бота

Этот план описывает шаги по созданию нового бота для управления доступом к приватному каналу и добавлению функции автоматического проставления реакций после пересылки постов.

## Часть 1: Новый бот для доступа к приватному каналу

В соответствии с вашим запросом, я создам нового бота как отдельный сервис в проекте.

1.  **Создание директории**: Я создам новую директорию `access_bot` в корне проекта по пути `D:\PycharmProjects\telegram-parse-bot\access_bot`.
2.  **Базовая структура**: Внутри `access_bot` я создам файлы, аналогичные структуре существующего `bot`:

    -   `main.py`: Главный файл для запуска бота.
    -   `handlers.py`: Обработчики команд (`/start`, меню оплаты).
    -   `config.py`: Конфигурация (токен бота, данные для платежей).
    -   `database.py`: Логика для работы с базой данных (пользователи, подписки).
    -   `requirements.txt`: Список зависимостей.
    -   `Dockerfile`: Для сборки docker-образа.

3.  **Docker Compose**: Я обновлю файл `docker-compose.yml`, добавив новый сервис `access_bot`, чтобы он запускался вместе с остальными частями проекта.

## Часть 2: Автоматическое проставление реакций после пересылки

Я интегрирую функциональность реакций в существующий процесс пересылки сообщений.

### Шаг 1: Обновление интерфейса настроек (сервис `bot`)

1.  **Добавление кнопки**: В файле `bot/states.py` я добавлю новую кнопку "Реакции" в клавиатуру `get_forwarding_settings_keyboard`.
2.  **Реализация обработчиков**: В `bot/handlers.py`, в функции `forwarding_callback_handler`, я добавлю логику для новой кнопки:

    -   Она будет предлагать включить/выключить реакции.
    -   При включении, бот запросит список эмодзи для использования.
    -   Эти настройки будут сохраняться в `user_states[user_id]['forward_settings']`.

### Шаг 2: Передача настроек в парсер (API)

1.  **Обновление запроса к API**: В файле `bot/core.py` (функция `start_forwarding_parsing_api`) я добавлю новые параметры (`reactions_enabled`, `reaction_emojis`) в тело запроса, отправляемого в сервис парсера.
2.  **Обновление эндпоинта**: В `parser/main.py` я обновлю эндпоинт `/forwarding/parse`, чтобы он мог принимать и обрабатывать новые настройки реакций.

### Шаг 3: Реализация логики в парсере (сервис `parser`)

1.  **Модификация `TelegramForwarder`**: Основные изменения будут в файле `parser/forwarder.py`.

    -   В методе `start_forwarding` я буду извлекать настройки реакций из полученной конфигурации.
    -   В обработчике `handle_new_message`, сразу после успешной пересылки сообщения (после вызова `_forward_single_message`), я добавлю блок кода.
    -   Этот блок будет проверять, включены ли реакции. Если да, он будет вызывать `ReactionManager`, чтобы поставить случайную реакцию из списка на только что пересланный пост. Для этого будет использоваться `reaction_manager.add_reaction`, который задействует все доступные сессии.
    -   Я обеспечу, чтобы ошибки при проставлении реакций логировались, но не прерывали процесс пересылки следующих сообщений.

### To-dos

- [ ] Create a new directory access_bot with a structure similar to the existing bot directory.
- [ ] Add a "Reactions" button to the forwarding settings keyboard in bot/states.py.
- [ ] Implement callback handlers in bot/handlers.py to manage reaction settings (enable/disable, choose emojis).
- [ ] Update the API call in bot/core.py to pass reaction settings to the parser service.
- [ ] Update the parser endpoint in parser/main.py to accept the new reaction settings.
- [ ] Modify TelegramForwarder in parser/forwarder.py to add reactions to forwarded messages based on the settings.