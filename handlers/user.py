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
    # 1. ЗАХИСТ ВІД АДМІНСЬКИХ КНОПОК
    # Якщо це адмін, перевіряємо, чи не натиснув він системну кнопку
    if message.from_user.id in config.ADMIN_IDS:
        text_lower = message.text.lower()
        admin_keywords = [
            "список питань", "додати питання", "розсилка",
            "статистика", "налаштування", "скасувати", "головне меню",
            "видалити", "редагувати"
        ]
        # Якщо в повідомленні є хоча б одне слово з кнопок меню - ігноруємо
        if any(keyword in text_lower for keyword in admin_keywords):
            return

    # 2. ПЕРЕВІРКА СТАНУ FSM (якщо юзер щось вводить у меню)
    if await state.get_state() is not None:
        return

    # 3. ПЕРЕВІРКА АКТИВНОГО ПИТАННЯ В РЕДІСІ
    active_q_bytes = await state.storage.redis.get("active_question_id")
    if not active_q_bytes:
        # Адмінів не спамимо
        if message.from_user.id in config.ADMIN_IDS: return
        await message.answer("Наразі немає активних опитувань. Відпочивайте! 😊")
        return

    q_id = int(active_q_bytes.decode('utf-8'))
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    # 4. РОБОТА З БАЗОЮ: ЖОРСТКА ПЕРЕВІРКА НА ДУБЛІКАТИ
    async with pool.acquire() as conn:
        # Оновлюємо дані про користувача
        await conn.execute(
            'INSERT INTO users (telegram_id, username) VALUES ($1, $2) ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username',
            user_id, username
        )

        # 100% надійна перевірка: чи вже є відповідь від цього юзера на це питання?
        existing_answer = await conn.fetchval(
            'SELECT id FROM answers WHERE telegram_id = $1 AND question_id = $2',
            user_id, q_id
        )

        if existing_answer:
            # Якщо запис знайдено, зупиняємо процес і НІЧОГО не перезаписуємо
            await message.answer("🛑 Ви вже відповіли на це питання! Відповідь приймається лише один раз.")
            return

        # Якщо відповіді ще немає - безпечно зберігаємо нову
        await conn.execute('''
            INSERT INTO answers (telegram_id, username, question_id, answer_text, reaction_time)
            VALUES ($1, $2, $3, $4, EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - (SELECT last_delivered_at FROM users WHERE telegram_id = $1))))
        ''', user_id, username, q_id, message.text)

    # Сповіщаємо про успіх
    await message.answer("Вашу відповідь успішно прийнято! Дякую. ✅")