from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
import asyncpg
import config

router = Router()

# Множина всіх текстів з кнопок адміна (працює миттєво, без навантаження)
ADMIN_BUTTONS = {
    "📊 Переглянути відповіді", "📃 Список питань", "➕ Створити питання",
    "🚀 Запуск опитування"
}


@router.message(CommandStart())
async def cmd_start(message: Message, pool: asyncpg.Pool):
    await message.answer(f"Привіт, {message.from_user.first_name}! Я бот-вікторина. Чекай на питання! 🚀")

    # Реєструємо юзера одразу при старті, щоб зняти навантаження під час вікторини
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

    # 3. ПЕРЕВІРКА АКТИВНОГО ПИТАННЯ В РЕДІСІ
    active_q_bytes = await state.storage.redis.get("active_question_id")
    if not active_q_bytes:
        if message.from_user.id in config.ADMIN_IDS: return
        await message.answer("Наразі немає активних опитувань. Відпочивайте! 😊")
        return

    q_id = int(active_q_bytes.decode('utf-8'))
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    # 4. АТОМАРНА РОБОТА З БАЗОЮ (Highload Safe)
    async with pool.acquire() as conn:
        # Спочатку швидко оновлюємо юзера (якщо змінив нік)
        await conn.execute(
            'INSERT INTO users (telegram_id, username) VALUES ($1, $2) ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username',
            user_id, username
        )

        # ЄДИНИЙ ЗАПИТ: Вставка з вбудованою перевіркою на дублікат та підрахунком часу
        # Це вирішує проблему Race Condition (коли юзер клікає 2 рази за мілісекунду)
        try:
            result = await conn.execute('''
                INSERT INTO answers (telegram_id, username, question_id, answer_text, reaction_time)
                VALUES ($1, $2, $3, $4, EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - (SELECT last_delivered_at FROM users WHERE telegram_id = $1))))
                ON CONFLICT (telegram_id, question_id) DO NOTHING
            ''', user_id, username, q_id, message.text)

            # Якщо INSERT не відбувся (бо такий запис вже є)
            if result == "INSERT 0":
                await message.answer("🛑 Ви вже відповіли на це питання! Відповідь приймається лише один раз.")
            else:
                await message.answer("Вашу відповідь успішно прийнято! Дякую. ✅")

        except Exception as e:
            # На випадок непередбачених збоїв БД
            await message.answer("🛑 Відбулася помилка при збереженні. Спробуйте ще раз або ви вже відповіли.")