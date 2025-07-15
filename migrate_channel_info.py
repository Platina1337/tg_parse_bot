import sqlite3

db_path = "parser.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Добавляем недостающие поля (если их нет)
try:
    cursor.execute("ALTER TABLE channel_info ADD COLUMN members_count INTEGER")
except sqlite3.OperationalError:
    print("members_count уже есть")
try:
    cursor.execute("ALTER TABLE channel_info ADD COLUMN description TEXT")
except sqlite3.OperationalError:
    print("description уже есть")
try:
    cursor.execute("ALTER TABLE channel_info ADD COLUMN created_at TEXT")
except sqlite3.OperationalError:
    print("created_at уже есть")
try:
    cursor.execute("ALTER TABLE channel_info ADD COLUMN total_posts INTEGER")
except sqlite3.OperationalError:
    print("total_posts уже есть")

conn.commit()
conn.close()
print("Миграция завершена!")