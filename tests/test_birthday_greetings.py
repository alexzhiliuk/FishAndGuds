from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from app.bot.handlers import admin
from app.bot.states import BirthdayGreetingEdit
from app.config import Settings
from app.services.birthdays import BirthdayGreetingService


def inbound_message(user_id=42, **kwargs):
    return MagicMock(
        spec=Message,
        from_user=SimpleNamespace(id=user_id, first_name="Ольга"),
        reply_markup=None,
        answer=AsyncMock(),
        **kwargs,
    )


def callback(data, user_id=42):
    message = inbound_message(user_id)
    return MagicMock(
        spec=CallbackQuery,
        from_user=message.from_user,
        data=data,
        answer=AsyncMock(),
        message=message,
    )


def callbacks(message):
    markup = message.answer.await_args.kwargs["reply_markup"]
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def test_admin_can_edit_preview_disable_and_enable_birthday_greeting(session):
    settings = Settings(_env_file=None, admin_ids=(42,))
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=42, user_id=42))
    service = BirthdayGreetingService(session)
    refresh = AsyncMock()

    opened = callback("birthday:open")
    await admin.birthday_greeting_open(opened, state, session, settings)
    assert "birthday:disable" in callbacks(opened.message)
    assert "birthday:edit_time" in callbacks(opened.message)
    assert "10:00" in opened.message.answer.await_args.args[0]
    assert "birthday:remove_photo" not in callbacks(opened.message)
    assert callbacks(opened.message)[-1] == "admin:list"

    await admin.birthday_greeting_edit(callback("birthday:edit_text"), state, settings)
    assert await state.get_state() == BirthdayGreetingEdit.text.state
    await admin.birthday_greeting_save_text(
        inbound_message(text="🎂 {name}, ждём вас!", entities=[]),
        state,
        session,
        settings,
    )
    assert await state.get_state() is None

    await admin.birthday_greeting_edit(callback("birthday:edit_image"), state, settings)
    assert await state.get_state() == BirthdayGreetingEdit.image.state
    photo = inbound_message(photo=[SimpleNamespace(file_id="birthday-photo")])
    await admin.birthday_greeting_save_image(photo, state, session, settings)
    assert "birthday:remove_photo" in callbacks(photo)

    disabled = callback("birthday:disable")
    await admin.birthday_greeting_action(disabled, state, session, settings, refresh)
    assert "birthday:enable" in callbacks(disabled.message)
    assert not (await service.get()).enabled
    refresh.assert_awaited_once_with()

    bot = SimpleNamespace(send_photo=AsyncMock())
    await admin.birthday_greeting_preview(
        callback("birthday:preview"), session, bot, settings
    )
    bot.send_photo.assert_awaited_once_with(
        42,
        "birthday-photo",
        caption="🎂 Ольга, ждём вас!",
        caption_entities=[],
        parse_mode=None,
    )

    await admin.birthday_greeting_action(
        callback("birthday:remove_photo"), state, session, settings, refresh
    )
    assert (await service.get()).template.image_file_id is None
    refresh.assert_awaited_once_with()
    await admin.birthday_greeting_action(
        callback("birthday:enable"), state, session, settings, refresh
    )
    greeting = await service.get()
    assert greeting.enabled
    assert greeting.template.text == "🎂 {name}, ждём вас!"
    assert refresh.await_count == 2


async def test_admin_can_change_birthday_time_after_correcting_input(session):
    settings = Settings(_env_file=None, admin_ids=(42,))
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=42, user_id=42))
    await admin.birthday_greeting_edit(callback("birthday:edit_time"), state, settings)
    service = BirthdayGreetingService(session)
    refresh = AsyncMock()

    invalid = inbound_message(text="25:30")
    await admin.birthday_greeting_save_time(invalid, state, session, settings, refresh)
    assert await state.get_state() == BirthdayGreetingEdit.send_time.state
    assert "ЧЧ:ММ" in invalid.answer.await_args.args[0]
    assert (await service.get()).send_time == time(10)
    refresh.assert_not_awaited()

    valid = inbound_message(text="09:30")
    await admin.birthday_greeting_save_time(valid, state, session, settings, refresh)
    assert await state.get_state() is None
    assert (await service.get()).send_time == time(9, 30)
    assert "09:30" in valid.answer.await_args.args[0]
    refresh.assert_awaited_once_with()


async def test_non_admin_cannot_disable_or_edit_birthday_greetings(session):
    settings = Settings(_env_file=None, admin_ids=(42,))
    state = SimpleNamespace(clear=AsyncMock())
    refresh = AsyncMock()
    action = callback("birthday:disable", user_id=43)
    await admin.birthday_greeting_action(action, state, session, settings, refresh)
    action.answer.assert_awaited_once_with("Доступ запрещён", show_alert=True)

    message = inbound_message(user_id=43, text="Изменённый текст", entities=[])
    await admin.birthday_greeting_save_text(message, state, session, settings)
    message.answer.assert_awaited_once_with("Доступ запрещён")
    state.clear.assert_not_awaited()
    refresh.assert_not_awaited()
    greeting = await BirthdayGreetingService(session).get()
    assert greeting.enabled and greeting.template.text.startswith("🎉 {name}")


async def test_birthday_photo_limit_uses_rendered_utf16_text(session):
    service = BirthdayGreetingService(session)
    text = "{name}" + "я" * 825
    await service.set_text(text)
    with pytest.raises(ValueError, match="1024"):
        await service.set_image("birthday-photo")
    greeting = await service.get()
    assert greeting.template.image_file_id is None
    assert greeting.template.text == text
