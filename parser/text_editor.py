"""
Модуль для редактирования текста постов в каналах
Позволяет добавлять дополнительные гиперссылки к существующим постам
"""

import asyncio
import logging
import os
from typing import Optional, Dict, Any, List
from pyrogram import Client, enums
from pyrogram.types import Message
from pyrogram.errors import FloodWait, RPCError

logger = logging.getLogger(__name__)


class TextEditor:
    """Класс для редактирования текста постов в каналах"""
    
    def __init__(self, session_manager=None):
        self.session_manager = session_manager
        self._userbot = None
        self._editing_tasks = {}  # task_id -> task_info
        self._task_counter = 0
        
    async def get_userbot(self) -> Optional[Client]:
        """Получение userbot для редактирования"""
        if self._userbot and getattr(self._userbot, 'is_connected', False):
            return self._userbot
            
        if not self.session_manager:
            logger.error(f"[TEXT_EDITOR] session_manager не инициализирован")
            return None
            
        try:
            # Сначала пробуем использовать любой уже подключенный клиент из SessionManager
            logger.info(f"[TEXT_EDITOR] Проверяем уже подключенные клиенты в SessionManager")
            for alias, client in self.session_manager.clients.items():
                if client and getattr(client, 'is_connected', False):
                    logger.info(f"[TEXT_EDITOR] Найден подключенный клиент для сессии: {alias}")
                    self._userbot = client
                    return self._userbot
            
            # Если нет подключенных клиентов, пробуем получить сессии для text_editing
            sessions = await self.session_manager.get_sessions_for_task("text_editing")
            logger.info(f"[TEXT_EDITOR] Сессии для text_editing: {len(sessions) if sessions else 0}")
            
            if not sessions:
                # Если нет назначенных сессий для text_editing, используем все активные сессии
                logger.info(f"[TEXT_EDITOR] Нет назначенных сессий для text_editing, получаем все активные")
                all_sessions = await self.session_manager.get_all_sessions()
                sessions = [s for s in all_sessions if s.is_active]
                logger.info(f"[TEXT_EDITOR] Активные сессии: {len(sessions) if sessions else 0}")
            
            if not sessions:
                logger.error(f"[TEXT_EDITOR] Нет доступных активных сессий для редактирования")
                return None
                
            # Используем первую доступную сессию
            session = sessions[0]
            session_name = session.alias if hasattr(session, 'alias') else session.session_path
            logger.info(f"[TEXT_EDITOR] Используем сессию: {session_name}")
            
            # Используем метод get_client из SessionManager
            logger.info(f"[TEXT_EDITOR] Получаем клиент через SessionManager для сессии {session_name}")
            self._userbot = await self.session_manager.get_client(session_name)
            
            if not self._userbot:
                logger.warning(f"[TEXT_EDITOR] Не удалось получить клиент для сессии {session_name}, пробуем другие сессии")
                # Пробуем получить клиент из любой доступной активной сессии
                for alt_session in sessions[1:]:  # Пропускаем первую, т.к. уже попробовали
                    alt_session_name = alt_session.alias if hasattr(alt_session, 'alias') else alt_session.session_path
                    logger.info(f"[TEXT_EDITOR] Пробуем альтернативную сессию: {alt_session_name}")
                    self._userbot = await self.session_manager.get_client(alt_session_name)
                    if self._userbot:
                        session_name = alt_session_name  # Обновляем имя сессии для логов
                        logger.info(f"[TEXT_EDITOR] Успешно получили клиент для альтернативной сессии: {session_name}")
                        break
                
                if not self._userbot:
                    logger.error(f"[TEXT_EDITOR] Не удалось получить клиент ни для одной из доступных сессий")
                    return None
            
            # Проверяем подключение и подключаемся если нужно
            if not getattr(self._userbot, 'is_connected', False):
                logger.info(f"[TEXT_EDITOR] Подключаем клиент для сессии {session_name}")
                try:
                    await self._userbot.start()
                except Exception as start_error:
                    logger.warning(f"[TEXT_EDITOR] Ошибка при запуске клиента: {start_error}")
                    # Если ошибка связана с блокировкой базы данных, проверяем подключение еще раз
                    if "database is locked" in str(start_error).lower():
                        logger.info(f"[TEXT_EDITOR] База данных заблокирована, проверяем текущее подключение...")
                        # Возможно клиент уже подключен в другом месте
                        if not getattr(self._userbot, 'is_connected', False):
                            logger.error(f"[TEXT_EDITOR] Клиент не подключен и не может быть запущен из-за блокировки БД")
                            return None
                        else:
                            logger.info(f"[TEXT_EDITOR] Клиент уже подключен, продолжаем работу")
                    else:
                        # Для других ошибок прекращаем выполнение
                        logger.error(f"[TEXT_EDITOR] Критическая ошибка запуска клиента: {start_error}")
                        return None
                
            logger.info(f"[TEXT_EDITOR] Userbot успешно инициализирован с сессией: {session_name}")
            return self._userbot
            
        except Exception as e:
            logger.error(f"[TEXT_EDITOR] Ошибка инициализации userbot: {e}", exc_info=True)
            return None
    
    async def start_text_editing(self, channel_id: int, link_text: str, link_url: str, max_posts: int = 100) -> str:
        """
        Запуск редактирования текста постов в канале
        
        Args:
            channel_id: ID канала для редактирования
            link_text: Текст для гиперссылки
            link_url: URL для гиперссылки
            max_posts: Максимальное количество постов для редактирования
            
        Returns:
            str: ID задачи редактирования
        """
        try:
            logger.info(f"[TEXT_EDITOR] Получаем userbot для редактирования канала {channel_id}")
            userbot = await self.get_userbot()
            if not userbot:
                error_msg = "Не удалось получить userbot для редактирования. Проверьте доступность сессий."
                logger.error(f"[TEXT_EDITOR] {error_msg}")
                raise Exception(error_msg)
                
            # Создаем уникальный ID задачи
            self._task_counter += 1
            task_id = f"edit_{self._task_counter}_{channel_id}"
            
            # Сохраняем информацию о задаче
            task_info = {
                'task_id': task_id,
                'channel_id': channel_id,
                'link_text': link_text,
                'link_url': link_url,
                'max_posts': max_posts,
                'status': 'running',
                'processed_count': 0,
                'modified_count': 0,
                'error': None
            }
            
            self._editing_tasks[task_id] = task_info
            
            # Запускаем задачу редактирования
            asyncio.create_task(self._edit_posts_task(task_id, userbot, channel_id, link_text, link_url, max_posts))
            
            logger.info(f"[TEXT_EDITOR] Запущена задача редактирования {task_id} для канала {channel_id}")
            return task_id
            
        except Exception as e:
            logger.error(f"[TEXT_EDITOR] Ошибка запуска редактирования: {e}", exc_info=True)
            raise
    
    async def _edit_posts_task(self, task_id: str, userbot: Client, channel_id: int, 
                              link_text: str, link_url: str, max_posts: int):
        """Основная задача редактирования постов"""
        try:
            task_info = self._editing_tasks[task_id]
            logger.info(f"[TEXT_EDITOR] Начинаем редактирование в канале {channel_id}")
            logger.info(f"[TEXT_EDITOR] Параметры: текст='{link_text}', ссылка='{link_url}', лимит={max_posts}")
            
            # Проверяем подключение userbot
            if not getattr(userbot, 'is_connected', False):
                logger.error(f"[TEXT_EDITOR] Userbot не подключен для задачи {task_id}")
                task_info['status'] = 'failed'
                task_info['error'] = 'Userbot не подключен'
                return
            
            # Проверяем права в канале
            try:
                logger.info(f"[TEXT_EDITOR] Проверяем права в канале {channel_id}")
                chat_member = await userbot.get_chat_member(channel_id, "me")
                logger.info(f"[TEXT_EDITOR] Статус в канале: {chat_member.status}")
                logger.info(f"[TEXT_EDITOR] Права: {chat_member.privileges if hasattr(chat_member, 'privileges') else 'Нет привилегий'}")
                
                # Проверяем информацию о канале
                chat_info = await userbot.get_chat(channel_id)
                logger.info(f"[TEXT_EDITOR] Информация о канале: {chat_info.title}, тип: {chat_info.type}")
                
            except Exception as rights_error:
                logger.warning(f"[TEXT_EDITOR] Не удалось проверить права в канале: {rights_error}")
                # Продолжаем работу, даже если не можем проверить права
            
            processed_posts = 0
            modified_count = 0
            processed_media_groups = set()  # Для отслеживания уже обработанных медиагрупп
            
            # Получаем сообщения из канала (от новых к старым)
            logger.info(f"[TEXT_EDITOR] Получаем историю чата для канала {channel_id}")
            logger.info(f"[TEXT_EDITOR] Лимит постов (не сообщений): {max_posts}")
            
            # Получаем сообщения из канала
            async for message in userbot.get_chat_history(channel_id, limit=max_posts * 50):
                
                try:
                    # Определяем, это новый пост или часть уже обработанного
                    is_new_post = True
                    text_message_id = None  # ID сообщения с текстом для медиагруппы
                    
                    # Если это медиагруппа, проверяем, не обрабатывали ли мы её уже
                    if message.media_group_id:
                        if message.media_group_id in processed_media_groups:
                            logger.info(f"[TEXT_EDITOR] Сообщение {message.id} из уже обработанной медиагруппы {message.media_group_id}, пропускаем")
                            continue
                        else:
                            # Отмечаем эту медиагруппу как обработанную
                            processed_media_groups.add(message.media_group_id)
                            logger.info(f"[TEXT_EDITOR] Начинаем обработку медиагруппы {message.media_group_id}, сообщение {message.id}")
                            
                            # Для медиагруппы нужно найти сообщение с текстом
                            current_text = None
                            text_message_id = None
                            
                            # Получаем все сообщения медиагруппы
                            try:
                                media_group_messages = await userbot.get_media_group(channel_id, message.id)
                                logger.info(f"[TEXT_EDITOR] Найдено {len(media_group_messages)} сообщений в медиагруппе {message.media_group_id}")
                                
                                # Ищем сообщение с текстом в медиагруппе
                                for mg_message in media_group_messages:
                                    if mg_message.text:
                                        current_text = mg_message.text
                                        text_message_id = mg_message.id
                                        logger.info(f"[TEXT_EDITOR] Найден текст в сообщении {mg_message.id} медиагруппы")
                                        break
                                    elif mg_message.caption:
                                        current_text = mg_message.caption
                                        text_message_id = mg_message.id
                                        logger.info(f"[TEXT_EDITOR] Найден caption в сообщении {mg_message.id} медиагруппы")
                                        break
                                
                                if not current_text:
                                    logger.info(f"[TEXT_EDITOR] В медиагруппе {message.media_group_id} не найден текст")
                                    
                            except Exception as mg_error:
                                logger.warning(f"[TEXT_EDITOR] Ошибка при получении медиагруппы: {mg_error}")
                                # Продолжаем с текущим сообщением
                                if message.text:
                                    current_text = message.text
                                elif message.caption:
                                    current_text = message.caption
                    else:
                        # Это одиночное сообщение - новый пост
                        logger.info(f"[TEXT_EDITOR] Обрабатываем одиночное сообщение {message.id}")
                        
                        # Проверяем, есть ли текст в одиночном сообщении
                        current_text = None
                        if message.text:
                            current_text = message.text
                        elif message.caption:
                            current_text = message.caption
                    
                    # Увеличиваем счетчик постов (не сообщений)
                    processed_posts += 1
                    task_info['processed_count'] = processed_posts
                    
                    # Проверяем лимит постов
                    if processed_posts > max_posts:
                        logger.info(f"[TEXT_EDITOR] Достигнут лимит постов ({max_posts}), завершаем обработку")
                        break
                    
                    # Проверяем, есть ли текст в сообщении
                    if not current_text:
                        message_type = "unknown"
                        if message.photo:
                            message_type = "photo"
                        elif message.video:
                            message_type = "video"
                        elif message.document:
                            message_type = "document"
                        elif message.animation:
                            message_type = "animation"
                        elif message.sticker:
                            message_type = "sticker"
                        elif message.voice:
                            message_type = "voice"
                        elif message.video_note:
                            message_type = "video_note"
                        elif message.audio:
                            message_type = "audio"
                        elif message.text:
                            message_type = "text"
                        elif message.caption:
                            message_type = "caption"
                            
                        logger.info(f"[TEXT_EDITOR] Сообщение {message.id} типа '{message_type}' не содержит текста, пропускаем")
                        continue
                    
                    # Проверяем, нужно ли редактировать сообщение
                    logger.info(f"[TEXT_EDITOR] Проверяем сообщение {message.id}: текст длиной {len(current_text)} символов")
                    logger.info(f"[TEXT_EDITOR] Первые 100 символов: {current_text[:100]}...")
                    logger.info(f"[TEXT_EDITOR] Автор сообщения: {message.from_user.username if message.from_user else 'Канал'}")
                    logger.info(f"[TEXT_EDITOR] ID автора: {message.from_user.id if message.from_user else 'N/A'}")
                    logger.info(f"[TEXT_EDITOR] Дата сообщения: {message.date}")
                    logger.info(f"[TEXT_EDITOR] Полный текст сообщения {message.id}: {current_text}")
                    
                    if self._should_edit_message(current_text, link_text):
                        # Определяем ID сообщения для редактирования
                        edit_message_id = text_message_id if text_message_id else message.id
                        is_text_message = text_message_id and text_message_id != message.id
                        
                        logger.info(f"[TEXT_EDITOR] Сообщение {edit_message_id} подходит для редактирования")
                        # Создаем новый текст с добавленной ссылкой
                        new_text = self._add_link_to_text(current_text, link_text, link_url)
                        logger.info(f"[TEXT_EDITOR] Новый текст для сообщения {edit_message_id}: {new_text[:200]}...")
                        
                        # Редактируем сообщение
                        if await self._edit_message_text(userbot, channel_id, edit_message_id, new_text, is_text_message):
                            modified_count += 1
                            task_info['modified_count'] = modified_count
                            logger.info(f"[TEXT_EDITOR] Отредактировано сообщение {edit_message_id}")
                        else:
                            logger.warning(f"[TEXT_EDITOR] Не удалось отредактировать сообщение {edit_message_id}")
                        
                        # Небольшая задержка между редактированиями
                        await asyncio.sleep(1)
                    else:
                        logger.info(f"[TEXT_EDITOR] Сообщение {message.id} не подходит для редактирования")
                        logger.info(f"[TEXT_EDITOR] Причина: не выполнены условия (хэштеги, TSSH_link, или уже есть новая ссылка)")
                        
                except FloodWait as e:
                    logger.warning(f"[TEXT_EDITOR] FloodWait {e.value} секунд при обработке сообщения {message.id}")
                    await asyncio.sleep(e.value)
                    continue
                    
                except Exception as e:
                    logger.error(f"[TEXT_EDITOR] Ошибка при обработке сообщения {message.id}: {e}")
                    continue
            
            # Завершаем задачу
            task_info['status'] = 'completed'
            logger.info(f"[TEXT_EDITOR] Задача {task_id} завершена. Обработано постов: {processed_posts}, изменено: {modified_count}")
            logger.info(f"[TEXT_EDITOR] Медиагрупп обработано: {len(processed_media_groups)}")
            
        except Exception as e:
            logger.error(f"[TEXT_EDITOR] Ошибка в задаче редактирования {task_id}: {e}")
            self._editing_tasks[task_id]['status'] = 'error'
            self._editing_tasks[task_id]['error'] = str(e)
    
    def _should_edit_message(self, text: str, link_text: str) -> bool:
        """
        Проверяет, нужно ли редактировать сообщение
        
        Args:
            text: Текущий текст сообщения
            link_text: Текст ссылки который мы хотим добавить
            
        Returns:
            bool: True если сообщение нужно редактировать
        """
        # Проверяем только наличие _TSSH_Fans_ и отсутствие новой ссылки
        has_tssh_link = '_TSSH_Fans_' in text or 'TSSH_Fans' in text
        already_has_new_link = link_text in text
        
        logger.info(f"[TEXT_EDITOR] Проверка условий: TSSH_link={has_tssh_link}, уже_есть_ссылка={already_has_new_link}")
        
        # Редактируем только если есть TSSH ссылка, но нет нашей новой ссылки
        result = has_tssh_link and not already_has_new_link
        
        # Детальное логирование причин
        if not has_tssh_link:
            logger.info(f"[TEXT_EDITOR] Сообщение не подходит: нет _TSSH_Fans_")
        elif already_has_new_link:
            logger.info(f"[TEXT_EDITOR] Сообщение не подходит: уже есть новая ссылка '{link_text}'")
        else:
            logger.info(f"[TEXT_EDITOR] Сообщение подходит для редактирования")
            
        logger.info(f"[TEXT_EDITOR] Результат проверки: {result}")
        return result
    
    def _add_link_to_text(self, original_text: str, link_text: str, link_url: str) -> str:
        """
        Добавляет новую гиперссылку к тексту, сохраняя существующие ссылки
        
        Args:
            original_text: Исходный текст
            link_text: Текст для ссылки
            link_url: URL для ссылки
            
        Returns:
            str: Новый текст с добавленной ссылкой
        """
        # Проверяем, есть ли уже ссылка _TSSH_Fans_ в тексте
        if '_TSSH_Fans_' in original_text:
            # Если есть _TSSH_Fans_, добавляем новую ссылку с HTML-разметкой
            # но сохраняем существующую ссылку _TSSH_Fans_
            new_link = f'<a href="{link_url}">{link_text}</a>'
            # Заменяем _TSSH_Fans_ на HTML-ссылку, если её ещё нет
            if '<a href="https://t.me/TESAMSH/' not in original_text:
                # Если _TSSH_Fans_ не является HTML-ссылкой, делаем её кликабельной
                original_text = original_text.replace('_TSSH_Fans_', '<a href="https://t.me/TESAMSH/4026">_TSSH_Fans_</a>')
            return f"{original_text}\n\n{new_link}"
        else:
            # Если нет _TSSH_Fans_, используем HTML-разметку для новой ссылки
            new_link = f'<a href="{link_url}">{link_text}</a>'
            return f"{original_text}\n{new_link}"
    
    async def _edit_message_text(self, userbot: Client, channel_id: int, message_id: int, 
                                new_text: str, is_text_message: bool) -> bool:
        """
        Редактирует текст сообщения
        
        Args:
            userbot: Pyrogram клиент
            channel_id: ID канала
            message_id: ID сообщения
            new_text: Новый текст
            is_text_message: True если это текстовое сообщение, False если caption
            
        Returns:
            bool: True если сообщение успешно отредактировано
        """
        try:
            # Пытаемся отредактировать сообщение
            if is_text_message:
                await userbot.edit_message_text(
                    chat_id=channel_id,
                    message_id=message_id,
                    text=new_text,
                    parse_mode=enums.ParseMode.HTML
                )
            else:
                await userbot.edit_message_caption(
                    chat_id=channel_id,
                    message_id=message_id,
                    caption=new_text,
                    parse_mode=enums.ParseMode.HTML
                )
            logger.info(f"[TEXT_EDITOR] Сообщение {message_id} успешно отредактировано")
            return True
            
        except RPCError as e:
            error_msg = str(e)
            if "MESSAGE_ID_INVALID" in error_msg:
                logger.warning(f"[TEXT_EDITOR] Сообщение {message_id} недоступно для редактирования (ID недействителен)")
            elif "MESSAGE_NOT_MODIFIED" in error_msg:
                logger.info(f"[TEXT_EDITOR] Сообщение {message_id} не изменено (содержимое идентично)")
                return True  # Считаем это успехом
            elif "CHAT_ADMIN_REQUIRED" in error_msg:
                logger.error(f"[TEXT_EDITOR] Нет прав администратора для редактирования сообщений в канале {channel_id}")
            else:
                logger.error(f"[TEXT_EDITOR] RPC ошибка при редактировании сообщения {message_id}: {e}")
            return False
            
        except Exception as e:
            logger.error(f"[TEXT_EDITOR] Ошибка при редактировании сообщения {message_id}: {e}")
            return False
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Получение статуса задачи редактирования"""
        return self._editing_tasks.get(task_id)
    
    def get_all_tasks(self) -> List[Dict[str, Any]]:
        """Получение всех задач редактирования"""
        return list(self._editing_tasks.values())
    
    def stop_task(self, task_id: str) -> bool:
        """
        Остановка задачи редактирования
        
        Args:
            task_id: ID задачи
            
        Returns:
            bool: True если задача была остановлена
        """
        if task_id in self._editing_tasks:
            task_info = self._editing_tasks[task_id]
            if task_info['status'] == 'running':
                task_info['status'] = 'stopped'
                logger.info(f"[TEXT_EDITOR] Задача {task_id} остановлена")
                return True
        return False
    
    async def cleanup(self):
        """Очистка ресурсов"""
        if self._userbot and getattr(self._userbot, 'is_connected', False):
            await self._userbot.stop()
        self._editing_tasks.clear()