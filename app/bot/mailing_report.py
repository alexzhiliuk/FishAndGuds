from app.models.entities import MailingRun, RunStatus
from datetime import timezone
from zoneinfo import ZoneInfo


def mailing_report(run: MailingRun) -> str:
    if run.status == RunStatus.sending:
        title = "Рассылка отправляется."
    elif run.status == RunStatus.failed:
        title = "Рассылка прервана."
    elif run.failed_count:
        title = "Рассылка завершена с ошибками."
    else:
        title = "Рассылка завершена."
    lines = [title, ""]
    summary = run.audience_summary
    if summary is not None:
        lines.append(f"Пользователей в базе: {summary['total_users']}")
        if "included_admins" in summary:
            lines.append(f"Администраторов включено: {summary['included_admins']}")
        elif summary.get("excluded_admins"):
            lines.append(
                f"Исключено администраторов: {summary['excluded_admins']} (в старой рассылке)"
            )
        if "excluded_inactive" in summary:
            lines.append(f"Неактивных исключено: {summary['excluded_inactive']}")
        if summary.get("excluded_push"):
            lines.append(
                f"Отключили Telegram-рассылки: {summary['excluded_push']} (в старой рассылке)"
            )
        lines.append("")
    lines.extend(
        [
            f"Получателей: {run.total_count}",
            f"✅ Отправлено: {run.sent_count}",
            f"❌ Ошибок: {run.failed_count}",
        ]
    )
    if remaining := run.total_count - run.sent_count - run.failed_count:
        label = (
            "Не подтверждено / не обработано"
            if run.snapshot_text is not None
            else "Не обработано"
        )
        lines.append(f"{label}: {remaining}")
    if run.error:
        lines.append(run.error)
    return "\n".join(lines)


def mailing_run_report(run: MailingRun, counts, timezone_name: str) -> str:
    date = run.started_at
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    removed = counts.get("deleted", 0) + counts.get("missing", 0)
    if run.snapshot_text is None:
        status = "неизвестен"
    elif run.deletion_status == "deleting":
        status = "удаляется"
    elif (
        run.sent_count > 0
        and removed >= run.sent_count
        and not counts.get("unknown_sent", 0)
    ):
        status = "удалена"
    elif removed:
        status = "частично удалена"
    else:
        status = "не удалена"
    lines = [
        f"Запуск #{run.id}: {run.snapshot_name or 'историческая отправка'}",
        f"Дата: {date.astimezone(ZoneInfo(timezone_name)):%d.%m.%Y %H:%M}",
        "",
        f"Получателей: {run.total_count}",
        f"✅ Отправлено: {run.sent_count}",
        f"❌ Ошибок: {run.failed_count}",
        "",
        f"Статус: {status}",
    ]
    if run.snapshot_text is None:
        lines.append("Подробные данные не сохранялись. Удаление сообщений недоступно.")
    elif run.status == RunStatus.sending:
        lines.append("Отправка выполняется.")
    elif run.status == RunStatus.failed:
        lines.append("Отправка прервана.")
        remaining = (
            run.total_count
            - run.sent_count
            - run.failed_count
            - counts.get("unknown_sent", 0)
        )
        if remaining > 0:
            lines.append(f"Не обработано: {remaining}")
    if run.deletion_status == "interrupted":
        lines.append("Удаление прервано. Можно продолжить кнопкой удаления.")
    for key, label in (
        ("unknown_sent", "Отправок с неизвестным результатом"),
        ("error", "Ошибок удаления"),
        ("unknown", "Результат удаления неизвестен"),
        ("expired", "Истёк срок удаления"),
    ):
        if counts.get(key, 0):
            lines.append(f"{label}: {counts[key]}")
    return "\n".join(lines)
