from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Settings
from instructions import build_instruction
from models import JobContext, JobState, JobStatus
from runner import JobRunner
from security import AccessPolicy

log = logging.getLogger("strix_bot")

MODE_CALLBACK_CHAT = "mode_chat"
MODE_CALLBACK_SCAN = "mode_scan"
STOP_CALLBACK = "stop_job"


@dataclass
class PendingContext:
    text: str
    message_id: int
    has_attachments: bool = False


_GREETINGS = frozenset({
    "hola", "buenas", "buen dia", "buenos dias", "buenas tardes", "buenas noches",
    "que tal", "como estas", "como andas", "hello", "hi", "hey", "que onda",
})

_SCAN_INTENT = frozenset({
    "scan", "scanea", "escanea", "analiza", "analizá", "analizar",
    "escanear", "scanear", "scannear", "hacer scan", "hacer un scan",
    "analizalo", "escanearlo", "scanea esto", "escanea esto",
})


def _is_greeting(text: str) -> bool:
    cleaned = text.strip().lower().rstrip("!.,;?¡¿")
    if cleaned in _GREETINGS:
        return True
    if len(cleaned) > 25 or not cleaned:
        return False
    if "://" in cleaned:
        return False
    if re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", cleaned):
        return False
    if re.search(r"\b[a-z0-9]([a-z0-9-]*[a-z0-9])?\.[a-z]{2,}\b", cleaned):
        return False
    return cleaned in _GREETINGS or cleaned.split()[0] in _GREETINGS


def _safe_filename(filename: str) -> str:
    safe = filename.replace("/", "_").replace("\\", "_")
    safe = safe.lstrip("./-_ ")
    safe = safe.replace("..", "_")
    safe = safe.replace("\x00", "")
    return safe or "attachment"


def _mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💬 Chat", callback_data=MODE_CALLBACK_CHAT),
            InlineKeyboardButton("🔍 Scan", callback_data=MODE_CALLBACK_SCAN),
        ]
    ])


def _stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⏹ STOP", callback_data=STOP_CALLBACK)]]
    )


def _has_attachments(message) -> bool:
    if not message:
        return False
    return bool(
        message.document or message.photo or message.audio
        or message.video or message.voice
    )


def _has_real_target(text: str) -> bool:
    if not text.strip():
        return False
    if "://" in text:
        return True
    if re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text):
        return True
    if re.search(r"\b[a-z0-9]([a-z0-9-]*[a-z0-9])?\.[a-z]{2,}\b", text):
        return True
    if len(text.strip()) > 100:
        return True
    return False


