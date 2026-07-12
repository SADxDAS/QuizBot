import logging

from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
import asyncpg
import config
import html

router = Router()

# Множина всіх текстів з кнопок адміна (працює миттєво, без навантаження)
ADMIN_BUTTONS = {
    "📊 Переглянути відповіді", "📃 Список питань", "➕ Створити питання",
    "🚀 Запуск опитування"
}


@router.message(CommandStart())
async def cmd_start(message: Message, pool: asyncpg.Pool):
    safe_name = html.escape(message.from_user.first_name)
    await message.answer(f"Привіт, {safe_name}! Я бот для опитувань.\nКоли з'явиться нове запитання, я надішлю його сюди.\nПросто чекай! 😊")

    # Реєструємо користувача одразу при старті, щоб зняти навантаження під час вікторини
    async with pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO users (telegram_id, username) VALUES ($1, $2) ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username',
            message.from_user.id, message.from_user.username or message.from_user.full_name
        )


@router.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"🆔 Твій Telegram ID: <code>{message.from_user.id}</code>")


@router.message(F.text & ~F.text.startswith('/'))
async def handle_any_text_answer(message: Message, state: FSMContext, pool: asyncpg.Pool):
    # 1. МИТТЄВИЙ ВІДСІВ КНОПОК
    if message.text in ADMIN_BUTTONS:
        return

    # 2. ПЕРЕВІРКА СТАНУ
    if await state.get_state() is not None:
        return

    # --- НОВИЙ ЗАХИСТ: ОБМЕЖЕННЯ ДОВЖИНИ ВІДПОВІДІ ---
    if len(message.text) > 500:
        await message.answer(
            "⚠️ Ваша відповідь занадто довга! Максимальна довжина — 500 символів. Спробуйте ще раз по-коротше.")
        return

    # 3. ПЕРЕВІРКА АКТИВНОГО ПИТАННЯ В РЕДИСІ
    active_q_bytes = await state.storage.redis.get("active_question_id")
    if not active_q_bytes:
        if message.from_user.id in config.ADMIN_IDS: return
        await message.answer("Наразі немає активних опитувань. Відпочивайте! 😊")
        return

    q_id = int(active_q_bytes.decode('utf-8'))
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    # 4. АТОМАРНА РОБОТА З БАЗОЮ ТА ЗАХИСТ ВІД ФАЛЬСТАРТУ
    async with pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO users (telegram_id, username) VALUES ($1, $2) ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username',
            user_id, username
        )

        # Дістаємо час доставки для цього конкретного користувача
        last_delivery = await conn.fetchval('SELECT last_delivered_at FROM users WHERE telegram_id = $1', user_id)

        # Якщо часу немає, значить повідомлення з питанням до нього ще не дійшло (або він фальстартує)
        if not last_delivery and user_id not in config.ADMIN_IDS:
            await message.answer(
                "⚠️ <b>Фальстарт!</b>\nВи ще не отримали повідомлення з новим питанням. Будь ласка, дочекайтеся розсилки.")
            return

        try:
            # Якщо це адмін (якому розсилка не йде), ставимо час 0. Для всіх інших - точний розрахунок.
            inserted_id = await conn.fetchval('''
                    INSERT INTO answers (telegram_id, username, question_id, answer_text, reaction_time)
                    VALUES ($1, $2, $3, $4, 
                            CASE WHEN $5::TIMESTAMP IS NULL THEN 0 ELSE EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - $5::TIMESTAMP)) END)
                    ON CONFLICT (telegram_id, question_id) DO NOTHING
                    RETURNING id
                ''', user_id, username, q_id, message.text, last_delivery)

            if not inserted_id:
                await message.answer("🛑 Ви вже відповіли на це питання! Відповідь приймається лише один раз.")
            else:
                await message.answer("Вашу відповідь успішно прийнято! Дякую. ✅")

        except Exception as e:
            import logging
            logging.error(f"Помилка збереження відповіді для {user_id}: {e}", exc_info=True)
            await message.answer("🛑 Відбулася помилка при збереженні. Спробуйте ще раз або ви вже відповіли.")

@router.message(F.content_type.in_({'photo', 'video', 'document', 'sticker', 'voice', 'audio', 'animation'}))
async def handle_non_text(message: Message, state: FSMContext):
    # Перевіряємо, чи йде зараз вікторина
    active_q_bytes = await state.storage.redis.get("active_question_id")
    if active_q_bytes:
        await message.answer("⚠️ Будь ласка, надішліть вашу відповідь **текстом**. Медіафайли не приймаються.")