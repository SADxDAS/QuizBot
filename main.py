import asyncio
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


# 🟢 ТЕСТОВА СТОРІНКА ДЛЯ БРАУЗЕРА (HEALTHCHECK)
async def ping_handler(request):
    return web.Response(text="✅ Бот успішно працює і готовий приймати повідомлення від Telegram!")


async def main():
    if not config.BASE_WEBHOOK_URL:
        raise ValueError("Критична помилка: BASE_WEBHOOK_URL не знайдено у змінних середовища!")

    print("⏳ Ініціалізація бази даних...")
    pool = await init_db()

    # Синхронізація кешу
    async with pool.acquire() as conn:
        active_q = await conn.fetchval('SELECT id FROM questions WHERE is_active = TRUE LIMIT 1')
        config.ACTIVE_QUESTION_ID = active_q

    # Вмикаємо Redis
    print("⏳ Підключення до Redis...")
    storage = RedisStorage.from_url(config.REDIS_URL)
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=storage)

    # Підключаємо мідлвари (Тепер вони гарантовано підключаються без помилок)
    dp.update.middleware(ThrottlingMiddleware(time_limit=0.8))
    dp.update.middleware(DbSessionMiddleware(pool))

    # Реєструємо роутери
    dp.include_router(admin.router)
    dp.include_router(user.router)

    # 🟢 Гарантуємо, що URL завжди має https://
    base_url = config.BASE_WEBHOOK_URL.strip()
    if not base_url.startswith("http"):
        base_url = "https://" + base_url
    webhook_url = base_url.rstrip('/') + "/webhook"

    print(f"⏳ Встановлюємо вебхук: {webhook_url}")
    await bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True
    )
    print("✅ Вебхук успішно встановлено!")

    # Налаштування aiohttp веб-сервера
    app = web.Application()
    app.router.add_get("/", ping_handler)

    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")

    setup_application(app, dp, bot=bot)

    # Запускаємо сервер
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)

    print(f"🚀 Сервер запущено на порту {port}!")
    await site.start()

    try:
        # Тримаємо програму активною нескінченно
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        # Коректне закриття при зупинці сервера Railway
        print("🔌 Закриваємо з'єднання з БД...")

        # ❌ РЯДОК await bot.delete_webhook() ВИДАЛЕНО ЗВІДСИ НАЗАВЖДИ!

        await pool.close()
        await bot.session.close()
        await runner.cleanup()
        print("🛑 Сервер безпечно зупинено.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass