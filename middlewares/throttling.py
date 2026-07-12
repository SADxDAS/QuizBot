from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from cachetools import TTLCache


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, time_limit: float = 0.8):
        self.limit = TTLCache(maxsize=10000, ttl=time_limit)

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any]
    ) -> Any:
        user_id = None

        # Перехоплюємо і текст, і натискання кнопок
        if event.message:
            user_id = event.message.from_user.id
        elif event.callback_query:
            user_id = event.callback_query.from_user.id

        if user_id:
            if user_id in self.limit:
                # Якщо це натискання кнопки - кажемо Telegram, що ми її обробили, щоб вона не "висіла"
                if event.callback_query:
                    await event.callback_query.answer("Занадто швидко!", show_alert=False)
                return
            self.limit[user_id] = None

        return await handler(event, data)