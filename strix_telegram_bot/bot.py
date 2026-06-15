from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .telegram import get_updates, send_message, edit_message, answer_callback
from .security import is_authorized
from .models import JobPhase, JobState, MenuState, ScanMode
from .ui.keyboards import (
    main_menu,
    job_panel,
    back_to_menu,
    agent_selector,
    parse_callback,
)
from .ui.messages import (
    job_status_text,
    main_menu_text,
    escape_md,
)
from .ui.panels import get_panel_manager
from .jobs.job_store import JobStore
from .strix.runtime_bridge import StrixRuntimeBridge

logger = logging.getLogger("strix_bot")


class StrixBot:
    def __init__(self) -> None:
        self._updates_offset: Optional[int] = None
        self._running = False
        self._job_store = JobStore()
        self._bridge = StrixRuntimeBridge()
        self._last_broadcast: dict[str, float] = {}

        self._active_job_chat_id: Optional[int] = None
        self._active_job_message_id: Optional[int] = None
        self._active_job_run_name: Optional[str] = None

        self._command_handlers: dict[str, Callable] = {}
        self._callback_handlers: dict[str, Callable] = {}
        self._register_handlers()

    def _register_handlers(self) -> None:
        from .commands.start import cmd_start, cmd_help, callback_menu
        from .commands.health import cmd_health, cmd_version, cmd_uptime, callback_health
        from .commands.jobs import cmd_jobs, cmd_status, cmd_stop, callback_jobs
        from .commands.reports import cmd_reports, callback_reports
        from .commands.config import cmd_config, callback_config

        self._command_handlers = {
            "/start": cmd_start,
            "/help": cmd_help,
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
            "job": callback_jobs,
            "report": callback_reports,
            "config": callback_config,
            "health": callback_health,
            "agent": self._callback_agent_select,
        }

    def _register_slash_commands(self) -> None:
        from .telegram import _request
        commands = [
            {"command": "start", "description": "Menú principal"},
            {"command": "help", "description": "Ayuda y comandos"},
            {"command": "health", "description": "Estado del sistema"},
            {"command": "version", "description": "Versión de STRIX"},
            {"command": "status", "description": "Estado del escaneo activo"},
            {"command": "stop", "description": "Detener escaneo activo"},
            {"command": "jobs", "description": "Historial de trabajos"},
            {"command": "reports", "description": "Centro de reportes"},
            {"command": "config", "description": "Configuración del bot"},
        ]
        _request("setMyCommands", {"commands": commands})

    def _handle_command(self, update: dict) -> None:
        msg = update.get("message", {})
        chat_id = msg.get("chat", {}).get("id", 0)
        user_id = str(msg.get("from", {}).get("id", ""))

        if not is_authorized(user_id, str(chat_id)):
            send_message(self, chat_id, "No autorizado.")
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

        from .telegram import send_chat_action
        send_chat_action(self, chat_id)

        # If bridge is running, forward text to agent
        if self._bridge.is_running:
            root_id = self._bridge.root_agent_id
            ok = self._bridge.send_message_to_agent(text, agent_id=root_id)
            if ok:
                send_message(self, chat_id, "Mensaje enviado a STRIX.")
            else:
                send_message(self, chat_id, "Error al enviar el mensaje.")
            return

        pm = get_panel_manager(chat_id)
        if pm.current == MenuState.WAITING_FOR_TARGETS:
            self._parse_and_launch(chat_id, text, msg)
        else:
            send_message(
                self, chat_id,
                "Usá /start o el botón Escanear para iniciar.",
                reply_markup=main_menu(),
            )

    def _parse_and_launch(self, chat_id: int, text: str, msg: dict) -> None:
        targets = [t.strip() for t in text.replace("\n", ",").split(",") if t.strip()]
        if not targets:
            send_message(self, chat_id, "Enviá al menos un objetivo.")
            return

        from .safety.attachment_policy import sanitize_target
        for t in targets:
            ok, err = sanitize_target(t)
            if not ok:
                send_message(self, chat_id, f"Objetivo inválido {t}: {err}")
                return

        self._launch_scan(chat_id, targets, msg.get("message_id"))

    def _handle_callback(self, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", 0)
        user_id = str(cb.get("from", {}).get("id", ""))
        cb_id = cb.get("id", "")

        if not data or not is_authorized(user_id, str(chat_id)):
            answer_callback(self, cb_id)
            return

        answer_callback(self, cb_id)

        prefix = data.split(":")[0] if ":" in data else data
        handler = self._callback_handlers.get(prefix)
        if handler:
            handler(self, update)

    def _callback_agent_select(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)

        if len(parts) < 2:
            return

        agent_id = parts[1]
        agents = self._bridge.list_agents()
        agent = next((a for a in agents if a["id"] == agent_id), None)
        if not agent:
            edit_message(bot, chat_id, msg_id, "Agente no encontrado.", reply_markup=back_to_menu())
            return

        # Store selected agent ID in bridge as preferred target
        self._bridge._preferred_agent_id = agent_id
        name = agent.get("name", agent_id)
        edit_message(
            bot, chat_id, msg_id,
            f"Ahora enviando mensajes a: {escape_md(name)}",
            reply_markup=job_panel(running=True),
        )

    def _handle_document(self, update: dict) -> None:
        msg = update.get("message", {})
        chat_id = msg.get("chat", {}).get("id", 0)

        from .telegram import send_chat_action
        send_chat_action(self, chat_id)

        doc = None
        if msg.get("document"):
            doc = msg["document"]
        elif msg.get("photo"):
            doc = msg["photo"][-1]
        if not doc:
            send_message(self, chat_id, "No se pudo leer el archivo.")
            return

        from .telegram import get_file
        from .strix.evidence_vault import EvidenceVault

        file_id = doc.get("file_id", "")
        file_name = doc.get("file_name", "upload.bin") if "file_name" in doc else "photo.jpg"

        file_bytes = get_file(self, file_id)
        if file_bytes is None:
            send_message(self, chat_id, "Error al descargar el archivo.")
            return

        pm = get_panel_manager(chat_id)
        run_name = "upload"
        if self._bridge.is_running:
            run_name = self._bridge.run_name or "upload"

        vault = EvidenceVault(run_name)
        artifact = vault.store_bytes(file_bytes, file_name, subdir="files", sensitive=False)
        if artifact is None:
            send_message(self, chat_id, "Error al guardar el archivo.")
            return

        abs_path = Path(artifact["absolute_path"])

        if pm.current == MenuState.WAITING_FOR_TARGETS:
            self._launch_scan(chat_id, [str(abs_path)])
        elif self._bridge.is_running:
            send_message(self, chat_id, f"Archivo guardado: {abs_path.name}")
        else:
            send_message(
                self, chat_id,
                f"Archivo guardado: {file_name}\n"
                "Usá /start para escanearlo.",
            )

    def _prepare_scan_targets(self, targets: list[str]) -> tuple[list[str], list[dict[str, str]]]:
        from strix_telegram_bot.config import settings
        from strix_telegram_bot.strix.runtime_bridge import clone_repository

        final_targets: list[str] = []
        local_sources: list[dict[str, str]] = []
        repos_dir = settings.strix_runs_dir / "repos"

        def _add_local(path: Path, subdir: str) -> None:
            sr = str(path.resolve())
            local_sources.append({"source_path": sr, "workspace_subdir": subdir})

        for t in targets:
            t = t.strip()
            p = Path(t)

            if p.exists():
                if p.is_dir():
                    sr = str(p.resolve())
                    final_targets.append(sr)
                    _add_local(p, p.name)
                else:
                    wrap_dir = repos_dir / "_attachments" / p.stem
                    wrap_dir.mkdir(parents=True, exist_ok=True)
                    target_path = wrap_dir / p.name
                    if not target_path.exists():
                        try:
                            target_path.symlink_to(p.resolve())
                        except OSError:
                            try:
                                import shutil
                                shutil.copy2(str(p.resolve()), str(target_path))
                            except OSError as e:
                                logger.warning("Failed to copy attachment %s: %s", p, e)
                                final_targets.append(t)
                                continue
                    final_targets.append(str(wrap_dir))
                    _add_local(wrap_dir, p.stem)
                continue

            m = re.search(r'github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$', t)
            if m:
                repo_full = m.group(1).rstrip("/")
                clone_dir = repos_dir / repo_full
                should_clone = not clone_dir.exists()

                try:
                    if should_clone and clone_repository:
                        clone_dir.parent.mkdir(parents=True, exist_ok=True)
                        clone_repository(
                            repo_url=f"https://github.com/{repo_full}.git",
                            clone_dir=str(clone_dir),
                        )
                    elif should_clone:
                        subprocess.run(
                            ["git", "clone", f"https://github.com/{repo_full}.git", str(clone_dir)],
                            capture_output=True, text=True, timeout=120, check=True,
                        )
                    if clone_dir.exists():
                        git_dir = clone_dir / ".git"
                        if (git_dir / "shallow").exists():
                            (git_dir / "shallow").unlink()
                            fetch_dir = git_dir / "fetch"
                            if fetch_dir.exists():
                                fetch_dir.rmdir()
                except Exception as e:
                    logger.warning("Failed to clone %s: %s", t, e)
                    final_targets.append(t)
                    continue

                final_targets.append(str(clone_dir))
                _add_local(clone_dir, repo_full.split("/")[-1].removesuffix(".git"))
                continue

            final_targets.append(t)

        return final_targets, local_sources

    def _launch_scan(
        self,
        chat_id: int,
        targets: list[str],
        msg_id: Optional[int] = None,
    ) -> None:
        from .telegram import send_chat_action
        send_chat_action(self, chat_id)

        if not targets:
            text = "No se especificó objetivo."
            if msg_id:
                edit_message(self, chat_id, msg_id, text, reply_markup=back_to_menu())
            else:
                send_message(self, chat_id, text, reply_markup=back_to_menu())
            return

        prepared_targets, local_sources = self._prepare_scan_targets(targets)

        ok, start_msg = self._bridge.start_scan(
            targets=prepared_targets,
            scan_mode="deep",
            instruction="",
            scope_mode="auto",
            non_interactive=False,
            local_sources=local_sources,
        )

        if not ok:
            text = f"Error: {start_msg}"
            if msg_id:
                edit_message(self, chat_id, msg_id, text, reply_markup=back_to_menu())
            else:
                send_message(self, chat_id, text, reply_markup=back_to_menu())
            return

        run_name = self._bridge.run_name or f"scan-{time.time():.0f}"
        self._active_job_chat_id = chat_id
        self._active_job_message_id = msg_id
        self._active_job_run_name = run_name

        job = JobState(
            run_name=run_name,
            target=targets,
            mode=ScanMode.DEEP,
            phase=JobPhase.SCANNING,
        )
        self._job_store.save(job)

        pm = get_panel_manager(chat_id)
        pm.back_to_main()

        status = self._bridge.to_status_dict()
        text = job_status_text(status) if self._bridge.run_name else "Escaneo iniciado"

        if msg_id:
            edit_message(self, chat_id, msg_id, text, reply_markup=job_panel(running=True))
        else:
            send_message(self, chat_id, text, reply_markup=job_panel(running=True))

    def _drain_update_queue(self) -> None:
        events = self._bridge.poll_events()
        self._process_scan_events(events)

        status = self._bridge.to_status_dict()
        run_name = status.get("run_name")

        if run_name:
            job = self._job_store.get(run_name)
            if job:
                phase_str = status.get("phase", "running")
                _PHASE_MAP: dict[str, JobPhase] = {
                    "initializing": JobPhase.SCANNING,
                    "running": JobPhase.SCANNING,
                    "completed": JobPhase.COMPLETED,
                    "failed": JobPhase.FAILED,
                    "stopped": JobPhase.STOPPED,
                }
                job.phase = _PHASE_MAP.get(phase_str, JobPhase.SCANNING)
                job.awaiting_input = status.get("awaiting_input", False)
                job.input_prompt = status.get("input_prompt")
                job.error = status.get("error")
                self._job_store.save(job)

            if not status.get("is_active"):
                if job and job.is_active:
                    if status.get("error"):
                        job.phase = JobPhase.FAILED
                    else:
                        job.phase = JobPhase.COMPLETED
                    job.error = status.get("error")
                    self._job_store.save(job)

        if self._active_job_chat_id is not None:
            text = job_status_text(status)
            edit_message(
                self,
                self._active_job_chat_id,
                self._active_job_message_id,
                text,
                reply_markup=job_panel(running=status.get("is_active", False)),
            )

            if not status.get("is_active") and run_name:
                delta = status.get("elapsed", "0s")
                phase = status.get("phase", "completed")
                final = f"Escaneo finalizado.\nEstado: {phase}\nDuración: {delta}"
                chat_id = self._active_job_chat_id
                self._active_job_chat_id = None
                self._active_job_message_id = None
                self._active_job_run_name = None
                send_message(self, chat_id, final, reply_markup=back_to_menu())

    def _process_scan_events(self, events: list) -> None:
        if not events or self._active_job_chat_id is None:
            return

        from .telegram import send_chat_action, send_message

        chat_id = self._active_job_chat_id
        last_ts: float = self._last_broadcast.get("event", 0.0)

        for ev in events:
            if ev.timestamp <= last_ts:
                continue

            if ev.type == "agent_message":
                send_chat_action(self, chat_id)
                content = ev.content[:4000] if ev.content else "..."
                send_message(self, chat_id, f"*{escape_md(ev.agent_id)}*:\n{escape_md(content)}")

            elif ev.type == "tool_call":
                content = ev.content[:200] if ev.content else "..."
                send_message(self, chat_id, f"▶ *{escape_md(ev.agent_id)}* ejecuta: {escape_md(content)}")

            elif ev.type == "tool_output":
                try:
                    data = json.loads(ev.content)
                    tool_name = data.get("tool_name", "?")
                    output = data.get("output", "")[:500]
                except Exception:
                    tool_name = "?"
                    output = ev.content[:200]
                send_message(self, chat_id, f"✅ *{escape_md(tool_name)}* completado:\n`{escape_md(output)}`")

            elif ev.type == "tool_cancelled":
                send_message(self, chat_id, f"⏹ *{escape_md(ev.content)}* cancelada")

            elif ev.type == "scan_complete":
                send_message(self, chat_id, "✅ Escaneo completado.", reply_markup=main_menu())

            elif ev.type == "scan_error":
                send_message(self, chat_id, f"❌ Error: {escape_md(ev.content)}", reply_markup=main_menu())

            elif ev.type == "scan_cancelled":
                send_message(self, chat_id, "⏹ Escaneo detenido.", reply_markup=main_menu())

        if events:
            self._last_broadcast["event"] = events[-1].timestamp

    def process_update(self, update: dict) -> None:
        if "message" in update:
            self._handle_command(update)
        elif "callback_query" in update:
            self._handle_callback(update)

    def run(self, poll_interval: float = 1.0) -> None:
        logger.info("STRIX Bot starting...")
        self._register_slash_commands()
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
        self._bridge.cleanup()
