"""STRIX Control Center — main bot engine (raw HTTP polling)."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .config import settings
from .telegram import get_updates, send_message, edit_message, answer_callback
from .security import is_authorized
from .models import FocusPreset, JobPhase, MenuState, ProfileType, ScanMode, ScopeMode
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
from .strix.runtime_bridge import StrixRuntimeBridge

logger = logging.getLogger("strix_bot")

_TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".json", ".csv", ".xml", ".yaml", ".yml",
    ".py", ".js", ".ts", ".go", ".rs", ".java", ".kt",
    ".html", ".htm", ".css", ".log", ".cfg", ".ini", ".conf",
    ".sh", ".bash", ".zsh", ".env", ".toml",
})


def _is_likely_binary(path: Path) -> bool:
    if path.suffix.lower() not in _TEXT_EXTENSIONS:
        return True
    try:
        head = path.read_bytes(8192)
        return b"\x00" in head
    except OSError:
        return True


class StrixBot:
    def __init__(self) -> None:
        self._updates_offset: Optional[int] = None
        self._running = False
        self._job_store = JobStore()
        self._bridge = StrixRuntimeBridge()
        self._chat_mode: dict[int, bool] = {}
        self._last_broadcast: dict[str, float] = {}
        self._active_job_chat_id: Optional[int] = None
        self._active_job_message_id: Optional[int] = None

        self._pending_attachment: Optional[str] = None

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

        pm = get_panel_manager(chat_id)
        status = self._bridge.to_status_dict()
        is_active = self._bridge.is_running
        awaiting_input = status.get("awaiting_input", False)

        if pm.current == MenuState.NEW_PENTEST_TARGET:
            self._handle_wizard_target(chat_id, text, msg)
        elif pm.current == MenuState.NEW_PENTEST_DIFF_BASE:
            pm._selected_diff_base = text
            pm.push(MenuState.NEW_PENTEST_FOCUS)
            from .ui.keyboards import focus_presets
            send_message(
                self, chat_id,
                f"Diff base: {text}\n\nInstrucción / Enfoque:",
                reply_markup=focus_presets(),
            )
        elif pm.current == MenuState.NEW_PENTEST_INSTRUCTION:
            from .models import get_focus_instruction
            pm._selected_instruction = get_focus_instruction(FocusPreset.CUSTOM, text)
            pm.push(MenuState.NEW_PENTEST_DEPTH)
            send_message(
                self, chat_id,
                "Instrucción guardada.\n\nSeleccioná modo de escaneo:",
                reply_markup=depth_selector(),
            )
        elif awaiting_input:
            self._bridge.send_message_to_agent(text)
            send_message(self, chat_id, "Respuesta enviada a STRIX.")
        elif is_active:
            self._bridge.send_message_to_agent(text)
            send_message(self, chat_id, "Mensaje enviado a STRIX.")
        elif self._chat_mode.get(chat_id):
            if is_active:
                self._bridge.send_message_to_agent(text)
            send_message(
                self, chat_id,
                "Mensaje enviado a STRIX.\n"
                "Usá /start para salir del modo chat.",
            )
        else:
            send_message(
                self, chat_id,
                "No hay un trabajo activo. Usá /start para comenzar.",
                reply_markup=main_menu(),
            )

    def _handle_wizard_target(self, chat_id: int, text: str, msg: dict) -> None:
        pm = get_panel_manager(chat_id)
        targets = [t.strip() for t in text.replace("\n", ",").split(",") if t.strip()]

        if not targets:
            send_message(self, chat_id, "Enviá un objetivo válido.")
            return

        from .safety.attachment_policy import sanitize_target
        for t in targets:
            ok, err = sanitize_target(t)
            if not ok:
                send_message(self, chat_id, f"Objetivo inválido {t}: {err}")
                return

        pm._selected_targets = targets
        pm.push(MenuState.NEW_PENTEST_PROFILE)
        from .ui.keyboards import profile_selector

        send_message(
            self, chat_id,
            f"Objetivo: {', '.join(targets)}\nSeleccioná perfil:",
            reply_markup=profile_selector(),
        )

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

    def _callback_target(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)

        if len(parts) < 2:
            return

        pm = get_panel_manager(chat_id)
        target_type = parts[1]
        pm.push(MenuState.NEW_PENTEST_TARGET)

        if target_type == "attachment":
            pm.push(MenuState.NEW_PENTEST_ATTACHMENT)
            edit_message(
                bot, chat_id, msg_id,
                "Subí el archivo como documento en este chat.\n"
                "El bot lo guardará y lo pasará a STRIX.",
                reply_markup=back_to_menu(),
            )
            return

        prompt = {
            "url": "Enviá la URL o dominio:",
            "github": "Enviá la URL del repo GitHub:",
            "local": "Enviá la ruta local:",
            "multi": "Enviá los objetivos (separados por coma o línea):",
        }.get(target_type, "Enviá el objetivo:")

        edit_message(bot, chat_id, msg_id, prompt, reply_markup=back_to_menu())

    def _callback_depth(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)

        if len(parts) < 2:
            return

        pm = get_panel_manager(chat_id)
        action = parts[1]

        if action in ("quick", "standard", "deep"):
            pm._selected_depth = ScanMode(action)
            names = {"quick": "RÁPIDO", "standard": "ESTÁNDAR", "deep": "PROFUNDO"}
            edit_message(
                bot, chat_id, msg_id,
                f"Modo: {names.get(action, action.upper())}\n{pm.wizard_summary()}",
                reply_markup=depth_selector(),
            )

        elif action == "confirm":
            if pm.wizard_complete:
                self._launch_scan(bot, chat_id, msg_id)
            else:
                edit_message(
                    bot, chat_id, msg_id,
                    "Seleccioná un objetivo primero.",
                    reply_markup=back_to_menu(),
                )

    def _handle_document(self, update: dict) -> None:
        msg = update.get("message", {})
        chat_id = msg.get("chat", {}).get("id", 0)

        from .telegram import send_chat_action
        send_chat_action(self, chat_id)

        doc = msg.get("document")
        if not doc and msg.get("photo"):
            doc = msg.get("photo", [None])[-1]
        if not doc:
            send_message(self, chat_id, "No se pudo leer el archivo.")
            return

        from .telegram import get_file
        from pathlib import Path
        from .strix.evidence_vault import EvidenceVault
        from .ui.keyboards import attachment_offer

        file_id = doc.get("file_id", "")
        file_name = doc.get("file_name", "upload.bin") if "file_name" in doc else "photo.jpg"

        file_bytes = get_file(self, file_id)
        if file_bytes is None:
            send_message(self, chat_id, "Error al descargar el archivo.")
            return

        pm = get_panel_manager(chat_id)
        run_name = "upload"
        active = self._job_store.list_active()
        if active:
            run_name = active[0].run_name

        vault = EvidenceVault(run_name)
        artifact = vault.store_bytes(file_bytes, file_name, subdir="files", sensitive=False)
        if artifact is None:
            send_message(self, chat_id, "Error al guardar el archivo.")
            return

        abs_path = Path(artifact["absolute_path"])

        if pm.current == MenuState.NEW_PENTEST_ATTACHMENT:
            pm._selected_targets = [str(abs_path)]
            pm.push(MenuState.NEW_PENTEST_DEPTH)
            send_message(
                self, chat_id,
                f"Archivo listo: {abs_path.name}\nSeleccioná modo de escaneo:",
                reply_markup=self._depth_selector(),
            )
        elif not active:
            self._pending_attachment = str(abs_path)
            send_message(
                self, chat_id,
                f"Recibí el archivo: {file_name}\n"
                "¿Querés iniciar un escaneo con él?",
                reply_markup=attachment_offer(),
            )
        else:
            send_message(
                self, chat_id,
                f"Archivo guardado: {file_name}\n"
                f"SHA256: {artifact['sha256'][:16]}...",
            )

    def _callback_profile(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)

        if len(parts) < 2:
            return

        pm = get_panel_manager(chat_id)
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
            f"Perfil: {pm._selected_profile.value}\n\nConfigurá el alcance:",
            reply_markup=scope_mode_selector(),
        )

    def _callback_scope_mode(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)

        if len(parts) < 2:
            return

        pm = get_panel_manager(chat_id)
        action = parts[1]

        if action in ("auto", "diff", "full"):
            pm._selected_scope_mode = ScopeMode(action)
            names = {"auto": "AUTO", "diff": "DIFF", "full": "FULL"}
            from .ui.keyboards import scope_mode_selector
            edit_message(
                bot, chat_id, msg_id,
                f"Alcance: {names.get(action, action.upper())}\n"
                "Opcional: configurá una diff base o continuá.",
                reply_markup=scope_mode_selector(),
            )
        elif action == "diff_base":
            pm.push(MenuState.NEW_PENTEST_DIFF_BASE)
            edit_message(
                bot, chat_id, msg_id,
                "Enviá una diff base (ej: 'origin/main' o un commit hash):",
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

        if len(parts) < 2:
            return

        pm = get_panel_manager(chat_id)
        action = parts[1]

        if action == "skip":
            pm.push(MenuState.NEW_PENTEST_DEPTH)
            edit_message(
                bot, chat_id, msg_id,
                f"Objetivo: {', '.join(pm._selected_targets)}\nSeleccioná modo de escaneo:",
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
                "Enviá tu instrucción personalizada:",
                reply_markup=back_to_menu(),
            )
        else:
            pm._selected_instruction = get_focus_instruction(preset)
            pm.push(MenuState.NEW_PENTEST_DEPTH)
            edit_message(
                bot, chat_id, msg_id,
                f"Enfoque: {preset.value}\nInstrucción lista.\n\nSeleccioná modo de escaneo:",
                reply_markup=depth_selector(),
            )

    def _callback_evidence(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)

        if len(parts) < 2:
            return

        from .strix.evidence_vault import EvidenceVault
        from .ui.keyboards import evidence_detail_menu, evidence_list_menu
        from .ui.messages import evidence_text

        store = self._job_store
        jobs = [j for j in store.list_recent(5) if j.is_terminal and j.run_name != "pending"]
        if not jobs:
            edit_message(bot, chat_id, msg_id, "No hay trabajos completados.", reply_markup=back_to_menu())
            return

        vault = EvidenceVault(jobs[0].run_name)
        action = parts[1]

        if action == "list":
            artifacts = vault.list_evidence()
            if not artifacts:
                edit_message(bot, chat_id, msg_id, "No hay evidencia.", reply_markup=back_to_menu())
                return
            text = evidence_text(vault.get_manifest())
            edit_message(bot, chat_id, msg_id, text, reply_markup=evidence_list_menu(artifacts))

        elif action.startswith("preview:"):
            artifact_id = action.split(":", 1)[1]
            preview = vault.redacted_preview(artifact_id)
            if preview:
                send_message(bot, chat_id, f"Vista previa (censurada):\n\n{preview[:3500]}")
                edit_message(bot, chat_id, msg_id, "Vista previa enviada.", reply_markup=evidence_detail_menu(artifact_id))
            else:
                edit_message(bot, chat_id, msg_id, "No se puede previsualizar.", reply_markup=back_to_menu())

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
                        if _is_likely_binary(full_path):
                            send_message(
                                bot, chat_id,
                                f"El artefacto es binario ({full_path.suffix}).\n"
                                f"Ruta: {full_path}\n"
                                f"Tamaño: {full_path.stat().st_size / 1024:.1f} KB\n"
                                f"SHA256: {match.get('sha256', '?')[:16]}..."
                            )
                        else:
                            content = full_path.read_text(encoding="utf-8", errors="replace")
                            send_message(bot, chat_id, f"Artefacto CRUDO:\n\n{content[:3500]}")
                        edit_message(bot, chat_id, msg_id, "CRUDO enviado.", reply_markup=evidence_detail_menu(artifact_id))
                        return
            edit_message(bot, chat_id, msg_id, "Artefacto no encontrado.", reply_markup=back_to_menu())

        elif action.startswith("redacted:"):
            artifact_id = action.split(":", 1)[1]
            preview = vault.redacted_preview(artifact_id)
            if preview:
                send_message(bot, chat_id, f"Artefacto censurado:\n\n{preview[:3500]}")
                edit_message(bot, chat_id, msg_id, "Censurado enviado.", reply_markup=evidence_detail_menu(artifact_id))
            else:
                edit_message(bot, chat_id, msg_id, "No se puede censurar.", reply_markup=back_to_menu())

        elif len(parts) >= 2:
            artifact_id = parts[1]
            edit_message(bot, chat_id, msg_id, "Detalle de evidencia:", reply_markup=evidence_detail_menu(artifact_id))

    def _callback_tools(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)

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
                "Trabajo no encontrado.", reply_markup=back_to_menu(),
            )

    def _callback_caido(self, bot: Any, update: dict) -> None:
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
        msg_id = cb.get("message", {}).get("message_id", "")
        parts = parse_callback(data)

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
                status = "No hay trabajo activo para detectar Caido."
            edit_message(bot, chat_id, msg_id, status, reply_markup=caido_main_menu())

        elif action == "artifacts":
            if run_name:
                artifacts = cp.collect_caido_artifacts(run_name)
                if artifacts:
                    lines = ["Artefactos Caido:"]
                    for a in artifacts:
                        lines.append(f"  {a['name']} ({a['size']/1024:.1f} KB)")
                    text = "\n".join(lines)
                else:
                    text = "No se encontraron artefactos Caido."
            else:
                text = "No hay trabajo activo."
            edit_message(bot, chat_id, msg_id, text, reply_markup=back_to_menu())

        elif action == "instructions":
            text = (
                "Caido es un proxy web para inspección manual de tráfico.\n\n"
                "STRIX expone Caido al ejecutar escaneos.\n"
                "Usá la URL de arriba para:\n"
                "  - Inspeccionar requests/responses HTTP\n"
                "  - Replay y modificar requests\n"
                "  - Explorar el sitemap\n"
                "  - Testear manualmente junto al agente\n\n"
                "Caido corre solo en localhost."
            )
            edit_message(bot, chat_id, msg_id, text, reply_markup=caido_main_menu())

    def _drain_update_queue(self) -> None:
        if self._active_job_chat_id is None or self._active_job_message_id is None:
            self._bridge.poll_events()
            return

        status = self._bridge.to_status_dict()
        if not status.get("run_name") and not self._bridge.is_running:
            return

        text = job_status_text(status)
        edit_message(
            self,
            self._active_job_chat_id,
            self._active_job_message_id,
            text,
            reply_markup=job_panel(running=status.get("is_active", False)),
        )

        if not status.get("is_active") and status.get("run_name"):
            from .ui.messages import job_completed_text
            phase = status.get("phase", "completed")
            delta = status.get("elapsed", "0s")
            final = (
                f"Escaneo finalizado.\n"
                f"Estado: {phase}\n"
                f"Duración: {delta}"
            )
            chat_id = self._active_job_chat_id
            self._active_job_chat_id = None
            self._active_job_message_id = None
            send_message(self, chat_id, final, reply_markup=back_to_menu())

    def _launch_scan(
        self,
        bot: Any,
        chat_id: int,
        msg_id: int,
        targets: Optional[list[str]] = None,
        mode: Optional[ScanMode] = None,
    ) -> None:
        pm = get_panel_manager(chat_id)

        from .telegram import send_chat_action
        send_chat_action(bot, chat_id)

        if targets is None:
            targets = pm._selected_targets
        if mode is None:
            mode = pm._selected_depth

        if not targets:
            edit_message(bot, chat_id, msg_id, "No se especificó objetivo.", reply_markup=back_to_menu())
            return

        ok, start_msg = self._bridge.start_scan(
            targets=targets,
            scan_mode=mode.value,
            instruction=pm._selected_instruction,
            scope_mode=pm._selected_scope_mode.value,
            non_interactive=(pm._selected_profile == ProfileType.HEADLESS),
            diff_base=pm._selected_diff_base or None,
        )

        if ok:
            self._active_job_chat_id = chat_id
            self._active_job_message_id = msg_id
            pm.reset_wizard()
            status = self._bridge.to_status_dict()
            text = job_status_text(status) if status.get("run_name") else "Escaneo iniciado"
            edit_message(bot, chat_id, msg_id, text, reply_markup=job_panel(running=True))
        else:
            edit_message(bot, chat_id, msg_id, f"Error: {start_msg}", reply_markup=back_to_menu())

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
