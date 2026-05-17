from __future__ import annotations

import asyncio
import logging
import shutil
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

from .config import Settings
from .instructions import build_instruction
from .models import JobContext, JobState, JobStatus
from .runner import JobRunner
from .security import AccessPolicy

log = logging.getLogger("strix_bot")

STOP_CALLBACK = "stop_job"


def _safe_filename(filename: str) -> str:
    safe = filename.replace("/", "_").replace("\\", "_")
    safe = safe.lstrip("./-_ ")
    safe = safe.replace("..", "_")
    safe = safe.replace("\x00", "")
    return safe or "attachment"


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

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.effective_chat:
            return
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        if not self.policy.is_allowed(user_id, chat_id):
            log.warning("Unauthorized access attempt: user=%d chat=%d", user_id, chat_id)
            return

        text = update.message.text or update.message.caption or ""

        if user_id in self.active_jobs:
            state = self.active_jobs[user_id]
            if self.runner.is_running(state.job_id):
                agent = self.runner.get_agent(state.job_id)
                if agent and agent.state.is_waiting_for_input():
                    if getattr(state, '_reports_pre_sent', False):
                        await update.message.reply_text("⏹️ El scan anterior ya finalizó. Mandá un nuevo target para empezar otro.")
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
                # Job anterior ya terminó — limpiar y tratar como nuevo scan
                self.active_jobs.pop(user_id, None)
                log.info("Cleaned up finished job for user %d, treating as new scan", user_id)

        # Rate limiting: check concurrent job capacity
        async with self._job_count_lock:
            if self._active_job_count >= self.settings.max_concurrent_jobs:
                await update.message.reply_text(
                    "⚠️ Strix está al máximo de trabajos concurrentes "
                    f"({self.settings.max_concurrent_jobs}). "
                    "Esperá a que termine uno e intentá de nuevo."
                )
                return
            self._active_job_count += 1

        ctx = JobContext(
            user_id=user_id,
            chat_id=chat_id,
            message_id=update.message.message_id,
            text=text,
            attachments=[],
        )
        state = self.runner.create_job(ctx)
        self.active_jobs[user_id] = state

        try:
            attachments = await self._download_attachments(update, context, state.work_dir / "attachments")
        except Exception:
            self.active_jobs.pop(user_id, None)
            shutil.rmtree(state.work_dir, ignore_errors=True)
            async with self._job_count_lock:
                self._active_job_count -= 1
            await update.message.reply_text("Error descargando archivos adjuntos.")
            return

        ctx.attachments = attachments
        instruction = build_instruction(text, attachments)
        state.instruction_path.write_text(instruction)

        log.info("Job %s created: user=%d text_len=%d attachments=%d text=%s",
                 state.job_id, user_id, len(text), len(attachments), ctx.text)

        await update.message.chat.send_action(ChatAction.TYPING)
        stop_keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("STOP", callback_data=STOP_CALLBACK)]]
        )
        status_msg = await update.message.reply_text("🚀 Iniciando sesión de Strix...", reply_markup=stop_keyboard)

        async def on_new_message(job_state: JobState, text: str) -> None:
            try:
                for i in range(0, len(text), 4000):
                    await update.message.chat.send_message(text[i:i+4000])
            except Exception:
                log.exception("Error sending message for job %s", job_state.job_id)

        async def on_waiting(job_state: JobState) -> None:
            if getattr(job_state, '_reports_pre_sent', False):
                return

            report_files = list(job_state.work_dir.rglob("penetration_test_report.md"))
            csv_files = list(job_state.work_dir.rglob("vulnerabilities.csv"))

            if report_files or csv_files:
                try:
                    if report_files:
                        with open(report_files[0], "rb") as f:
                            await update.message.chat.send_document(
                                document=f,
                                filename="penetration_test_report.md",
                                caption="Reporte de seguridad generado por Strix."
                            )
                    if csv_files:
                        with open(csv_files[0], "rb") as f:
                            await update.message.chat.send_document(
                                document=f,
                                filename="vulnerabilities.csv",
                                caption="Vulnerabilidades encontradas."
                            )
                except Exception:
                    log.exception("Error sending report for job %s", job_state.job_id)

                job_state._reports_pre_sent = True
                return

            try:
                await update.message.chat.send_message("🧠 *Strix está esperando instrucciones.* Responde enviando un mensaje normal aquí.", parse_mode="Markdown")
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
                await status_msg.edit_text(
                    f"🏁 Sesión {result}."
                )
            except Exception:
                log.exception("Error editing status message for job %s", job_state.job_id)

            if job_state.status == JobStatus.COMPLETED and not getattr(job_state, '_reports_pre_sent', False):
                try:
                    report_files = list(job_state.work_dir.rglob("penetration_test_report.md"))
                    csv_files = list(job_state.work_dir.rglob("vulnerabilities.csv"))

                    if report_files:
                        with open(report_files[0], "rb") as f:
                            await update.message.chat.send_document(
                                document=f,
                                filename="penetration_test_report.md",
                                caption="Reporte de seguridad generado por Strix."
                            )
                    if csv_files:
                        with open(csv_files[0], "rb") as f:
                            await update.message.chat.send_document(
                                document=f,
                                filename="vulnerabilities.csv",
                                caption="Listado de vulnerabilidades en CSV."
                            )
                except Exception:
                    log.exception("Error sending final files for job %s", job_state.job_id)

            job_state._on_complete_done = True
            self.active_jobs.pop(user_id, None)
            async with self._job_count_lock:
                self._active_job_count -= 1

        task = asyncio.create_task(self.runner.run_job(ctx, state, on_new_message, on_waiting, on_complete))
        self.runner._tasks[state.job_id] = task

    async def on_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.effective_chat:
            return
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        if not self.policy.is_allowed(user_id, chat_id):
            return
        state = self.active_jobs.get(user_id)
        if not state:
            try:
                await update.callback_query.answer("No hay job activo")
            except Exception:
                pass
            return
        try:
            await update.callback_query.answer("Deteniendo...")
        except Exception:
            pass
        log.info("Stop requested for job %s by user %d", state.job_id, user_id)
        await self.runner.stop_job(state)

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
    app.add_handler(CallbackQueryHandler(service.on_stop, pattern=f"^{STOP_CALLBACK}$"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, service.on_message))
    app.add_error_handler(lambda update, context: log.exception(
        "Telegram polling error: %s", context.error,
    ))
    return app
