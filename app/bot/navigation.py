import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message


logger = logging.getLogger(__name__)


async def answer_with_buttons(
    message: Message,
    text: str,
    *,
    reply_markup=None,
    **kwargs,
):
    await clear_inline_keyboard(message)
    return await message.answer(
        text,
        reply_markup=reply_markup,
        **kwargs,
    )


async def answer_photo_with_buttons(
    message: Message,
    photo,
    caption: str,
    *,
    reply_markup=None,
    **kwargs,
):
    await clear_inline_keyboard(message)
    return await message.answer_photo(
        photo,
        caption=caption,
        reply_markup=reply_markup,
        **kwargs,
    )


async def clear_inline_keyboard(message: Message) -> None:
    """Remove buttons from a screen before a new screen is sent."""
    if getattr(message, "reply_markup", None) is None:
        return
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as exc:
        logger.warning(
            "Could not remove inline keyboard chat_id=%s message_id=%s: %s",
            getattr(getattr(message, "chat", None), "id", None),
            getattr(message, "message_id", None),
            exc,
        )
