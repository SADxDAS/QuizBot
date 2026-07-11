from aiogram.utils.keyboard import ReplyKeyboardBuilder

def get_admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📊 Переглянути відповіді")
    builder.button(text="⚙️ Список питань")
    builder.button(text="➕ Створити питання")
    builder.button(text="🚀 Запуск опитування")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)