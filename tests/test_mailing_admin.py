from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery

from app.bot.handlers import admin
from app.bot.keyboards.common import (
    mailing_actions,
    mailing_history_keyboard,
    mailing_list_keyboard,
    mailing_run_keyboard,
)
from app.models.entities import MailingRun, RunStatus
from app.services import MailingService
from tests.test_mailing_history import create_run


def actions(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def message():
    return SimpleNamespace(
        reply_markup=None, answer=AsyncMock(), answer_photo=AsyncMock()
    )


def callback(data, uid=42):
    return MagicMock(
        spec=CallbackQuery,
        data=data,
        from_user=SimpleNamespace(id=uid),
        answer=AsyncMock(),
        message=message(),
    )


def settings(tmp_path):
    return SimpleNamespace(
        admin_ids=(42,), timezone="Europe/Moscow", assets_dir=tmp_path
    )


def test_button_navigation_preserves_history_archive_and_list_pages():
    run = MailingRun(
        id=22,
        mailing_id=7,
        started_at=datetime.now(timezone.utc),
        sent_count=3,
        total_count=4,
    )
    archive = actions(
        mailing_list_keyboard(
            [SimpleNamespace(id=7, name="Тест")], 2, True, archived=True
        )
    )
    assert archive[:3] == [
        "mail:archived:7:2",
        "mail:archive_list:1",
        "mail:archive_list:3",
    ]
    assert archive[-1] == "admin:back"
    assert "admin:list:0" not in archive
    history = actions(
        mailing_history_keyboard(7, [run], 3, True, 2, True, timezone.utc)
    )
    assert history == [
        "mail:run:22:3:2:1",
        "mail:history:7:2:2:1",
        "mail:history:7:4:2:1",
        "mail:archived:7:2",
    ]
    run_actions = actions(mailing_run_keyboard(run, 3, 2, True, True))
    assert run_actions == [
        "mail:remove:22:3:2:1",
        "mail:run:22:3:2:1",
        "mail:history:7:3:2:1",
    ]
    confirm = actions(mailing_run_keyboard(run, 3, 2, True, True, confirm=True))
    assert confirm == ["mail:confirm:22:3:2:1", "mail:run:22:3:2:1"]
    archived_actions = actions(mailing_actions(7, "sent", 2, 22, archived=True))
    assert archived_actions == [
        "mail:history:7:0:2:1",
        "mail:purge_prompt:7:2",
        "mail:archive_list:2",
    ]
    assert all(
        len(value.encode()) <= 64 for value in archive + history + run_actions + confirm
    )


@pytest.mark.parametrize(
    "handler,data,needs_bot",
    [
        (admin.archived_mailings, "mail:archive_list:0", False),
        (admin.prompt_purge_archived, "mail:purge_prompt:1:0", False),
        (admin.purge_archived, "mail:purge_confirm:1:0", False),
        (admin.mailing_history, "mail:history:1:0:0:0", False),
        (admin.open_mailing_run, "mail:run:1:0:0:0", False),
        (admin.confirm_mailing_removal, "mail:remove:1:0:0:0", False),
        (admin.remove_mailing_messages, "mail:confirm:1:0:0:0", True),
        (admin.delete, "mail:delete:1:0", False),
        (admin.send_now, "mail:send:1:0", True),
    ],
)
async def test_every_management_callback_checks_admin_rights(
    tmp_path, handler, data, needs_bot
):
    cb = callback(data, uid=123)
    if needs_bot:
        await handler(cb, object(), object(), settings(tmp_path))
    else:
        await handler(cb, object(), settings(tmp_path))
    cb.answer.assert_awaited_once_with("Доступ запрещён", show_alert=True)
    cb.message.answer.assert_not_awaited()


async def test_legacy_send_button_requires_reopening(tmp_path):
    cb = callback("mail:send:1")
    await admin.send_now(cb, object(), object(), settings(tmp_path))
    assert cb.answer.await_args.kwargs["show_alert"]
    cb.message.answer.assert_not_awaited()


async def test_archive_pages_contain_five_items_and_only_admin_menu_back(
    session, tmp_path
):
    service = MailingService(session)
    archived_ids = []
    for index in range(11):
        item = await service.create(f"Архив {index}", "Текст")
        archived_ids.append(item.id)
        await service.delete(item.id)
    active = await service.create("Активная", "Текст")
    seen = []
    for page, expected_size in ((0, 5), (1, 5), (2, 1)):
        cb = callback(f"mail:archive_list:{page}")
        await admin.archived_mailings(cb, session, settings(tmp_path))
        markup = cb.message.answer.await_args.kwargs["reply_markup"]
        buttons = actions(markup)
        selected = [value for value in buttons if value.startswith("mail:archived:")]
        assert len(selected) == expected_size
        seen.extend(int(value.split(":")[2]) for value in selected)
        assert buttons[-1] == "admin:back"
        assert "admin:list:0" not in buttons
        assert (f"mail:archive_list:{page + 1}" in buttons) == (page < 2)
        assert (f"mail:archive_list:{page - 1}" in buttons) == (page > 0)
    assert seen == archived_ids[::-1]
    assert active.id not in seen


async def test_run_card_shows_snapshot_and_removal_requires_confirmation(
    session, tmp_path
):
    service, item, run = await create_run(session)
    await service.update(item.id, text="Текущий изменённый текст")
    cb = callback(f"mail:run:{run.id}:2:3:0")
    await admin.open_mailing_run(cb, session, settings(tmp_path))
    assert cb.message.answer.await_args_list[0].args[0] == "Меню"
    report = cb.message.answer.await_args_list[-1]
    assert "Получателей: 3" in report.args[0]
    assert "Статус: не удалена" in report.args[0]
    assert "Пользователей в базе" not in report.args[0]
    assert "Рассылка завершена" not in report.args[0]
    assert f"mail:remove:{run.id}:2:3:0" in actions(report.kwargs["reply_markup"])
    confirm = callback(f"mail:remove:{run.id}:2:3:0")
    await admin.confirm_mailing_removal(confirm, session, settings(tmp_path))
    assert "Доступно сообщений: 3" in confirm.message.answer.await_args.args[0]
    assert actions(confirm.message.answer.await_args.kwargs["reply_markup"]) == [
        f"mail:confirm:{run.id}:2:3:0",
        f"mail:run:{run.id}:2:3:0",
    ]
    assert all(d.deletion_status == "pending" for d in await service.deliveries(run.id))


async def test_legacy_card_does_not_show_current_template_as_sent_content(
    session, tmp_path
):
    service = MailingService(session)
    item = await service.create("Имя", "Этот текст не отправлялся")
    run = MailingRun(
        mailing_id=item.id,
        started_at=datetime.now(timezone.utc),
        status=RunStatus.sent,
        sent_count=5,
        total_count=5,
    )
    session.add(run)
    await session.commit()
    cb = callback(f"mail:run:{run.id}:0:0:0")
    await admin.open_mailing_run(cb, session, settings(tmp_path))
    cb.message.answer.assert_awaited_once()
    report = cb.message.answer.await_args
    assert "Подробные данные не сохранялись" in report.args[0]
    assert "Этот текст не отправлялся" not in report.args[0]
    assert all(
        "mail:remove:" not in value for value in actions(report.kwargs["reply_markup"])
    )


async def test_creation_and_editing_preserve_native_links_in_fsm_and_database(
    session, tmp_path
):
    from aiogram.types import MessageEntity

    state = SimpleNamespace(
        update_data=AsyncMock(),
        set_state=AsyncMock(),
        get_data=AsyncMock(),
        clear=AsyncMock(),
    )
    incoming = message()
    incoming.from_user = SimpleNamespace(id=42)
    incoming.text = "Сайт"
    incoming.entities = [
        MessageEntity(type="text_link", offset=0, length=4, url="https://example.com")
    ]
    await admin.create_text(incoming, state, settings(tmp_path))
    saved = state.update_data.await_args.kwargs
    assert saved["entities"][0]["url"] == "https://example.com"
    item = await MailingService(session).create(
        "Ссылка", saved["text"], entities=saved["entities"]
    )
    state.get_data.return_value = dict(mailing_id=item.id, field="text", list_page=0)
    incoming.text, incoming.entities = "Обычный текст", []
    incoming.photo = []
    await admin.edit_value(incoming, state, session, settings(tmp_path))
    assert (await MailingService(session).get(item.id)).entities == []


async def test_without_photo_button_creates_text_mailing_and_rejects_stale_click(
    session, tmp_path
):
    from app.bot.states import MailingCreate

    state = SimpleNamespace(
        get_state=AsyncMock(return_value=MailingCreate.image.state),
        get_data=AsyncMock(
            return_value=dict(name="Без картинки", text="Текст", entities=[])
        ),
        clear=AsyncMock(),
    )
    cb = callback("mail:without_photo")
    await admin.create_without_photo(cb, state, session, settings(tmp_path))
    items, _ = await MailingService(session).page(0)
    assert len(items) == 1
    assert items[0].name == "Без картинки" and items[0].image_file_id is None
    state.clear.assert_awaited_once()
    state.get_state.return_value = None
    await admin.create_without_photo(cb, state, session, settings(tmp_path))
    assert len((await MailingService(session).page(0))[0]) == 1
    assert cb.answer.await_args.kwargs["show_alert"]


async def test_image_step_offers_red_cross_button_and_keeps_it_on_invalid_input(
    session, tmp_path
):
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
    incoming = message()
    incoming.from_user = SimpleNamespace(id=42)
    incoming.text, incoming.entities, incoming.photo = "Текст", [], []
    await admin.create_text(incoming, state, settings(tmp_path))
    markup = incoming.answer.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "❌ Без фото"
    assert actions(markup) == ["mail:without_photo", "mail:create_back"]
    incoming.text = "не изображение"
    await admin.create_image(incoming, state, session, settings(tmp_path))
    assert actions(incoming.answer.await_args.kwargs["reply_markup"]) == actions(markup)


async def test_permanent_deletion_requires_confirmation_and_returns_to_archive(
    session, tmp_path
):
    service, item, run = await create_run(session)
    item_id, run_id = item.id, run.id
    await service.delete(item_id)
    cb = callback(f"mail:purge_prompt:{item_id}:2")
    await admin.prompt_purge_archived(cb, session, settings(tmp_path))
    prompt = cb.message.answer.await_args
    assert "Отправленные сообщения останутся у получателей" in prompt.args[0]
    assert "невозможно" in prompt.args[0]
    assert actions(prompt.kwargs["reply_markup"]) == [
        f"mail:purge_confirm:{item_id}:2",
        f"mail:archived:{item_id}:2",
    ]
    assert await service.get(item_id) is not None
    confirm = callback(f"mail:purge_confirm:{item_id}:2")
    await admin.purge_archived(confirm, session, settings(tmp_path))
    assert await service.get(item_id) is None
    assert "Архив пока пуст" in confirm.message.answer.await_args.args[0]
    assert (
        actions(confirm.message.answer.await_args.kwargs["reply_markup"])[-1]
        == "admin:back"
    )
    stale = callback(f"mail:remove:{run_id}:0:0:1")
    await admin.confirm_mailing_removal(stale, session, settings(tmp_path))
    stale.answer.assert_awaited_once_with("Запуск не найден", show_alert=True)
    stale.message.answer.assert_not_awaited()
