import os
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

import config
from database.core import init_db
from middlewares.db import DbSessionMiddleware
from handlers import admin, user

db_pool = None


# Спеціальний радар, який покаже, чи доходять повідомлення до бота взагалі
class LoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        print("===========================================")
        print("📥 МАЯЧОК: ОТРИМАНО ЗАПИТ ВІД ТЕЛЕГРАМ!")
        print("===========================================")
        return await handler(event, data)


async def on_startup(bot: Bot, dispatcher: Dispatcher):
    global db_pool
    print("⏳ Ініціалізація бази даних...")
    db_pool = await init_db()

    async with db_pool.acquire() as conn:
        active_q = await conn.fetchval('SELECT id FROM questions WHERE is_active = TRUE LIMIT 1')
        config.ACTIVE_QUESTION_ID = active_q

    # Підключаємо наш радар першим
    dispatcher.update.middleware(LoggingMiddleware())
    # Підключаємо БД
    dispatcher.update.middleware(DbSessionMiddleware(db_pool))

    # Кажемо Telegram'у, куди надсилати повідомлення
    await bot.set_webhook(
        url=config.WEBHOOK_URL,
        drop_pending_updates=True
    )
    print(f"✅ Вебхук успішно встановлено на: {config.WEBHOOK_URL}")


async def on_shutdown(bot: Bot):
    global db_pool
    print("🔌 Видаляємо вебхук та закриваємо з'єднання...")
    await bot.delete_webhook()
    if db_pool:
        await db_pool.close()
    await bot.session.close()
    print("🛑 Сервер безпечно зупинено.")


def main():
    if not config.WEBHOOK_URL:
        raise ValueError("Критична помилка: BASE_WEBHOOK_URL не знайдено у змінних середовища!")

    # 🔴 ТИМЧАСОВО ПОВЕРТАЄМО MemoryStorage ЗАМІСТЬ REDIS
    storage = MemoryStorage()

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=storage)

    dp.include_router(admin.router)
    dp.include_router(user.router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=config.WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 Запускаємо веб-сервер на порту {port}...")
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()