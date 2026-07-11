from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from cachetools import TTLCache


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, time_limit: float = 1.0):
        # maxsize=10000 означає, що ми тримаємо в пам'яті до 10 000 унікальних юзерів одночасно
        # ttl=time_limit - запис живе, наприклад, 1 секунду
        self.limit = TTLCache(maxsize=10000, ttl=time_limit)

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any]
    ) -> Any:
        # Перевіряємо тільки текстові повідомлення (не натискання кнопок інлайн клавіатури)
        if event.message:
            user_id = event.message.from_user.id
            if user_id in self.limit:
                # Якщо користувач є в кеші (тобто писав менше 1 секунди тому) - просто ігноруємо
                return

                # Якщо користувача немає, додаємо його в кеш
            self.limit[user_id] = None

        return await handler(event, data)