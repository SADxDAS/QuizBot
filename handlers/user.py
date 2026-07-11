from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
import asyncpg
import config

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(f"Привіт, {message.from_user.first_name}! Я бот-вікторина. Чекай на питання! 🚀")


@router.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"🆔 Твій Telegram ID: <code>{message.from_user.id}</code>")


@router.message(F.text & ~F.text.startswith('/'))
async def handle_any_text_answer(message: Message, state: FSMContext, pool: asyncpg.Pool):
    # Игнорируем кнопки админ-меню
    if message.text in ["⚙️ Список питань", "📃 Список питань"] and message.from_user.id in config.ADMIN_IDS:
        return

    if await state.get_state() is not None:
        return

    # Достаем ID вопроса из сверхбыстрого Redis
    active_q_bytes = await state.storage.redis.get("active_question_id")

    if not active_q_bytes:
        await message.answer("Наразі немає активних опитувань. Відпочивайте! 😊")
        return

    q_id = int(active_q_bytes.decode('utf-8'))
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    # Быстрая работа с БД (открыли, записали, закрыли)
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

    # Отправка сообщений вынесена ЗА пределы пула соединений БД
    if result == 'INSERT 0':
        await message.answer("🛑 Ви вже відповіли на це питання! Відповідь приймається лише один раз.")
    else:
        await message.answer("Вашу відповідь успішно прийнято! Дякую. ✅")