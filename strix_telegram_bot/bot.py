from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .telegram import get_updates, send_message, send_document, edit_message, delete_message, answer_callback
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

_URL_RE = re.compile(r"https?://[^\s,>\]\)]+")
_GITHUB_RE = re.compile(r"github\.com[:/][^\s,>\]\)]+")


class StrixBot:
    _MAX_MSG = 4000

    def __init__(self) -> None:
        self._updates_offset: Optional[int] = self._load_offset()
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

        self._final_reports_delivered: set[str] = set()
        self._terminal_notified: set[str] = set()
        self._report_delivered: set[str] = set()
        self._report_pending: set[str] = set()
        self._report_pending_until: dict[str, float] = {}

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

    def _load_offset(self) -> Optional[int]:
        """Load the persisted Telegram updates offset from .bot-state/telegram_offset.json."""
        try:
            from .config import settings
            state_dir = settings.strix_runs_dir / ".bot-state"
            offset_file = state_dir / "telegram_offset.json"
            if offset_file.exists():
                import json as _json
                data = _json.loads(offset_file.read_text())
                offset = data.get("offset")
                if isinstance(offset, int):
                    return offset
        except Exception:
            pass
        return None

    def _save_offset(self) -> None:
        """Persist the current Telegram updates offset atomically with fsync."""
        try:
            if self._updates_offset is not None:
                import json as _json
                from .config import settings
                state_dir = settings.strix_runs_dir / ".bot-state"
                state_dir.mkdir(parents=True, exist_ok=True)
                offset_file = state_dir / "telegram_offset.json"
                tmp_file = state_dir / ".telegram_offset.json.tmp"
                payload = _json.dumps({"offset": self._updates_offset})
                with open(tmp_file, "w") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(str(tmp_file), str(offset_file))
        except Exception:
            pass

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
            send_message(
                self,
                chat_id,
                "El análisis está siendo ejecutado automáticamente por Strix.\n"
                "El chat es de solo lectura hasta que termine este run.",
                parse_mode=None,
                disable_web_page_preview=True,
            )
            return

        pm = get_panel_manager(chat_id)

        if pm.current == MenuState.WAITING_FOR_TARGETS:
            self._parse_and_launch(chat_id, msg, text)
            return

        targets, instruction = self._extract_targets_from_message(msg, text)

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

    def _extract_targets_from_message(self, msg: dict, text: str) -> tuple[list[str], str]:
        """Extract targets from a Telegram message.

        Processing order:
        1. Telegram entities (text_link, url)
        2. Markdown links [text](url)
        3. Raw URLs
        4. GitHub repos
        5. Domains, IPs, paths
        """
        # Phase 1: Telegram entities
        entity_urls, remaining = self._extract_entities(msg, text)

        # Phase 2: Markdown links
        md_urls, remaining = self._extract_markdown_links(remaining)

        # Phase 3-5: Standard extraction on remaining text
        raw_targets, instruction = self._extract_targets(remaining)

        all_targets = list(dict.fromkeys(entity_urls + md_urls + raw_targets))
        return all_targets, instruction

    @staticmethod
    def _validate_url(url: str) -> str:
        """Validate a URL using urllib.parse.urlsplit.

        Returns the cleaned URL if valid, empty string if rejected.
        """
        from urllib.parse import urlparse, urlsplit

        url = url.rstrip(".,;:!?)]}>")

        # Reject residual markdown or control characters
        if re.search(r"[\[\]\(\)]", url):
            return ""
        if re.search(r"[\x00-\x1f]", url):
            return ""

        if "://" in url:
            if url.count("://") > 1:
                return ""
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https"):
                    return ""
                if not parsed.hostname:
                    return ""
                if " " in parsed.hostname or re.search(r"[\x00-\x1f]", parsed.hostname):
                    return ""
                if parsed.port is not None and not (1 <= parsed.port <= 65535):
                    return ""
                if parsed.username or parsed.password:
                    # Credentials embedded — reject unless the project policy allows
                    # For general use, reject.
                    return ""
            except Exception:
                return ""
            return url

        # Bare github.com/user/repo — normalize
        if re.match(r"github\.com[:/]", url):
            return f"https://{url}"

        # Bare domain, IP, or path — leave as-is for later routing
        return url

    def _extract_entities(self, msg: dict, text: str) -> tuple[list[str], str]:
        """Extract targets from Telegram message entities (text_link, url).

        Returns (extracted_urls, remaining_text).
        """
        entities = msg.get("entities") or msg.get("caption_entities") or []
        if not entities:
            return [], text

        extracted: list[str] = []
        # Process in reverse offset order to avoid index shifting
        sorted_ents = sorted(entities, key=lambda e: e.get("offset", 0), reverse=True)

        chars = list(text)
        for ent in sorted_ents:
            etype = ent.get("type", "")
            offset = ent.get("offset", 0)
            length = ent.get("length", 0)
            if etype == "text_link":
                url = ent.get("url", "")
                cleaned = self._validate_url(url)
                if cleaned:
                    extracted.append(cleaned)
                # Blank out the entity range
                for i in range(offset, min(offset + length, len(chars))):
                    chars[i] = " "
            elif etype == "url" and length > 0:
                raw = "".join(chars[offset:offset + length])
                cleaned = self._validate_url(raw)
                if cleaned:
                    extracted.append(cleaned)
                for i in range(offset, min(offset + length, len(chars))):
                    chars[i] = " "

        remaining = "".join(chars).strip()
        return extracted, remaining

    @staticmethod
    def _extract_markdown_links(text: str) -> tuple[list[str], str]:
        """Extract markdown links [text](url) from text.

        Returns (urls, remaining_text).
        """
        _MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
        urls: list[str] = []
        remaining = text

        for match in _MD_LINK_RE.finditer(text):
            url = match.group(2).strip()
            cleaned = StrixBot._validate_url(url)
            if cleaned:
                urls.append(cleaned)

        remaining = _MD_LINK_RE.sub("", text).strip()
        return urls, remaining

    def _extract_targets(self, text: str) -> tuple[list[str], str]:
        raw_urls = _URL_RE.findall(text)
        urls = [u for u in (self._validate_url(u) for u in raw_urls) if u]
        remaining = _URL_RE.sub("", text).strip()
        raw_repos = _GITHUB_RE.findall(remaining)
        repos = [r for r in (self._validate_url(r) for r in raw_repos) if r]
        remaining = _GITHUB_RE.sub("", remaining).strip()

        candidates = [
            t.strip().rstrip(".,;:!?)]}>")
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

    def _parse_and_launch(self, chat_id: int, msg: dict, text: str) -> None:
        targets, instruction = self._extract_targets_from_message(msg, text)
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

        exact_instruction = instruction.strip()

        ok, start_msg = self._bridge.start_scan(
            targets=prepared_targets,
            scan_mode=scan_mode,
            instruction=exact_instruction,
            scope_mode="auto",
            local_sources=local_sources,
        )

        if not ok:
            send_message(self, chat_id, f"Error: {start_msg}", reply_markup=back_to_menu())
            return

        run_name = self._bridge.run_name or f"scan-{time.time():.0f}"

        try:
            mode_enum = ScanMode(scan_mode)
        except ValueError:
            mode_enum = ScanMode.DEEP
        job = JobState(
            run_name=run_name,
            target=targets,
            mode=mode_enum,
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
        self._chat_fragments.clear()
        self._chat_event_version.clear()
        self._tool_message_ids.clear()
        self._final_reports_delivered.clear()
        self._terminal_notified.clear()
        self._report_delivered.clear()
        self._report_pending.clear()
        self._report_pending_until.clear()

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

                chat_id = self._active_job_chat_id
                if (
                    run_name
                    and chat_id is not None
                    and run_name not in self._report_delivered
                    and run_name not in self._terminal_notified
                ):
                    phase_str = status.get("phase", "running")

                    if phase_str == "completed":
                        result = self._deliver_final_report(chat_id, run_name)
                        if result == "delivered":
                            self._report_delivered.add(run_name)
                            send_message(
                                self, chat_id,
                                "Informe completo enviado como archivo Markdown.",
                                reply_markup=main_menu(),
                                parse_mode=None,
                            )
                        elif result == "not_completed":
                            now = time.time()
                            pending_until = self._report_pending_until.get(run_name, 0.0)
                            if pending_until == 0.0:
                                self._report_pending_until[run_name] = now + 60.0
                                self._report_pending.add(run_name)
                            elif now < pending_until:
                                return
                            else:
                                send_message(
                                    self, chat_id,
                                    "Strix emitió una señal de finalización, "
                                    "pero el run todavía no está marcado como "
                                    "completed. No se envió ningún informe.",
                                    reply_markup=main_menu(),
                                    parse_mode=None,
                                )
                                self._terminal_notified.add(run_name)
                                self._report_pending.discard(run_name)
                                self._report_pending_until.pop(run_name, None)
                        elif result == "missing":
                            now = time.time()
                            pending_until = self._report_pending_until.get(run_name, 0.0)
                            if pending_until == 0.0:
                                self._report_pending_until[run_name] = now + 60.0
                                self._report_pending.add(run_name)
                            elif now < pending_until:
                                return
                            else:
                                send_message(
                                    self, chat_id,
                                    "Escaneo completado.\n"
                                    "El informe final no fue generado por Strix.",
                                    reply_markup=main_menu(),
                                    parse_mode=None,
                                )
                                self._terminal_notified.add(run_name)
                                self._report_pending.discard(run_name)
                                self._report_pending_until.pop(run_name, None)
                        elif result == "send_transient":
                            now = time.time()
                            pending_until = self._report_pending_until.get(run_name, 0.0)
                            if pending_until == 0.0:
                                self._report_pending_until[run_name] = now + 60.0
                                self._report_pending.add(run_name)
                            elif now >= pending_until:
                                send_message(
                                    self, chat_id,
                                    "Escaneo completado.\n"
                                    "El informe fue generado pero no pudo enviarse tras varios intentos. "
                                    "Disponible en Reportes.",
                                    reply_markup=main_menu(),
                                    parse_mode=None,
                                )
                                self._terminal_notified.add(run_name)
                                self._report_pending.discard(run_name)
                                self._report_pending_until.pop(run_name, None)
                        elif result == "send_permanent":
                            send_message(
                                self, chat_id,
                                "Escaneo completado.\n"
                                "El informe fue generado pero no pudo enviarse (error permanente). "
                                "Disponible en Reportes.",
                                reply_markup=main_menu(),
                                parse_mode=None,
                            )
                            self._terminal_notified.add(run_name)
                    elif phase_str in ("failed", "stopped"):
                        send_message(
                            self, chat_id,
                            "El análisis terminó con error antes de generar el informe oficial. "
                            "No se produjo ningún archivo Markdown para este run.",
                            reply_markup=main_menu(),
                            parse_mode=None,
                        )
                        self._terminal_notified.add(run_name)
                    else:
                        if run_name not in self._report_pending:
                            self._report_pending.add(run_name)
                            self._report_pending_until[run_name] = time.time() + 60.0

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
                        disable_web_page_preview=True,
                    )
                    self._last_panel_text = text
                    self._last_panel_edit = now
                except Exception:
                    pass  # Panel edit is best-effort; don't crash drain loop

            if not status.get("is_active") and run_name:
                if run_name in self._report_pending:
                    pass  # Keep chat_id alive for retry
                else:
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
                # Section 8.3: Only root agent messages in main chat
                agent_id = ev.get("agent_id", "")
                root_id = self._bridge.root_agent_id
                if root_id and agent_id and agent_id != root_id:
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
                # Tool events are only visible in the menu tree/agent views,
                # not in the main chat.  Skip silently.
                pass

            elif ev_type == "system":
                event_name = data.get("event", "")
                if event_name == "agent_waiting":
                    # In non_interactive mode, check_waiting_notification
                    # already returns None.  In interactive mode, the status
                    # panel shows "esperando" — no chat bubble needed.
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
                    edit_message(self, chat_id, existing_ids[i], frag, parse_mode=None,
                                 disable_web_page_preview=True)
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
        """Finalize a streaming event: update needed fragments, delete extras, clean up."""
        fragments = self._split_into_fragments(full_text)
        existing_ids = self._chat_fragments.get(ev_id, [])
        existing_count = len(existing_ids)

        # Update or create the fragments that the final text needs
        kept_ids: list[int] = []
        for i, frag in enumerate(fragments):
            if i < existing_count:
                try:
                    edit_message(self, chat_id, existing_ids[i], frag, parse_mode=None,
                                 disable_web_page_preview=True)
                except Exception:
                    pass
                kept_ids.append(existing_ids[i])
            else:
                resp = send_message(self, chat_id, frag, parse_mode=None)
                if resp and resp.get("message_id"):
                    kept_ids.append(resp["message_id"])

        # Delete surplus fragments (those beyond what the final text needs)
        for stale_id in existing_ids[len(fragments):]:
            try:
                delete_message(self, chat_id, stale_id)
            except Exception:
                try:
                    edit_message(self, chat_id, stale_id, "[mensaje eliminado]", parse_mode=None)
                except Exception:
                    pass

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

    def _deliver_final_report(self, chat_id: int, run_name: str) -> str:
        """Delegate to deliver_report_document and track delivered runs."""
        from .strix.report_delivery import deliver_report_document

        result = deliver_report_document(self, chat_id, run_name)
        if result == "delivered":
            self._final_reports_delivered.add(run_name)
        return result

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
                    next_offset = upd["update_id"] + 1
                    self.process_update(upd)
                    self._updates_offset = next_offset
                    self._save_offset()
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
