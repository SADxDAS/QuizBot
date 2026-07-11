import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
from database.core import init_db
from middlewares.db import DbSessionMiddleware
from handlers import admin, user


async def main():
    print("⏳ Ініціалізація бази даних...")
    pool = await init_db()

    # Синхронізація кешу під час запуску (якщо бот перезавантажився під час активного опитування)
    async with pool.acquire() as conn:
        active_q = await conn.fetchval('SELECT id FROM questions WHERE is_active = TRUE LIMIT 1')
        config.ACTIVE_QUESTION_ID = active_q

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Підключаємо мідлвар, який роздаватиме pool в усі обробники
    dp.update.middleware(DbSessionMiddleware(pool))

    # Підключаємо наші модульні обробники
    dp.include_router(admin.router)
    dp.include_router(user.router)

    print("✅ Робот-опитувальник успішно запущено!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Бота зупинено.")