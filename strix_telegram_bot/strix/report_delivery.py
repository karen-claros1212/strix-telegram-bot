from __future__ import annotations

import json
import logging
from typing import Any

from strix.core.paths import run_dir_for

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
    from strix_telegram_bot.telegram import send_document

    run_dir = run_dir_for(run_name)
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
        if run_data.get("status") in ("failed", "error"):
            logger.error(
                "ROOT FAILED BEFORE FINISH_SCAN → OFFICIAL REPORT NOT PRODUCED. "
                "Context size exceeded. Root agent death means finish_scan never ran."
            )
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

    if not resp:
        logger.error(
            "deliver_report: send_document returned None for %s",
            run_name,
        )
        return "send_transient"

    if not resp.get("message_id"):
        error_code = resp.get("error_code", 0)
        if 400 <= error_code < 500:
            logger.error(
                "deliver_report: send_document permanent failure for %s"
                " — error_code=%s",
                run_name, error_code,
            )
            return "send_permanent"
        logger.error(
            "deliver_report: send_document transient failure for %s — %s",
            run_name, resp,
        )
        return "send_transient"

    logger.info("deliver_report: delivered for %s (message_id=%s)", run_name, resp.get("message_id"))
    return "delivered"
