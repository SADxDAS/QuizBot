from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
import asyncpg
import config

router = Router()

# 1. ОБРОБНИК КОМАНДИ /start (щоб бот вітався)
@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(f"Привіт, {message.from_user.first_name}! Я бот-вікторина. Чекай на питання! 🚀")


# 2. ОБРОБНИК КОМАНДИ /myid
@router.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"🆔 Твій Telegram ID: <code>{message.from_user.id}</code>")


# 3. ОБРОБНИК БУДЬ-ЯКОГО ІНШОГО ТЕКСТУ
@router.message(F.text & ~F.text.startswith('/'))
async def handle_any_text_answer(message: Message, state: FSMContext, pool: asyncpg.Pool):
    # Пропускаємо кнопки адмін-меню
    if message.text in ["⚙️ Список питань", "📃 Список питань"] and message.from_user.id in config.ADMIN_IDS:
        return

    if await state.get_state() is not None: return

    # Перевірка на активне питання
    if not config.ACTIVE_QUESTION_ID:
        # Я ВИДАЛИВ РЯДОК З RETURN ДЛЯ АДМІНІВ. ТЕПЕР БОТ ВІДПОВІДАТИМЕ ВСІМ!
        await message.answer("Наразі немає активних опитувань. Відпочивайте! 😊")
        return

    q_id = config.ACTIVE_QUESTION_ID
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    async with pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO users (telegram_id, username) VALUES ($1, $2) ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username',
            user_id, username
        )

        result = await conn.execute('''
            INSERT INTO answers (telegram_id, username, question_id, answer_text, reaction_time)
            VALUES ($1, $2, $3, $4, EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - (SELECT last_delivered_at FROM users WHERE telegram_id = $1))))
            ON CONFLICT (telegram_id, question_id) DO NOTHING
        ''', user_id, username, q_id, message.text)

    if result == 'INSERT 0':
        await message.answer("🛑 Ви вже відповіли на це питання! Відповідь приймається лише один раз.")
    else:
        await message.answer("Вашу відповідь успішно прийнято! Дякую. ✅")