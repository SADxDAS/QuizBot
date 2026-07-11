import os
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

import config
from database.core import init_db
from middlewares.db import DbSessionMiddleware
from middlewares.throttling import ThrottlingMiddleware
from handlers import admin, user

db_pool = None


async def on_startup(bot: Bot, dispatcher: Dispatcher):
    global db_pool
    print("⏳ Ініціалізація бази даних...")
    db_pool = await init_db()

    async with db_pool.acquire() as conn:
        active_q = await conn.fetchval('SELECT id FROM questions WHERE is_active = TRUE LIMIT 1')
        config.ACTIVE_QUESTION_ID = active_q

    # Підключаємо мідлвари
    dispatcher.update.middleware(ThrottlingMiddleware(time_limit=0.8))
    dispatcher.update.middleware(DbSessionMiddleware(db_pool))

    # Гарантуємо, що URL буде правильно сформований зі слешем
    webhook_url = config.BASE_WEBHOOK_URL.rstrip('/') + "/webhook"

    await bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True
    )
    print(f"✅ Вебхук успішно встановлено на: {webhook_url}")


async def on_shutdown(bot: Bot):
    global db_pool
    print("🔌 Видаляємо вебхук та закриваємо з'єднання...")
    await bot.delete_webhook()
    if db_pool:
        await db_pool.close()
    await bot.session.close()
    print("🛑 Сервер безпечно зупинено.")


# 🟢 ТЕСТОВА СТОРІНКА ДЛЯ БРАУЗЕРА
async def ping_handler(request):
    return web.Response(text="✅ Бот успішно працює і готовий приймати повідомлення від Telegram!")


def main():
    if not config.BASE_WEBHOOK_URL:
        raise ValueError("Критична помилка: BASE_WEBHOOK_URL не знайдено у змінних середовища!")

    # Вмикаємо Redis
    storage = RedisStorage.from_url(config.REDIS_URL)
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=storage)

    dp.include_router(admin.router)
    dp.include_router(user.router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()

    # 🟢 Реєструємо тестову сторінку на головний домен
    app.router.add_get("/", ping_handler)

    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)

    # 🟢 ЖОРСТКО прописуємо шлях "/webhook", щоб уникнути помилок з config.py
    webhook_requests_handler.register(app, path="/webhook")

    setup_application(app, dp, bot=bot)

    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 Запускаємо веб-сервер на порту {port}...")
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()