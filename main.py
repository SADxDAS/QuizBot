import os
import asyncio
import asyncpg
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
#ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
ADMIN_ID =973920888
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
 # --- ИНИЦИАЛИЗАЦИЯ БД ---
async def init_db():
    pool = await asyncpg.create_pool(DATABASE_URL)
    dp["db_pool"] = pool
    
    async with pool.acquire() as conn:
        # ⚠️ ВРЕМЕННЫЕ СТРОКИ: Жестко удаляем старые таблицы со всеми связями
        await conn.execute('DROP TABLE IF EXISTS answers CASCADE;')
        await conn.execute('DROP TABLE IF EXISTS questions CASCADE;')
        
        # Создаем чистые таблицы с правильной структурой
        await conn.execute('''
            CREATE TABLE questions (
                id SERIAL PRIMARY KEY,
                question_text TEXT NOT NULL,
                is_active BOOLEAN DEFAULT FALSE
            );
            
            CREATE TABLE answers (
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

@dp.message(Command("start"))
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    
    # 1. Автоматическое определение администратора
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "🔑 Приветствую! Вы распознаны как администратор. Вот панель управления:", 
            reply_markup=get_admin_keyboard()
        )
        return
        
    # 2. Маршрутизация для обычных пользователей
    pool: asyncpg.Pool = dp["db_pool"]
    
    async with pool.acquire() as conn:
        active_q = await conn.fetchrow('SELECT id, question_text FROM questions WHERE is_active = TRUE LIMIT 1')
        
    if not active_q:
        await message.answer("В данный момент нет активных опросов. Отдыхайте! 😊")
        return

    # Если активный вопрос есть, запускаем процесс сбора ответа
    await state.update_data(current_q_id=active_q['id'])
    await message.answer(
        f"📢 **Внимание, активный вопрос!**\n\n{active_q['question_text']}\n\nНапишите ваш ответ в ответном сообщении:"
    )
    # Переводим в состояние ожидания ответа. НИКАКИХ state.clear() после этой строки быть не должно!
    await state.set_state(UserStates.waiting_for_answer)
    await state.clear()
    
    # 1. Автоматическое определение администратора
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "🔑 Приветствую! Вы распознаны как администратор. Вот панель управления:", 
            reply_markup=get_admin_keyboard()
        )
        return # Останавливаем выполнение, админу вопросы не показываем
        
    # 2. Маршрутизация для обычных пользователей
    pool: asyncpg.Pool = dp["db_pool"]
    
    async with pool.acquire() as conn:
        active_q = await conn.fetchrow('SELECT id, question_text FROM questions WHERE is_active = TRUE LIMIT 1')
        
    if not active_q:
        await message.answer("В данный момент нет активных опросов. Отдыхайте! 😊")
        return

    # Если активный вопрос есть, запускаем процесс сбора ответа
    await state.update_data(current_q_id=active_q['id'])
    await message.answer(
        f"📢 **Внимание, активный вопрос!**\n\n{active_q['question_text']}\n\nНапишите ваш ответ в ответном сообщении:"
    )
    await state.set_state(UserStates.waiting_for_answer)
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

# --- АДМИН-ПАНЕЛЬ (REPLY КЛАВИАТУРА ПОД ПОЛЕМ ВВОДА) ---
def get_admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📊 Посмотреть ответы")
    builder.button(text="⚙️ Список вопросов")
    builder.button(text="➕ Создать вопрос")
    builder.button(text="🚀 Запуск опроса")
    builder.adjust(2) # Делаем по 2 кнопки в ряд для красоты
    # resize_keyboard=True делает их нормального размера
    return builder.as_markup(resize_keyboard=True)

# --- ОБРАБОТЧИКИ ТЕКСТОВЫХ АДМИН-КНОПОК ---

# 1. Просмотр ответов
@dp.message(F.text == "📊 Посмотреть ответы")
async def view_answers_list(message: Message):
    if message.from_user.id != ADMIN_ID: return
    
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        questions = await conn.fetch('SELECT id, question_text FROM questions ORDER BY id')
    
    if not questions:
        await message.answer("Список вопросов пуст.")
        return
        
    builder = InlineKeyboardBuilder()
    for q in questions:
        builder.button(text=f"❓ {q['question_text'][:30]}...", callback_data=f"show_ans_{q['id']}")
    builder.button(text="❌ Закрыть список", callback_data="delete_this_msg")
    builder.adjust(1)
    
    await message.answer("Выберите вопрос, чтобы увидеть ответы:", reply_markup=builder.as_markup())

# 2. Список вопросов (Редактирование / Удаление)
@dp.message(F.text == "⚙️ Список вопросов")
async def list_questions_manage(message: Message):
    if message.from_user.id != ADMIN_ID: return
    
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        questions = await conn.fetch('SELECT id, question_text FROM questions ORDER BY id')
        
    if not questions:
        await message.answer("Вопросов пока нет.")
        return
        
    await message.answer("⚙️ **Управление вопросами:**")
    for q in questions:
        builder = InlineKeyboardBuilder()
        builder.button(text="✏️ Ред.", callback_data=f"edit_q_{q['id']}")
        builder.button(text="❌ Удал.", callback_data=f"conf_del_{q['id']}")
        builder.adjust(2)
        await message.answer(f"Вопрос №{q['id']}: {q['question_text']}", reply_markup=builder.as_markup())

# 3. Создание вопроса
@dp.message(F.text == "➕ Создать вопрос")
async def create_question_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    
    await message.answer("Напишите текст нового вопроса:", reply_markup=get_admin_keyboard())
    await state.set_state(AdminStates.waiting_for_new_question)

# 4. Сбор ответов на текущий вопрос (Активация)
@dp.message(F.text == "🚀 Запуск опроса")
async def toggle_collection_menu_msg(message: Message):
    if message.from_user.id != ADMIN_ID: return
    
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        questions = await conn.fetch('SELECT id, question_text, is_active FROM questions ORDER BY id')
        
    if not questions:
        await message.answer("Список вопросов пуст. Сначала создайте вопрос.")
        return
        
    builder = InlineKeyboardBuilder()
    for q in questions:
        status = "🟢 АКТИВЕН" if q['is_active'] else "⚪️ Запустить"
        builder.button(text=f"{status} | {q['question_text'][:20]}...", callback_data=f"activate_{q['id']}")
    
    builder.button(text="⏹ Остановить все опросы", callback_data="stop_all_quizzes")
    builder.button(text="❌ Закрыть", callback_data="delete_this_msg")
    builder.adjust(1)
    
    await message.answer("🚀 Выберите вопрос для запуска сбора ответов:", reply_markup=builder.as_markup())


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
# Кнопка закрытия инлайн-сообщения
@dp.callback_query(F.data == "delete_this_msg")
async def delete_inline_message(callback: CallbackQuery):
    await callback.message.delete()

async def main():
    await init_db()
    print("Робот-опросник успешно запущен и ждет команды /admin...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())