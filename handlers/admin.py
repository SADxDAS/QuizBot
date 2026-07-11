import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
import asyncpg

import config
from keyboards.inline import get_single_question_keyboard, get_pagination_keyboard

router = Router()


# =======================================================
# 1. ФОНОВА РОЗСИЛКА (КУЛЕНЕПРОБИВНА)
# =======================================================
async def background_broadcast(bot: Bot, users: list, msg_text: str, pool: asyncpg.Pool):
    success_count = 0
    delivered_data = []

    for u in users:
        user_id = u['telegram_id']

        # Не шлемо розсилку адмінам
        if user_id in config.ADMIN_IDS:
            continue

        try:
            await bot.send_message(user_id, msg_text)
            delivered_data.append((user_id,))
            success_count += 1
        except TelegramRetryAfter as e:
            # Якщо Telegram просить почекати (зловити ліміт) - чекаємо і повторюємо
            await asyncio.sleep(e.retry_after)
            await bot.send_message(user_id, msg_text)
            delivered_data.append((user_id,))
            success_count += 1
        except TelegramForbiddenError:
            # Користувач заблокував бота (ігноруємо)
            pass
        except Exception as e:
            logging.error(f"Помилка розсилки для {user_id}: {e}")

        # Затримка, щоб не перевищувати ліміти Telegram (25 повідомлень на секунду)
        await asyncio.sleep(0.04)

        # Масовий апдейт БД (один швидкий запит на всіх успішних юзерів)
    if delivered_data:
        async with pool.acquire() as conn:
            await conn.executemany(
                'UPDATE users SET last_delivered_at = CURRENT_TIMESTAMP WHERE telegram_id = $1',
                delivered_data
            )

    # Сповіщаємо адміна про завершення
    await bot.send_message(config.ADMIN_IDS[0],
                           f"✅ Розсилку завершено!\nУспішно доставлено: {success_count} користувачам.")


# =======================================================
# 2. ВІДОБРАЖЕННЯ СПИСКУ ПИТАНЬ (ПАГІНАЦІЯ)
# =======================================================
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

    # Отримуємо з Redis збережені ID повідомлень
    state_data = await state.get_data()
    old_msg_ids = state_data.get("q_msg_ids", [])
    new_msg_ids = []

    # Редагуємо старі повідомлення або надсилаємо нові
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

    # Видаляємо "зайві" старі повідомлення, якщо їх більше, ніж питань на поточній сторінці
    for i in range(len(questions), len(old_msg_ids)):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_msg_ids[i])
        except Exception:
            pass

    # Блок навігації
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

    # Оновлюємо дані у Redis (пам'ять FSM)
    await state.update_data(q_msg_ids=new_msg_ids, nav_msg_id=nav_msg_id)


# =======================================================
# 3. ОБРОБНИКИ КНОПОК ТА КОМАНД
# =======================================================

# Головне меню списку питань
@router.message(F.text.in_(["⚙️ Список питань", "📃 Список питань"]))
async def mng_list(message: Message, pool: asyncpg.Pool, bot: Bot, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS: return

    # Очищаємо старі ID перед новим викликом меню
    await state.update_data(q_msg_ids=[], nav_msg_id=None)
    await send_questions_page(message.chat.id, bot, pool, page=0, state=state)


# Кнопки пагінації (Вперед / Назад)
@router.callback_query(F.data.startswith("mng_page_"))
async def mng_page_callback(callback: CallbackQuery, bot: Bot, pool: asyncpg.Pool, state: FSMContext):
    page = int(callback.data.split("_")[2])

    await send_questions_page(
        chat_id=callback.message.chat.id,
        bot=bot,
        pool=pool,
        page=page,
        state=state,
        nav_msg_id=callback.message.message_id
    )
    await callback.answer()


# Закриття меню (З ОЧИЩЕННЯМ ПАМ'ЯТІ REDIS)
@router.callback_query(F.data == "delete_this_msg")
async def close_admin_menu(callback: CallbackQuery, state: FSMContext):
    # Повністю очищаємо state, щоб уникнути витоків пам'яті
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("Меню закрито. 🗑")


# Підтвердження видалення
@router.callback_query(F.data.startswith("conf_del_"))
async def conf_del(callback: CallbackQuery):
    q_id = callback.data.split('_')[2]
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Так, видалити", callback_data=f"delete_q_{q_id}")
    builder.button(text="❌ Скасувати", callback_data=f"cancel_del_{q_id}")
    await callback.message.edit_reply_markup(reply_markup=builder.as_markup())


# Скасування видалення
@router.callback_query(F.data.startswith("cancel_del_"))
async def cancel_del(callback: CallbackQuery):
    q_id = int(callback.data.split('_')[2])
    await callback.message.edit_reply_markup(reply_markup=get_single_question_keyboard(q_id))


# Безпосереднє видалення з БД
@router.callback_query(F.data.startswith("delete_q_"))
async def del_q(callback: CallbackQuery, bot: Bot, pool: asyncpg.Pool, state: FSMContext):
    q_id = int(callback.data.split('_')[2])

    # Швидке видалення
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM questions WHERE id = $1', q_id)

    await callback.answer("🗑 Питання успішно видалено!", show_alert=True)

    # Отримуємо ID блоку навігації та перемальовуємо сторінку №1
    state_data = await state.get_data()
    nav_msg_id = state_data.get("nav_msg_id")

    await send_questions_page(callback.message.chat.id, bot, pool, page=0, state=state, nav_msg_id=nav_msg_id)

# 💡 ПРИМІТКА ЩОДО REDIS:
# Якщо у тебе в цьому файлі є функція увімкнення питання (наприклад, кнопка "Старт"),
# не забудь додати туди оновлення глобального статусу в Redis!
# Приклад:
# await state.storage.redis.set("active_question_id", str(q_id))