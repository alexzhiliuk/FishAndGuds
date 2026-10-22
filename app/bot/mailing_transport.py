from aiogram import Bot
from aiogram.types import Message

from app.bot.media import resolve_mailing_photo
from app.services.mailing_content import MailingContent


async def send_mailing(
    bot: Bot, settings, telegram_id: int, content: MailingContent
) -> Message:
    photo = resolve_mailing_photo(content.image_file_id, settings.assets_dir)
    if photo:
        return await bot.send_photo(
            telegram_id,
            photo,
            caption=content.text,
            caption_entities=content.telegram_entities(),
            parse_mode=None,
        )
    return await bot.send_message(
        telegram_id,
        content.text,
        entities=content.telegram_entities(),
        parse_mode=None,
    )
