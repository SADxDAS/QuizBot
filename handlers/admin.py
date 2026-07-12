import asyncio
import logging
import asyncpg
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

import config
from keyboards.reply import get_admin_keyboard
from keyboards.inline import (
    get_answers_list_keyboard,
    get_toggle_keyboard,
    get_single_question_keyboard,
    get_pagination_keyboard
)
import html

router = Router()
ADMIN_MENU_BUTTONS = ["📊 Переглянути відповіді", "📃 Список питань", "➕ Створити питання", "🚀 Запуск опитування"]


class AdminStates(StatesGroup):
    waiting_for_new_question = State()
    waiting_for_edit_question = State()


@router.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery):
    await callback.answer()


# Очищення пам'яті Redis при закритті будь-якого меню
@router.callback_query(F.data == "delete_this_msg")
async def close_admin_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass


# --- 1. ВИПРАВЛЕННЯ: ГОЛОВНИЙ ОБРОБНИК КНОПОК МЕНЮ (ПЕРЕБИВАЄ БУДЬ-ЯКИЙ СТАН) ---
@router.message(F.text.in_(set(ADMIN_MENU_BUTTONS)), StateFilter("*"))
async def handle_admin_menu_buttons(message: Message, state: FSMContext, bot: Bot, pool: asyncpg.Pool):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    await state.clear()
    text = message.text

    if text == "🚀 Запуск опитування":
        await toggle_collection_menu_msg(message, pool)
    elif text == "➕ Створити питання":
        await create_q_start(message, state)
    elif text == "📃 Список питань":
        await mng_list(message, pool, bot, state)
    elif text == "📊 Переглянути відповіді":
        await v_ans(message, pool)


async def background_broadcast(bot: Bot, users: list, msg_text: str, pool: asyncpg.Pool, redis_client):
    success_count = 0
    blocked_users = []

    async def send_to_user(u):
        user_id = u['telegram_id']
        if user_id in config.ADMIN_IDS:
            return None
        try:
            await bot.send_message(user_id, msg_text)
            return user_id
        except TelegramForbiddenError:
            # Користувач заблокував бота або видалив акаунт
            blocked_users.append((user_id,))
            return None
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(user_id, msg_text)
                return user_id
            except Exception:
                return None
        except Exception as e:
            logging.error(f"Помилка відправки для {user_id}: {e}")
            return None

    try:
        # Розбиваємо на батчі по 25 повідомлень (ліміт Telegram - 30/сек)
        batch_size = 25
        for i in range(0, len(users), batch_size):
            batch = users[i:i + batch_size]

            # 1. Фіксуємо час доставки ДО відправки (щоб час реакції був точним)
            batch_user_ids = [(u['telegram_id'],) for u in batch if u['telegram_id'] not in config.ADMIN_IDS]
            if batch_user_ids:
                async with pool.acquire() as conn:
                    await conn.executemany(
                        'UPDATE users SET last_delivered_at = CURRENT_TIMESTAMP WHERE telegram_id = $1',
                        batch_user_ids
                    )

            # 2. Відправляємо повідомлення батчу паралельно
            tasks = [send_to_user(u) for u in batch]
            results = await asyncio.gather(*tasks)
            success_count += sum(1 for res in results if res is not None)

            # 3. Безпечна пауза між батчами
            if i + batch_size < len(users):
                await asyncio.sleep(1.0)

                # 4. Видаляємо "мертві душі", щоб не гальмувати майбутні розсилки
        if blocked_users:
            async with pool.acquire() as conn:
                await conn.executemany('DELETE FROM users WHERE telegram_id = $1', blocked_users)

    finally:
        # 5. ГАРАНТОВАНО знімаємо замок, навіть якщо код впаде з помилкою
        await redis_client.delete("is_broadcasting")

    # 6. Сповіщаємо адміна про результати
    try:
        admin_text = (
            f"✅ <b>Розсилку завершено!</b>\n"
            f"Успішно доставлено: {success_count} користувачам.\n"
        )
        if blocked_users:
            admin_text += f"🗑 Видалено неактивних (заблокували бота): {len(blocked_users)}"

        await bot.send_message(config.ADMIN_IDS[0], admin_text)
    except Exception:
        pass


