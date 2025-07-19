import logging
import re
from typing import Optional, Dict, List
from pyrogram import Client
from pyrogram.types import Message, BotCommand
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from bot.states import user_states, FSM_MAIN_MENU
from bot.core import api_client, show_main_menu
from bot.handlers import safe_edit_message, safe_edit_callback_message, resolve_channel, resolve_group

logger = logging.getLogger(__name__)

def safe_markdown_text(text: str) -> str:
    """Безопасно экранирует текст для markdown"""
    if not text:
        return ""
    # Экранируем специальные символы markdown
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

# Состояния FSM для публичных групп
FSM_PUBLIC_GROUPS_SOURCE = "public_groups_source"
FSM_PUBLIC_GROUPS_TARGET = "public_groups_target"
FSM_PUBLIC_GROUPS_SETTINGS = "public_groups_settings"

async def start_public_groups_manager(client: Client, message: Message):
    """Запуск менеджера публичных групп"""
    user_id = message.from_user.id
    
    # Сбрасываем состояние
    user_states[user_id] = {**user_states.get(user_id, {}), "state": FSM_PUBLIC_GROUPS_SOURCE}
    
    # Показываем меню выбора исходного канала
    kb = await get_source_channel_keyboard(user_id)
    sent = await message.reply(
        "🎯 **Менеджер публичных групп**\n\n"
        "Выберите канал-источник для пересылки в публичные группы:",
        reply_markup=kb or ReplyKeyboardRemove()
    )
    
    if sent is not None:
        user_states[user_id]["last_msg_id"] = sent.id

async def handle_public_groups_text(client: Client, message: Message) -> bool:
    """Обработчик текстовых сообщений для менеджера публичных групп"""
    user_id = message.from_user.id
    text = message.text.strip()
    state = user_states.get(user_id, {}).get('state')
    
    # Обработка состояний публичных групп
    if state and state.startswith('public_groups_'):
        if state == FSM_PUBLIC_GROUPS_SOURCE:
            return await handle_source_selection(client, message)
        elif state == FSM_PUBLIC_GROUPS_TARGET:
            return await handle_target_selection(client, message)
        elif state == FSM_PUBLIC_GROUPS_SETTINGS:
            return await handle_settings_input(client, message)
    
    return False

async def handle_source_selection(client: Client, message: Message) -> bool:
    """Обработка выбора исходного канала"""
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == "Назад":
        await show_main_menu(client, message, "Выберите действие:")
        return True
    
    # Парсим выбор канала из истории
    match = re.match(r"(.+) \(ID: (-?\d+)(?:, @(\w+))?\)", text)
    if match:
        channel_title = match.group(1)
        channel_id = match.group(2)
        username = match.group(3)
        
        user_states[user_id]["public_source_id"] = int(channel_id)
        user_states[user_id]["public_source_title"] = channel_title
        if username:
            user_states[user_id]["public_source_username"] = username
    else:
        # Пользователь ввел новый канал
        channel_id, channel_title, channel_username = await resolve_channel(api_client, text)
        
        if not channel_id:
            sent = await message.reply("❌ Не удалось определить канал. Введите корректный username или ID.")
            if sent is not None:
                user_states[user_id]["last_msg_id"] = sent.id
            return True
        
        user_states[user_id]["public_source_id"] = int(channel_id)
        user_states[user_id]["public_source_title"] = channel_title
        if channel_username:
            user_states[user_id]["public_source_username"] = channel_username
    
    # Переходим к выбору публичных групп
    await show_public_groups_selection(client, message)
    return True

