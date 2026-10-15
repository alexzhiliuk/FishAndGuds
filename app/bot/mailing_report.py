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
        lines.extend(
            [
                f"Пользователей в базе: {summary['total_users']}",
                f"Исключено администраторов: {summary['excluded_admins']}",
                f"Неактивных: {summary['excluded_inactive']}",
                f"Отключили Telegram-рассылки: {summary['excluded_push']}",
                "",
            ]
        )
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