# --- ТЕСТОВІ КОМАНДИ ---
@router.message(Command("cleardb"))
async def cmd_clear_db(message: Message, pool: asyncpg.Pool, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS: return
    async with pool.acquire() as conn:
        await conn.execute('TRUNCATE TABLE answers, questions RESTART IDENTITY CASCADE;')
    await state.storage.redis.delete("active_question_id")
    await message.answer("🧹 <b>Базу даних повністю очищено!</b>")


@router.message(Command("testdata"))
async def cmd_test_data(message: Message, pool: asyncpg.Pool):
    if message.from_user.id not in config.ADMIN_IDS: return
    async with pool.acquire() as conn:
        for i in range(1, 11):
            await conn.execute('INSERT INTO questions (question_text) VALUES ($1)', f'Тестове запитання №{i}')
        q_id = await conn.fetchval('INSERT INTO questions (question_text) VALUES ($1) RETURNING id', 'КАК КАКАТЬ')
        for i in range(1, 71):
            await conn.execute('INSERT INTO users (telegram_id, username) VALUES ($1, $2) ON CONFLICT DO NOTHING',
                               1000000 + i, f"user_{i}")
            await conn.execute(
                'INSERT INTO answers (telegram_id, username, question_id, answer_text, reaction_time) VALUES ($1, $2, $3, $4, $5)',
                1000000 + i, f"user_{i}", q_id, f"Відповідь {i}", 0.5 + (i * 0.1)
            )
    await message.answer("🧪 <b>Тестові дані успішно згенеровано!</b>")


# --- ЗАПУСК / ЗУПИНКА ОПИТУВАННЯ ---
async def toggle_collection_menu_msg(message: Message, pool: asyncpg.Pool):
    if message.from_user.id not in config.ADMIN_IDS: return
    async with pool.acquire() as conn:
        if await conn.fetchval('SELECT COUNT(*) FROM questions') == 0:
            return await message.answer("Список питань порожній.")
    await message.answer("🚀 Оберіть питання для запуску:", reply_markup=await get_toggle_keyboard(pool, page=0))


@router.callback_query(F.data.startswith("tgl_page_"))
async def tgl_page_callback(callback: CallbackQuery, pool: asyncpg.Pool):
    await callback.message.edit_reply_markup(
        reply_markup=await get_toggle_keyboard(pool, int(callback.data.split("_")[2])))


@router.callback_query(F.data.startswith("activate_"))
async def activate_question(callback: CallbackQuery, bot: Bot, pool: asyncpg.Pool, state: FSMContext):
    # 1. Перевірка замка (чи не йде зараз інша розсилка)
    is_broadcasting = await state.storage.redis.get("is_broadcasting")
    if is_broadcasting:
        return await callback.answer("⏳ Розсилка вже триває! Зачекайте.", show_alert=True)

    parts = callback.data.split("_")
    q_id, page = int(parts[1]), int(parts[2]) if len(parts) > 2 else 0

    # 2. Робимо питання активним у Redis миттєво
    await state.storage.redis.set("active_question_id", str(q_id))

    # 3. Оновлюємо статус у базі даних та дістаємо користувачів
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('UPDATE questions SET is_active = FALSE')
            await conn.execute('UPDATE questions SET is_active = TRUE WHERE id = $1', q_id)
            await conn.execute('UPDATE users SET last_delivered_at = NULL')
        q_text = await conn.fetchval('SELECT question_text FROM questions WHERE id = $1', q_id)
        users = await conn.fetch('SELECT telegram_id FROM users')

    # 4. Встановлюємо динамічний замок на час розсилки
    # (враховуємо кількість людей: швидкість 25/сек + 2 хвилини запасу)
    estimated_time = (len(users) // 25) + 120
    await state.storage.redis.set("is_broadcasting", "1", ex=max(300, estimated_time))

    # 5. Оновлюємо клавіатуру та сповіщаємо адміна
    await callback.message.edit_reply_markup(reply_markup=await get_toggle_keyboard(pool, page))
    await callback.answer("Опитування запущено!")

    # 6. Захист від HTML-ін'єкцій у тексті питання
    import html
    safe_q_text = html.escape(q_text)

    # 7. ВИПРАВЛЕНО: Запускаємо розсилку і ПЕРЕДАЄМО redis_client останнім аргументом
    asyncio.create_task(background_broadcast(
        bot,
        users,
        f"🔔 <b>УВАГА, НОВЕ ЗАПИТАННЯ!</b> 🔔\n\n❓ <b>{safe_q_text}</b>\n\n💬 <i>Просто напишіть вашу відповідь у цей чат:</i>",
        pool,
        state.storage.redis  # <--- Тепер аргумент на місці!
    ))

@router.callback_query(F.data.startswith("stop_"))
async def stop_question(callback: CallbackQuery, pool: asyncpg.Pool, state: FSMContext):
    page = int(callback.data.split("_")[2]) if len(callback.data.split("_")) > 2 else 0
    # СКИДАЄМО РЕДІС
    await state.storage.redis.delete("active_question_id")
    async with pool.acquire() as conn:
        await conn.execute('UPDATE questions SET is_active = FALSE')
    await callback.message.edit_reply_markup(reply_markup=await get_toggle_keyboard(pool, page))
    await callback.answer("Опитування зупинено!")


# --- СТВОРЕННЯ / РЕДАГУВАННЯ ---
async def create_q_start(message: Message, state: FSMContext):
    if message.from_user.id in config.ADMIN_IDS:
        await message.answer("Напишіть текст нового питання:")
        await state.set_state(AdminStates.waiting_for_new_question)


@router.message(AdminStates.waiting_for_new_question)
async def create_q_finish(message: Message, state: FSMContext, pool: asyncpg.Pool):
    if len(message.text) > 3000:
        return await message.answer("⚠️ Текст питання занадто довгий! Максимум 3000 символів.")
    async with pool.acquire() as conn:
        if await conn.fetchval('SELECT 1 FROM questions WHERE question_text = $1', message.text):
            await state.clear()
            return await message.answer("⚠️ Таке питання вже існує!", reply_markup=get_admin_keyboard())
        await conn.execute('INSERT INTO questions (question_text) VALUES ($1)', message.text)
    await message.answer("✨ Питання успішно додано!", reply_markup=get_admin_keyboard())
    await state.clear()


# --- НОВИЙ СПИСОК ПИТАНЬ З ПАГІНАЦІЄЮ ---
async def send_questions_page(chat_id: int, bot: Bot, pool: asyncpg.Pool, page: int, state: FSMContext,
                              nav_msg_id: int = None):
    limit, offset = 5, page * 5
    async with pool.acquire() as conn:
        total = await conn.fetchval('SELECT COUNT(*) FROM questions')
        if total == 0:
            text = "🤷‍♂️ Питань поки немає."
            if nav_msg_id:
                try:
                    await bot.edit_message_text(text, chat_id=chat_id, message_id=nav_msg_id)
                except Exception:
                    await bot.send_message(chat_id, text)
            else:
                await bot.send_message(chat_id, text)
            return

        questions = await conn.fetch(
            'SELECT id, question_text FROM questions ORDER BY id DESC LIMIT $1 OFFSET $2',
            limit, offset
        )

    state_data = await state.get_data()
    old_msg_ids = state_data.get("q_msg_ids", [])
    new_msg_ids = []

    for i, q in enumerate(questions):
        text = f"📖 <b>Питання:</b>\n{q['question_text']}"
        markup = get_single_question_keyboard(q['id'])
        if i < len(old_msg_ids):
            try:
                await bot.edit_message_text(text, chat_id=chat_id, message_id=old_msg_ids[i], reply_markup=markup)
                new_msg_ids.append(old_msg_ids[i])
            except Exception:
                msg = await bot.send_message(chat_id, text, reply_markup=markup)
                new_msg_ids.append(msg.message_id)
        else:
            msg = await bot.send_message(chat_id, text, reply_markup=markup)
            new_msg_ids.append(msg.message_id)

    for i in range(len(questions), len(old_msg_ids)):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_msg_ids[i])
        except Exception:
            pass

    total_pages = max(1, (total + limit - 1) // limit)
    pag_text = f"⚙️ <b>Навігація по списку</b>\nСторінка {page + 1} із {total_pages}"
    pag_markup = get_pagination_keyboard(page, total, limit)

    if nav_msg_id:
        try:
            await bot.edit_message_text(pag_text, chat_id=chat_id, message_id=nav_msg_id, reply_markup=pag_markup)
        except Exception:
            nav_msg = await bot.send_message(chat_id, pag_text, reply_markup=pag_markup)
            nav_msg_id = nav_msg.message_id
    else:
        nav_msg = await bot.send_message(chat_id, pag_text, reply_markup=pag_markup)
        nav_msg_id = nav_msg.message_id

    await state.update_data(q_msg_ids=new_msg_ids, nav_msg_id=nav_msg_id)


async def mng_list(message: Message, pool: asyncpg.Pool, bot: Bot, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS: return
    await state.update_data(q_msg_ids=[], nav_msg_id=None)
    await send_questions_page(message.chat.id, bot, pool, page=0, state=state)


@router.callback_query(F.data.startswith("mng_page_"))
async def mng_page_callback(callback: CallbackQuery, bot: Bot, pool: asyncpg.Pool, state: FSMContext):
    page = int(callback.data.split("_")[2])
    await send_questions_page(callback.message.chat.id, bot, pool, page, state, callback.message.message_id)
    await callback.answer()


@router.callback_query(F.data.startswith("conf_del_"))
async def conf_del(callback: CallbackQuery):
    q_id = callback.data.split('_')[2]
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Так, видалити", callback_data=f"delete_q_{q_id}")
    builder.button(text="❌ Скасувати", callback_data=f"cancel_del_{q_id}")
    await callback.message.edit_reply_markup(reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("cancel_del_"))
async def cancel_del(callback: CallbackQuery):
    q_id = int(callback.data.split('_')[2])
    await callback.message.edit_reply_markup(reply_markup=get_single_question_keyboard(q_id))


@router.callback_query(F.data.startswith("delete_q_"))
async def del_q(callback: CallbackQuery, bot: Bot, pool: asyncpg.Pool, state: FSMContext):
    q_id = int(callback.data.split('_')[2])

    # ВИПРАВЛЕННЯ 1: Якщо ми видаляємо активне питання, зупиняємо його в Redis
    active_q_bytes = await state.storage.redis.get("active_question_id")
    if active_q_bytes and int(active_q_bytes.decode('utf-8')) == q_id:
        await state.storage.redis.delete("active_question_id")

    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM questions WHERE id = $1', q_id)

    await callback.answer("🗑 Питання успішно видалено!", show_alert=True)
    state_data = await state.get_data()
    nav_msg_id = state_data.get("nav_msg_id")
    await send_questions_page(callback.message.chat.id, bot, pool, page=0, state=state, nav_msg_id=nav_msg_id)


@router.callback_query(F.data.startswith("edit_q_"))
async def edit_q_start(callback: CallbackQuery, state: FSMContext):
    await state.update_data(edit_q_id=int(callback.data.split("_")[2]))
    await callback.message.answer("Введіть новий текст для цього питання:")
    await state.set_state(AdminStates.waiting_for_edit_question)


@router.message(AdminStates.waiting_for_edit_question)
async def edit_q_finish(message: Message, state: FSMContext, pool: asyncpg.Pool):
    if len(message.text) > 3000:
        return await message.answer("⚠️ Текст питання занадто довгий! Максимум 3000 символів.")
    q_id = (await state.get_data()).get("edit_q_id")
    async with pool.acquire() as conn:
        if await conn.fetchval('SELECT 1 FROM questions WHERE question_text = $1 AND id != $2', message.text, q_id):
            await state.clear()
            return await message.answer("⚠️ Таке питання вже існує!", reply_markup=get_admin_keyboard())
        await conn.execute('UPDATE questions SET question_text = $1 WHERE id = $2', message.text, q_id)
    await message.answer("📝 Питання успішно оновлено!", reply_markup=get_admin_keyboard())
    await state.clear()


# --- ПЕРЕГЛЯД ВІДПОВІДЕЙ (СТАТИСТИКА) ---
async def v_ans(message: Message, pool: asyncpg.Pool):
    if message.from_user.id not in config.ADMIN_IDS: return
    async with pool.acquire() as conn:
        if await conn.fetchval('SELECT COUNT(*) FROM questions') == 0: return await message.answer("Список порожній.")
    await message.answer("Оберіть питання:", reply_markup=await get_answers_list_keyboard(pool, page=0))


@router.callback_query(F.data.startswith("ans_list_page_"))
async def ans_page_cb(callback: CallbackQuery, pool: asyncpg.Pool):
    await callback.message.edit_reply_markup(
        reply_markup=await get_answers_list_keyboard(pool, int(callback.data.split("_")[3])))


@router.callback_query(F.data == "back_to_answers_list")
async def back_to_ans(callback: CallbackQuery, pool: asyncpg.Pool):
    await callback.message.edit_text("Оберіть питання:", reply_markup=await get_answers_list_keyboard(pool, page=0))


@router.callback_query(F.data.startswith("show_ans_"))
async def show_ans(callback: CallbackQuery, pool: asyncpg.Pool):
    parts = callback.data.split("_")
    q_id, page = int(parts[2]), int(parts[3]) if len(parts) > 3 else 0
    limit, offset = 15, page * 15
    async with pool.acquire() as conn:
        q_text = await conn.fetchval('SELECT question_text FROM questions WHERE id = $1', q_id)
        total = await conn.fetchval('SELECT COUNT(*) FROM answers WHERE question_id = $1', q_id)
        answers = await conn.fetch(
            'SELECT username, answer_text, reaction_time FROM answers WHERE question_id = $1 ORDER BY reaction_time ASC NULLS LAST LIMIT $2 OFFSET $3',
            q_id, limit, offset)

    safe_q_text = html.escape(q_text)
    text = f"📊 <b>Відповіді:</b>\n<i>{safe_q_text}</i>\n\n"
    if not answers and page == 0:
        text += "Відповідей поки немає."
    else:
        for idx, ans in enumerate(answers, offset + 1):
            t = f"{ans['reaction_time']:.2f} сек" if ans['reaction_time'] else "Час невідомий"

            # Екрануємо і обрізаємо відповідь до 120 символів, щоб не вийти за 4096 ліміт
            safe_ans = html.escape(ans['answer_text'])
            if len(safe_ans) > 120:
                safe_ans = safe_ans[:120] + "..."

            safe_uname = html.escape(ans['username'])

            text += f"<b>{idx}.</b> @{safe_uname}: {safe_ans} <i>(⏱ {t})</i>\n"

    b = InlineKeyboardBuilder()
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️ Попередня", callback_data=f"show_ans_{q_id}_{page - 1}"))
    if offset + limit < total: nav.append(
        InlineKeyboardButton(text="Наступна ➡️", callback_data=f"show_ans_{q_id}_{page + 1}"))
    if nav: b.row(*nav)
    b.row(InlineKeyboardButton(text="⬅️ Назад до списку", callback_data="back_to_answers_list"))
    b.row(InlineKeyboardButton(text="❌ Закрити", callback_data="delete_this_msg"))
    await callback.message.edit_text(text, reply_markup=b.as_markup())