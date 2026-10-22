import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import DeleteMessage, SendMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.entities import (
    MailingDelivery,
    MailingRun,
    MailingStatus,
    RunStatus,
    User,
)
from app.repositories import MailingRepository, UserRepository
from app.services import MailingService
from app.services.mailing_lock import MailingBusy


def receipt(uid, message_id=10):
    return SimpleNamespace(
        chat=SimpleNamespace(id=uid),
        message_id=message_id,
        date=datetime.now(timezone.utc),
    )


async def successful_sender(uid, content):
    return receipt(uid)


async def create_run(
    session, recipients=(1, 2, 99), text="[Меню](https://menu.example)"
):
    service = MailingService(session)
    item = await service.create("Меню", text)
    run = await service.send(
        item.id, successful_sender, include_telegram_ids=recipients
    )
    return service, item, run


async def test_snapshots_and_deliveries_survive_edits_user_removal_and_archive(session):
    user = User(telegram_id=1, first_name="Гость", phone="+10000000001")
    session.add(user)
    await session.commit()
    service, item, first = await create_run(session)
    await service.update(
        item.id,
        name="Новое имя",
        text="[Новое](https://new.example)",
        image_file_id="photo",
    )
    second = await service.send(
        item.id, successful_sender, include_telegram_ids=(1, 2, 99)
    )
    assert (first.snapshot_name, first.snapshot_text, first.snapshot_image_file_id) == (
        "Меню",
        "Меню",
        None,
    )
    assert first.snapshot_entities[0]["url"] == "https://menu.example"
    assert (second.snapshot_text, second.snapshot_image_file_id) == ("Новое", "photo")
    assert second.snapshot_entities[0]["url"] == "https://new.example"
    await UserRepository(session).delete_by_telegram_id(1)
    await service.delete(item.id)
    archived = await MailingRepository(session).get(item.id)
    assert archived.archived_at is not None
    assert {r.id for r in archived.runs} == {first.id, second.id}
    assert len(await service.deliveries(first.id)) == 3
    assert (await service.page(0))[0] == []
    assert [i.id for i in (await service.page(0, archived=True))[0]] == [item.id]
    for action in (
        lambda: service.update(item.id, text="Нельзя"),
        lambda: service.send(item.id, successful_sender),
    ):
        with pytest.raises(ValueError, match="архив"):
            await action()
    deleter = AsyncMock(return_value=True)
    await service.delete_messages(first.id, deleter)
    assert deleter.await_count == 3
    assert all(
        d.deletion_status == "pending" for d in await service.deliveries(second.id)
    )


async def test_send_lock_and_consumed_button_prevent_duplicate_runs(session):
    service = MailingService(session)
    item = await service.create("Тест", "Текст")
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_sender(uid, content):
        entered.set()
        await release.wait()
        return receipt(uid)

    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    task = asyncio.create_task(
        service.send(
            item.id, slow_sender, include_telegram_ids=(99,), expected_run_id=0
        )
    )
    await entered.wait()
    try:
        async with factory() as other:
            another = MailingService(other)
            for action in (
                lambda: another.send(item.id, successful_sender, expected_run_id=0),
                lambda: another.delete(item.id),
                lambda: another.update(item.id, text="Новый"),
            ):
                with pytest.raises(MailingBusy):
                    await action()
    finally:
        release.set()
        run = await task
    with pytest.raises(ValueError, match="кнопка уже использована"):
        await service.send(item.id, successful_sender, expected_run_id=0)
    next_run = await service.send(item.id, successful_sender, expected_run_id=run.id)
    assert next_run.id != run.id


async def test_scheduled_send_and_archive_cancel_stale_scheduler_work(session):
    service = MailingService(session)
    item = await service.create("Тест", "Текст")
    await service.schedule(item.id, datetime.now(timezone.utc) - timedelta(minutes=1))
    await service.send(item.id, successful_sender, scheduled=True)
    with pytest.raises(ValueError, match="уже выполнена"):
        await service.send(item.id, successful_sender, scheduled=True)
    await service.schedule(item.id, datetime.now(timezone.utc) - timedelta(minutes=1))
    await service.delete(item.id)
    assert item.status == MailingStatus.cancelled
    assert item.scheduled_at is None
    assert await MailingRepository(session).due(datetime.now(timezone.utc)) == []


