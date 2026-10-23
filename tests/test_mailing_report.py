from datetime import datetime, timezone

import pytest

from app.bot.mailing_report import mailing_report, mailing_run_report
from app.models.entities import MailingRun, RunStatus


def test_report_explains_audience_and_handles_legacy_runs():
    run = MailingRun(
        total_count=12,
        sent_count=12,
        failed_count=0,
        status=RunStatus.sent,
        audience_summary=dict(total_users=33, included_admins=1, excluded_inactive=1),
    )
    report = mailing_report(run)
    assert "Пользователей в базе: 33" in report
    assert "Администраторов включено: 1" in report
    assert "Неактивных исключено: 1" in report
    assert "Получателей: 12" in report
    run.audience_summary = dict(
        total_users=33, excluded_admins=1, excluded_inactive=1, excluded_push=19
    )
    legacy_report = mailing_report(run)
    assert "Исключено администраторов: 1 (в старой рассылке)" in legacy_report
    assert "Отключили Telegram-рассылки: 19 (в старой рассылке)" in legacy_report
    run.audience_summary = None
    assert "Пользователей в базе" not in mailing_report(run)
    assert "Получателей: 12" in mailing_report(run)


def test_failed_run_does_not_claim_success_and_counts_remaining():
    run = MailingRun(
        total_count=12, sent_count=5, failed_count=1, status=RunStatus.failed
    )
    report = mailing_report(run)
    assert report.startswith("Рассылка прервана.")
    assert "Не обработано: 6" in report
    run.status = RunStatus.sent
    assert mailing_report(run).startswith("Рассылка завершена с ошибками.")


@pytest.mark.parametrize(
    "counts,deletion_status,expected",
    [
        ({}, "idle", "не удалена"),
        ({"deleted": 2}, "finished", "удалена"),
        ({"deleted": 1, "missing": 1}, "finished", "удалена"),
        ({"deleted": 1, "error": 1}, "finished", "частично удалена"),
        ({"deleted": 2, "unknown_sent": 1}, "finished", "частично удалена"),
        ({}, "deleting", "удаляется"),
    ],
)
def test_history_summary_is_compact_and_tracks_deletion(
    counts, deletion_status, expected
):
    run = MailingRun(
        id=2,
        snapshot_name="тест 2",
        snapshot_text="Контент",
        total_count=2,
        sent_count=2,
        failed_count=0,
        status=RunStatus.sent,
        started_at=datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc),
        deletion_status=deletion_status,
        audience_summary=dict(total_users=0, included_admins=2, excluded_inactive=0),
    )
    report = mailing_run_report(run, counts, "Europe/Moscow")
    expected_report = (
        "Запуск #2: тест 2\nДата: 22.10.2026 15:00\n\n"
        "Получателей: 2\n✅ Отправлено: 2\n❌ Ошибок: 0\n\n"
        f"Статус: {expected}"
    )
    assert report.startswith(expected_report)
    assert "Пользователей в базе" not in report
    assert "Рассылка завершена" not in report
    assert "Администраторов" not in report
    if not counts.get("error") and not counts.get("unknown_sent"):
        assert report == expected_report


def test_interrupted_history_run_reports_unprocessed_recipients():
    run = MailingRun(
        id=3,
        snapshot_name="Прерванная",
        snapshot_text="Текст",
        total_count=5,
        sent_count=1,
        failed_count=1,
        status=RunStatus.failed,
        started_at=datetime.now(timezone.utc),
        deletion_status="idle",
    )
    report = mailing_run_report(run, {"unknown_sent": 1}, "Europe/Moscow")
    assert "Отправка прервана." in report
    assert "Не обработано: 2" in report
    assert "Отправок с неизвестным результатом: 1" in report
