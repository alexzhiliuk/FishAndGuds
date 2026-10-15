from app.bot.mailing_report import mailing_report
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
