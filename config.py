import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}" if BASE_WEBHOOK_URL else None
admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip().isdigit()]

if not ADMIN_IDS and os.getenv("ADMIN_ID", "").strip().isdigit():
    ADMIN_IDS = [int(os.getenv("ADMIN_ID").strip())]

if not BOT_TOKEN or not DATABASE_URL:
    raise ValueError("КРИТИЧНА ПОМИЛКА: Не вистачає BOT_TOKEN або DATABASE_URL у файлі .env")

# Глобальний кеш для швидких відповідей
ACTIVE_QUESTION_ID = None