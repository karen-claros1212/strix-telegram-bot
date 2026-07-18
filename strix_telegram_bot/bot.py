from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
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
from .strix.telegram_renderers import render_tool_event

logger = logging.getLogger("strix_bot")

_URL_RE = re.compile(r"https?://[^\s,]+")
_GITHUB_RE = re.compile(r"github\.com[:/][^\s,]+")


class StrixBot:
    _MAX_MSG = 4000

    def __init__(self) -> None:
        self._updates_offset: Optional[int] = None
        self._running = False
        self._job_store = JobStore()
        self._bridge = StrixRuntimeBridge()
        self._active_job_chat_id: Optional[int] = None
        self._active_job_message_id: Optional[int] = None
        self._active_job_run_name: Optional[str] = None

        # Chat fragmentation: event_id -> list of Telegram message_ids
        self._chat_fragments: dict[str, list[int]] = {}
        self._chat_fragment_count: dict[str, int] = {}
        self._chat_event_version: dict[str, int] = {}
        self._tool_message_ids: dict[str, int] = {}

        self._active_chat_agent_id: Optional[str] = None
        self._active_chat_message_id: Optional[int] = None
        self._active_chat_chat_id: Optional[int] = None

        self._command_handlers: dict[str, Callable] = {}
        self._callback_handlers: dict[str, Callable] = {}
        self._drain_thread: Optional[threading.Thread] = None
        self._last_panel_text: str = ""
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
            {"command": "scan", "description": "Iniciar escaneo de seguridad"},
            {"command": "status", "description": "Estado del escaneo activo"},
            {"command": "chat", "description": "Abrir conversacion con un agente"},
            {"command": "stop", "description": "Detener escaneo activo"},
            {"command": "jobs", "description": "Historial de trabajos"},
            {"command": "reports", "description": "Centro de reportes"},
            {"command": "help", "description": "Ayuda y comandos"},
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

        if self._bridge.is_running:
            agent_id = (
                getattr(self._bridge, "_preferred_agent_id", None)
                or self._bridge.root_agent_id
            )
            ok = self._bridge.send_message_to_agent(text, agent_id=agent_id)
            if not ok:
                send_message(self, chat_id, "STRIX no pudo recibir el mensaje.")
            return

        pm = get_panel_manager(chat_id)

        if pm.current == MenuState.WAITING_FOR_TARGETS:
            self._parse_and_launch(chat_id, text)
            return

        targets, instruction = self._extract_targets(text)

        if targets:
            scan_mode = getattr(pm, "_selected_scan_mode", "deep")
            if isinstance(scan_mode, ScanMode):
                scan_mode = scan_mode.value
            self._launch_scan(chat_id, targets, instruction, scan_mode=scan_mode)
        else:
            send_message(
                self,
                chat_id,
                "Envía una URL, dominio, IP, repositorio o carpeta para iniciar.",
                reply_markup=main_menu(),
            )

    @staticmethod
    def _clean_url(url: str) -> str:
        return url.rstrip(".,;:!?)]}")

    def _extract_targets(self, text: str) -> tuple[list[str], str]:
        raw_urls = _URL_RE.findall(text)
        urls = [self._clean_url(u) for u in raw_urls]
        remaining = _URL_RE.sub("", text).strip()
        raw_repos = _GITHUB_RE.findall(remaining)
        repos = [self._clean_url(r) for r in raw_repos]
        remaining = _GITHUB_RE.sub("", remaining).strip()

        candidates = [
            t.strip().rstrip(".,;:!?)]}")
            for t in remaining.replace("\n", ",").split(",")
            if t.strip()
        ]

        _DOMAIN_RE = re.compile(
            r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?'
            r'(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$'
        )
        _IP_RE = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(/\d{1,2})?$')

        extra_targets: list[str] = []
        instruction_parts: list[str] = []

        for c in candidates:
            p = Path(c)
            if _DOMAIN_RE.match(c) or _IP_RE.match(c) or p.exists():
                extra_targets.append(c)
            else:
                instruction_parts.append(c)

        targets = list(dict.fromkeys(urls + repos + extra_targets))
        return targets, ", ".join(instruction_parts)

    def _parse_and_launch(self, chat_id: int, text: str) -> None:
        targets, instruction = self._extract_targets(text)
        if not targets:
            send_message(self, chat_id, "No encontre ningun objetivo (URL, ruta, repo).")
            return

        from .safety.attachment_policy import sanitize_target
        for t in targets:
            ok, err = sanitize_target(t)
            if not ok:
                send_message(self, chat_id, f"Objetivo invalido {t}: {err}")
                return

        pm = get_panel_manager(chat_id)
        scan_mode = getattr(pm, "_selected_scan_mode", "deep")
        if isinstance(scan_mode, ScanMode):
            scan_mode = scan_mode.value
        self._launch_scan(chat_id, targets, instruction, scan_mode=scan_mode)

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

        self._bridge._preferred_agent_id = agent_id
        name = agent.get("name", agent_id)

        from .commands.jobs import _show_agent_chat
        _show_agent_chat(bot, chat_id, msg_id, self._bridge, agent_id)

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
                "Usá el botón Escanear para iniciar un escaneo.",
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
                    import shutil

                    source_path = p.resolve()

                    try:
                        # LocalDir no admite symlinks. El archivo entregado al sandbox
                        # debe ser un archivo regular dentro del directorio montado.
                        if target_path.is_symlink() or target_path.exists():
                            target_path.unlink()

                        shutil.copy2(source_path, target_path)

                        if not target_path.is_file() or target_path.is_symlink():
                            raise RuntimeError(
                                f"Attachment was not materialized as a regular file: {target_path}"
                            )

                        logger.info(
                            "Attachment prepared for sandbox: source=%s target=%s size=%d",
                            source_path,
                            target_path,
                            target_path.stat().st_size,
                        )

                    except (OSError, shutil.Error, RuntimeError) as exc:
                        logger.exception(
                            "Failed to prepare attachment %s for sandbox: %s",
                            source_path,
                            exc,
                        )
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
        instruction: str = "",
        scan_mode: str = "deep",
    ) -> None:
        from .telegram import send_chat_action
        send_chat_action(self, chat_id)

        if not targets:
            send_message(self, chat_id, "No se especificó objetivo.", reply_markup=back_to_menu())
            return

        prepared_targets, local_sources = self._prepare_scan_targets(targets)

        # Always prepend Spanish instruction
        _LANGUAGE_INSTRUCTION = (
            "Responde siempre al usuario en español. "
            "Describe en español el progreso, los hallazgos y las preguntas. "
            "Usa inglés solo en comandos, código, nombres técnicos y salidas literales."
        )
        full_instruction = _LANGUAGE_INSTRUCTION
        if instruction.strip():
            full_instruction += (
                "\n\nInstrucción específica del usuario:\n"
                + instruction.strip()
            )

        ok, start_msg = self._bridge.start_scan(
            targets=prepared_targets,
            scan_mode=scan_mode,
            instruction=full_instruction,
            scope_mode="auto",
            non_interactive=True,
            local_sources=local_sources,
        )

        if not ok:
            send_message(self, chat_id, f"Error: {start_msg}", reply_markup=back_to_menu())
            return

        run_name = self._bridge.run_name or f"scan-{time.time():.0f}"

        job = JobState(
            run_name=run_name,
            target=targets,
            mode=ScanMode.DEEP,
            phase=JobPhase.SCANNING,
            instruction=instruction,
        )
        self._job_store.save(job)

        pm = get_panel_manager(chat_id)
        pm.back_to_main()

        status = self._bridge.to_status_dict()
        text = job_status_text(status) if self._bridge.run_name else "STRIX — Inicializando…"
        agent_count = len(self._bridge.list_agents() or [])
        resp = send_message(self, chat_id, text, reply_markup=job_panel(running=True, agent_count=agent_count))
        panel_msg_id = resp.get("message_id") if isinstance(resp, dict) else None

        self._active_job_chat_id = chat_id
        self._active_job_message_id = panel_msg_id
        self._active_job_run_name = run_name
        self._streaming_message_id = None
        self._streaming_event_id = None
        self._streaming_version = 0
        self._tool_message_ids.clear()

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

        if self._active_job_chat_id is not None and self._active_job_message_id is not None:
            tool_state = self._bridge.get_tool_state()
            text = job_status_text(status, tool_state=tool_state)
            agent_count = len(self._bridge.list_agents() or [])
            # Throttle panel edits: max once per 3s, and only when content changes
            now = time.time()
            _last = getattr(self, '_last_panel_edit', 0.0)
            if text != self._last_panel_text and now - _last >= 3.0:
                try:
                    edit_message(
                        self,
                        self._active_job_chat_id,
                        self._active_job_message_id,
                        text,
                        reply_markup=job_panel(running=status.get("is_active", False), agent_count=agent_count),
                        parse_mode=None,
                    )
                    self._last_panel_text = text
                    self._last_panel_edit = now
                except Exception:
                    pass  # Panel edit is best-effort; don't crash drain loop

            if not status.get("is_active") and run_name:
                self._active_job_chat_id = None
                self._active_job_message_id = None
                self._active_job_run_name = None
                self._active_chat_agent_id = None
                self._active_chat_message_id = None

        # Live refresh: if a chat view is open, push latest agent timeline
        # Throttled: max 1 refresh per 3s, and only when signature changes
        if (self._active_chat_agent_id and self._active_chat_message_id
                and self._active_chat_chat_id and self._bridge.is_running):
            now = time.time()
            _last_chat_refresh = getattr(self, '_last_chat_refresh', 0.0)
            if now - _last_chat_refresh >= 3.0:
                try:
                    from .commands.jobs import _show_agent_chat
                    cursor = getattr(self, '_active_chat_cursor', '__latest__')
                    _show_agent_chat(
                        self,
                        self._active_chat_chat_id,
                        self._active_chat_message_id,
                        self._bridge,
                        self._active_chat_agent_id,
                        before_event_id=cursor,
                    )
                    self._last_chat_refresh = now
                except Exception:
                    pass

        # Check for waiting transition (from coordinator, not inferred)
        if self._active_job_chat_id is not None and self._bridge.is_running:
            waiting_ev = self._bridge.check_waiting_notification()
            if waiting_ev:
                self._process_scan_events([waiting_ev])

    @staticmethod
    def _sanitize_agent_content(content: str) -> str:
        """Strip base64, data URLs, internal paths, and raw tool output from agent messages."""
        import re
        content = re.sub(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]{80,}', '[imagen]', content)
        content = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=]{80,}', '[datos binarios]', content)
        content = re.sub(r'/(home|tmp|root|strix|sandbox)/[^ ]*/(scan-[a-f0-9]+)', r'[sandbox]/\2', content)
        content = re.sub(r'/sandbox/[^ ]{20,}', '[ruta interna]', content)
        return content

    def _process_scan_events(self, events: list[dict]) -> None:
        if not events or self._active_job_chat_id is None:
            return

        chat_id = self._active_job_chat_id
        current_run = self._active_job_run_name

        for ev in events:
            ev_type = ev.get("type", "")
            data = ev.get("data", {})
            ev_run = data.get("run_name", "")
            ev_id = ev.get("id", "")
            ev_version = int(ev.get("version", 0))

            if current_run and ev_run and ev_run != current_run:
                continue

            if ev_type == "chat":
                role = data.get("role", "")
                if role != "assistant":
                    continue
                streaming = data.get("metadata", {}).get("streaming", False)
                content = data.get("content", "")
                if not content:
                    continue
                raw = self._sanitize_agent_content(content)

                header = "STRIX:\n"
                full_text = header + raw

                if streaming:
                    self._update_chat_fragments(chat_id, ev_id, ev_version, full_text)
                else:
                    if ev_id in self._chat_fragments:
                        self._finalize_chat_fragments(chat_id, ev_id, ev_version, full_text)
                    else:
                        self._send_fragmented(chat_id, ev_id, ev_version, full_text)

            elif ev_type == "tool":
                status = data.get("status", "")
                tool_name = data.get("tool_name", "tool")
                args = data.get("args", {})
                result = data.get("result")
                call_id = data.get("call_id", "")

                text = render_tool_event(tool_name, status, args, result)

                if status == "running":
                    resp = send_message(self, chat_id, text, parse_mode=None)
                    if resp and call_id:
                        self._tool_message_ids[call_id] = resp.get("message_id")
                elif status in ("completed", "failed"):
                    if call_id and call_id in self._tool_message_ids:
                        edit_message(self, chat_id, self._tool_message_ids[call_id], text, parse_mode=None)
                    else:
                        send_message(self, chat_id, text, parse_mode=None)

            elif ev_type == "system":
                event_name = data.get("event", "")
                if event_name == "agent_waiting":
                    agent_name = data.get("content", "strix")
                    send_message(
                        self, chat_id,
                        f"STRIX esta esperando instrucciones.\n\n"
                        f"Agente: {agent_name}\n\n"
                        f"Escribe un mensaje para continuar.",
                        parse_mode=None,
                    )
                elif event_name == "scan_complete":
                    delta = self._bridge.to_status_dict().get("elapsed", "0s")
                    send_message(
                        self, chat_id,
                        f"Escaneo completado.\n"
                        f"Duracion: {delta}",
                        reply_markup=main_menu(),
                        parse_mode=None,
                    )
                elif event_name == "scan_error":
                    content = data.get("content", "")
                    send_message(self, chat_id, f"Error: {escape_md(content)}", reply_markup=main_menu())
                elif event_name == "scan_cancelled":
                    pass

    def _send_long_message(self, chat_id: int, text: str, sender) -> Optional[dict]:
        """Split text into valid Telegram messages (max 4096 chars each)."""
        MAX_LEN = self._MAX_MSG
        last_resp = None
        for i in range(0, len(text), MAX_LEN):
            frag = text[i:i + MAX_LEN]
            last_resp = sender(self, chat_id, frag, parse_mode=None)
        return last_resp

    def _split_into_fragments(self, text: str) -> list[str]:
        """Split text into Telegram-safe fragments of _MAX_MSG chars."""
        parts: list[str] = []
        for i in range(0, len(text), self._MAX_MSG):
            parts.append(text[i:i + self._MAX_MSG])
        return parts

    def _update_chat_fragments(
        self, chat_id: int, ev_id: str, ev_version: int, full_text: str
    ) -> None:
        """Update (or create) fragmented Telegram messages for a streaming event.

        Skips if version hasn't changed (same content). Splits full_text into
        fragments, edits existing message_ids, creates new ones as needed.
        Never truncates silently.
        """
        existing_version = self._chat_event_version.get(ev_id, -1)
        if ev_version <= existing_version:
            return

        fragments = self._split_into_fragments(full_text)
        existing_ids = self._chat_fragments.get(ev_id, [])
        existing_count = len(existing_ids)

        for i, frag in enumerate(fragments):
            if i < existing_count:
                try:
                    edit_message(self, chat_id, existing_ids[i], frag, parse_mode=None)
                except Exception:
                    pass
            else:
                resp = send_message(self, chat_id, frag, parse_mode=None)
                if resp and resp.get("message_id"):
                    existing_ids.append(resp["message_id"])

        self._chat_fragments[ev_id] = existing_ids
        self._chat_event_version[ev_id] = ev_version

    def _finalize_chat_fragments(
        self, chat_id: int, ev_id: str, ev_version: int, full_text: str
    ) -> None:
        """Finalize a streaming event: update all fragments, then clean up."""
        fragments = self._split_into_fragments(full_text)
        existing_ids = self._chat_fragments.get(ev_id, [])
        existing_count = len(existing_ids)

        for i, frag in enumerate(fragments):
            if i < existing_count:
                try:
                    edit_message(self, chat_id, existing_ids[i], frag, parse_mode=None)
                except Exception:
                    pass
            else:
                resp = send_message(self, chat_id, frag, parse_mode=None)
                if resp and resp.get("message_id"):
                    existing_ids.append(resp["message_id"])

        self._chat_fragments.pop(ev_id, None)
        self._chat_event_version.pop(ev_id, None)

    def _send_fragmented(
        self, chat_id: int, ev_id: str, ev_version: int, full_text: str
    ) -> None:
        """Send a non-streaming event as one or more fragments."""
        fragments = self._split_into_fragments(full_text)
        ids: list[int] = []
        for frag in fragments:
            resp = send_message(self, chat_id, frag, parse_mode=None)
            if resp and resp.get("message_id"):
                ids.append(resp["message_id"])
        self._chat_fragments[ev_id] = ids
        self._chat_event_version[ev_id] = ev_version

    @staticmethod
    def _sanitize_tool_args(args: dict) -> str:
        if not args:
            return ""
        safe_parts = []
        for k, v in args.items():
            if isinstance(v, str):
                v = v[:200]
                v = v.replace("\n", " ")
            safe_parts.append(f"{k}: {v}")
        return "\n".join(safe_parts[:5])

    @staticmethod
    def _sanitize_tool_result(result) -> str:
        if result is None:
            return ""
        if isinstance(result, str):
            return result[:500]
        if isinstance(result, (int, float)):
            return str(result)
        try:
            import json
            return json.dumps(result, ensure_ascii=False, default=str)[:500]
        except Exception:
            return str(result)[:500]

    def _drain_loop(self) -> None:
        _last_typing: float = 0.0
        while self._running:
            try:
                self._drain_update_queue()
            except Exception as e:
                logger.error(f"Drain error: {e}")
            now = time.time()
            # Keepalive: send typing indicator only while actively working (not waiting)
            if self._active_job_chat_id is not None and self._bridge.is_actively_working and now - _last_typing > 4.0:
                try:
                    from .telegram import send_chat_action
                    send_chat_action(self, self._active_job_chat_id)
                except Exception:
                    pass
                _last_typing = now
            time.sleep(0.5)

    def process_update(self, update: dict) -> None:
        if "message" in update:
            self._handle_command(update)
        elif "callback_query" in update:
            self._handle_callback(update)

    def run(self, poll_interval: float = 1.0) -> None:
        logger.info("STRIX Bot starting...")
        self._register_slash_commands()
        self._running = True

        self._drain_thread = threading.Thread(target=self._drain_loop, daemon=True)
        self._drain_thread.start()

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
        self._bridge.cleanup()