async def test_unknown_delivery_is_not_counted_as_sent_or_retried(session):
    service = MailingService(session)
    item = await service.create("Тест", "Текст")
    seen = []

    async def sender(uid, content):
        seen.append(uid)
        if uid == 1:
            raise TelegramNetworkError(
                method=SendMessage(chat_id=uid, text=content.text),
                message="lost response",
            )
        if uid == 2:
            raise RuntimeError("blocked")
        return receipt(uid)

    run = await service.send(item.id, sender, include_telegram_ids=(1, 2, 3))
    assert seen == [1, 2, 3]
    assert (run.sent_count, run.failed_count, run.status) == (1, 1, RunStatus.failed)
    rows = await service.deliveries(run.id)
    assert [d.status for d in rows] == ["unknown", "failed", "sent"]
    assert (
        rows[2].chat_id == 3
        and rows[2].message_id == 10
        and rows[2].sent_at is not None
    )
    counts, _ = await service.deletion_summary(run.id)
    assert counts["unknown_sent"] == 1 and counts["available"] == 1


async def test_failed_receipt_commit_stops_sending_and_preserves_uncertainty(
    session, monkeypatch
):
    service = MailingService(session)
    item = await service.create("Тест", "Текст")
    item_id = item.id
    seen = []
    original_commit = session.commit
    fail_next = False

    async def commit():
        nonlocal fail_next
        if fail_next:
            fail_next = False
            raise RuntimeError("DB write failed")
        await original_commit()

    async def sender(uid, content):
        nonlocal fail_next
        seen.append(uid)
        fail_next = True
        return receipt(uid)

    monkeypatch.setattr(session, "commit", commit)
    with pytest.raises(RuntimeError, match="DB write failed"):
        await service.send(item_id, sender, include_telegram_ids=(1, 2))
    assert seen == [1]
    run = (await service.runs_page(item_id))[0][0]
    assert (run.status, run.sent_count, run.failed_count) == (RunStatus.failed, 0, 0)
    assert [d.status for d in await service.deliveries(run.id)] == [
        "unknown",
        "pending",
    ]


async def test_process_interruption_can_be_reviewed_without_resending(session):
    service = MailingService(session)
    item = await service.create("Тест", "Текст")

    async def interrupted(uid, content):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await service.send(item.id, interrupted, include_telegram_ids=(1, 2))
    run = (await service.runs_page(item.id))[0][0]
    assert run.status == RunStatus.sending
    run = await service.refresh_run(run.id)
    assert run.status == RunStatus.failed
    assert [d.status for d in await service.deliveries(run.id)] == [
        "unknown",
        "pending",
    ]


async def test_deletion_is_per_run_resumable_and_honors_exact_expiration(session):
    service, item, run = await create_run(session, (1, 2, 3, 4, 5))
    second = await service.send(item.id, successful_sender, include_telegram_ids=(1,))
    rows = await service.deliveries(run.id)
    rows[0].sent_at = datetime.now(timezone.utc) - timedelta(hours=48)
    rows[1].deletion_status = "deleted"
    await session.commit()
    method = DeleteMessage(chat_id=3, message_id=10)

    async def deleter(chat, message):
        if chat == 3:
            raise TelegramBadRequest(
                method=method, message="Bad Request: message to delete not found"
            )
        if chat == 4:
            raise TelegramForbiddenError(method=method, message="Forbidden")
        return True

    await service.delete_messages(run.id, deleter)
    assert [d.deletion_status for d in await service.deliveries(run.id)] == [
        "expired",
        "deleted",
        "missing",
        "error",
        "deleted",
    ]
    retry = AsyncMock(return_value=True)
    await service.delete_messages(run.id, retry)
    retry.assert_awaited_once_with(4, 10)
    assert (await service.deliveries(second.id))[0].deletion_status == "pending"
    counts, deadline = await service.deletion_summary(run.id)
    assert counts["deleted"] == 3 and counts["missing"] == 1 and counts["expired"] == 1
    assert counts["available"] == 0 and deadline is None


async def test_deletion_retries_retry_after_and_network_errors(session, monkeypatch):
    service, _, run = await create_run(session, (1, 2))
    calls = {1: 0, 2: 0}
    sleep = AsyncMock()
    monkeypatch.setattr("app.services.mailings.asyncio.sleep", sleep)
    method = DeleteMessage(chat_id=1, message_id=10)

    async def deleter(chat, message):
        calls[chat] += 1
        if chat == 1 and calls[chat] == 1:
            raise TelegramRetryAfter(method=method, message="Flood", retry_after=7)
        if chat == 2:
            raise TelegramNetworkError(method=method, message="lost response")
        return True

    await service.delete_messages(run.id, deleter)
    assert calls == {1: 2, 2: 4}
    assert [d.deletion_status for d in await service.deliveries(run.id)] == [
        "deleted",
        "unknown",
    ]
    assert [call.args[0] for call in sleep.await_args_list] == [7, 1, 2, 4]


