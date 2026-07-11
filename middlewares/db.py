from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from typing import Callable, Dict, Any, Awaitable

class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, pool):
        super().__init__()
        self.pool = pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        data["pool"] = self.pool
        return await handler(event, data)