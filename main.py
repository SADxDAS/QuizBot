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

# Глобальна змінна для пулу бази даних, щоб ми могли безпечно її закрити
db_pool = None


# Змінюємо 'dp' на 'dispatcher' в аргументах функції
async def on_startup(bot: Bot, dispatcher: Dispatcher):
    global db_pool
    print("⏳ Ініціалізація бази даних...")
    db_pool = await init_db()

    # Синхронізація кешу
    async with db_pool.acquire() as conn:
        active_q = await conn.fetchval('SELECT id FROM questions WHERE is_active = TRUE LIMIT 1')
        config.ACTIVE_QUESTION_ID = active_q

    # Підключаємо мідлвари до dispatcher
    dispatcher.update.middleware(ThrottlingMiddleware(time_limit=0.8))
    dispatcher.update.middleware(DbSessionMiddleware(db_pool))

    # Кажемо Telegram'у, куди надсилати повідомлення
    await bot.set_webhook(
        url=config.WEBHOOK_URL,
        drop_pending_updates=True
    )
    print(f"✅ Вебхук успішно встановлено на: {config.WEBHOOK_URL}")


# Функція, яка виконується ПРИ ЗУПИНЦІ сервера (Graceful Shutdown)
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

    # Ініціалізація Redis та Aiogram
    storage = RedisStorage.from_url(config.REDIS_URL)
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=storage)

    # Реєструємо роутери
    dp.include_router(admin.router)
    dp.include_router(user.router)

    # Реєструємо життєвий цикл бота
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Створюємо веб-сервер
    app = web.Application()

    # Зв'язуємо диспетчер з веб-сервером
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=config.WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    # Railway автоматично видає порт через змінну PORT (зазвичай 8080 або інші)
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 Запускаємо веб-сервер на порту {port}...")

    # Запускаємо сервер
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()