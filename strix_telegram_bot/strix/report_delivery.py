from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("strix_bot")


def deliver_report_document(
    bot: Any,
    chat_id: int,
    run_name: str,
) -> str:
    """Validate and deliver the official penetration test report as a single Markdown document.

    Validation:
        - run.json must exist with status == "completed"
        - penetration_test_report.md must exist and be non-empty

    Returns one of:
        "delivered"       — file sent successfully via sendDocument
        "missing"         — report file missing or empty / run.json missing
        "not_completed"   — run.json status != "completed"
        "send_failed"     — sendDocument call failed (no message_id)
    """
    from strix_telegram_bot.config import settings
    from strix_telegram_bot.telegram import send_document

    run_dir = settings.strix_runs_dir / run_name
    run_json_path = run_dir / "run.json"
    report_path = run_dir / "penetration_test_report.md"

    # 1. Verify run.json exists and status == completed
    if not run_json_path.is_file():
        logger.warning("deliver_report: run.json not found for %s", run_name)
        return "missing"

    try:
        with open(run_json_path) as f:
            run_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("deliver_report: cannot parse run.json for %s — %s", run_name, e)
        return "missing"

    if run_data.get("status") != "completed":
        logger.info(
            "deliver_report: run %s status is %s (not completed)",
            run_name, run_data.get("status"),
        )
        return "not_completed"

    # 2. Verify penetration_test_report.md exists and is non-empty
    if not report_path.is_file() or report_path.stat().st_size == 0:
        logger.warning("deliver_report: penetration_test_report.md missing or empty for %s", run_name)
        return "missing"

    # 3. Send via sendDocument
    display_name = f"STRIX_{run_name}_INFORME_COMPLETO.md"
    caption = f"Informe completo oficial de Strix\nRun: {run_name}"

    resp = send_document(
        bot,
        chat_id,
        str(report_path),
        filename=display_name,
        caption=caption,
    )

    if not resp or not resp.get("message_id"):
        logger.error(
            "deliver_report: send_document failed for %s — %s",
            run_name, resp,
        )
        return "send_failed"

    logger.info("deliver_report: delivered for %s (message_id=%s)", run_name, resp.get("message_id"))
    return "delivered"
