from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
import asyncpg
import config
from keyboards.reply import get_admin_keyboard

router = Router()


@router.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"🆔 Твій Telegram ID: <code>{message.from_user.id}</code>")

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

    # БЛИСКАВИЧНА ПЕРЕВІРКА КЕШУ
    if not config.ACTIVE_QUESTION_ID:
        if message.from_user.id in config.ADMIN_IDS: return
        await message.answer("Наразі немає активних опитувань. Відпочивайте! 😊")
        return

    q_id = config.ACTIVE_QUESTION_ID
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    async with pool.acquire() as conn:
        # 1. Оновлюємо дані користувача
        await conn.execute(
            'INSERT INTO users (telegram_id, username) VALUES ($1, $2) ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username',
            user_id, username
        )

        # 2. ОДНИМ ЗАПИТОМ намагаємося вставити відповідь.
        # Якщо конфлікт (унікальність telegram_id + question_id) - нічого не робимо (DO NOTHING).
        result = await conn.execute('''
            INSERT INTO answers (telegram_id, username, question_id, answer_text, reaction_time)
            VALUES ($1, $2, $3, $4, EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - (SELECT last_delivered_at FROM users WHERE telegram_id = $1))))
            ON CONFLICT (telegram_id, question_id) DO NOTHING
        ''', user_id, username, q_id, message.text)

    # Якщо result == 'INSERT 0', це означає, що рядок не вставився (спрацював DO NOTHING)
    if result == 'INSERT 0':
        await message.answer("🛑 Ви вже відповіли на це питання! Відповідь приймається лише один раз.")
    else:
        await message.answer("Вашу відповідь успішно прийнято! Дякую. ✅")