import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("ACCESS_BOT_TOKEN", "8252264132:AAGRg1_bYB_ONv4Uq0_PaRbXkKsXLod7C_A")
ADMIN_ID = int(os.getenv("ADMIN_ID", 8185973411))
PRIVATE_CHANNEL_ID = int(os.getenv("PRIVATE_CHANNEL_ID", -1002885587089))

DATABASE_URL = "access_bot.db"
