import os
import asyncio
import asyncpg
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# Безпечно отримуємо список адмінів (навіть якщо він один або їх кілька через кому)
admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip().isdigit()]

# Резервний варіант, якщо ти забув перейменувати змінну в .env
if not ADMIN_IDS and os.getenv("ADMIN_ID", "").strip().isdigit():
    ADMIN_IDS = [int(os.getenv("ADMIN_ID").strip())]

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не знайдено в .env!")

# Додаємо HTML-форматування за замовчуванням, щоб текст був красивим
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- СТАНИ (Тільки для адміна) ---
class AdminStates(StatesGroup):
    waiting_for_new_question = State()
    waiting_for_edit_question = State()

# --- ІНІЦІАЛІЗАЦІЯ БД ---
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

# --- ЗАГАЛЬНІ КОМАНДИ ---
@dp.message(Command("myid"))
async def get_my_id(message: Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")

@dp.message(Command("start"))
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    
    pool: asyncpg.Pool = dp["db_pool"]
    # Зберігаємо користувача для майбутніх розсилок
    async with pool.acquire() as conn:
        await conn.execute('INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT DO NOTHING', message.from_user.id)
    
    # Якщо це адмін - мовчки даємо йому меню
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Головне меню:", reply_markup=get_admin_keyboard())
        return
        
    # Якщо звичайний користувач - перевіряємо, чи є активне питання
    async with pool.acquire() as conn:
        active_q = await conn.fetchrow('SELECT id, question_text FROM questions WHERE is_active = TRUE LIMIT 1')
        
    if not active_q:
        await message.answer("Привіт! Я бот для опитувань. Коли з'явиться нове запитання, я обов'язково тобі його надішлю. Просто чекай! 😊")
        return

    # Гарне форматування запитання
    msg_text = (
        "🔔 <b>АКТИВНЕ ЗАПИТАННЯ</b> 🔔\n\n"
        f"❓ <b>{active_q['question_text']}</b>\n\n"
        "💬 <i>Просто напишіть вашу відповідь у цей чат:</i>"
    )
    await message.answer(msg_text)

# --- АДМІН-ПАНЕЛЬ (REPLY КЛАВІАТУРА) ---
def get_admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📊 Переглянути відповіді")
    builder.button(text="⚙️ Список питань")
    builder.button(text="➕ Створити питання")
    builder.button(text="🚀 Запуск опитування")
    builder.adjust(2) 
    return builder.as_markup(resize_keyboard=True)

# 1. Перегляд відповідей
@dp.message(F.text == "📊 Переглянути відповіді")
async def view_answers_list(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        questions = await conn.fetch('SELECT id, question_text FROM questions ORDER BY id')
    
    if not questions:
        await message.answer("Список питань порожній.")
        return
        
    builder = InlineKeyboardBuilder()
    for q in questions:
        builder.button(text=f"❓ {q['question_text'][:30]}...", callback_data=f"show_ans_{q['id']}")
    builder.button(text="❌ Закрити список", callback_data="delete_this_msg")
    builder.adjust(1)
    
    await message.answer("Оберіть питання, щоб побачити відповіді:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("show_ans_"))
async def show_answers_for_question(callback: CallbackQuery):
    q_id = int(callback.data.split("_")[2])
    pool: asyncpg.Pool = dp["db_pool"]
    
    async with pool.acquire() as conn:
        q_text = await conn.fetchval('SELECT question_text FROM questions WHERE id = $1', q_id)
        answers = await conn.fetch('SELECT username, answer_text, submitted_at FROM answers WHERE question_id = $1 ORDER BY submitted_at', q_id)
        
    text = f"📊 <b>Відповіді на питання:</b>\n<i>{q_text}</i>\n\n"
    if not answers:
        text += "Відповідей поки немає."
    else:
        for idx, ans in enumerate(answers, 1):
            text += f"<b>{idx}.</b> @{ans['username']}: {ans['answer_text']}\n"
            
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Закрити", callback_data="delete_this_msg")
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

# 2. Список питань (Редагування / Видалення)
@dp.message(F.text == "⚙️ Список питань")
async def list_questions_manage(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        questions = await conn.fetch('SELECT id, question_text FROM questions ORDER BY id')
        
    if not questions:
        await message.answer("Питань поки немає.")
        return
        
    await message.answer("⚙️ <b>Керування питаннями:</b>")
    for q in questions:
        builder = InlineKeyboardBuilder()
        builder.button(text="✏️ Ред.", callback_data=f"edit_q_{q['id']}")
        builder.button(text="❌ Видал.", callback_data=f"conf_del_{q['id']}")
        builder.adjust(2)
        await message.answer(f"<b>Питання №{q['id']}:</b> {q['question_text']}", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("conf_del_"))
async def confirm_delete_q(callback: CallbackQuery):
    q_id = int(callback.data.split("_")[2])
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Так, видалити", callback_data=f"delete_q_{q_id}")
    builder.button(text="❌ Скасувати", callback_data="delete_this_msg")
    await callback.message.edit_text("⚠️ Ви впевнені, що хочете видалити це питання та ВСІ відповіді на нього?", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("delete_q_"))
async def delete_question(callback: CallbackQuery):
    q_id = int(callback.data.split("_")[2])
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM questions WHERE id = $1', q_id)
    await callback.message.edit_text("🗑 Питання успішно видалено.")

@dp.callback_query(F.data.startswith("edit_q_"))
async def edit_question_start(callback: CallbackQuery, state: FSMContext):
    q_id = int(callback.data.split("_")[2])
    await state.update_data(edit_q_id=q_id)
    await callback.message.answer("Введіть новий текст для цього питання:")
    await state.set_state(AdminStates.waiting_for_edit_question)

@dp.message(AdminStates.waiting_for_edit_question)
async def edit_question_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    q_id = data.get("edit_q_id")
    new_text = message.text
    
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        await conn.execute('UPDATE questions SET question_text = $1 WHERE id = $2', new_text, q_id)
        
    await message.answer("📝 Питання успішно оновлено!", reply_markup=get_admin_keyboard())
    await state.clear()

# 3. Створення питання
@dp.message(F.text == "➕ Створити питання")
async def create_question_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    
    await message.answer("Напишіть текст нового питання:")
    await state.set_state(AdminStates.waiting_for_new_question)

@dp.message(AdminStates.waiting_for_new_question)
async def create_question_finish(message: Message, state: FSMContext):
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        await conn.execute('INSERT INTO questions (question_text) VALUES ($1)', message.text)
        
    await message.answer("✨ Питання успішно додано до бази даних!", reply_markup=get_admin_keyboard())
    await state.clear()

# 4. Меню керування опитуваннями (Розсилка та зупинка)
async def get_toggle_keyboard(conn):
    questions = await conn.fetch('SELECT id, question_text, is_active FROM questions ORDER BY id')
    builder = InlineKeyboardBuilder()
    
    for q in questions:
        if q['is_active']:
            builder.button(text=f"🛑 Зупинити | {q['question_text'][:20]}...", callback_data=f"stop_{q['id']}")
        else:
            builder.button(text=f"⚪️ Запустити | {q['question_text'][:20]}...", callback_data=f"activate_{q['id']}")
            
    builder.adjust(1)
    builder.button(text="❌ Закрити меню", callback_data="delete_this_msg")
    return builder.as_markup()

@dp.message(F.text == "🚀 Запуск опитування")
async def toggle_collection_menu_msg(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        questions = await conn.fetch('SELECT id FROM questions LIMIT 1')
        if not questions:
            await message.answer("Список питань порожній. Спочатку створіть питання.")
            return
        keyboard = await get_toggle_keyboard(conn)
        
    await message.answer("🚀 Оберіть питання для запуску (одночасно працює лише одне):", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("activate_"))
async def activate_question(callback: CallbackQuery):
    q_id = int(callback.data.split("_")[1])
    pool: asyncpg.Pool = dp["db_pool"]
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Зупиняємо всі інші та запускаємо обране
            await conn.execute('UPDATE questions SET is_active = FALSE')
            await conn.execute('UPDATE questions SET is_active = TRUE WHERE id = $1', q_id)
            
        q_text = await conn.fetchval('SELECT question_text FROM questions WHERE id = $1', q_id)
        users = await conn.fetch('SELECT telegram_id FROM users')
        keyboard = await get_toggle_keyboard(conn)
            
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    
    # РОЗСИЛКА ПИТАННЯ КОРИСТУВАЧАМ
    success_count = 0
    msg_text = (
        "🔔 <b>УВАГА, НОВЕ ЗАПИТАННЯ!</b> 🔔\n\n"
        f"❓ <b>{q_text}</b>\n\n"
        "💬 <i>Просто напишіть вашу відповідь у цей чат:</i>"
    )
    
    for u in users:
        if u['telegram_id'] not in ADMIN_IDS: # Не спамимо адмінів
            try:
                await bot.send_message(u['telegram_id'], msg_text)
                success_count += 1
            except Exception:
                pass # Користувач заблокував бота
                
    await callback.answer(f"Запущено! Повідомлено {success_count} користувачів.", show_alert=True)

@dp.callback_query(F.data.startswith("stop_"))
async def stop_question(callback: CallbackQuery):
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        await conn.execute('UPDATE questions SET is_active = FALSE')
        keyboard = await get_toggle_keyboard(conn)
        
    await callback.answer("Опитування зупинено!")
    await callback.message.edit_reply_markup(reply_markup=keyboard)


# --- ПРИЙОМ ВІДПОВІДЕЙ ВІД КОРИСТУВАЧІВ ---
# Важливо: Фільтр ~F.text.startswith('/') означає "реагувати тільки на текст, який НЕ починається на скісну риску"
@dp.message(F.text & ~F.text.startswith('/'))
async def handle_any_text_answer(message: Message, state: FSMContext):
    # Якщо це адмін і він зараз створює/редагує питання - ігноруємо
    current_state = await state.get_state()
    if current_state is not None:
        return
        
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        # Додаємо користувача в базу, якщо він тут вперше
        await conn.execute('INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT DO NOTHING', message.from_user.id)
        
        active_q = await conn.fetchrow('SELECT id FROM questions WHERE is_active = TRUE LIMIT 1')
        if not active_q:
            # Якщо адмін пише щось повз кнопки меню
            if message.from_user.id in ADMIN_IDS: return 
            await message.answer("Наразі немає активних опитувань. Відпочивайте! 😊")
            return
            
        q_id = active_q['id']
        
        # Перевірка на дублювання відповіді
        already_answered = await conn.fetchval('SELECT 1 FROM answers WHERE telegram_id = $1 AND question_id = $2', message.from_user.id, q_id)
        if already_answered:
            await message.answer("🛑 Ви вже відповіли на це питання! Відповідь приймається лише один раз.")
            return
            
        username = message.from_user.username or message.from_user.full_name
        await conn.execute('''
            INSERT INTO answers (telegram_id, username, question_id, answer_text)
            VALUES ($1, $2, $3, $4)
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