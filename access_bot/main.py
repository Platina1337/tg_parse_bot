import logging
import asyncio
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from .config import BOT_TOKEN
from .database import init_db
from .handlers import start_handler, subscription_callback, back_to_main_callback, payment_callback, crypto_callback, crypto_paid_callback, admin_handler, admin_callback, photo_handler, check_expired_subscriptions

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def main() -> None:
    """Start the bot asynchronously."""
    # Initialize database
    await init_db()
    logger.info("Database initialized successfully")

    # Create the Application and pass it your bot's token.
    application = Application.builder().token(BOT_TOKEN).build()

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("admin", admin_handler))

    # Add message handlers
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    # Add callback query handlers
    application.add_handler(CallbackQueryHandler(subscription_callback, pattern="^subscription_"))
    application.add_handler(CallbackQueryHandler(back_to_main_callback, pattern="^back_to_main$"))
    application.add_handler(CallbackQueryHandler(payment_callback, pattern="^(pay_|activate_trial|upload_receipt_)"))
    application.add_handler(CallbackQueryHandler(crypto_callback, pattern="^crypto_"))
    application.add_handler(CallbackQueryHandler(crypto_paid_callback, pattern="^paid_crypto_"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^(admin_|approve_payment_|reject_payment_)"))

    # Add job queue for checking expired subscriptions (every hour)
    application.job_queue.run_repeating(check_expired_subscriptions, interval=3600, first=60)

    # Run the bot until the user presses Ctrl-C
    await application.run_polling()

if __name__ == "__main__":
    try:
        # Try to use nest_asyncio for environments with existing event loops
        try:
            import nest_asyncio
            nest_asyncio.apply()
            logger.info("Using nest_asyncio for event loop compatibility")
        except ImportError:
            logger.info("nest_asyncio not available, using standard asyncio")

        # Now safe to use asyncio.run()
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user")
    except Exception as e:
        print(f"Error: {e}")
