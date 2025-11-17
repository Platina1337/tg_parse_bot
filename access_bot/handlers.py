from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
import logging
import aiosqlite
from .config import ADMIN_ID, DATABASE_URL, PRIVATE_CHANNEL_ID
from .states import PaymentStates, SubscriptionType, PaymentMethod

# Глобальное хранилище состояний пользователей
user_states = {}

logger = logging.getLogger(__name__)

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Красивое приветствие с выбором типа доступа."""
    user = update.effective_user

    welcome_text = f"""🎉 Добро пожаловать, {user.first_name}!

🚀 **Бот управления доступом к премиум-каналу**

Выберите желаемый тип подписки:"""

    keyboard = [
        [InlineKeyboardButton("🆓 Пробный доступ (3 дня)", callback_data="subscription_trial")],
        [InlineKeyboardButton("⭐️ Месяц (200⭐️)", callback_data="subscription_monthly")],
        [InlineKeyboardButton("💎 Навсегда (700₽)", callback_data="subscription_permanent")],
        [InlineKeyboardButton("ℹ️ Справка", callback_data="help")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        welcome_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора типа подписки."""
    query = update.callback_query
    await query.answer()

    subscription_type = query.data.replace("subscription_", "")

    if subscription_type == "trial":
        await handle_trial_subscription(query, context)
    elif subscription_type == "monthly":
        await handle_monthly_subscription(query, context)
    elif subscription_type == "permanent":
        await handle_permanent_subscription(query, context)
    elif subscription_type == "help":
        await handle_help(query, context)

