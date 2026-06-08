"""STRIX Control Center — main bot engine (raw HTTP polling)."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from .config import settings
from .telegram import get_updates, send_message, edit_message, answer_callback
from .security import is_authorized
from .models import MenuState, ScanMode
from .ui.keyboards import (
    main_menu,
    target_type_selector,
    depth_selector,
    job_panel,
    back_to_menu,
    parse_callback,
)
from .ui.messages import (
    main_menu_text,
    job_status_text,
    help_text,
    escape_md,
)
from .ui.panels import get_panel_manager
from .jobs.job_store import JobStore
from .jobs.job_runner import JobRunner
from .jobs.process_control import ProcessController

logger = logging.getLogger("strix_bot")


class StrixBot:
    def __init__(self) -> None:
        self._updates_offset: Optional[int] = None
        self._running = False
        self._job_store = JobStore()
        self._process_controller = ProcessController()
        self._job_runner = JobRunner(self._job_store, self._process_controller)
        self._job_runner.set_update_callback(self._on_job_update)
        self._chat_wizard: dict[int, bool] = {}
        self._last_broadcast: dict[str, float] = {}

        self._command_handlers: dict[str, Callable] = {}
        self._callback_handlers: dict[str, Callable] = {}
        self._register_handlers()

    def _register_handlers(self) -> None:
        from .commands.start import cmd_start, callback_menu
        from .commands.health import cmd_health, cmd_version, cmd_uptime, callback_health
        from .commands.jobs import cmd_jobs, cmd_status, cmd_stop, callback_jobs
        from .commands.reports import cmd_reports, callback_reports
        from .commands.config import cmd_config, callback_config

        self._command_handlers = {
            "/start": cmd_start,
            "/help": cmd_start,
            "/health": cmd_health,
            "/version": cmd_version,
            "/uptime": cmd_uptime,
            "/jobs": cmd_jobs,
            "/status": cmd_status,
            "/stop": cmd_stop,
            "/reports": cmd_reports,
            "/config": cmd_config,
        }

        self._callback_handlers = {
            "menu": callback_menu,
            "target": self._callback_target,
            "depth": self._callback_depth,
            "job": callback_jobs,
            "job_detail": self._callback_job_detail,
            "report": callback_reports,
            "approve": self._callback_approve,
            "config": callback_config,
            "health": callback_health,
        }

    def _handle_command(self, update: dict) -> None:
        msg = update.get("message", {})
        text = (msg.get("text") or "").strip()
        chat_id = msg.get("chat", {}).get("id", 0)
        user_id = str(msg.get("from", {}).get("id", ""))

        if not text:
            return

        if not is_authorized(user_id, str(chat_id)):
            send_message(self, chat_id, "Unauthorized.")
            return

        handler = self._command_handlers.get(text.split()[0].lower())
        if handler:
            handler(self, update)
        else:
            self._handle_text_message(update)

    def _handle_text_message(self, update: dict) -> None:
        msg = update.get("message", {})
        text = (msg.get("text") or "").strip()
        chat_id = msg.get("chat", {}).get("id", 0)

        pm = get_panel_manager()
        job = self._job_runner.state

        if pm.current == MenuState.NEW_PENTEST_TARGET:
            self._handle_wizard_target(chat_id, text, msg)
        elif job and job.awaiting_input:
            self._job_runner.inject_input(text)
            send_message(self, chat_id, "Response sent to STRIX.")
        elif job and job.is_active:
            self._job_runner.inject_input(text)
            send_message(self, chat_id, "Message sent to STRIX.")
        else:
            send_message(
                self, chat_id,
                "No active job. Use /start to begin.",
                reply_markup=main_menu(),
            )

    def _handle_wizard_target(self, chat_id: int, text: str, msg: dict) -> None:
        pm = get_panel_manager()
        targets = [t.strip() for t in text.replace("\n", ",").split(",") if t.strip()]

        if not targets:
            send_message(self, chat_id, "Please send a valid target.")
            return

        from .safety.attachment_policy import sanitize_target
        for t in targets:
            ok, err = sanitize_target(t)
            if not ok:
                send_message(self, chat_id, f"Invalid target {t}: {err}")
                return

        pm._selected_targets = targets
        pm.push(MenuState.NEW_PENTEST_DEPTH)

        send_message(
            self, chat_id,
            f"Target: {', '.join(targets)}\nSelect scan mode:",
            reply_markup=depth_selector(),
        )

    def _handle_callback(self, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", 0)
        user_id = str(cb.get("from", {}).get("id", ""))

        if not data or not is_authorized(user_id, str(chat_id)):
            answer_callback(self, cb.get("id", ""))
            return

        prefix = data.split(":")[0] if ":" in data else data
        handler = self._callback_handlers.get(prefix)
        if handler:
            handler(self, update)
        else:
            answer_callback(self, cb.get("id", ""))

    def _callback_target(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)
        answer_callback(bot, cb.get("id", ""))

        if len(parts) < 2:
            return

        pm = get_panel_manager()
        target_type = parts[1]
        pm.push(MenuState.NEW_PENTEST_TARGET)

        prompt = {
            "url": "Send the URL or domain:",
            "github": "Send the GitHub repo URL:",
            "local": "Send the local path:",
            "attachment": "Upload the file:",
            "multi": "Send targets (comma or line separated):",
        }.get(target_type, "Send the target:")

        edit_message(bot, chat_id, msg_id, prompt, reply_markup=back_to_menu())

    def _callback_depth(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)
        answer_callback(bot, cb.get("id", ""))

        if len(parts) < 2:
            return

        pm = get_panel_manager()
        action = parts[1]

        if action in ("quick", "standard", "deep"):
            pm._selected_depth = ScanMode(action)
            edit_message(
                bot, chat_id, msg_id,
                f"Mode: {action.upper()}\n{pm.wizard_summary()}",
                reply_markup=depth_selector(),
            )

        elif action == "confirm":
            if pm.wizard_complete:
                self._launch_scan(bot, chat_id, msg_id)
            else:
                edit_message(
                    bot, chat_id, msg_id,
                    "Please select a target first.",
                    reply_markup=back_to_menu(),
                )

    def _callback_job_detail(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)
        answer_callback(bot, cb.get("id", ""))

        if len(parts) < 2:
            return

        run_name = parts[1]
        job = self._job_store.get(run_name)
        if job:
            text = job_status_text(job)
            edit_message(
                bot, chat_id, msg_id, text,
                reply_markup=job_panel(running=job.is_active),
            )
        else:
            edit_message(
                bot, chat_id, msg_id,
                "Job not found.", reply_markup=back_to_menu(),
            )

    def _callback_approve(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)
        answer_callback(bot, cb.get("id", ""))

        if len(parts) < 2:
            return

        decision = parts[1]
        from .safety.approval_gate import get_gate
        gate = get_gate()
        pending = gate.list_pending()
        if pending:
            req = pending[0]
            approved = decision in ("yes", "deep")
            gate.resolve(req.job_run_name, approved)
            edit_message(
                bot, chat_id, msg_id,
                f"{'Approved' if approved else 'Cancelled'}",
                reply_markup=back_to_menu(),
            )
            if approved:
                self._launch_scan(
                    bot, chat_id, msg_id,
                    targets=req.target,
                    mode=req.mode,
                    force=True,
                )

    def _on_job_update(self, job) -> None:
        pass

    def _launch_scan(
        self,
        bot: Any,
        chat_id: int,
        msg_id: int,
        targets: Optional[list[str]] = None,
        mode: Optional[ScanMode] = None,
        force: bool = False,
    ) -> None:
        pm = get_panel_manager()

        if targets is None:
            targets = pm._selected_targets
        if mode is None:
            mode = pm._selected_depth

        if not targets:
            edit_message(bot, chat_id, msg_id, "No target specified.", reply_markup=back_to_menu())
            return

        from .safety.scope_policy import validate_scope
        ok, msg = validate_scope(targets)
        if not ok and not force:
            edit_message(bot, chat_id, msg_id, f"Scope issue: {msg}", reply_markup=back_to_menu())
            return

        ok, start_msg = self._job_runner.start(
            targets=targets,
            mode=mode,
            instruction=pm._selected_instruction,
        )

        if ok:
            pm.reset_wizard()
            job = self._job_runner.state
            text = job_status_text(job) if job else "Scan started"
            edit_message(bot, chat_id, msg_id, text, reply_markup=job_panel(running=True))
        else:
            edit_message(bot, chat_id, msg_id, f"Failed: {start_msg}", reply_markup=back_to_menu())

    def process_update(self, update: dict) -> None:
        if "message" in update:
            self._handle_command(update)
        elif "callback_query" in update:
            self._handle_callback(update)

    def run(self, poll_interval: float = 1.0) -> None:
        logger.info("STRIX Bot starting...")
        self._running = True

        while self._running:
            try:
                updates = get_updates(offset=self._updates_offset, timeout=30)
                for upd in updates:
                    self._updates_offset = upd["update_id"] + 1
                    self.process_update(upd)
            except KeyboardInterrupt:
                logger.info("Shutdown requested.")
                break
            except Exception as e:
                logger.error(f"Poll error: {e}")
                time.sleep(5)

        self.shutdown()

    def shutdown(self) -> None:
        logger.info("Shutting down...")
        self._running = False
        self._job_runner.cleanup()
