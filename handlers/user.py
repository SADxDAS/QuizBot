from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
import asyncpg
import config

router = Router()

# 1. ОБРОБНИК КОМАНДИ /myid (Повинен бути першим!)
@router.message(Command("myid"))
async def cmd_myid(message: Message):
    # Використовуємо message.from_user.id (цифри), а не message.text (текст кнопки)
    await message.answer(f"🆔 Твій Telegram ID: <code>{message.from_user.id}</code>")


# 2. ГАРАНТОВАНО СПРАЦЮЄ ТІЛЬКИ НА ТЕКСТ, ЯКИЙ НЕ Є КОМАНДОЮ АБО КНОПКОЮ АДМІНА
# (Твій оптимізований обробник відповідей на вікторину)
@router.message(F.text & ~F.text.startswith('/'))
async def handle_any_text_answer(message: Message, state: FSMContext, pool: asyncpg.Pool):
    # Якщо юзер адмін і натискає кнопку меню - ігноруємо, щоб це перехопив admin.py
    if message.text in ["⚙️ Список питань", "📃 Список питань"] and message.from_user.id in config.ADMIN_IDS:
        return

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