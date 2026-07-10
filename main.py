import os
import asyncio
import asyncpg
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# 1. Загружаем секреты из .env файла (локально) 
# На Railway эта функция просто ничего не сделает, так как переменные уже будут в системе
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))  # int, так как Telegram ID это число
DATABASE_URL = os.getenv("DATABASE_URL")

# Если токена нет, бот даже не попытается запуститься
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден! Проверьте файл .env или настройки Railway.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# 2. Машина состояний
class QuizForm(StatesGroup):
    question_1 = State()
    question_2 = State()

# 3. Настройка Базы Данных (создаем пулы и таблицы)
async def init_db():
    pool = await asyncpg.create_pool(DATABASE_URL)
    dp["db_pool"] = pool
    
    # Автоматически создаем таблицы при запуске, если их еще нет
    async with pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS questions (
                id SERIAL PRIMARY KEY,
                question_text TEXT NOT NULL
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
        
        # Заглушка: добавляем два вопроса, если таблица пуста
        # (В реальном проекте вы добавите их вручную через админку или напрямую в БД)
        count = await conn.fetchval('SELECT COUNT(*) FROM questions')
        if count == 0:
            await conn.execute("INSERT INTO questions (id, question_text) VALUES (1, 'Как называется столица Франции?')")
            await conn.execute("INSERT INTO questions (id, question_text) VALUES (2, 'Сколько будет 2+2?')")

# 4. Логика бота
@dp.message(Command("start"))
async def start_survey(message: Message, state: FSMContext):
    await state.clear()
    
    # Получаем текст первого вопроса из БД
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        q_text = await conn.fetchval('SELECT question_text FROM questions WHERE id = 1')
        
    await message.answer(f"Привет! Ответь на пару вопросов.\n\n**Вопрос 1:** {q_text}")
    await state.set_state(QuizForm.question_1)

@dp.message(QuizForm.question_1)
async def process_question_1(message: Message, state: FSMContext):
    await state.update_data(answer_1=message.text)
    
    # Получаем текст второго вопроса
    pool: asyncpg.Pool = dp["db_pool"]
    async with pool.acquire() as conn:
        q_text = await conn.fetchval('SELECT question_text FROM questions WHERE id = 2')
        
    await message.answer(f"**Вопрос 2:** {q_text}")
    await state.set_state(QuizForm.question_2)

@dp.message(QuizForm.question_2)
async def process_question_2(message: Message, state: FSMContext):
    data = await state.get_data()
    answer_1 = data.get("answer_1")
    answer_2 = message.text
    
    user_id = message.from_user.id
    username = message.from_user.username or "Без_юзернейма"
    
    pool: asyncpg.Pool = dp["db_pool"]
    
    insert_query = '''
        INSERT INTO answers (telegram_id, username, question_id, answer_text)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (telegram_id, question_id) 
        DO UPDATE SET answer_text = EXCLUDED.answer_text, username = EXCLUDED.username
    '''
    
    async with pool.acquire() as conn:
        # Пишем оба ответа в базу
        await conn.execute(insert_query, user_id, username, 1, answer_1)
        await conn.execute(insert_query, user_id, username, 2, answer_2)

    await message.answer("Все твои ответы успешно сохранены! 🎉")
    
    # Опционально: Уведомляем админа
    if ADMIN_ID != 0:
        await bot.send_message(
            ADMIN_ID, 
            f"✅ @{username} прошел опрос!"
        )
        
    await state.clear()

async def main():
    await init_db()
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())