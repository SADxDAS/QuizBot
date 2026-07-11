import os
import asyncio
import asyncpg
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip().isdigit()]

if not ADMIN_IDS and os.getenv("ADMIN_ID", "").strip().isdigit():
    ADMIN_IDS = [int(os.getenv("ADMIN_ID").strip())]

if not BOT_TOKEN:
    raise ValueError("КРИТИЧНА ПОМИЛКА: BOT_TOKEN не знайдено!")
if not DATABASE_URL:
    raise ValueError("КРИТИЧНА ПОМИЛКА: DATABASE_URL порожній! Railway не передає посилання на базу.")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Список кнопок меню для захисту від випадкового введення
ADMIN_MENU_BUTTONS = ["📊 Переглянути відповіді", "⚙️ Список питань", "➕ Створити питання", "🚀 Запуск опитування"]


class AdminStates(StatesGroup):
    waiting_for_new_question = State()
    waiting_for_edit_question = State()


async def init_db():
    pool = await asyncpg.create_pool(DATABASE_URL)
    dp["db_pool"] = pool

    async with pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS questions (
                id SERIAL PRIMARY KEY,
                question_text TEXT NOT NULL,
                is_active BOOLEAN DEFAULT FALSE
            );

            CREATE TABLE IF NOT EXISTS answers (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                username VARCHAR(255),
                question_id INT REFERENCES questions(id) ON DELETE CASCADE,
                answer_text TEXT NOT NULL,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT unique_user_question UNIQUE (telegram_id, question_id)
            );
        ''')
        await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS last_delivered_at TIMESTAMP;')
        await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(255);')
        await conn.execute('ALTER TABLE answers ADD COLUMN IF NOT EXISTS reaction_time REAL;')


@dp.message(Command("myid"))
async def get_my_id(message: Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@dp.message(Command("start"))
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()

    # Зберігаємо або оновлюємо користувача в базі
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO users (telegram_id, username) 
            VALUES ($1, $2) 
            ON CONFLICT (telegram_id) 
            DO UPDATE SET username = EXCLUDED.username
        ''', message.from_user.id, message.from_user.username)

    if message.from_user.id in ADMIN_IDS:
        await message.answer("Головне меню:", reply_markup=get_admin_keyboard())
        return

    await message.answer(
        "👋 <b>Привіт!</b> Я бот для опитувань.\n\n"
        "Коли з'явиться нове запитання, я надішлю його сюди. Просто чекай! 😊"
    )


# === ТЕСТОВІ АДМІН-КОМАНДИ ===

@dp.message(Command("cleardb"))
async def cmd_clear_db(message: Message):
    # Команда доступна тільки адміністраторам
    if message.from_user.id not in ADMIN_IDS: return

    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        # TRUNCATE CASCADE миттєво очищає таблиці та скидає лічильники ID (SERIAL) назад до 1
        await conn.execute('TRUNCATE TABLE answers, questions RESTART IDENTITY CASCADE;')

    await message.answer(
        "🧹 <b>Базу даних повністю очищено!</b>\nВсі питання та відповіді видалено. Лічильники ID скинуто.")


