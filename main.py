import os
import asyncio
import asyncpg
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- СОСТОЯНИЯ (FSM) ---
class AdminStates(StatesGroup):
    waiting_for_new_question = State()
    waiting_for_edit_question = State()

class UserStates(StatesGroup):
    waiting_for_answer = State()

# --- ИНИЦИАЛИЗАЦИЯ БД ---
async def init_db():
    pool = await asyncpg.create_pool(DATABASE_URL)
    dp["db_pool"] = pool
    
    async with pool.acquire() as conn:
        await conn.execute('''
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

# --- ОБЩИЕ КОМАНДЫ ---
@dp.message(Command("myid"))
async def get_my_id(message: Message):
    """Команда для получения Telegram ID пользователя"""
    await message.answer(f"Ваш Telegram ID: `{message.from_user.id}`\nСкопируйте его и добавьте в переменную ADMIN_ID.")

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---
@dp.message(Command("start"))
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    pool: asyncpg.Pool = dp["db_pool"]
    
    async with pool.acquire() as conn:
        # Ищем активный вопрос
        active_q = await conn.fetchrow('SELECT id, question_text FROM questions WHERE is_active = TRUE LIMIT 1')
        
    if not active_q:
        await message.answer("В данный момент нет активных опросов. Отдыхайте! 😊")
        return

    # Если активный вопрос есть, переводим пользователя в режим ответа
    await state.update_data(current_q_id=active_q['id'])
    await message.answer(f"📢 **Внимание, активный вопрос!**\n\n{active_q['question_text']}\n\nНапишите ваш ответ в ответном сообщении:")
    await state.set_state(UserStates.waiting_for_answer)

@dp.message(UserStates.waiting_for_answer)
async def handle_user_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    q_id = data.get("current_q_id")
    
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name
    answer_text = message.text
    
    pool: asyncpg.Pool = dp["db_pool"]
    
    try:
        async with pool.acquire() as conn:
            # Проверяем, активен ли еще этот вопрос (защита на случай, если админ закрыл опрос, пока юзер писал)
            is_still_active = await conn.fetchval('SELECT is_active FROM questions WHERE id = $1', q_id)
            if not is_still_active:
                await message.answer("Извините, сбор ответов на этот вопрос уже закрыт. ⏱")
                await state.clear()
                return

            await conn.execute('''
                INSERT INTO answers (telegram_id, username, question_id, answer_text)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (telegram_id, question_id) 
                DO UPDATE SET answer_text = EXCLUDED.answer_text, username = EXCLUDED.username
            ''', user_id, username, q_id, answer_text)
            
        await message.answer("Ваш ответ успешно принят! Спасибо. ✅")
        await state.clear()
        
        # Уведомляем админа
        await bot.send_message(ADMIN_ID, f"📩 Получен новый ответ от @{username}")
    except Exception as e:
        await message.answer("Произошла ошибка при сохранении ответа.")

# --- АДМИН-ПАНЕЛЬ (КЛАВИАТУРА И КОМАНДА) ---
def get_admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Посмотреть ответы", callback_code="adm_view_answers")
    builder.button(text="⚙️ Список вопросов (Ред/Уд)", callback_code="adm_list_questions")
    builder.button(text="➕ Создать вопрос", callback_code="adm_create_q")
    builder.button(text="🚀 Управление сбором ответов", callback_code="adm_toggle_collection")
    builder.adjust(1) # Все кнопки в один ряд сверху вниз
    return builder.as_markup()

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🔑 Добро пожаловать в панель управления ботом:", reply_markup=get_admin_keyboard())

# --- ОБРАБОТЧИКИ АДМИН-КНОПОК ---

# 1. Просмотр ответов
@dp.callback_query(F.data == "adm_view_answers")
async def view_answers_list(callback: CallbackQuery):
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        questions = await conn.fetch('SELECT id, question_text FROM questions ORDER BY id')
    
    if not questions:
        await callback.message.edit_text("Список вопросов пуст.", reply_markup=get_admin_keyboard())
        return
        
    builder = InlineKeyboardBuilder()
    for q in questions:
        builder.button(text=f"❓ {q['question_text'][:30]}...", callback_data=f"show_ans_{q['id']}")
    builder.button(text="⬅️ Назад", callback_data="back_to_menu")
    builder.adjust(1)
    
    await callback.message.edit_text("Выберите вопрос, чтобы увидеть ответы:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("show_ans_"))
async def show_answers_for_question(callback: CallbackQuery):
    q_id = int(callback.data.split("_")[2])
    pool: asyncpg.Pool = dp["db_pool"]
    
    async with pool.acquire() as conn:
        q_text = await conn.fetchval('SELECT question_text FROM questions WHERE id = $1', q_id)
        answers = await conn.fetch('SELECT username, answer_text, submitted_at FROM answers WHERE question_id = $1 ORDER BY submitted_at', q_id)
        
    text = f"📊 **Ответы на вопрос:** {q_text}\n\n"
    if not answers:
        text += "Ответов пока нет."
    else:
        for idx, ans in enumerate(answers, 1):
            text += f"{idx}. @{ans['username']}: {ans['answer_text']}\n"
            
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ К списку вопросов", callback_data="adm_view_answers")
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

# 2. Список вопросов (Редактирование / Удаление)
@dp.callback_query(F.data == "adm_list_questions")
async def list_questions_manage(callback: CallbackQuery):
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        questions = await conn.fetch('SELECT id, question_text FROM questions ORDER BY id')
        
    if not questions:
        await callback.message.edit_text("Вопросов пока нет.", reply_markup=get_admin_keyboard())
        return
        
    await callback.message.answer("⚙️ **Управление вопросами:**")
    for q in questions:
        builder = InlineKeyboardBuilder()
        builder.button(text="✏️ Ред.", callback_data=f"edit_q_{q['id']}")
        builder.button(text="❌ Удал.", callback_data=f"conf_del_{q['id']}")
        builder.adjust(2)
        await callback.message.answer(f"Вопрос №{q['id']}: {q['question_text']}", reply_markup=builder.as_markup())

# Подтверждение удаления
@dp.callback_query(F.data.startswith("conf_del_"))
async def confirm_delete_q(callback: CallbackQuery):
    q_id = int(callback.data.split("_")[2])
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=f"delete_q_{q_id}")
    builder.button(text="❌ Отмена", callback_data="back_to_menu")
    await callback.message.edit_text("⚠️ Вы уверены, что хотите удалить этот вопрос и ВСЕ ответы на него?", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("delete_q_"))
async def delete_question(callback: CallbackQuery):
    q_id = int(callback.data.split("_")[2])
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM questions WHERE id = $1', q_id)
    await callback.message.edit_text("🗑 Вопрос успешно удален.", reply_markup=get_admin_keyboard())

# Редактирование вопроса (Запрос нового текста)
@dp.callback_query(F.data.startswith("edit_q_"))
async def edit_question_start(callback: CallbackQuery, state: FSMContext):
    q_id = int(callback.data.split("_")[2])
    await state.update_data(edit_q_id=q_id)
    await callback.message.answer("Введите новый текст для этого вопроса:")
    await state.set_state(AdminStates.waiting_for_edit_question)

@dp.message(AdminStates.waiting_for_edit_question)
async def edit_question_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    q_id = data.get("edit_q_id")
    new_text = message.text
    
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        await conn.execute('UPDATE questions SET question_text = $1 WHERE id = $2', new_text, q_id)
        
    await message.answer("📝 Вопрос успешно обновлен!", reply_markup=get_admin_keyboard())
    await state.clear()

# 3. Создание вопроса
@dp.callback_query(F.data == "adm_create_q")
async def create_question_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Напишите текст нового вопроса:")
    await state.set_state(AdminStates.waiting_for_new_question)

@dp.message(AdminStates.waiting_for_new_question)
async def create_question_finish(message: Message, state: FSMContext):
    q_text = message.text
    pool: asyncpg.Pool = dp["db_pool"]
    
    async with pool.acquire() as conn:
        await conn.execute('INSERT INTO questions (question_text) VALUES ($1)', q_text)
        
    await message.answer("✨ Вопрос успешно добавлен в базу данных!", reply_markup=get_admin_keyboard())
    await state.clear()

# 4. Сбор ответов на текущий вопрос (Активация)
@dp.callback_query(F.data == "adm_toggle_collection")
async def toggle_collection_menu(callback: CallbackQuery):
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        questions = await conn.fetch('SELECT id, question_text, is_active FROM questions ORDER BY id')
        
    if not questions:
        await callback.message.edit_text("Список вопросов пуст. Сначала создайте вопрос.", reply_markup=get_admin_keyboard())
        return
        
    builder = InlineKeyboardBuilder()
    for q in questions:
        status = "🟢 АКТИВЕН" if q['is_active'] else "⚪️ Запустить"
        builder.button(text=f"{status} | {q['question_text'][:20]}...", callback_data=f"activate_{q['id']}")
    
    builder.button(text="⏹ Остановить все опросы", callback_data="stop_all_quizzes")
    builder.button(text="⬅️ Назад", callback_data="back_to_menu")
    builder.adjust(1)
    
    await callback.message.edit_text("🚀 Выберите вопрос для запуска сбора ответов (может быть активен только один):", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("activate_"))
async def activate_question(callback: CallbackQuery):
    q_id = int(callback.data.split("_")[1])
    pool: asyncpg.Pool = dp["db_pool"]
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Деактивируем все вопросы
            await conn.execute('UPDATE questions SET is_active = FALSE')
            # Активируем выбранный
            await conn.execute('UPDATE questions SET is_active = TRUE WHERE id = $1', q_id)
            
    await callback.answer("Опрос успешно запущен!")
    await toggle_collection_menu(callback) # Обновляем меню статусов

@dp.callback_query(F.data == "stop_all_quizzes")
async def stop_all_quizzes(callback: CallbackQuery):
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        await conn.execute('UPDATE questions SET is_active = FALSE')
    await callback.answer("Все опросы остановлены.")
    await toggle_collection_menu(callback)

# Назад в главное меню
@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu_callback(callback: CallbackQuery):
    await callback.message.edit_text("🔑 Панель управления бота:", reply_markup=get_admin_keyboard())


async def main():
    await init_db()
    print("Робот-опросник успешно запущен и ждет команды /admin...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())