async def handle_target_selection(client: Client, message: Message) -> bool:
    """Обработка выбора групп"""
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == "Назад":
        kb = await get_source_channel_keyboard(user_id)
        sent = await message.reply(
            "Выберите канал-источник:",
            reply_markup=kb or ReplyKeyboardRemove()
        )
        if sent is not None:
            user_states[user_id]["last_msg_id"] = sent.id
        user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_SOURCE
        return True
    
    # Проверяем, не выбрана ли группа из истории
    try:
        groups = await api_client.get_user_groups(user_id)
        for group in groups:
            group_id = group.get('group_id', '')
            group_title = group.get('group_title', 'Без названия')
            username = group.get('username', '')
            
            # Проверяем разные варианты отображения группы
            possible_texts = [
                f"{group_title} (@{username})" if username else f"{group_title} [ID: {group_id}]",
                f"{group_title} (@{username})" if username else f"{group_title}",
                f"@{username}" if username else f"{group_id}"
            ]
            
            if text in possible_texts:
                user_states[user_id]["public_target_id"] = group_id
                user_states[user_id]["public_target_name"] = group_title
                
                # Обновляем время последнего использования
                await api_client.update_user_group_last_used(user_id, str(group_id))
                
                # Показываем настройки
                await show_public_groups_settings(client, message)
                return True
    except Exception as e:
        logger.error(f"[PUBLIC_GROUPS] Ошибка при поиске группы: {e}")
    
    # Если группа не найдена в истории, пробуем добавить новую
    try:
        # Разрешаем группу (получаем ID, title, username)
        group_id, group_title, username = await resolve_group(api_client, text)
        
        if not group_id:
            sent = await message.reply("❌ Не удалось найти группу. Проверьте ID или username группы.")
            if sent is not None:
                user_states[user_id]["last_msg_id"] = sent.id
            return True
        
        # Добавляем группу в базу данных
        await api_client.add_user_group(user_id, str(group_id), group_title, username)
        
        # Обновляем время последнего использования
        await api_client.update_user_group_last_used(user_id, str(group_id))
        
        # Сохраняем выбранную группу
        user_states[user_id]["public_target_id"] = str(group_id)
        user_states[user_id]["public_target_name"] = group_title
        
        try:
            await message.reply(
                f"✅ Группа успешно добавлена и выбрана!\n\n"
                f"📋 **Название:** {safe_markdown_text(group_title)}\n"
                f"🆔 **ID:** {group_id}\n"
                f"🔗 **Username:** @{username if username else 'Нет'}\n\n"
                f"Группа сохранена в вашей истории.",
                parse_mode="markdown"
            )
        except Exception as e:
            logger.error(f"[PUBLIC_GROUPS] Ошибка markdown парсинга: {e}")
            # Пробуем без markdown
            await message.reply(
                f"✅ Группа успешно добавлена и выбрана!\n\n"
                f"📋 Название: {group_title}\n"
                f"🆔 ID: {group_id}\n"
                f"🔗 Username: @{username if username else 'Нет'}\n\n"
                f"Группа сохранена в вашей истории."
            )
        
        # Показываем настройки
        await show_public_groups_settings(client, message)
        return True
        
    except Exception as e:
        logger.error(f"[PUBLIC_GROUPS] Ошибка при добавлении группы: {e}")
        sent = await message.reply(f"❌ Ошибка при добавлении группы: {str(e)}")
        if sent is not None:
            user_states[user_id]["last_msg_id"] = sent.id
        return True

async def handle_settings_input(client: Client, message: Message) -> bool:
    """Обработка ввода настроек"""
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == "Назад":
        await show_public_groups_selection(client, message)
        return True
    
    # Здесь можно добавить обработку различных настроек
    # Пока просто показываем меню настроек
    await show_public_groups_settings(client, message)
    return True