@dp.message(Command("testdata"))
async def cmd_test_data(message: Message):
    if message.from_user.id not in ADMIN_IDS: return

    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        # 1. Створюємо 10 звичайних тестових питань
        for i in range(1, 11):
            await conn.execute('INSERT INTO questions (question_text) VALUES ($1)', f'Тестове запитання №{i}')

        # 2. Створюємо цільове питання та отримуємо його ID
        q_id = await conn.fetchval('INSERT INTO questions (question_text) VALUES ($1) RETURNING id', 'ЯК ЦЕ РОБИТИ...')

        # 3. Генеруємо 70 відповідей від різних "фейкових" користувачів
        for i in range(1, 71):
            fake_telegram_id = 1000000 + i  # Унікальний ID, щоб не спрацьовувало блокування на 1 відповідь
            fake_username = f"test_user_{i}"
            fake_answer = f"Ось так потрібно це робити, варіант {i} 💩"
            fake_reaction_time = 0.5 + (i * 0.1)  # Імітація різного часу реакції (від 0.6 до 7.5 секунд)

            # Додаємо фейкового юзера в таблицю users (щоб не порушувати зовнішні ключі, якщо вони є)
            await conn.execute('''
                INSERT INTO users (telegram_id, username) 
                VALUES ($1, $2) 
                ON CONFLICT DO NOTHING
            ''', fake_telegram_id, fake_username)

            # Записуємо відповідь
            await conn.execute('''
                INSERT INTO answers (telegram_id, username, question_id, answer_text, reaction_time)
                VALUES ($1, $2, $3, $4, $5)
            ''', fake_telegram_id, fake_username, q_id, fake_answer, fake_reaction_time)

    await message.answer(
        "🧪 <b>Тестові дані успішно згенеровано!</b>\nДодано 10 питань та 70 відповідей на спеціальне запитання. Перевірте списки!")

def get_admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📊 Переглянути відповіді")
    builder.button(text="⚙️ Список питань")
    builder.button(text="➕ Створити питання")
    builder.button(text="🚀 Запуск опитування")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


# Заглушка для декоративних кнопок без дії
@dp.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery):
    await callback.answer()


# === ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ КЛАВІАТУР З ПАГІНАЦІЄЮ ===

async def get_answers_list_keyboard(conn, page: int = 0):
    limit = 7
    offset = page * limit
    total = await conn.fetchval('SELECT COUNT(*) FROM questions')
    questions = await conn.fetch('SELECT id, question_text FROM questions ORDER BY id DESC LIMIT $1 OFFSET $2', limit,
                                 offset)

    builder = InlineKeyboardBuilder()
    for q in questions:
        builder.button(text=f"❓ {q['question_text'][:30]}...", callback_data=f"show_ans_{q['id']}_0")
    builder.adjust(1)

    nav_btns = []
    if page > 0:
        nav_btns.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ans_list_page_{page - 1}"))
    if offset + limit < total:
        nav_btns.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"ans_list_page_{page + 1}"))

    if nav_btns:
        builder.row(*nav_btns)

    builder.row(InlineKeyboardButton(text="❌ Закрити список", callback_data="delete_this_msg"))
    return builder.as_markup()


async def get_manage_list_keyboard(conn, page: int = 0):
    limit = 5
    offset = page * limit
    total = await conn.fetchval('SELECT COUNT(*) FROM questions')
    questions = await conn.fetch('SELECT id, question_text FROM questions ORDER BY id DESC LIMIT $1 OFFSET $2', limit,
                                 offset)

    builder = InlineKeyboardBuilder()
    for q in questions:
        # Текст питання як заголовок (без дії)
        builder.row(InlineKeyboardButton(text=f"📌 {q['question_text'][:35]}", callback_data="ignore"))
        # Кнопки дій під ним
        builder.row(
            InlineKeyboardButton(text="✏️ Ред.", callback_data=f"edit_q_{q['id']}"),
            InlineKeyboardButton(text="❌ Видал.", callback_data=f"conf_del_{q['id']}")
        )

    nav_btns = []
    if page > 0:
        nav_btns.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"mng_page_{page - 1}"))
    if offset + limit < total:
        nav_btns.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"mng_page_{page + 1}"))
    if nav_btns:
        builder.row(*nav_btns)

    builder.row(InlineKeyboardButton(text="❌ Закрити меню", callback_data="delete_this_msg"))
    return builder.as_markup()


