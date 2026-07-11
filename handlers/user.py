from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
import asyncpg
import config
from keyboards.reply import get_admin_keyboard

router = Router()


@router.message(Command("start"))
async def start_cmd(message: Message, pool: asyncpg.Pool, state: FSMContext):
    await state.clear()
    async with pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO users (telegram_id, username) VALUES ($1, $2) 
            ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username
        ''', message.from_user.id, message.from_user.username)

    if message.from_user.id in config.ADMIN_IDS:
        await message.answer("Головне меню:", reply_markup=get_admin_keyboard())
        return

    await message.answer(
        "👋 <b>Привіт!</b> Я бот для опитувань.\nКоли з'явиться нове запитання, я надішлю його сюди. Просто чекай! 😊")


@router.message(F.text & ~F.text.startswith('/'))
async def handle_any_text_answer(message: Message, state: FSMContext, pool: asyncpg.Pool):
    if await state.get_state() is not None: return

    # БЛИСКАВИЧНА ПЕРЕВІРКА КЕШУ (Без запиту до бази!)
    if not config.ACTIVE_QUESTION_ID:
        if message.from_user.id in config.ADMIN_IDS: return
        await message.answer("Наразі немає активних опитувань. Відпочивайте! 😊")
        return

    q_id = config.ACTIVE_QUESTION_ID

    async with pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO users (telegram_id, username) VALUES ($1, $2) ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username',
            message.from_user.id, message.from_user.username)

        already_answered = await conn.fetchval('SELECT 1 FROM answers WHERE telegram_id = $1 AND question_id = $2',
                                               message.from_user.id, q_id)
        if already_answered:
            await message.answer("🛑 Ви вже відповіли на це питання! Відповідь приймається лише один раз.")
            return

        username = message.from_user.username or message.from_user.full_name
        await conn.execute('''
            INSERT INTO answers (telegram_id, username, question_id, answer_text, reaction_time)
            VALUES ($1, $2, $3, $4, EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - (SELECT last_delivered_at FROM users WHERE telegram_id = $1))))
        ''', message.from_user.id, username, q_id, message.text)

    await message.answer("Вашу відповідь успішно прийнято! Дякую. ✅")