async def show_public_groups_selection(client: Client, message: Message):
    """Показать выбор групп"""
    user_id = message.from_user.id
    
    try:
        # Получаем группы пользователя
        groups = await api_client.get_user_groups(user_id)
        
        if not groups:
            # Если нет групп, показываем приглашение добавить первую
            keyboard = ReplyKeyboardMarkup([
                ["🔙 Назад"]
            ], resize_keyboard=True)
            
            try:
                sent = await message.reply(
                    "📢 **Выберите группу для пересылки**\n\n"
                    "У вас пока нет сохраненных групп.\n\n"
                    "Введите ID группы или username для добавления:\n"
                    "📝 **Примеры:**\n"
                    "• ID группы: `-1001234567890`\n"
                    "• Username: `@mygroup` или `mygroup`",
                    reply_markup=keyboard,
                    parse_mode="markdown"
                )
            except Exception as e:
                logger.error(f"[PUBLIC_GROUPS] Ошибка markdown парсинга: {e}")
                # Пробуем без markdown
                sent = await message.reply(
                    "📢 Выберите группу для пересылки\n\n"
                    "У вас пока нет сохраненных групп.\n\n"
                    "Введите ID группы или username для добавления:\n"
                    "📝 Примеры:\n"
                    "• ID группы: -1001234567890\n"
                    "• Username: @mygroup или mygroup",
                    reply_markup=keyboard
                )
        else:
            # Создаем клавиатуру с группами из БД
            keyboard_buttons = []
            for group in groups:
                group_id = group.get('group_id', '')
                group_title = group.get('group_title', 'Без названия')
                username = group.get('username', '')
                
                # Формируем текст кнопки (без markdown, так как это обычный текст)
                if username:
                    button_text = f"{group_title} (@{username})"
                else:
                    button_text = f"{group_title} [ID: {group_id}]"
                
                keyboard_buttons.append([button_text])
            
            # Добавляем кнопку "Назад"
            keyboard_buttons.append(["🔙 Назад"])
            
            keyboard = ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)
            
            # Формируем текст сообщения
            text = "📢 **Выберите группу для пересылки**\n\n"
            text += "Выберите группу из истории или введите новую:\n\n"
            
            for i, group in enumerate(groups, 1):
                group_id = group.get('group_id', '')
                group_title = safe_markdown_text(group.get('group_title', 'Без названия'))
                username = group.get('username', '')
                last_used = group.get('last_used', '')
                
                if username:
                    text += f"{i}. **{group_title}** (@{username})\n"
                else:
                    text += f"{i}. **{group_title}** [ID: {group_id}]\n"
                
                if last_used:
                    text += f"   📅 Последнее использование: {last_used}\n"
                text += "\n"
            
            text += "Или введите ID/username новой группы:"
            
            try:
                sent = await message.reply(text, reply_markup=keyboard, parse_mode="markdown")
            except Exception as e:
                logger.error(f"[PUBLIC_GROUPS] Ошибка markdown парсинга: {e}")
                # Пробуем без markdown
                text_plain = text.replace('**', '').replace('`', '')
                sent = await message.reply(text_plain, reply_markup=keyboard)
        
        if sent is not None:
            user_states[user_id]["last_msg_id"] = sent.id
        user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_TARGET
        
    except Exception as e:
        logger.error(f"[PUBLIC_GROUPS] Ошибка при получении групп: {e}")
        sent = await message.reply(f"❌ Ошибка при получении списка групп: {str(e)}")
        if sent is not None:
            user_states[user_id]["last_msg_id"] = sent.id
        user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_TARGET

async def show_public_groups_settings(client: Client, message: Message):
    """Показать настройки для публичных групп"""
    user_id = message.from_user.id
    
    source_title = user_states[user_id].get("public_source_title", "Неизвестно")
    target_name = user_states[user_id].get("public_target_name", "Неизвестно")
    
    kb = get_public_groups_settings_keyboard()
    text = f"""
⚙️ **Настройки пересылки в публичные группы**

📤 Источник: {source_title}
📢 Цель: {target_name}

Выберите настройки:
"""
    
    sent = await message.reply(text, reply_markup=kb)
    
    if sent is not None:
        user_states[user_id]["last_msg_id"] = sent.id
    user_states[user_id]["state"] = FSM_PUBLIC_GROUPS_SETTINGS

async def get_source_channel_keyboard(user_id: int) -> Optional[ReplyKeyboardMarkup]:
    """Получить клавиатуру с историей каналов-источников"""
    channels = await api_client.get_user_channels(user_id)
    
    if not channels:
        return None
    
    buttons = []
    for ch in channels:
        title = ch.get('title', '')
        channel_id = ch.get('id', '')
        username = ch.get('username', '')
        if username:
            btn_text = f"{title} (ID: {channel_id}, @{username})"
        else:
            btn_text = f"{title} (ID: {channel_id})"
        buttons.append([KeyboardButton(btn_text)])
    
    buttons.append([KeyboardButton("Назад")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)



def get_public_groups_settings_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура настроек для публичных групп"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Статистика", callback_data="public_stats"),
            InlineKeyboardButton("⚙️ Настройки", callback_data="public_settings")
        ],
        [
            InlineKeyboardButton("▶️ Запустить", callback_data="public_start"),
            InlineKeyboardButton("⏹️ Остановить", callback_data="public_stop")
        ],
        [
            InlineKeyboardButton("🔙 Назад", callback_data="public_back")
        ]
    ])



async def handle_public_groups_callback(client: Client, callback_query) -> bool:
    """Обработчик callback для менеджера публичных групп"""
    data = callback_query.data
    
    if not data.startswith('public_'):
        return False
    
    if data == "public_stats":
        await show_public_stats(client, callback_query)
    elif data == "public_settings":
        await show_public_settings(client, callback_query)
    elif data == "public_start":
        await start_public_forwarding(client, callback_query)
    elif data == "public_stop":
        await stop_public_forwarding(client, callback_query)
    elif data == "public_back":
        await go_back_to_public_groups(client, callback_query)
    
    return True

async def show_public_stats(client: Client, callback_query):
    """Показать статистику публичных групп"""
    user_id = callback_query.from_user.id
    
    try:
        # Получаем все задачи публичных групп
        result = await api_client.get_all_public_groups_tasks()
        
        if result.get("status") == "success":
            tasks = result.get("tasks", [])
            user_tasks = [task for task in tasks if task.get("user_id") == user_id]
            
            if user_tasks:
                total_forwarded = sum(task.get("forwarded_count", 0) for task in user_tasks)
                active_tasks = [task for task in user_tasks if task.get("status") == "running"]
                
                text = f"""
📊 **Статистика пересылки в публичные группы**

📤 Всего переслано: {total_forwarded}
🔄 Активных задач: {len(active_tasks)}
📋 Всего задач: {len(user_tasks)}
"""
            else:
                text = """
📊 **Статистика пересылки в публичные группы**

📤 Переслано: 0
🔄 Активных задач: 0
📋 Всего задач: 0
"""
        else:
            text = f"❌ Ошибка получения статистики: {result.get('message', 'Неизвестная ошибка')}"
        
        await safe_edit_callback_message(callback_query, text)
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики публичных групп: {e}")
        await safe_edit_callback_message(callback_query, f"❌ Ошибка: {str(e)}")

async def show_public_settings(client: Client, callback_query):
    """Показать настройки публичных групп"""
    user_id = callback_query.from_user.id
    
    text = """
⚙️ **Настройки пересылки в публичные группы**

🔍 Режим: Все сообщения
⏱️ Задержка: 0 сек
📝 Приписка: Нет
"""
    
    await safe_edit_callback_message(callback_query, text)

async def start_public_forwarding(client: Client, callback_query):
    """Запустить пересылку в публичные группы"""
    user_id = callback_query.from_user.id
    
    source_id = user_states[user_id].get("public_source_id")
    target_id = user_states[user_id].get("public_target_id")
    
    if not source_id or not target_id:
        await safe_edit_callback_message(callback_query, "❌ Не выбраны источник или цель")
        return
    
    try:
        # Настройки по умолчанию
        settings = {
            "max_posts": 10,
            "delay_seconds": 0,
            "media_filter": "all",
            "footer_text": ""
        }
        
        # Запускаем пересылку через API
        result = await api_client.start_public_groups_forwarding(
            str(source_id),
            target_id,
            user_id,
            settings
        )
        
        if result.get("status") == "success":
            task_id = result.get("task_id")
            user_states[user_id]["public_task_id"] = task_id
            
            text = f"✅ Пересылка запущена!\n\n📤 Источник: {user_states[user_id].get('public_source_title')}\n📢 Цель: {user_states[user_id].get('public_target_name')}\n🆔 Задача: {task_id}"
        else:
            text = f"❌ Ошибка запуска: {result.get('message', 'Неизвестная ошибка')}"
        
        await safe_edit_callback_message(callback_query, text)
        
    except Exception as e:
        logger.error(f"Ошибка запуска пересылки в публичные группы: {e}")
        await safe_edit_callback_message(callback_query, f"❌ Ошибка: {str(e)}")

async def stop_public_forwarding(client: Client, callback_query):
    """Остановить пересылку в публичные группы"""
    user_id = callback_query.from_user.id
    
    task_id = user_states[user_id].get("public_task_id")
    
    if not task_id:
        await safe_edit_callback_message(callback_query, "❌ Нет активной задачи для остановки")
        return
    
    try:
        # Останавливаем пересылку через API
        result = await api_client.stop_public_groups_forwarding(task_id)
        
        if result.get("status") == "success":
            text = "⏹️ Пересылка остановлена"
            # Очищаем task_id из состояния
            user_states[user_id].pop("public_task_id", None)
        else:
            text = f"❌ Ошибка остановки: {result.get('message', 'Неизвестная ошибка')}"
        
        await safe_edit_callback_message(callback_query, text)
        
    except Exception as e:
        logger.error(f"Ошибка остановки пересылки в публичные группы: {e}")
        await safe_edit_callback_message(callback_query, f"❌ Ошибка: {str(e)}")

async def go_back_to_public_groups(client: Client, callback_query):
    """Вернуться к выбору публичных групп"""
    user_id = callback_query.from_user.id
    
    await show_public_groups_selection(client, callback_query.message)

 