async def get_toggle_keyboard(conn, page: int = 0):
    limit = 5
    offset = page * limit
    total = await conn.fetchval('SELECT COUNT(*) FROM questions')
    questions = await conn.fetch(
        'SELECT id, question_text, is_active FROM questions ORDER BY id DESC LIMIT $1 OFFSET $2', limit, offset)

    builder = InlineKeyboardBuilder()
    for q in questions:
        # Кнопка запуску/зупинки
        if q['is_active']:
            builder.row(InlineKeyboardButton(text=f"🛑 Зупинити | {q['question_text'][:20]}...",
                                             callback_data=f"stop_{q['id']}_{page}"))
        else:
            builder.row(InlineKeyboardButton(text=f"⚪️ Запустити | {q['question_text'][:20]}...",
                                             callback_data=f"activate_{q['id']}_{page}"))

        # Кнопка перегляду відповідей прямо під запуском
        builder.row(InlineKeyboardButton(text="📊 Відповіді", callback_data=f"show_ans_{q['id']}_0"))

    nav_btns = []
    if page > 0:
        nav_btns.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"tgl_page_{page - 1}"))
    if offset + limit < total:
        nav_btns.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"tgl_page_{page + 1}"))

    if nav_btns:
        builder.row(*nav_btns)

    builder.row(InlineKeyboardButton(text="❌ Закрити меню", callback_data="delete_this_msg"))
    return builder.as_markup()


# === РОЗДІЛ: ПЕРЕГЛЯД ВІДПОВІДЕЙ ===

