import asyncio
import os
import logging
import sys

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

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

db_pool = None


async def ping_handler(request):
    return web.Response(text="✅ Бот успешно работает и готов принимать сообщения!")


async def on_startup(bot: Bot, dispatcher: Dispatcher):
    global db_pool
    logging.info("⏳ Инициализация базы данных...")
    db_pool = await init_db()

    # Записываем активный вопрос в Redis при старте
    async with db_pool.acquire() as conn:
        active_q = await conn.fetchval('SELECT id FROM questions WHERE is_active = TRUE LIMIT 1')

    if active_q:
        await dispatcher.storage.redis.set("active_question_id", str(active_q))
    else:
        await dispatcher.storage.redis.delete("active_question_id")

    dispatcher.update.middleware(ThrottlingMiddleware(time_limit=0.8))
    dispatcher.update.middleware(DbSessionMiddleware(db_pool))

    base_url = config.BASE_WEBHOOK_URL.strip()
    if not base_url.startswith("http"):
        base_url = "https://" + base_url
    webhook_url = base_url.rstrip('/') + "/webhook"

    logging.info(f"⏳ Устанавливаем вебхук: {webhook_url}")
    await bot.set_webhook(url=webhook_url, drop_pending_updates=False)
    logging.info("✅ Вебхук успешно установлен!")


async def on_shutdown(bot: Bot):
    global db_pool
    logging.info("🔌 Закрываем соединения...")
    if db_pool:
        await db_pool.close()
    await bot.session.close()
    logging.info("🛑 Сервер безопасно остановлен.")


async def main():
    if not config.BASE_WEBHOOK_URL:
        raise ValueError("Критическая ошибка: BASE_WEBHOOK_URL не найден!")

    logging.info("⏳ Подключение к Redis...")
    storage = RedisStorage.from_url(config.REDIS_URL)
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=storage)

    dp.include_router(admin.router)
    dp.include_router(user.router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    app.router.add_get("/", ping_handler)

    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")

    setup_application(app, dp, bot=bot)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)

    logging.info(f"🚀 Сервер запущен на порту {port}!")
    await site.start()

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass