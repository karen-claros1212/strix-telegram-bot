"""STRIX Control Center — main bot engine (raw HTTP polling)."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from .config import settings
from .telegram import get_updates, send_message, edit_message, answer_callback
from .security import is_authorized
from .models import FocusPreset, MenuState, ProfileType, ScanMode, ScopeMode
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
        self._chat_wizard: dict[int, bool] = {}
        self._last_broadcast: dict[str, float] = {}
        self._active_job_chat_id: Optional[int] = None
        self._active_job_message_id: Optional[int] = None

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
            "profile": self._callback_profile,
            "scope": self._callback_scope_mode,
            "focus": self._callback_focus,
            "job": callback_jobs,
            "job_detail": self._callback_job_detail,
            "report": callback_reports,
            "evidence": self._callback_evidence,
            "tools": self._callback_tools,
            "caido": self._callback_caido,
            "config": callback_config,
            "health": callback_health,
        }

    def _handle_command(self, update: dict) -> None:
        msg = update.get("message", {})
        chat_id = msg.get("chat", {}).get("id", 0)
        user_id = str(msg.get("from", {}).get("id", ""))

        if not is_authorized(user_id, str(chat_id)):
            send_message(self, chat_id, "Unauthorized.")
            return

        if "document" in msg or "photo" in msg:
            self._handle_document(update)
            return

        text = (msg.get("text") or "").strip()
        if not text:
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
        elif pm.current == MenuState.NEW_PENTEST_DIFF_BASE:
            pm._selected_diff_base = text
            pm.push(MenuState.NEW_PENTEST_FOCUS)
            from .ui.keyboards import focus_presets
            send_message(
                self, chat_id,
                f"Diff base: {text}\n\nFocus / Instruction:",
                reply_markup=focus_presets(),
            )
        elif pm.current == MenuState.NEW_PENTEST_INSTRUCTION:
            from .models import get_focus_instruction
            pm._selected_instruction = get_focus_instruction(FocusPreset.CUSTOM, text)
            pm.push(MenuState.NEW_PENTEST_DEPTH)
            send_message(
                self, chat_id,
                "Custom instruction saved.\n\nSelect scan mode:",
                reply_markup=depth_selector(),
            )
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
        pm.push(MenuState.NEW_PENTEST_PROFILE)
        from .ui.keyboards import profile_selector

        send_message(
            self, chat_id,
            f"Target: {', '.join(targets)}\nSelect profile:",
            reply_markup=profile_selector(),
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

        if target_type == "attachment":
            pm.push(MenuState.NEW_PENTEST_ATTACHMENT)
            edit_message(
                bot, chat_id, msg_id,
                "Upload the file as a document in this chat.\n"
                "The bot will save it and pass it to STRIX.",
                reply_markup=back_to_menu(),
            )
            return

        prompt = {
            "url": "Send the URL or domain:",
            "github": "Send the GitHub repo URL:",
            "local": "Send the local path:",
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

    def _handle_document(self, update: dict) -> None:
        msg = update.get("message", {})
        chat_id = msg.get("chat", {}).get("id", 0)
        doc = msg.get("document") or msg.get("photo", [None])[-1] if msg.get("photo") else None
        if not doc:
            send_message(self, chat_id, "Could not read file.")
            return

        from .telegram import get_file
        import tempfile
        from pathlib import Path
        from .strix.evidence_vault import EvidenceVault

        file_id = doc.get("file_id", "")
        file_name = doc.get("file_name", "upload.bin")

        file_bytes = get_file(self, file_id)
        if file_bytes is None:
            send_message(self, chat_id, "Failed to download file.")
            return

        pm = get_panel_manager()
        run_name = "upload"
        active = self._job_store.list_active()
        if active:
            run_name = active[0].run_name

        vault = EvidenceVault(run_name)
        artifact = vault.store_bytes(file_bytes, file_name, subdir="files", sensitive=False)
        if artifact is None:
            send_message(self, chat_id, "Failed to store file in evidence vault.")
            return

        save_path = Path(artifact["path"])
        abs_path = save_path.resolve()

        send_message(
            self, chat_id,
            f"File saved: {file_name}\nSHA256: {artifact['sha256'][:16]}...\n"
        )

        if pm.current == MenuState.NEW_PENTEST_ATTACHMENT:
            pm._selected_targets = [str(abs_path)]
            pm.push(MenuState.NEW_PENTEST_DEPTH)
            send_message(
                self, chat_id,
                f"Attachment ready: {abs_path.name}\nSelect scan mode:",
                reply_markup=self._depth_selector(),
            )

    def _callback_profile(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)
        answer_callback(bot, cb.get("id", ""))

        if len(parts) < 2:
            return

        pm = get_panel_manager()
        if parts[1] == "interactive":
            pm._selected_profile = ProfileType.INTERACTIVE
        elif parts[1] == "headless":
            pm._selected_profile = ProfileType.HEADLESS
        else:
            return

        pm.push(MenuState.NEW_PENTEST_SCOPE)
        from .ui.keyboards import scope_mode_selector
        edit_message(
            bot, chat_id, msg_id,
            f"Profile: {pm._selected_profile.value}\n\nConfigure scope mode:",
            reply_markup=scope_mode_selector(),
        )

    def _callback_scope_mode(self, bot: Any, update: dict) -> None:
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

        if action in ("auto", "diff", "full"):
            pm._selected_scope_mode = ScopeMode(action)
            from .ui.keyboards import scope_mode_selector
            edit_message(
                bot, chat_id, msg_id,
                f"Scope: {action.upper()}\n"
                "Optionally set a diff base or continue.",
                reply_markup=scope_mode_selector(),
            )
        elif action == "diff_base":
            pm.push(MenuState.NEW_PENTEST_DIFF_BASE)
            edit_message(
                bot, chat_id, msg_id,
                "Send a diff base (e.g. 'origin/main' or a commit hash):",
                reply_markup=back_to_menu(),
            )
        elif action == "done":
            pm.push(MenuState.NEW_PENTEST_FOCUS)
            from .ui.keyboards import focus_presets
            from .ui.messages import instruction_text
            edit_message(
                bot, chat_id, msg_id,
                instruction_text(),
                reply_markup=focus_presets(),
            )

    def _callback_focus(self, bot: Any, update: dict) -> None:
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

        if action == "skip":
            pm.push(MenuState.NEW_PENTEST_DEPTH)
            edit_message(
                bot, chat_id, msg_id,
                f"Target: {', '.join(pm._selected_targets)}\nSelect scan mode:",
                reply_markup=depth_selector(),
            )
            return

        preset_map = {
            "business_logic": FocusPreset.BUSINESS_LOGIC,
            "auth_jwt": FocusPreset.AUTH_JWT,
            "sql": FocusPreset.SQL,
            "xss": FocusPreset.XSS,
            "ssrf": FocusPreset.SSRF,
            "kubernetes": FocusPreset.KUBERNETES,
            "secrets": FocusPreset.SECRETS,
            "custom": FocusPreset.CUSTOM,
        }
        preset = preset_map.get(action)
        if preset is None:
            return

        pm._selected_focus = preset
        from .models import get_focus_instruction
        if preset == FocusPreset.CUSTOM:
            pm.push(MenuState.NEW_PENTEST_INSTRUCTION)
            edit_message(
                bot, chat_id, msg_id,
                "Send your custom instruction:",
                reply_markup=back_to_menu(),
            )
        else:
            pm._selected_instruction = get_focus_instruction(preset)
            pm.push(MenuState.NEW_PENTEST_DEPTH)
            edit_message(
                bot, chat_id, msg_id,
                f"Focus: {preset.value}\nInstruction ready.\n\nSelect scan mode:",
                reply_markup=depth_selector(),
            )

    def _callback_evidence(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)
        answer_callback(bot, cb.get("id", ""))

        if len(parts) < 2:
            return

        from .strix.evidence_vault import EvidenceVault
        from .ui.keyboards import evidence_detail_menu, evidence_list_menu
        from .ui.messages import evidence_text

        store = self._job_store
        jobs = [j for j in store.list_recent(5) if j.is_terminal and j.run_name != "pending"]
        if not jobs:
            edit_message(bot, chat_id, msg_id, "No completed jobs.", reply_markup=back_to_menu())
            return

        vault = EvidenceVault(jobs[0].run_name)
        action = parts[1]

        if action == "list":
            artifacts = vault.list_evidence()
            if not artifacts:
                edit_message(bot, chat_id, msg_id, "No evidence.", reply_markup=back_to_menu())
                return
            text = evidence_text(vault.get_manifest())
            edit_message(bot, chat_id, msg_id, text, reply_markup=evidence_list_menu(artifacts))

        elif action.startswith("preview:"):
            artifact_id = action.split(":", 1)[1]
            preview = vault.redacted_preview(artifact_id)
            if preview:
                send_message(bot, chat_id, f"Redacted preview:\n\n{preview[:3500]}")
                edit_message(bot, chat_id, msg_id, "Preview sent.", reply_markup=evidence_detail_menu(artifact_id))
            else:
                edit_message(bot, chat_id, msg_id, "Cannot preview.", reply_markup=back_to_menu())

        elif action.startswith("raw:"):
            artifact_id = action.split(":", 1)[1]
            manifest = vault.get_manifest()
            match = None
            for a in manifest.get("artifacts", []):
                if a["id"] == artifact_id:
                    match = a
                    break
            if match:
                vault_dir = vault._vault_dir or vault._resolve_vault()
                if vault_dir:
                    full_path = vault_dir / artifact_id
                    if full_path.exists():
                        content = full_path.read_text(encoding="utf-8", errors="replace")
                        send_message(bot, chat_id, f"RAW artifact:\n\n{content[:3500]}")
                        edit_message(bot, chat_id, msg_id, "RAW sent.", reply_markup=evidence_detail_menu(artifact_id))
                        return
            edit_message(bot, chat_id, msg_id, "Artifact not found.", reply_markup=back_to_menu())

        elif action.startswith("redacted:"):
            artifact_id = action.split(":", 1)[1]
            preview = vault.redacted_preview(artifact_id)
            if preview:
                send_message(bot, chat_id, f"Redacted artifact:\n\n{preview[:3500]}")
                edit_message(bot, chat_id, msg_id, "Redacted sent.", reply_markup=evidence_detail_menu(artifact_id))
            else:
                edit_message(bot, chat_id, msg_id, "Cannot redact.", reply_markup=back_to_menu())

        elif len(parts) >= 2:
            artifact_id = parts[1]
            edit_message(bot, chat_id, msg_id, "Evidence detail:", reply_markup=evidence_detail_menu(artifact_id))

    def _callback_tools(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)
        answer_callback(bot, cb.get("id", ""))

        from .strix.caido_panel import CaidoPanel
        from .strix.report_collector import ReportCollector
        from .ui.keyboards import tools_panel
        from .ui.messages import tools_panel_text

        store = self._job_store
        active = store.list_active()
        active_tools: list[dict] = []

        if active:
            job = active[0]
            rc = ReportCollector(job.run_name)
            events = rc.get_json_events()
            if events:
                seen: set[str] = set()
                for ev in events:
                    tool = ev.get("data", {}).get("tool", "")
                    if tool and tool not in seen:
                        seen.add(tool)
                        active_tools.append({"name": tool, "status": "active"})

        text = tools_panel_text(active_tools)
        edit_message(bot, chat_id, msg_id, text, reply_markup=tools_panel(active_tools))

    def _depth_selector(self):
        from .ui.keyboards import depth_selector
        return depth_selector()

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

    def _callback_caido(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)
        answer_callback(bot, cb.get("id", ""))

        if len(parts) < 2:
            return

        action = parts[1]
        from strix_telegram_bot.strix.caido_panel import CaidoPanel
        from strix_telegram_bot.ui.keyboards import caido_main_menu, back_to_menu

        store = self._job_store
        active = store.list_active()
        job = active[0] if active else None
        run_name = job.run_name if job else ""
        cp = CaidoPanel()

        if action == "detect" or action == "status":
            if run_name:
                status = cp.build_caido_panel(run_name)
            else:
                status = "No active job to detect Caido on."
            edit_message(bot, chat_id, msg_id, status, reply_markup=caido_main_menu())

        elif action == "artifacts":
            if run_name:
                artifacts = cp.collect_caido_artifacts(run_name)
                if artifacts:
                    lines = ["Caido Artifacts:"]
                    for a in artifacts:
                        lines.append(f"  {a['name']} ({a['size']/1024:.1f} KB)")
                    text = "\n".join(lines)
                else:
                    text = "No Caido artifacts found."
            else:
                text = "No active job."
            edit_message(bot, chat_id, msg_id, text, reply_markup=back_to_menu())

        elif action == "instructions":
            text = (
                "Caido is a web proxy for manual traffic inspection.\n\n"
                "STRIX exposes Caido when running scans.\n"
                "Use the URL above to:\n"
                "  - Inspect HTTP requests/responses\n"
                "  - Replay and modify requests\n"
                "  - Explore the sitemap\n"
                "  - Test manually alongside the agent\n\n"
                "Caido runs on localhost only."
            )
            edit_message(bot, chat_id, msg_id, text, reply_markup=caido_main_menu())

    def _drain_update_queue(self) -> None:
        if self._active_job_chat_id is None or self._active_job_message_id is None:
            self._job_runner.update_queue.queue.clear()
            return

        processed = False
        while not self._job_runner.update_queue.empty():
            try:
                job = self._job_runner.update_queue.get_nowait()
                if job:
                    text = job_status_text(job)
                    edit_message(
                        self,
                        self._active_job_chat_id,
                        self._active_job_message_id,
                        text,
                        reply_markup=job_panel(running=job.is_active),
                    )
                    processed = True
            except Exception:
                break

        if not processed:
            job = self._job_runner.state
            if job:
                text = job_status_text(job)
                edit_message(
                    self,
                    self._active_job_chat_id,
                    self._active_job_message_id,
                    text,
                    reply_markup=job_panel(running=job.is_active),
                )

    def _launch_scan(
        self,
        bot: Any,
        chat_id: int,
        msg_id: int,
        targets: Optional[list[str]] = None,
        mode: Optional[ScanMode] = None,
    ) -> None:
        pm = get_panel_manager()

        if targets is None:
            targets = pm._selected_targets
        if mode is None:
            mode = pm._selected_depth

        if not targets:
            edit_message(bot, chat_id, msg_id, "No target specified.", reply_markup=back_to_menu())
            return

        ok, start_msg = self._job_runner.start(
            targets=targets,
            mode=mode,
            instruction=pm._selected_instruction,
            scope_mode=pm._selected_scope_mode.value,
            non_interactive=(pm._selected_profile == ProfileType.HEADLESS),
            diff_base=pm._selected_diff_base or None,
        )

        if ok:
            self._active_job_chat_id = chat_id
            self._active_job_message_id = msg_id
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
                self._drain_update_queue()
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
