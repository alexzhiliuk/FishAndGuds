from app.models.entities import MailingRun, RunStatus


def mailing_report(run: MailingRun) -> str:
    if run.status == RunStatus.failed:
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
        lines.append(f"Не обработано: {remaining}")
    return "\n".join(lines)
