from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

import asyncpg


def get_single_question_keyboard(q_id: int):
    """Клавіатура під кожним окремим питанням"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✏️ Редагувати", callback_data=f"edit_q_{q_id}"),
        InlineKeyboardButton(text="❌ Видалити", callback_data=f"conf_del_{q_id}")
    )
    return builder.as_markup()


def get_pagination_keyboard(page: int, total: int, limit: int):
    """Окрема клавіатура для пагінації повідомлень"""
    builder = InlineKeyboardBuilder()
    nav_btns = []

    if page > 0:
        nav_btns.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"mng_page_{page - 1}"))
    if (page + 1) * limit < total:
        nav_btns.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"mng_page_{page + 1}"))

    if nav_btns:
        builder.row(*nav_btns)

    builder.row(InlineKeyboardButton(text="❌ Закрити меню", callback_data="delete_this_msg"))
    return builder.as_markup()
async def get_answers_list_keyboard(pool: asyncpg.Pool, page: int = 0):
    limit, offset = 7, page * 7
    async with pool.acquire() as conn:
        total = await conn.fetchval('SELECT COUNT(*) FROM questions')
        questions = await conn.fetch('SELECT id, question_text FROM questions ORDER BY id DESC LIMIT $1 OFFSET $2',
                                     limit, offset)

    builder = InlineKeyboardBuilder()
    for q in questions:
        builder.button(text=f"❓ {q['question_text'][:30]}...", callback_data=f"show_ans_{q['id']}_0")
    builder.adjust(1)

    nav_btns = []
    if page > 0: nav_btns.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ans_list_page_{page - 1}"))
    if offset + limit < total: nav_btns.append(
        InlineKeyboardButton(text="Вперед ➡️", callback_data=f"ans_list_page_{page + 1}"))
    if nav_btns: builder.row(*nav_btns)
    builder.row(InlineKeyboardButton(text="❌ Закрити список", callback_data="delete_this_msg"))
    return builder.as_markup()


async def get_manage_list_keyboard(pool: asyncpg.Pool, page: int = 0):
    limit, offset = 5, page * 5
    async with pool.acquire() as conn:
        total = await conn.fetchval('SELECT COUNT(*) FROM questions')
        questions = await conn.fetch('SELECT id, question_text FROM questions ORDER BY id DESC LIMIT $1 OFFSET $2',
                                     limit, offset)

    builder = InlineKeyboardBuilder()
    for q in questions:
        builder.row(InlineKeyboardButton(text=f"📌 {q['question_text'][:35]}", callback_data="ignore"))
        builder.row(
            InlineKeyboardButton(text="✏️ Ред.", callback_data=f"edit_q_{q['id']}"),
            InlineKeyboardButton(text="❌ Видал.", callback_data=f"conf_del_{q['id']}")
        )

    nav_btns = []
    if page > 0: nav_btns.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"mng_page_{page - 1}"))
    if offset + limit < total: nav_btns.append(
        InlineKeyboardButton(text="Вперед ➡️", callback_data=f"mng_page_{page + 1}"))
    if nav_btns: builder.row(*nav_btns)
    builder.row(InlineKeyboardButton(text="❌ Закрити меню", callback_data="delete_this_msg"))
    return builder.as_markup()


async def get_toggle_keyboard(pool: asyncpg.Pool, page: int = 0):
    limit, offset = 5, page * 5
    async with pool.acquire() as conn:
        total = await conn.fetchval('SELECT COUNT(*) FROM questions')
        questions = await conn.fetch(
            'SELECT id, question_text, is_active FROM questions ORDER BY id DESC LIMIT $1 OFFSET $2', limit, offset)

    builder = InlineKeyboardBuilder()
    for q in questions:
        # Створюємо кнопку запуску/зупинки
        if q['is_active']:
            toggle_btn = InlineKeyboardButton(text=f"🛑 | {q['question_text'][:15]}...",
                                              callback_data=f"stop_{q['id']}_{page}")
        else:
            toggle_btn = InlineKeyboardButton(text=f"⚪️ | {q['question_text'][:15]}...",
                                              callback_data=f"activate_{q['id']}_{page}")

        # Створюємо кнопку відповідей
        ans_btn = InlineKeyboardButton(text="📊 Відповіді", callback_data=f"show_ans_{q['id']}_0")

        # Додаємо ОБИДВІ кнопки в ОДИН ряд (через кому)
        builder.row(toggle_btn, ans_btn)

    nav_btns = []
    if page > 0: nav_btns.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"tgl_page_{page - 1}"))
    if offset + limit < total: nav_btns.append(
        InlineKeyboardButton(text="Вперед ➡️", callback_data=f"tgl_page_{page + 1}"))
    if nav_btns: builder.row(*nav_btns)

    builder.row(InlineKeyboardButton(text="❌ Закрити меню", callback_data="delete_this_msg"))
    return builder.as_markup()