async def test_deletion_interruption_and_duplicate_operations(session):
    service, item, run = await create_run(session, (1, 2))
    entered, release = asyncio.Event(), asyncio.Event()

    async def interrupted(chat, message):
        entered.set()
        await release.wait()
        raise asyncio.CancelledError()

    task = asyncio.create_task(service.delete_messages(run.id, interrupted))
    await entered.wait()
    try:
        with pytest.raises(MailingBusy):
            await service.delete_messages(run.id, AsyncMock())
        with pytest.raises(MailingBusy):
            await service.delete(item.id)
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert (await service.deliveries(run.id))[0].deletion_status == "unknown"
    assert (await service.refresh_run(run.id)).deletion_status == "interrupted"
    retry = AsyncMock(return_value=True)
    await service.delete_messages(run.id, retry)
    assert retry.await_count == 2


async def test_failed_deletion_receipt_commit_stops_and_can_resume(
    session, monkeypatch
):
    service, _, run = await create_run(session, (1, 2))
    run_id = run.id
    original_commit = session.commit
    fail_next = False
    seen = []

    async def commit():
        nonlocal fail_next
        if fail_next:
            fail_next = False
            raise RuntimeError("DB delete receipt failed")
        await original_commit()

    async def deleter(chat, message):
        nonlocal fail_next
        seen.append(chat)
        fail_next = True
        return True

    monkeypatch.setattr(session, "commit", commit)
    with pytest.raises(RuntimeError, match="DB delete receipt failed"):
        await service.delete_messages(run_id, deleter)
    assert seen == [1]
    rows = await service.deliveries(run_id)
    assert [d.deletion_status for d in rows] == ["unknown", "pending"]
    retry = AsyncMock(return_value=True)
    await service.delete_messages(run_id, retry)
    assert retry.await_count == 2


async def test_actual_photo_reference_is_frozen_and_reused_for_recipients(session):
    service = MailingService(session)
    item = await service.create("Фото", "Подпись", image_file_id="asset:gallery_01.jpg")
    seen = []

    async def photo_sender(uid, content):
        seen.append(content.image_file_id)
        result = receipt(uid)
        result.photo = [SimpleNamespace(file_id="telegram-photo-reference")]
        return result

    run = await service.send(item.id, photo_sender, include_telegram_ids=(1, 2))
    assert seen == ["asset:gallery_01.jpg", "telegram-photo-reference"]
    assert run.snapshot_image_file_id == "telegram-photo-reference"


async def test_legacy_run_has_no_invented_snapshot_or_deletion(session):
    service = MailingService(session)
    item = await service.create("Текущий текст", "Новый контент")
    legacy = MailingRun(
        mailing_id=item.id,
        started_at=datetime.now(timezone.utc),
        status=RunStatus.sent,
        total_count=5,
        sent_count=5,
    )
    session.add(legacy)
    await session.commit()
    assert (await service.get_run(legacy.id)).snapshot_text is None
    with pytest.raises(ValueError, match="не сохранялись"):
        await service.delete_messages(legacy.id, AsyncMock())
    assert await session.scalar(select(MailingDelivery.id)) is None


async def test_permanent_deletion_removes_history_and_disables_old_message_deletion(
    session,
):
    service, item, run = await create_run(session)
    item_id, run_id = item.id, run.id
    with pytest.raises(ValueError, match="только из архива"):
        await service.purge_archived(item_id)
    await service.delete(item_id)
    assert await service.purge_archived(item_id)
    assert await service.get(item_id) is None
    assert (await service.runs_page(item_id))[0] == []
    assert await service.deliveries(run_id) == []
    deleter = AsyncMock()
    with pytest.raises(ValueError, match="Запуск не найден"):
        await service.delete_messages(run_id, deleter)
    deleter.assert_not_awaited()
    assert not await service.purge_archived(item_id)
    replacement = await service.create("Новая", "Текст")
    replacement_run = await service.send(replacement.id, successful_sender)
    # Old inline buttons must never point at a newly created template/run.
    assert replacement.id != item_id and replacement_run.id != run_id


async def test_permanent_deletion_is_blocked_while_message_removal_is_running(session):
    service, item, run = await create_run(session, (1,))
    await service.delete(item.id)
    entered, release = asyncio.Event(), asyncio.Event()

    async def deleter(chat, message):
        entered.set()
        await release.wait()
        return True

    task = asyncio.create_task(service.delete_messages(run.id, deleter))
    await entered.wait()
    try:
        with pytest.raises(MailingBusy):
            await service.purge_archived(item.id)
    finally:
        release.set()
        await task
    assert await service.get(item.id) is not None