@dp.message(F.text == "📊 Переглянути відповіді")
async def view_answers_list(message: Message):
    if message.from_user.id not in ADMIN_IDS: return

    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        total = await conn.fetchval('SELECT COUNT(*) FROM questions')
        if total == 0:
            await message.answer("Список питань порожній.")
            return
        keyboard = await get_answers_list_keyboard(conn, page=0)

    await message.answer("Оберіть питання, щоб побачити відповіді:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("ans_list_page_"))
async def ans_list_page_callback(callback: CallbackQuery):
    page = int(callback.data.split("_")[3])
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        keyboard = await get_answers_list_keyboard(conn, page)
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@dp.callback_query(F.data == "back_to_answers_list")
async def back_to_answers_list(callback: CallbackQuery):
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        keyboard = await get_answers_list_keyboard(conn, page=0)
    await callback.message.edit_text("Оберіть питання, щоб побачити відповіді:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("show_ans_"))
async def show_answers_for_question(callback: CallbackQuery):
    parts = callback.data.split("_")
    q_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0

    limit = 15
    offset = page * limit

    pool: asyncpg.Pool = dp["db_pool"]

    async with pool.acquire() as conn:
        q_text = await conn.fetchval('SELECT question_text FROM questions WHERE id = $1', q_id)
        total_answers = await conn.fetchval('SELECT COUNT(*) FROM answers WHERE question_id = $1', q_id)
        answers = await conn.fetch('''
            SELECT username, answer_text, reaction_time 
            FROM answers 
            WHERE question_id = $1 
            ORDER BY reaction_time ASC NULLS LAST
            LIMIT $2 OFFSET $3
        ''', q_id, limit, offset)

    text = f"📊 <b>Відповіді на питання:</b>\n<i>{q_text}</i>\n\n"
    if not answers and page == 0:
        text += "Відповідей поки немає."
    else:
        for idx, ans in enumerate(answers, offset + 1):
            time_str = f"{ans['reaction_time']:.2f} сек" if ans['reaction_time'] else "Час невідомий"
            text += f"<b>{idx}.</b> @{ans['username']}: {ans['answer_text']} <i>(⏱ {time_str})</i>\n"

    builder = InlineKeyboardBuilder()

    nav_btns = []
    if page > 0:
        nav_btns.append(InlineKeyboardButton(text="⬅️ Попередня", callback_data=f"show_ans_{q_id}_{page - 1}"))
    if offset + limit < total_answers:
        nav_btns.append(InlineKeyboardButton(text="Наступна ➡️", callback_data=f"show_ans_{q_id}_{page + 1}"))

    if nav_btns:
        builder.row(*nav_btns)

    builder.row(InlineKeyboardButton(text="⬅️ Назад до списку", callback_data="back_to_answers_list"))
    builder.row(InlineKeyboardButton(text="❌ Закрити", callback_data="delete_this_msg"))

    await callback.message.edit_text(text, reply_markup=builder.as_markup())


# === РОЗДІЛ: КЕРУВАННЯ ПИТАННЯМИ (НОВА ПАГІНАЦІЯ) ===

@dp.message(F.text == "⚙️ Список питань")
async def list_questions_manage(message: Message):
    if message.from_user.id not in ADMIN_IDS: return

    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        total = await conn.fetchval('SELECT COUNT(*) FROM questions')
        if total == 0:
            await message.answer("Питань поки немає.")
            return
        keyboard = await get_manage_list_keyboard(conn, page=0)

    await message.answer("⚙️ <b>Керування питаннями:</b>", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("mng_page_"))
async def mng_page_callback(callback: CallbackQuery):
    page = int(callback.data.split("_")[2])
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        keyboard = await get_manage_list_keyboard(conn, page)
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@dp.callback_query(F.data.startswith("conf_del_"))
async def confirm_delete_q(callback: CallbackQuery):
    q_id = int(callback.data.split("_")[2])
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Так, видалити", callback_data=f"delete_q_{q_id}")
    builder.button(text="❌ Скасувати", callback_data="mng_page_0")
    await callback.message.edit_text("⚠️ Ви впевнені, що хочете видалити це питання та ВСІ відповіді на нього?",
                                     reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("delete_q_"))
async def delete_question(callback: CallbackQuery):
    q_id = int(callback.data.split("_")[2])
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM questions WHERE id = $1', q_id)
        keyboard = await get_manage_list_keyboard(conn, page=0)
    await callback.message.edit_text("🗑 Питання успішно видалено. Оновлений список:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("edit_q_"))
async def edit_question_start(callback: CallbackQuery, state: FSMContext):
    q_id = int(callback.data.split("_")[2])
    await state.update_data(edit_q_id=q_id)
    await callback.message.answer("Введіть новий текст для цього питання:")
    await state.set_state(AdminStates.waiting_for_edit_question)


@dp.message(AdminStates.waiting_for_edit_question)
async def edit_question_finish(message: Message, state: FSMContext):
    if message.text in ADMIN_MENU_BUTTONS:
        await state.clear()
        await message.answer("Дію скасовано. Будь ласка, натисніть кнопку меню ще раз.")
        return

    data = await state.get_data()
    q_id = data.get("edit_q_id")

    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        exists = await conn.fetchval('SELECT 1 FROM questions WHERE question_text = $1 AND id != $2', message.text,
                                     q_id)
        if exists:
            await message.answer("⚠️ Таке питання вже існує! Спробуйте інше.", reply_markup=get_admin_keyboard())
            await state.clear()
            return

        await conn.execute('UPDATE questions SET question_text = $1 WHERE id = $2', message.text, q_id)

    await message.answer("📝 Питання успішно оновлено!", reply_markup=get_admin_keyboard())
    await state.clear()


@dp.message(F.text == "➕ Створити питання")
async def create_question_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return

    await message.answer("Напишіть текст нового питання:")
    await state.set_state(AdminStates.waiting_for_new_question)


@dp.message(AdminStates.waiting_for_new_question)
async def create_question_finish(message: Message, state: FSMContext):
    if message.text in ADMIN_MENU_BUTTONS:
        await state.clear()
        await message.answer("Дію скасовано. Будь ласка, натисніть кнопку меню ще раз.")
        return

    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        exists = await conn.fetchval('SELECT 1 FROM questions WHERE question_text = $1', message.text)
        if exists:
            await message.answer("⚠️ Таке питання вже існує! Спробуйте інше.", reply_markup=get_admin_keyboard())
            await state.clear()
            return

        await conn.execute('INSERT INTO questions (question_text) VALUES ($1)', message.text)

    await message.answer("✨ Питання успішно додано до бази даних!", reply_markup=get_admin_keyboard())
    await state.clear()


# === РОЗДІЛ: МЕНЮ ЗАПУСКУ ОПИТУВАННЯ ===

@dp.message(F.text == "🚀 Запуск опитування")
async def toggle_collection_menu_msg(message: Message):
    if message.from_user.id not in ADMIN_IDS: return

    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        total = await conn.fetchval('SELECT COUNT(*) FROM questions')
        if total == 0:
            await message.answer("Список питань порожній. Спочатку створіть питання.")
            return
        keyboard = await get_toggle_keyboard(conn, page=0)

    await message.answer("🚀 Оберіть питання для запуску:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("tgl_page_"))
async def tgl_page_callback(callback: CallbackQuery):
    page = int(callback.data.split("_")[2])
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        keyboard = await get_toggle_keyboard(conn, page)
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@dp.callback_query(F.data.startswith("activate_"))
async def activate_question(callback: CallbackQuery):
    parts = callback.data.split("_")
    q_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('UPDATE questions SET is_active = FALSE')
            await conn.execute('UPDATE questions SET is_active = TRUE WHERE id = $1', q_id)

        q_text = await conn.fetchval('SELECT question_text FROM questions WHERE id = $1', q_id)
        users = await conn.fetch('SELECT telegram_id FROM users')
        keyboard = await get_toggle_keyboard(conn, page)

    await callback.message.edit_reply_markup(reply_markup=keyboard)

    msg_text = (
        "🔔 <b>УВАГА, НОВЕ ЗАПИТАННЯ!</b> 🔔\n\n"
        f"❓ <b>{q_text}</b>\n\n"
        "💬 <i>Просто напишіть вашу відповідь у цей чат:</i>"
    )

    success_count = 0
    for u in users:
        if u['telegram_id'] not in ADMIN_IDS:
            try:
                await bot.send_message(u['telegram_id'], msg_text)
                async with pool.acquire() as conn:
                    await conn.execute('UPDATE users SET last_delivered_at = CURRENT_TIMESTAMP WHERE telegram_id = $1',
                                       u['telegram_id'])
                success_count += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)

    await callback.answer(f"Запущено! Повідомлено {success_count} користувачів.", show_alert=True)


@dp.callback_query(F.data.startswith("stop_"))
async def stop_question(callback: CallbackQuery):
    parts = callback.data.split("_")
    q_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        await conn.execute('UPDATE questions SET is_active = FALSE')
        keyboard = await get_toggle_keyboard(conn, page)

    await callback.answer("Опитування зупинено!")
    await callback.message.edit_reply_markup(reply_markup=keyboard)


# === ПРИЙОМ ВІДПОВІДЕЙ ===

@dp.message(F.text & ~F.text.startswith('/'))
async def handle_any_text_answer(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return

    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        # Зберігаємо або оновлюємо юзернейм
        await conn.execute('''
            INSERT INTO users (telegram_id, username) 
            VALUES ($1, $2) 
            ON CONFLICT (telegram_id) 
            DO UPDATE SET username = EXCLUDED.username
        ''', message.from_user.id, message.from_user.username)

        active_q = await conn.fetchrow('SELECT id FROM questions WHERE is_active = TRUE LIMIT 1')
        if not active_q:
            if message.from_user.id in ADMIN_IDS: return
            await message.answer("Наразі немає активних опитувань. Відпочивайте! 😊")
            return

        q_id = active_q['id']

        already_answered = await conn.fetchval('SELECT 1 FROM answers WHERE telegram_id = $1 AND question_id = $2',
                                               message.from_user.id, q_id)
        if already_answered:
            await message.answer("🛑 Ви вже відповіли на це питання! Відповідь приймається лише один раз.")
            return

        username = message.from_user.username or message.from_user.full_name

        await conn.execute('''
            INSERT INTO answers (telegram_id, username, question_id, answer_text, reaction_time)
            VALUES (
                $1, $2, $3, $4, 
                EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - (SELECT last_delivered_at FROM users WHERE telegram_id = $1)))
            )
        ''', message.from_user.id, username, q_id, message.text)

    await message.answer("Вашу відповідь успішно прийнято! Дякую. ✅")


@dp.callback_query(F.data == "delete_this_msg")
async def delete_inline_message(callback: CallbackQuery):
    await callback.message.delete()


async def main():
    await init_db()
    print("Робот-опитувальник успішно запущено!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())