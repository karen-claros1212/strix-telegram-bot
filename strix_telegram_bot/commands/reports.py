from __future__ import annotations

from typing import Any

from strix_telegram_bot.telegram import send_message, edit_message, answer_callback
from strix_telegram_bot.ui.keyboards import reports_list, back_to_menu, parse_callback
from strix_telegram_bot.ui.messages import escape_md
from strix_telegram_bot.strix.report_collector import ReportCollector
from strix_telegram_bot.jobs.job_store import JobStore
from strix_telegram_bot.security import authorized_only


@authorized_only
def cmd_reports(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    _show_reports(bot, chat_id)


@authorized_only
def callback_reports(bot: Any, update: dict) -> None:
    cb = update.get("callback_query", {})
    data = cb.get("data", "")
    chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
    msg_id = cb.get("message", {}).get("message_id", "")
    parts = parse_callback(data)

    answer_callback(bot, cb.get("id", ""))

    if len(parts) < 2:
        return

    action = parts[1]

    if action == "list":
        _show_reports(bot, chat_id, msg_id)

    elif len(parts) >= 3 and parts[0] == "report":
        report_name = parts[1]
        job_name = parts[2] if len(parts) > 2 else "unknown"
        rc = ReportCollector(job_name)
        content = rc.get_report_content(report_name)
        if content:
            send_message(bot, chat_id, f"Report: {report_name}\n\n{content[:4000]}")
        else:
            send_message(bot, chat_id, f"Report {report_name} not found.")


def _show_reports(bot, chat_id, msg_id=None) -> None:
    store = JobStore()
    completed = [j for j in store.list_all() if j.is_terminal and j.run_name != "pending"]

    all_reports = []
    for job in completed[:5]:
        rc = ReportCollector(job.run_name)
        reports = rc.collect()
        for r in reports:
            all_reports.append(r["name"])

    if not all_reports:
        text = "No reports available. Complete a scan first."
        kb = back_to_menu()
    else:
        text = "Available reports:"
        kb = reports_list(all_reports)

    if msg_id:
        edit_message(bot, chat_id, msg_id, text, reply_markup=kb)
    else:
        send_message(bot, chat_id, text, reply_markup=kb)


def _chat_id(update: dict) -> int:
    return (
        update.get("message", {}).get("chat", {}).get("id", "")
        or update.get("callback_query", {})
        .get("message", {})
        .get("chat", {})
        .get("id", 0)
    )