async def handle_trial_subscription(query, context) -> None:
    """Обработка пробного доступа."""
    text = """🆓 **Пробный доступ на 3 дня**

✅ Бесплатно
⏰ Срок: 3 дня
🔓 Полный доступ к премиум-каналу

Хотите активировать пробный доступ?"""

    keyboard = [
        [InlineKeyboardButton("✅ Активировать", callback_data="activate_trial")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text,
        reply_markup=reply_markup
    )

async def handle_monthly_subscription(query, context) -> None:
    """Обработка месячной подписки."""
    text = """⭐️ **Месячная подписка**

💰 Стоимость: 200⭐️ (звезд Telegram)
⏰ Срок: 30 дней
🔓 Полный доступ к премиум-каналу

Выберите способ оплаты:"""

    keyboard = [
        [InlineKeyboardButton("⭐️ Звезды Telegram", callback_data="pay_stars_monthly")],
        [InlineKeyboardButton("💳 Перевод на карту", callback_data="pay_card_monthly")],
        [InlineKeyboardButton("₿ Криптовалюта", callback_data="pay_crypto_monthly")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text,
        reply_markup=reply_markup
    )

async def handle_permanent_subscription(query, context) -> None:
    """Обработка постоянной подписки."""
    text = """💎 **Постоянная подписка**

💰 Стоимость: 700₽
⏰ Срок: навсегда
🔓 Полный доступ к премиум-каналу

Выберите способ оплаты:"""

    keyboard = [
        [InlineKeyboardButton("⭐️ Звезды Telegram", callback_data="pay_stars_permanent")],
        [InlineKeyboardButton("💳 Перевод на карту", callback_data="pay_card_permanent")],
        [InlineKeyboardButton("₿ Криптовалюта", callback_data="pay_crypto_permanent")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text,
        reply_markup=reply_markup
    )

async def handle_help(query, context) -> None:
    """Показать справку."""
    text = """ℹ️ **Справка по боту**

🎯 **Как получить доступ:**
1. Выберите тип подписки
2. Выберите удобный способ оплаты
3. Следуйте инструкциям
4. Дождитесь подтверждения

💳 **Способы оплаты:**
• ⭐️ Звезды Telegram - мгновенно
• 💳 Карта - перевод + чек
• ₿ Крипта - USDT/BTC/ETH

❓ **Поддержка:** @admin_username

⬅️ Вернуться назад"""

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text,
        reply_markup=reply_markup
    )

async def back_to_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Возврат в главное меню."""
    query = update.callback_query
    await query.answer()

    welcome_text = f"""🎉 Добро пожаловать, {query.from_user.first_name}!

🚀 **Бот управления доступом к премиум-каналу**

Выберите желаемый тип подписки:"""

    keyboard = [
        [InlineKeyboardButton("🆓 Пробный доступ (3 дня)", callback_data="subscription_trial")],
        [InlineKeyboardButton("⭐️ Месяц (200⭐️)", callback_data="subscription_monthly")],
        [InlineKeyboardButton("💎 Навсегда (700₽)", callback_data="subscription_permanent")],
        [InlineKeyboardButton("ℹ️ Справка", callback_data="help")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        welcome_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора способа оплаты."""
    query = update.callback_query
    await query.answer()

    callback_data = query.data

    if callback_data.startswith("pay_stars_"):
        subscription_type = callback_data.replace("pay_stars_", "")
        await handle_stars_payment(query, context, subscription_type)
    elif callback_data.startswith("pay_card_"):
        subscription_type = callback_data.replace("pay_card_", "")
        await handle_card_payment(query, context, subscription_type)
    elif callback_data.startswith("pay_crypto_"):
        subscription_type = callback_data.replace("pay_crypto_", "")
        await handle_crypto_payment(query, context, subscription_type)
    elif callback_data.startswith("activate_trial"):
        await activate_trial_access(query, context)
    elif callback_data == "confirm_payment_method":
        await confirm_payment_method(query, context)
    elif callback_data.startswith("upload_receipt_"):
        payment_id = callback_data.replace("upload_receipt_", "")
        await handle_receipt_upload(query, context, payment_id)

async def handle_stars_payment(query, context, subscription_type) -> None:
    """Обработка оплаты звездами Telegram."""
    prices = {
        "monthly": 200,
        "permanent": 700
    }

    price = prices.get(subscription_type, 200)
    subscription_name = "месячную" if subscription_type == "monthly" else "постоянную"

    text = f"""⭐️ **Оплата звездами Telegram**

💰 Стоимость {subscription_name} подписки: {price}⭐️

Для оплаты нажмите кнопку ниже и следуйте инструкциям Telegram.

После оплаты доступ будет предоставлен автоматически!"""

    keyboard = [
        [InlineKeyboardButton(f"💳 Оплатить {price}⭐️", pay=True)],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"subscription_{subscription_type}")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text,
        reply_markup=reply_markup
    )

async def confirm_payment_method(query, context) -> None:
    """Подтверждение выбранного метода оплаты."""
    user_id = query.from_user.id
    user_state = user_states.get(user_id, {})

    payment_method = user_state.get('payment_method')
    subscription_type = user_state.get('subscription_type')

    if not payment_method or not subscription_type:
        await query.edit_message_text(
            "❌ **Ошибка состояния**\n\nВыберите тип подписки и способ оплаты заново.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]])
        )
        return

    # Очищаем состояние
    if user_id in user_states:
        del user_states[user_id]

    # Перенаправляем на соответствующий обработчик оплаты
    if payment_method == 'stars':
        await handle_stars_payment(query, context, subscription_type)
    elif payment_method == 'card':
        await handle_card_payment(query, context, subscription_type)
    elif payment_method == 'crypto':
        await handle_crypto_payment(query, context, subscription_type)

async def handle_card_payment(query, context, subscription_type) -> None:
    """Обработка оплаты на карту."""
    prices = {
        "monthly": 200,
        "permanent": 700
    }

    price = prices.get(subscription_type, 200)
    price_display = f"{price}₽"
    subscription_name = "месячной" if subscription_type == "monthly" else "постоянной"

    # Сохраняем ожидаемый платеж в базу данных
    user_id = query.from_user.id
    try:
        async with aiosqlite.connect(DATABASE_URL) as db:
            await db.execute("""
                INSERT INTO payments (user_id, amount, currency, status)
                VALUES (?, ?, 'RUB', 'pending')
            """, (user_id, price))
            await db.commit()

            # Получаем ID созданного платежа
            cursor = await db.execute(
                "SELECT last_insert_rowid()"
            )
            payment_id = (await cursor.fetchone())[0]

    except Exception as e:
        logger.error(f"Error creating payment record: {e}")
        payment_id = None

    text = f"""💳 **Оплата на карту**

💰 Стоимость {subscription_name} подписки: {price_display}
🆔 Номер платежа: #{payment_id if payment_id else 'N/A'}

📋 **Реквизиты для оплаты:**
🏦 **Сбербанк**
💳 Номер карты: `1234 5678 9012 3456`
👤 Получатель: Иван Иванов

⚠️ **ВАЖНО:**
1. Переведите точную сумму: {price_display}
2. Сделайте скриншот/фото чека
3. Отправьте его в ответ на это сообщение
4. Дождитесь подтверждения администратора

После подтверждения доступ будет предоставлен в течение 10 минут."""

    keyboard = [
        [InlineKeyboardButton("📎 Отправить чек оплаты", callback_data=f"upload_receipt_{payment_id if payment_id else 'error'}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"subscription_{subscription_type}")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text,
        reply_markup=reply_markup
    )

async def handle_crypto_payment(query, context, subscription_type) -> None:
    """Обработка крипто-оплаты."""
    prices = {
        "monthly": "200₽ ≈ 2.5 USDT",
        "permanent": "700₽ ≈ 8.75 USDT"
    }

    price = prices.get(subscription_type, "200₽ ≈ 2.5 USDT")

    text = f"""₿ **Криптовалютная оплата**

💰 Стоимость: {price}

Выберите криптовалюту для оплаты:"""

    keyboard = [
        [InlineKeyboardButton("💲 USDT (Tether)", callback_data=f"crypto_usdt_{subscription_type}")],
        [InlineKeyboardButton("₿ BTC (Bitcoin)", callback_data=f"crypto_btc_{subscription_type}")],
        [InlineKeyboardButton("Ξ ETH (Ethereum)", callback_data=f"crypto_eth_{subscription_type}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"subscription_{subscription_type}")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text,
        reply_markup=reply_markup
    )

async def handle_receipt_upload(query, context, payment_id) -> None:
    """Обработка загрузки чека оплаты."""
    text = f"""📎 **Загрузка чека оплаты**

Платеж #{payment_id}

📸 **Отправьте фото или документ чека оплаты**

⚠️ **Требования к чеку:**
• Четкое изображение
• Видны все реквизиты
• Сумма платежа должна совпадать

После отправки чека администратор проверит оплату и активирует доступ."""

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text,
        reply_markup=reply_markup
    )

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка полученных фото (чеков оплаты)."""
    user = update.effective_user

    if not update.message.photo:
        return

    # Получаем файл чека
    photo = update.message.photo[-1]  # Берем самое большое фото
    file_id = photo.file_id

    try:
        # Сохраняем file_id чека в базу данных для последнего ожидающего платежа пользователя
        async with aiosqlite.connect(DATABASE_URL) as db:
            # Находим последний ожидающий платеж пользователя
            cursor = await db.execute("""
                SELECT payment_id FROM payments
                WHERE user_id = ? AND status = 'pending'
                ORDER BY payment_date DESC
                LIMIT 1
            """, (user.id,))

            payment = await cursor.fetchone()

            if payment:
                payment_id = payment[0]
                # Обновляем платеж с file_id чека
                await db.execute(
                    "UPDATE payments SET receipt_file_id = ?, status = 'receipt_uploaded' WHERE payment_id = ?",
                    (file_id, payment_id)
                )
                await db.commit()

                await update.message.reply_text(
                    f"✅ **Чек получен!**\n\n📄 Платеж #{payment_id}\n\n⏳ Администратор проверит оплату в ближайшее время.\n\n🔔 Вы получите уведомление о результате проверки.",
                )

                # Уведомляем админа о новом чеке
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"📎 **Новый чек оплаты!**\n\n👤 @{user.username or user.first_name}\n💰 Платеж #{payment_id}\n\nПроверьте в админ-панели: /admin",
                    )
                except Exception as e:
                    logger.error(f"Failed to notify admin about receipt: {e}")

            else:
                await update.message.reply_text(
                    "❌ **Не найдено активных платежей**\n\nСначала выберите подписку и способ оплаты.",
                )

    except Exception as e:
        logger.error(f"Error processing receipt: {e}")
        await update.message.reply_text(
            "❌ **Ошибка обработки чека**\n\nПопробуйте отправить чек еще раз или свяжитесь с поддержкой.",
            parse_mode='Markdown'
        )

async def activate_trial_access(query, context) -> None:
    """Активация пробного доступа."""
    user_id = query.from_user.id

    # Сохраняем пробный доступ в базу данных
    try:
        from datetime import datetime, timedelta

        start_date = datetime.now()
        end_date = start_date + timedelta(days=3)

        async with aiosqlite.connect(DATABASE_URL) as db:
            await db.execute("""
                INSERT OR REPLACE INTO users (user_id, subscription_type, start_date, end_date)
                VALUES (?, 'trial', ?, ?)
            """, (user_id, start_date.isoformat(), end_date.isoformat()))
            await db.commit()

        # Добавляем пользователя в канал
        channel_added = await add_user_to_channel(context, user_id)

        channel_status = "🔓 Доступ к премиум-каналу активирован!" if channel_added else "⚠️ Доступ будет предоставлен в ближайшее время."

        text = f"""✅ **Пробный доступ активирован!**

🎉 Поздравляем! Вам предоставлен бесплатный доступ на 3 дня.

{channel_status}

🔓 **Что теперь делать:**
1. Перейдите в премиум-канал: @premium_channel
2. Наслаждайтесь контентом!
3. Через 3 дня подписка закончится автоматически

Если хотите продолжить пользоваться сервисом, оформите платную подписку.

❓ **Поддержка:** @admin_username"""

    except Exception as e:
        logger.error(f"Error activating trial access for user {user_id}: {e}")
        text = """❌ **Ошибка активации**

Произошла ошибка при активации пробного доступа.
Попробуйте еще раз или свяжитесь с поддержкой.

❓ **Поддержка:** @admin_username"""

    keyboard = [[InlineKeyboardButton("🎯 Перейти в канал", url="https://t.me/premium_channel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text,
        reply_markup=reply_markup
    )

async def crypto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора криптовалюты."""
    query = update.callback_query
    await query.answer()

    callback_data = query.data
    if callback_data.startswith("crypto_"):
        # crypto_usdt_monthly -> usdt, monthly
        parts = callback_data.split("_")
        crypto = parts[1]  # usdt, btc, eth
        subscription_type = parts[2]  # monthly, permanent

        await handle_crypto_wallet(query, context, crypto, subscription_type)

async def handle_crypto_wallet(query, context, crypto, subscription_type) -> None:
    """Показать кошелек для оплаты криптовалютой."""
    # Мока данные - в реальности здесь будут настоящие кошельки
    wallets = {
        "usdt": {
            "address": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuW5",
            "network": "TRC20",
            "icon": "💲"
        },
        "btc": {
            "address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
            "network": "Bitcoin",
            "icon": "₿"
        },
        "eth": {
            "address": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
            "network": "ERC20",
            "icon": "Ξ"
        }
    }

    wallet = wallets.get(crypto, wallets["usdt"])

    prices = {
        "monthly": "2.5 USDT",
        "permanent": "8.75 USDT"
    }

    price = prices.get(subscription_type, "2.5 USDT")
    crypto_name = crypto.upper()

    text = f"""{wallet['icon']} **Оплата {crypto_name}**

💰 Сумма к оплате: {price}
🌐 Сеть: {wallet['network']}

📋 **Адрес кошелька:**
`{wallet['address']}`

⚠️ **ВАЖНО:**
• Отправьте ТОЛЬКО {crypto_name}
• Сумма должна быть точной: {price}
• Используйте сеть: {wallet['network']}
• После оплаты нажмите "✅ Я оплатил"

⏱️ **Время подтверждения:** 1-10 минут"""

    keyboard = [
        [InlineKeyboardButton("✅ Я оплатил", callback_data=f"paid_crypto_{crypto}_{subscription_type}")],
        [InlineKeyboardButton("📋 Скопировать адрес", callback_data=f"copy_{crypto}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"pay_crypto_{subscription_type}")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text,
        reply_markup=reply_markup
    )

async def crypto_paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка подтверждения оплаты криптой."""
    query = update.callback_query
    await query.answer()

    callback_data = query.data
    if callback_data.startswith("paid_crypto_"):
        # paid_crypto_usdt_monthly
        parts = callback_data.split("_")
        crypto = parts[2]
        subscription_type = parts[3]

        await confirm_crypto_payment(query, context, crypto, subscription_type)

async def confirm_crypto_payment(query, context, crypto, subscription_type) -> None:
    """Подтверждение оплаты криптой."""
    crypto_name = crypto.upper()
    subscription_name = "месячная" if subscription_type == "monthly" else "постоянная"

    text = f"""⏳ **Проверяем оплату {crypto_name}...**

🔍 Проверяем поступление средств на кошелек...
📊 {subscription_name.capitalize()} подписка

⚡ Обычно подтверждение занимает 1-10 минут.
После подтверждения вы получите уведомление и доступ к каналу.

Если оплата не подтвердится в течение 30 минут, свяжитесь с поддержкой:
❓ **Поддержка:** @admin_username"""

    keyboard = [[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text,
        reply_markup=reply_markup
    )

async def add_user_to_channel(context, user_id) -> bool:
    """Добавление пользователя в приватный канал."""
    try:
        if PRIVATE_CHANNEL_ID == 0:
            logger.warning("PRIVATE_CHANNEL_ID not configured")
            return False

        # Добавляем пользователя в канал
        await context.bot.invite_chat_member(
            chat_id=PRIVATE_CHANNEL_ID,
            user_id=user_id
        )

        logger.info(f"User {user_id} added to channel {PRIVATE_CHANNEL_ID}")
        return True

    except Exception as e:
        logger.error(f"Failed to add user {user_id} to channel: {e}")
        return False

async def remove_user_from_channel(context, user_id) -> bool:
    """Исключение пользователя из приватного канала."""
    try:
        if PRIVATE_CHANNEL_ID == 0:
            logger.warning("PRIVATE_CHANNEL_ID not configured")
            return False

        # Исключаем пользователя из канала
        await context.bot.ban_chat_member(
            chat_id=PRIVATE_CHANNEL_ID,
            user_id=user_id
        )

        # Разблокируем пользователя (чтобы он мог вернуться при новой подписке)
        await context.bot.unban_chat_member(
            chat_id=PRIVATE_CHANNEL_ID,
            user_id=user_id
        )

        logger.info(f"User {user_id} removed from channel {PRIVATE_CHANNEL_ID}")
        return True

    except Exception as e:
        logger.error(f"Failed to remove user {user_id} from channel: {e}")
        return False

async def check_expired_subscriptions(context) -> None:
    """Проверка и удаление истекших подписок."""
    try:
        from datetime import datetime

        async with aiosqlite.connect(DATABASE_URL) as db:
            cursor = await db.execute("""
                SELECT user_id, subscription_type, end_date FROM users
                WHERE end_date IS NOT NULL AND end_date < ?
            """, (datetime.now().isoformat(),))

            expired_users = await cursor.fetchall()

            for user_id, subscription_type, end_date in expired_users:
                # Исключаем пользователя из канала
                await remove_user_from_channel(context, user_id)

                # Обновляем статус в БД
                await db.execute(
                    "UPDATE users SET subscription_type = 'expired' WHERE user_id = ?",
                    (user_id,)
                )

                # Уведомляем пользователя
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"❌ **Подписка истекла**\n\nВаша {subscription_type} подписка закончилась {end_date[:10]}.\n\nДля продолжения доступа оформите новую подписку: /start",
                    )
                except Exception as e:
                    logger.error(f"Failed to notify user {user_id} about expired subscription: {e}")

            await db.commit()

        if expired_users:
            logger.info(f"Removed {len(expired_users)} expired subscriptions")

    except Exception as e:
        logger.error(f"Error checking expired subscriptions: {e}")

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Админ-панель для управления платежами."""
    user = update.effective_user

    if user.id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return

    text = """🔧 **Админ-панель**

Выберите действие:"""

    keyboard = [
        [InlineKeyboardButton("📋 Проверить платежи", callback_data="admin_payments")],
        [InlineKeyboardButton("👥 Управление пользователями", callback_data="admin_users")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        text,
        reply_markup=reply_markup
    )

async def show_admin_panel(query) -> None:
    """Показать админ-панель в callback режиме."""
    text = """🔧 **Админ-панель**

Выберите действие:"""

    keyboard = [
        [InlineKeyboardButton("📋 Проверить платежи", callback_data="admin_payments")],
        [InlineKeyboardButton("👥 Управление пользователями", callback_data="admin_users")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text,
        reply_markup=reply_markup
    )

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка админских callback запросов."""
    query = update.callback_query
    user = query.from_user

    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!")
        return

    await query.answer()
    callback_data = query.data

    if callback_data == "admin_handler":
        await show_admin_panel(query)
    elif callback_data == "admin_payments":
        await show_pending_payments(query, context)
    elif callback_data == "admin_users":
        await show_users_management(query, context)
    elif callback_data == "admin_stats":
        await show_statistics(query, context)
    elif callback_data.startswith("approve_payment_"):
        payment_id = callback_data.replace("approve_payment_", "")
        await approve_payment(query, context, payment_id)
    elif callback_data.startswith("reject_payment_"):
        payment_id = callback_data.replace("reject_payment_", "")
        await reject_payment(query, context, payment_id)
    elif callback_data.startswith("user_action_"):
        await handle_user_action(query, context, callback_data)
    elif callback_data.startswith("user_info_"):
        user_id = callback_data.replace("user_info_", "")
        await show_user_info(query, context, int(user_id))

async def show_users_management(query, context) -> None:
    """Показать управление пользователями - реальных участников канала."""
    try:
        if PRIVATE_CHANNEL_ID == 0:
            await query.edit_message_text(
                "❌ **Канал не настроен**\n\nУстановите PRIVATE_CHANNEL_ID в config.py",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_handler")]])
            )
            return

        # Получаем информацию о канале
        try:
            chat = await context.bot.get_chat(PRIVATE_CHANNEL_ID)
            channel_title = chat.title or f"Канал {PRIVATE_CHANNEL_ID}"
        except Exception as e:
            logger.error(f"Error getting channel info: {e}")
            channel_title = f"Канал {PRIVATE_CHANNEL_ID}"

        # Получаем количество участников (примерное)
        try:
            member_count = await context.bot.get_chat_member_count(PRIVATE_CHANNEL_ID)
        except Exception as e:
            logger.error(f"Error getting member count: {e}")
            member_count = "N/A"

        # Получаем администраторов канала (бот должен быть админом)
        admins = []
        try:
            chat_admins = await context.bot.get_chat_administrators(PRIVATE_CHANNEL_ID)
            admins = [admin.user.id for admin in chat_admins]
        except Exception as e:
            logger.error(f"Error getting admins: {e}")

        # Получаем пользователей из базы данных с информацией о подписках
        users_data = {}
        try:
            async with aiosqlite.connect(DATABASE_URL) as db:
                cursor = await db.execute("""
                    SELECT user_id, username, subscription_type, start_date, end_date
                    FROM users
                    ORDER BY start_date DESC
                """)
                db_users = await cursor.fetchall()

                for user in db_users:
                    user_id, username, subscription_type, start_date, end_date = user
                    users_data[user_id] = {
                        'username': username,
                        'subscription_type': subscription_type,
                        'start_date': start_date,
                        'end_date': end_date
                    }
        except Exception as e:
            logger.error(f"Error getting users from database: {e}")

        # Получаем список участников канала
        # В Telegram Bot API есть ограничения на получение участников каналов
        # Бот может получить только тех участников, которых он "видит"
        channel_members = []

        try:
            # Пытаемся получить информацию об админах (они всегда видны)
            for admin_id in admins:
                try:
                    member_info = await context.bot.get_chat_member(PRIVATE_CHANNEL_ID, admin_id)
                    user = member_info.user
                    if not user.is_bot:
                        channel_members.append({
                            'user_id': user.id,
                            'username': user.username,
                            'first_name': user.first_name,
                            'is_admin': True,
                            'db_info': users_data.get(user.id)
                        })
                except Exception as e:
                    logger.error(f"Error getting admin {admin_id} info: {e}")

            # Пытаемся получить информацию о пользователях из базы данных
            # Для тех, кто есть в БД, но не админы - создаем запись без детальной информации
            for user_id, db_info in users_data.items():
                if user_id not in admins and not any(m['user_id'] == user_id for m in channel_members):
                    try:
                        # Пытаемся получить информацию о пользователе через getChatMember
                        member_info = await context.bot.get_chat_member(PRIVATE_CHANNEL_ID, user_id)
                        user = member_info.user
                        channel_members.append({
                            'user_id': user.id,
                            'username': user.username,
                            'first_name': user.first_name,
                            'is_admin': False,
                            'db_info': db_info
                        })
                    except Exception as e:
                        # Если не можем получить информацию, создаем запись с данными из БД
                        logger.warning(f"Cannot get info for user {user_id}: {e}")
                        channel_members.append({
                            'user_id': user_id,
                            'username': db_info['username'],
                            'first_name': f"ID {user_id}",
                            'is_admin': False,
                            'db_info': db_info
                        })

        except Exception as e:
            logger.error(f"Error getting channel members: {e}")

        # Формируем сообщение
        text = f"""👥 **Управление пользователями канала**

📺 Канал: {channel_title}
👤 Участников: {member_count}
📊 В базе данных: {len(users_data)}
🔍 Получено участников: {len(channel_members)}

"""

        if not channel_members:
            text += "⚠️ Не удалось получить список участников\n"
            text += "Убедитесь что бот является администратором канала\n\n"
            keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="admin_handler")]]
        else:
            keyboard = []

            for member in channel_members[:20]:  # Показываем максимум 20 пользователей
                user_id = member['user_id']
                username = member['username']
                first_name = member['first_name']
                is_admin = member['is_admin']
                db_info = member['db_info']

                # Формируем отображение пользователя
                if username:
                    user_display = f"@{username}"
                else:
                    user_display = first_name or f"ID {user_id}"

                if is_admin:
                    user_display += " 👑"  # Админ

                text += f"👤 {user_display}\n"

                if db_info:
                    sub_type = db_info['subscription_type'] or 'нет'
                    start_date = db_info['start_date'][:10] if db_info['start_date'] else 'N/A'
                    text += f"📝 Подписка: {sub_type} | 📅 {start_date}\n"
                else:
                    text += "📝 Подписка: нет\n"

                text += "\n"

                # Кнопки действий (только для не-админов)
                if not is_admin:
                    keyboard.append([
                        InlineKeyboardButton("🚫 Заблокировать",
                                           callback_data=f"user_action_ban_{user_id}"),
                        InlineKeyboardButton("👀 Инфо",
                                           callback_data=f"user_info_{user_id}")
                    ])

            if len(channel_members) > 20:
                text += f"⚠️ Показаны первые 20 из {len(channel_members)} участников\n"

            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_handler")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text,
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing users management: {e}")
        await query.edit_message_text(
            "❌ Ошибка при загрузке участников канала",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_handler")]])
        )

async def show_statistics(query, context) -> None:
    """Показать статистику."""
    try:
        async with aiosqlite.connect(DATABASE_URL) as db:
            # Общая статистика
            cursor = await db.execute("SELECT COUNT(*) FROM users")
            total_users = (await cursor.fetchone())[0]

            cursor = await db.execute("SELECT COUNT(*) FROM payments WHERE status = 'approved'")
            approved_payments = (await cursor.fetchone())[0]

            cursor = await db.execute("SELECT COUNT(*) FROM payments WHERE status = 'pending'")
            pending_payments = (await cursor.fetchone())[0]

            cursor = await db.execute("SELECT SUM(amount) FROM payments WHERE status = 'approved'")
            total_revenue = (await cursor.fetchone())[0] or 0

            # Статистика по типам подписок
            cursor = await db.execute("""
                SELECT subscription_type, COUNT(*) as count
                FROM users
                WHERE subscription_type IS NOT NULL
                GROUP BY subscription_type
            """)
            subscription_stats = await cursor.fetchall()

        text = f"""📊 **Статистика бота**

👥 **Пользователи:** {total_users}
💰 **Одобренных платежей:** {approved_payments}
⏳ **Ожидающих платежей:** {pending_payments}
💵 **Общая выручка:** {total_revenue}₽

📈 **Распределение подписок:**
"""

        for sub_type, count in subscription_stats:
            sub_name = {
                'trial': 'Пробные',
                'monthly': 'Месячные',
                'permanent': 'Постоянные',
                'expired': 'Истекшие'
            }.get(sub_type, sub_type)
            text += f"• {sub_name}: {count}\n"

        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="admin_handler")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"Error showing statistics: {e}")
        await query.edit_message_text(
            "❌ Ошибка при загрузке статистики",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_handler")]])
        )

async def handle_user_action(query, context, callback_data) -> None:
    """Обработка действий с пользователями."""
    try:
        # user_action_ban_123456789 или user_action_unban_123456789
        parts = callback_data.split('_')
        action = parts[2]  # ban или unban
        user_id = int(parts[3])

        if action == "ban":
            success = await remove_user_from_channel(context, user_id)
            # Обновляем статус в БД
            async with aiosqlite.connect(DATABASE_URL) as db:
                await db.execute(
                    "UPDATE users SET subscription_type = 'banned' WHERE user_id = ?",
                    (user_id,)
                )
                await db.commit()

            status_msg = "🚫 Пользователь заблокирован" if success else "⚠️ Пользователь заблокирован (ошибка канала)"
        elif action == "unban":
            # Для разблокировки просто обновляем статус
            async with aiosqlite.connect(DATABASE_URL) as db:
                await db.execute(
                    "UPDATE users SET subscription_type = 'unbanned' WHERE user_id = ?",
                    (user_id,)
                )
                await db.commit()

            status_msg = "✅ Пользователь разблокирован"
        else:
            status_msg = "❌ Неизвестное действие"

        await query.edit_message_text(
            status_msg,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К пользователям", callback_data="admin_users")]])
        )

    except Exception as e:
        logger.error(f"Error handling user action {callback_data}: {e}")
        await query.edit_message_text(
            "❌ Ошибка при выполнении действия",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_users")]])
        )

async def show_user_info(query, context, user_id) -> None:
    """Показать подробную информацию о пользователе."""
    try:
        # Получаем информацию о пользователе из базы данных
        user_info = None
        payments = []

        async with aiosqlite.connect(DATABASE_URL) as db:
            # Информация о пользователе
            cursor = await db.execute("""
                SELECT username, subscription_type, start_date, end_date
                FROM users
                WHERE user_id = ?
            """, (user_id,))
            user_row = await cursor.fetchone()

            if user_row:
                username, subscription_type, start_date, end_date = user_row
                user_info = {
                    'username': username,
                    'subscription_type': subscription_type,
                    'start_date': start_date,
                    'end_date': end_date
                }

            # История платежей
            cursor = await db.execute("""
                SELECT payment_id, amount, currency, status, payment_date
                FROM payments
                WHERE user_id = ?
                ORDER BY payment_date DESC
                LIMIT 10
            """, (user_id,))
            payments = await cursor.fetchall()

        # Получаем информацию о пользователе из Telegram
        try:
            tg_user = await context.bot.get_chat_member(PRIVATE_CHANNEL_ID, user_id)
            tg_info = {
                'username': tg_user.user.username,
                'first_name': tg_user.user.first_name,
                'last_name': tg_user.user.last_name,
                'status': tg_user.status,
                'joined_date': getattr(tg_user, 'joined_date', None)
            }
        except Exception as e:
            logger.error(f"Error getting Telegram user info: {e}")
            tg_info = None

        # Формируем сообщение
        if tg_info:
            user_display = tg_info['username'] or tg_info['first_name'] or f"ID: {user_id}"
        else:
            user_display = f"ID: {user_id}"

        text = f"""👤 **Информация о пользователе**

🆔 **ID:** {user_id}
👨‍💼 **Имя:** {user_display}
"""

        if tg_info:
            text += f"📊 **Статус в канале:** {tg_info['status']}\n"
            if tg_info['joined_date']:
                text += f"📅 **Присоединился:** {tg_info['joined_date'].strftime('%Y-%m-%d %H:%M')}\n"

        text += "\n"

        if user_info:
            sub_type_display = {
                'trial': 'Пробная',
                'monthly': 'Месячная',
                'permanent': 'Постоянная',
                'expired': 'Истекшая',
                'banned': 'Заблокированная',
                None: 'Нет'
            }.get(user_info['subscription_type'], user_info['subscription_type'] or 'Нет')

            text += f"📝 **Подписка:** {sub_type_display}\n"
            if user_info['start_date']:
                text += f"📅 **Начало:** {user_info['start_date'][:10]}\n"
            if user_info['end_date']:
                text += f"⏰ **Окончание:** {user_info['end_date'][:10]}\n"
        else:
            text += "📝 **Подписка:** Нет\n"

        if payments:
            text += f"\n💰 **История платежей:** ({len(payments)})\n"
            for payment in payments:
                payment_id, amount, currency, status, payment_date = payment
                status_emoji = {
                    'pending': '⏳',
                    'approved': '✅',
                    'rejected': '❌'
                }.get(status, '❓')

                text += f"{status_emoji} #{payment_id}: {amount}{currency} - {payment_date[:10]}\n"

        keyboard = [
            [InlineKeyboardButton("🚫 Заблокировать", callback_data=f"user_action_ban_{user_id}")],
            [InlineKeyboardButton("✅ Разблокировать", callback_data=f"user_action_unban_{user_id}")],
            [InlineKeyboardButton("⬅️ К пользователям", callback_data="admin_users")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"Error showing user info for {user_id}: {e}")
        await query.edit_message_text(
            f"❌ Ошибка при загрузке информации о пользователе {user_id}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_users")]])
        )

async def show_pending_payments(query, context) -> None:
    """Показать ожидающие платежи."""
    try:
        async with aiosqlite.connect(DATABASE_URL) as db:
            cursor = await db.execute("""
                SELECT p.payment_id, p.user_id, p.amount, p.currency, p.status,
                       u.username, p.payment_date
                FROM payments p
                LEFT JOIN users u ON p.user_id = u.user_id
                WHERE p.status = 'pending'
                ORDER BY p.payment_date DESC
                LIMIT 10
            """)
            payments = await cursor.fetchall()

        if not payments:
            text = "✅ **Нет ожидающих платежей**"
            keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="admin_handler")]]
        else:
            text = "📋 **Ожидающие платежи:**\n\n"
            keyboard = []

            for payment in payments:
                payment_id, user_id, amount, currency, status, username, payment_date = payment
                user_display = f"@{username}" if username else f"ID: {user_id}"

                text += f"💰 Платеж #{payment_id}\n"
                text += f"👤 {user_display}\n"
                text += f"💵 {amount} {currency}\n"
                text += f"📅 {payment_date}\n\n"

                keyboard.append([
                    InlineKeyboardButton(f"✅ Одобрить #{payment_id}",
                                       callback_data=f"approve_payment_{payment_id}"),
                    InlineKeyboardButton(f"❌ Отклонить #{payment_id}",
                                       callback_data=f"reject_payment_{payment_id}")
                ])

            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_handler")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"Error showing pending payments: {e}")
        await query.edit_message_text(
            "❌ Ошибка при загрузке платежей",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_handler")]])
        )

async def approve_payment(query, context, payment_id) -> None:
    """Одобрить платеж."""
    try:
        async with aiosqlite.connect(DATABASE_URL) as db:
            # Обновляем статус платежа
            await db.execute(
                "UPDATE payments SET status = 'approved' WHERE payment_id = ?",
                (payment_id,)
            )

            # Получаем информацию о платеже
            cursor = await db.execute("""
                SELECT user_id, amount, currency FROM payments WHERE payment_id = ?
            """, (payment_id,))
            payment = await cursor.fetchone()

            if payment:
                user_id, amount, currency = payment

                # Определяем тип подписки на основе суммы
                subscription_type = "monthly" if amount in [200, "200"] else "permanent"

                # Добавляем/обновляем подписку пользователя
                from datetime import datetime, timedelta
                start_date = datetime.now()
                end_date = start_date + timedelta(days=30) if subscription_type == "monthly" else None

                await db.execute("""
                    INSERT OR REPLACE INTO users (user_id, subscription_type, start_date, end_date)
                    VALUES (?, ?, ?, ?)
                """, (user_id, subscription_type, start_date.isoformat(),
                      end_date.isoformat() if end_date else None))

            await db.commit()

        # Добавляем пользователя в канал
        channel_added = False
        if payment:
            user_id, amount, currency = payment
            channel_added = await add_user_to_channel(context, user_id)

        # Отправляем уведомление пользователю
        if payment:
            user_id, amount, currency = payment
            channel_status = "🔓 Доступ к премиум-каналу активирован!" if channel_added else "⚠️ Доступ будет предоставлен в ближайшее время."

            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"✅ **Платеж подтвержден!**\n\n🎉 Ваш платеж на сумму {amount} {currency} был одобрен!\n\n{channel_status}",
                )
            except Exception as e:
                logger.error(f"Failed to notify user {user_id}: {e}")

        status_message = f"✅ Платеж #{payment_id} одобрен!"
        if channel_added:
            status_message += "\n✅ Пользователь добавлен в канал"
        else:
            status_message += "\n⚠️ Не удалось добавить в канал (проверьте PRIVATE_CHANNEL_ID)"

        await query.edit_message_text(
            status_message,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К платежам", callback_data="admin_payments")]])
        )

    except Exception as e:
        logger.error(f"Error approving payment {payment_id}: {e}")
        await query.edit_message_text(
            f"❌ Ошибка при одобрении платежа #{payment_id}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_payments")]])
        )

async def reject_payment(query, context, payment_id) -> None:
    """Отклонить платеж."""
    try:
        async with aiosqlite.connect(DATABASE_URL) as db:
            await db.execute(
                "UPDATE payments SET status = 'rejected' WHERE payment_id = ?",
                (payment_id,)
            )
            await db.commit()

        # Получаем user_id для уведомления
        async with aiosqlite.connect(DATABASE_URL) as db:
            cursor = await db.execute(
                "SELECT user_id, amount, currency FROM payments WHERE payment_id = ?",
                (payment_id,)
            )
            payment = await cursor.fetchone()

        if payment:
            user_id, amount, currency = payment
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"❌ **Платеж отклонен**\n\nВаш платеж на сумму {amount} {currency} был отклонен.\n\nПроверьте корректность оплаты и попробуйте снова.",
                )
            except Exception as e:
                logger.error(f"Failed to notify user {user_id}: {e}")

        await query.edit_message_text(
            f"❌ Платеж #{payment_id} отклонен!\n\nПользователь уведомлен об отклонении.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К платежам", callback_data="admin_payments")]])
        )

    except Exception as e:
        logger.error(f"Error rejecting payment {payment_id}: {e}")
        await query.edit_message_text(
            f"❌ Ошибка при отклонении платежа #{payment_id}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_payments")]])
        )

# Экспортируем хэндлеры
start_handler = start_handler
subscription_callback = subscription_callback
back_to_main_callback = back_to_main_callback
payment_callback = payment_callback
crypto_callback = crypto_callback
crypto_paid_callback = crypto_paid_callback
admin_handler = admin_handler
admin_callback = admin_callback
photo_handler = photo_handler