class BotService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.policy = AccessPolicy(
            allowed_users=settings.allowed_users,
            allowed_chats=settings.allowed_chats,
        )
        self.runner = JobRunner(settings.work_root, settings.job_timeout_seconds)
        self.active_jobs: dict[int, JobState] = {}
        self._active_job_count = 0
        self._job_count_lock = asyncio.Lock()
        self.pending_context: dict[int, PendingContext] = {}
        self.last_target: dict[int, str] = {}

    async def on_error(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            log.error("Telegram polling error: %s", context.error, exc_info=context.error)
        except Exception:
            pass

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.effective_chat:
            return
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        if not self.policy.is_allowed(user_id, chat_id):
            log.warning("Unauthorized access attempt: user=%d chat=%d", user_id, chat_id)
            return

        text = update.message.text or update.message.caption or ""
        attachments_present = _has_attachments(update.message)

        if user_id in self.active_jobs:
            state = self.active_jobs[user_id]
            if self.runner.is_running(state.job_id):
                agent = self.runner.get_agent(state.job_id)
                if agent and agent.state.is_waiting_for_input():
                    if state.status in (JobStatus.COMPLETED, JobStatus.STOPPED, JobStatus.FAILED):
                        await update.message.reply_text("⏹️ El scan anterior ya finalizó. Mandá un nuevo target para empezar otro.")
                        return
                    if attachments_present:
                        await update.message.reply_text("⚠️ No puedo recibir archivos a mitad de un análisis. Respondé solo con texto.")
                        return
                    if not text.strip():
                        await update.message.reply_text("⚠️ Por favor, respondé con texto.")
                        return
                    agent.state.add_message("user", text)
                    agent.state.resume_from_waiting()
                    log.info("Injected user response into job %s", state.job_id)
                    await update.message.reply_text("✅ Respuesta inyectada a Strix.")
                    return
                else:
                    await update.message.reply_text("Ya hay un job en curso. Strix esta procesando, usa STOP si quieres cancelarlo.")
                    return
            else:
                self.active_jobs.pop(user_id, None)
                log.info("Cleaned up finished job for user %d, treating as new scan", user_id)

        if _is_greeting(text) and not attachments_present:
            await update.message.reply_text(
                "👋 ¡Hola! Soy **Strix**, tu asistente de ciberseguridad.\n\n"
                "Para realizar un análisis enviame un mensaje con:\n"
                "• Una **URL** o **IP** para escanear\n"
                "• Un **archivo** para analizar\n"
                "• Una **descripción** del target\n\n"
                "Ej: *\"Escaneá este sitio: https://ejemplo.com\"* o *\"analizá este APK\"*"
            )
            return

        if attachments_present:
            try:
                attachments = await self._download_attachments(update, context)
            except Exception:
                await update.message.reply_text("Error descargando archivos adjuntos.")
                return

            await self._trigger_scan(
                update=update,
                user_id=user_id, chat_id=chat_id,
                text=text,
                target_message_id=update.message.message_id,
                attachments=attachments,
            )
            return

        cleaned_text = text.strip().lower().rstrip("!.,;?¡¿")
        if cleaned_text in _SCAN_INTENT and not attachments_present:
            prev = self.pending_context.pop(user_id, None)
            scan_text = None
            if prev is not None and _has_real_target(prev.text):
                scan_text = prev.text
            else:
                scan_text = self.last_target.get(user_id)
            if scan_text:
                log.info("Scan intent detected for user %d — scanning last target", user_id)
                await self._trigger_scan(
                    update=update,
                    user_id=user_id,
                    chat_id=chat_id,
                    text=scan_text,
                    target_message_id=prev.message_id if prev else 0,
                )
                return

        if _has_real_target(text):
            self.last_target[user_id] = text
        self.pending_context[user_id] = PendingContext(
            text=text,
            message_id=update.message.message_id,
            has_attachments=attachments_present,
        )
        await update.message.reply_text(
            "¿Qué querés hacer con esto?",
            reply_markup=_mode_keyboard(),
        )

    async def on_mode_select(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        if not query.from_user or not query.message:
            return
        user_id = query.from_user.id
        chat_id = query.message.chat.id

        pending = self.pending_context.pop(user_id, None)

        if not pending or not pending.has_attachments and not _has_real_target(pending.text):
            saved = self.last_target.get(user_id)
            if saved:
                pending = PendingContext(text=saved, message_id=0, has_attachments=False)
            elif not pending:
                try:
                    await query.edit_message_text("⏳ Ese mensaje ya expiró. Mandá uno nuevo.")
                except Exception:
                    pass
                return
            else:
                try:
                    await query.edit_message_text("⏳ Ese contenido no es un target válido. Mandá URLs, IPs o un archivo.")
                except Exception:
                    pass
                return

        if query.data == MODE_CALLBACK_CHAT:
            await self._handle_chat_mode(query, user_id, chat_id, pending)
        elif query.data == MODE_CALLBACK_SCAN:
            await self._handle_scan_mode(query, user_id, chat_id, pending)

    async def _handle_chat_mode(
        self, query, user_id: int, chat_id: int, pending: PendingContext,
    ) -> None:
        try:
            await query.edit_message_text("💬 Modo chat activado.")
        except Exception:
            pass

    async def _trigger_scan(
        self,
        *,
        update: Update | None = None,
        query=None,
        user_id: int,
        chat_id: int,
        text: str,
        target_message_id: int,
        attachments: list[Path] | None = None,
    ) -> None:
        async with self._job_count_lock:
            if self._active_job_count >= self.settings.max_concurrent_jobs:
                msg = (
                    "⚠️ Strix está al máximo de trabajos concurrentes "
                    f"({self.settings.max_concurrent_jobs}). "
                    "Esperá a que termine uno e intentá de nuevo."
                )
                if query:
                    try:
                        await query.edit_message_text(msg)
                    except Exception:
                        pass
                elif update:
                    await update.message.reply_text(msg)
                return
            self._active_job_count += 1

        try:
            if query:
                chat = query.message.chat
                status_msg = await chat.send_message(
                    "🚀 Inicializando scan...",
                    reply_markup=_stop_keyboard(),
                )
            else:
                chat = update.message.chat
                status_msg = await update.message.reply_text(
                    "🚀 Inicializando scan...",
                    reply_markup=_stop_keyboard(),
                )
        except Exception:
            async with self._job_count_lock:
                self._active_job_count -= 1
            return

        try:
            if query:
                await query.message.delete()
        except Exception:
            pass

        await self._run_scan(
            chat=chat,
            user_id=user_id, chat_id=chat_id,
            text=text,
            message_id=target_message_id,
            attachments=attachments or [],
            status_msg=status_msg,
        )

    async def _run_scan(
        self,
        chat,
        user_id: int,
        chat_id: int,
        text: str,
        message_id: int,
        attachments: list[Path],
        status_msg,
    ) -> None:
        ctx = JobContext(
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            attachments=attachments,
        )
        state = self.runner.create_job(ctx)
        self.active_jobs[user_id] = state

        instruction = build_instruction(text, attachments)
        try:
            state.instruction_path.write_text(instruction)
        except Exception:
            self.active_jobs.pop(user_id, None)
            shutil.rmtree(state.work_dir, ignore_errors=True)
            async with self._job_count_lock:
                self._active_job_count -= 1
            await status_msg.edit_text("Error escribiendo instrucción del scan.")
            return

        log.info("Job %s created: user=%d text_len=%d attachments=%d text=%s",
                 state.job_id, user_id, len(text), len(attachments), text)

        try:
            await chat.send_action(ChatAction.TYPING)
        except Exception:
            pass

        async def on_new_message(job_state: JobState, text: str) -> None:
            try:
                for i in range(0, len(text), 4000):
                    await chat.send_message(text[i:i+4000], parse_mode=None)
                preview = text[:80].replace('\n', ' ')
                if len(text) > 80:
                    preview += '…'
                try:
                    await status_msg.edit_text(
                        f"🔍 Strix trabajando…\n{preview}",
                        reply_markup=_stop_keyboard()
                    )
                except Exception:
                    pass
            except Exception:
                log.exception("Error sending message for job %s", job_state.job_id)

        async def on_waiting(job_state: JobState) -> None:
            if getattr(job_state, '_reports_pre_sent', False):
                try:
                    await status_msg.edit_text(
                        "⏸ Strix procesando resultados…",
                        reply_markup=_stop_keyboard()
                    )
                except Exception:
                    pass
                return

            report_files = list(job_state.work_dir.rglob("penetration_test_report.md"))
            csv_files = list(job_state.work_dir.rglob("vulnerabilities.csv"))

            if report_files or csv_files:
                try:
                    for rf in report_files:
                        with open(rf, "rb") as f:
                            await chat.send_document(
                                document=f,
                                filename=rf.name,
                                caption=f"Reporte: {rf.parent.name if rf.parent != job_state.work_dir else 'general'}"
                            )
                    for cf in csv_files:
                        with open(cf, "rb") as f:
                            await chat.send_document(
                                document=f,
                                filename=cf.name,
                                caption="Vulnerabilidades encontradas."
                            )
                    if not report_files:
                        vuln_files = sorted(job_state.work_dir.rglob("vulnerabilities/vuln-*.md"))
                        for vf in vuln_files:
                            with open(vf, "rb") as f:
                                await chat.send_document(
                                    document=f,
                                    filename=f"{vf.parent.name}/{vf.name}",
                                    caption=f"Vulnerabilidad: {vf.stem}"
                                )
                except Exception:
                    log.exception("Error sending report for job %s", job_state.job_id)

                job_state._reports_pre_sent = True
                try:
                    await status_msg.edit_text(
                        "📤 Reportes enviados. Strix continúa…",
                        reply_markup=_stop_keyboard()
                    )
                except Exception:
                    pass
                return

            try:
                await status_msg.edit_text(
                    "⏸ Strix esperando instrucciones. Respondé acá.",
                    reply_markup=_stop_keyboard()
                )
            except Exception:
                pass
            try:
                await chat.send_message(
                    "🧠 *Strix está esperando instrucciones.* Responde enviando un mensaje normal aquí.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        async def on_complete(job_state: JobState) -> None:
            if getattr(job_state, '_on_complete_done', False):
                return

            result = (
                "COMPLETADO" if job_state.status == JobStatus.COMPLETED else "FALLIDO"
            )
            if job_state.status == JobStatus.STOPPED:
                result = "DETENIDO"
            log.info("Job %s finished: status=%s exit_code=%s",
                     job_state.job_id, job_state.status.value, job_state.exit_code)
            try:
                await status_msg.edit_text(f"🏁 Sesión {result}.")
            except Exception:
                log.exception("Error editing status message for job %s", job_state.job_id)

            if job_state.status == JobStatus.COMPLETED:
                try:
                    report_files = list(job_state.work_dir.rglob("penetration_test_report.md"))
                    csv_files = list(job_state.work_dir.rglob("vulnerabilities.csv"))

                    for rf in report_files:
                        with open(rf, "rb") as f:
                            await chat.send_document(
                                document=f,
                                filename=rf.name,
                                caption=f"Reporte: {rf.parent.name if rf.parent != job_state.work_dir else 'general'}"
                            )
                    for cf in csv_files:
                        with open(cf, "rb") as f:
                            await chat.send_document(
                                document=f,
                                filename=cf.name,
                                caption="Listado de vulnerabilidades en CSV."
                            )
                    if not report_files:
                        vuln_files = sorted(job_state.work_dir.rglob("vulnerabilities/vuln-*.md"))
                        for vf in vuln_files:
                            with open(vf, "rb") as f:
                                await chat.send_document(
                                    document=f,
                                    filename=f"{vf.parent.name}/{vf.name}",
                                    caption=f"Vulnerabilidad: {vf.stem}"
                                )
                except Exception:
                    log.exception("Error sending final files for job %s", job_state.job_id)

            job_state._on_complete_done = True
            self.active_jobs.pop(user_id, None)
            async with self._job_count_lock:
                self._active_job_count -= 1

        task = asyncio.create_task(self.runner.run_job(ctx, state, on_new_message, on_waiting, on_complete))
        self.runner._tasks[state.job_id] = task

    async def _handle_scan_mode(
        self, query, user_id: int, chat_id: int, pending: PendingContext,
    ) -> None:
        await self._trigger_scan(
            query=query,
            user_id=user_id, chat_id=chat_id,
            text=pending.text,
            target_message_id=pending.message_id,
        )

    async def on_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.from_user:
            return
        user_id = query.from_user.id
        if not self.policy.is_allowed(user_id, query.message.chat.id if query.message else 0):
            return
        state = self.active_jobs.get(user_id)
        if not state:
            try:
                await query.answer("No hay job activo")
            except Exception:
                pass
            return
        try:
            await query.answer("⏹ Deteniendo scan...")
        except Exception:
            pass
        log.info("Stop requested for job %s by user %d", state.job_id, user_id)
        await self.runner.stop_job(state)
        try:
            await query.edit_message_text("⏹ Scan detenido.")
        except Exception:
            pass

    async def _download_attachments(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
        target_dir: Path | None = None,
    ) -> list[Path]:
        attachments: list[Path] = []
        if not update.message:
            return attachments
        message = update.message
        files = []
        if message.document:
            files.append(message.document)
        if message.audio:
            files.append(message.audio)
        if message.video:
            files.append(message.video)
        if message.voice:
            files.append(message.voice)
        if message.photo:
            files.append(message.photo[-1])

        for doc in files:
            file = await doc.get_file()
            filename = _safe_filename(doc.file_name or f"file_{doc.file_id}")
            if target_dir:
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / filename
            else:
                target = self.settings.work_root / filename
            await file.download_to_drive(custom_path=target)
            attachments.append(target)
        return attachments


def build_app(settings: Settings) -> Application:
    service = BotService(settings)
    app = Application.builder().token(settings.token).build()
    app.add_handler(CallbackQueryHandler(service.on_mode_select, pattern="^mode_(chat|scan)$"))
    app.add_handler(CallbackQueryHandler(service.on_stop, pattern=f"^{STOP_CALLBACK}$"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, service.on_message))
    app.add_error_handler(service.on_error)
    return app
