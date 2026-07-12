import asyncpg
from config import DATABASE_URL


async def init_db() -> asyncpg.Pool:
    # Зменшено max_size до 20 (Більш безпечно для Railway/Supabase тощо)
    pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=20,
        command_timeout=60,  # Захист від зависання запитів
        max_inactive_connection_lifetime=300  # Кожні 5 хв очищати "мертві" підключення
    )

    async with pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                last_delivered_at TIMESTAMP,
                username VARCHAR(255)
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
                reaction_time REAL,
                CONSTRAINT unique_user_question UNIQUE (telegram_id, question_id)
            );
        ''')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_ans_leaderboard ON answers (question_id, reaction_time);')
        await conn.execute('ALTER TABLE answers DROP CONSTRAINT IF EXISTS unique_user_question;')
        await conn.execute('ALTER TABLE answers ADD CONSTRAINT unique_user_question UNIQUE (telegram_id, question_id);')
        # Швидкісні індекси для оптимізації
        await conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_questions_active ON questions (is_active) WHERE is_active = TRUE;')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_answers_question_id ON answers (question_id);')

    return pool