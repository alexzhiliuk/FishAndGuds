from app.bot.handlers.admin import router as admin_router
from app.bot.handlers.user import router as user_router

__all__ = ["admin_router", "user_router"]
