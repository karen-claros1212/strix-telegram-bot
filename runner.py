"""Copyright 2026 Diego Claros

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

import asyncio
import asyncio.subprocess
import ipaddress
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from strix.agents.StrixAgent import StrixAgent
from strix.llm.config import LLMConfig
from strix.runtime import cleanup_runtime
from strix.telemetry.tracer import Tracer, set_global_tracer

from .models import JobContext, JobState, JobStatus, utc_now

log = logging.getLogger("strix_bot")


def _job_id() -> str:
    return uuid.uuid4().hex


def _is_private_target(token: str) -> bool:
    from urllib.parse import urlparse

    host = token
    if "://" in token:
        parsed = urlparse(token)
        host = parsed.hostname or token
    elif ":" in host and host.count(":") > 1:
        # Likely IPv6 — use raw host, skip port splitting
        host = host.split("/")[0].split("]")[0].lstrip("[")
    else:
        host = host.split("/")[0].split(":")[0]
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified
    except ValueError:
        return False


def _resolve_target(text: str, attachments: list[Path], work_root: Path | None = None) -> list[str]:
    targets: list[str] = []
    if attachments:
        targets.extend([str(path) for path in attachments])

    for token in text.split():
        if _is_private_target(token):
            continue
        if token.startswith("http://") or token.startswith("https://"):
            targets.append(token)
        elif token.startswith("git@"):
            targets.append(token)
        elif token.startswith("/") and os.path.exists(token):
            if work_root:
                resolved = os.path.realpath(token)
                root = os.path.realpath(work_root)
                if not resolved.startswith(str(root) + os.sep) and resolved != str(root):
                    continue
            targets.append(token)
        elif re.match(r'^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$', token):
            # Dominio válido (ej: credialianza.com, sub.domain.com.co)
            if not token.startswith("http"):
                token = "https://" + token
            targets.append(token)

    return targets


class JobRunner:
    def __init__(self, work_root: Path, timeout_seconds: int) -> None:
        self.work_root = work_root
        self.timeout_seconds = timeout_seconds
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._agents: dict[str, StrixAgent] = {}

    def create_job(self, ctx: JobContext) -> JobState:
        job_id = _job_id()
        work_dir = self.work_root.resolve() / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        instruction_path = work_dir / "instruction.md"
        return JobState(job_id=job_id, work_dir=work_dir, instruction_path=instruction_path)

    def is_running(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        return task is not None and not task.done()

    def get_agent(self, job_id: str) -> StrixAgent | None:
        return self._agents.get(job_id)

    async def run_job(
        self,
        ctx: JobContext,
        state: JobState,
        on_new_message,
        on_waiting,
        on_complete,
    ) -> None:
        state.status = JobStatus.RUNNING
        state.started_at = state.started_at or utc_now()
        targets = _resolve_target(ctx.text, ctx.attachments, self.work_root)
        if not targets:
            if ctx.text.strip():
                targets = [str(state.work_dir)]
            else:
                state.status = JobStatus.FAILED
                state.last_output = "No se encontro target en el mensaje ni adjuntos."
                await on_complete(state)
                return

        target_info_list = []
        for t in targets:
            if t == str(state.work_dir):
                target_info_list.append({"type": "local_code", "details": {"target_path": t}, "original": "chat_mode"})
            elif t.startswith("http"):
                target_info_list.append({"type": "url", "details": {"target_url": t}, "original": t})
            elif t.startswith("git@"):
                target_info_list.append({"type": "repository", "details": {"target_repo": t}, "original": t})
            elif os.path.exists(t):
                sandbox_path = f"/workspace/{Path(t).name}"
                target_info_list.append({"type": "local_code", "details": {"target_path": sandbox_path}, "original": t})
            else:
                target_info_list.append({"type": "domain", "details": {"target_domain": t}, "original": t})

        llm_config = LLMConfig(scan_mode="deep")
        llm_config.interactive = True

        agent_config = {
            "llm_config": llm_config,
            "max_iterations": 300,
        }

        scan_config = {
            "scan_id": state.job_id,
            "targets": target_info_list,
            "user_instructions": state.instruction_path.read_text() + "\n\nIMPORTANTE: Responde e interactua SIEMPRE en espanol.",
            "run_name": state.job_id,
        }

        tracer = Tracer(state.job_id)
        tracer.set_scan_config(scan_config)
        set_global_tracer(tracer)

        agent = StrixAgent(agent_config)
        self._agents[state.job_id] = agent

        agent_task = asyncio.create_task(agent.execute_scan(scan_config))
        log.info("Job %s running: target=%s interactive=True timeout=%ds",
                 state.job_id, target_info_list, self.timeout_seconds)

        if ctx.attachments:
            try:
                container_name = f"strix-scan-{state.job_id}"
                container_ready = False
                for _ in range(30):
                    proc = await asyncio.create_subprocess_exec(
                        "docker", "inspect", "-f", "{{.State.Status}}", container_name,
                        stdout=asyncio.PIPE, stderr=asyncio.DEVNULL,
                    )
                    stdout, _ = await proc.communicate()
                    if stdout.strip() == b"running":
                        container_ready = True
                        break
                    await asyncio.sleep(1)

                if not container_ready:
                    log.warning("Container %s not ready after 30s, skipping file copy", container_name)
                else:
                    for file_path in ctx.attachments:
                        dest = f"{container_name}:/workspace/{file_path.name}"
                        proc = await asyncio.create_subprocess_exec(
                            "docker", "cp", str(file_path), dest,
                            stdout=asyncio.DEVNULL, stderr=asyncio.PIPE,
                        )
                        _, stderr = await proc.communicate()
                        if proc.returncode == 0:
                            log.info("Copied %s to sandbox:/workspace/", file_path.name)
                        else:
                            log.warning("docker cp failed for %s: %s", file_path.name, stderr.decode().strip())
            except Exception as e:
                log.warning("Error copying files to sandbox: %s", e)

        try:
            try:
                await asyncio.wait_for(
                    self._monitor_agent(state, agent, agent_task, on_new_message, on_waiting),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError:
                await self.stop_job(state)
                state.status = JobStatus.STOPPED
                state.last_output = "Timeout del job"
                await on_complete(state)
                return

            try:
                await agent_task
            except asyncio.CancelledError:
                if getattr(state, '_reports_pre_sent', False):
                    state.exit_code = 0
                    state.status = JobStatus.COMPLETED
                else:
                    state.status = JobStatus.STOPPED
                await on_complete(state)
                return
            except Exception as e:
                state.status = JobStatus.FAILED
                state.last_output = str(e)
                await on_complete(state)
                return

            state.exit_code = 0
            state.status = JobStatus.COMPLETED
            await on_complete(state)
        except asyncio.CancelledError:
            agent_task.cancel()
            try:
                await agent_task
            except (asyncio.CancelledError, Exception):
                pass
            if getattr(state, '_reports_pre_sent', False):
                state.exit_code = 0
                state.status = JobStatus.COMPLETED
            else:
                state.status = JobStatus.STOPPED
            await on_complete(state)
        except Exception as e:
            agent_task.cancel()
            try:
                await agent_task
            except (asyncio.CancelledError, Exception):
                pass
            state.status = JobStatus.FAILED
            state.last_output = str(e)
            await on_complete(state)
        finally:
            self._tasks.pop(state.job_id, None)
            self._agents.pop(state.job_id, None)
            # Strix no limpia contenedores al terminar — lo hacemos nosotros
            try:
                cleanup_runtime()
            except Exception:
                log.warning("cleanup_runtime fallo (esperable si no hay contenedor)")
            # Fallback directo por si cleanup_runtime no existiera en futuras versiones
            try:
                container_name = f"strix-scan-{state.job_id}"
                proc = await asyncio.create_subprocess_exec(
                    "docker", "rm", "-f", container_name,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=10)
                if proc.returncode == 0:
                    log.info("🧹 Contenedor %s eliminado (fallback)", container_name)
                elif proc.returncode == 1:
                    log.debug("Contenedor %s no existia (ya limpio)", container_name)
                else:
                    log.warning("docker rm -f %s retorno codigo %d", container_name, proc.returncode)
            except asyncio.TimeoutError:
                log.warning("Timeout eliminando contenedor %s", container_name)
            except Exception as e:
                log.debug("Fallback cleanup para %s: %s", container_name, e)

            # Cleanup periódico de runs viejos (>7 días, cada 5 jobs)
            cleanup_key = "_last_cleanup"
            last = getattr(self, cleanup_key, 0.0)
            now = time.time()
            if now - last > 3600:  # 1 hora entre cleanups
                cutoff = now - (7 * 86_400)
                removed = 0
                try:
                    for entry in self.work_root.iterdir():
                        if entry.is_dir() and len(entry.name) >= 8:
                            mtime = entry.stat().st_mtime
                            if mtime < cutoff:
                                shutil.rmtree(entry, ignore_errors=True)
                                removed += 1
                except Exception:
                    pass
                if removed:
                    log.info("Cleanup removed %d old run(s)", removed)
                setattr(self, cleanup_key, now)

    async def _monitor_agent(self, state: JobState, agent: StrixAgent, agent_task: asyncio.Task, on_new_message, on_waiting) -> None:
        last_msg_count = 0
        was_waiting = False
        pending_content = ""
        last_content_time = 0.0
        _auto_continued = False

        while not agent_task.done():
            await asyncio.sleep(0.5)
            now = asyncio.get_event_loop().time()

            current_msgs = agent.state.messages
            if len(current_msgs) > last_msg_count:
                for i in range(last_msg_count, len(current_msgs)):
                    msg = current_msgs[i]
                    if msg.get("role") == "assistant" and msg.get("content"):
                        content = msg.get("content").strip()
                        if content:
                            if i < len(current_msgs) - 1:
                                state.last_output = content
                                await on_new_message(state, content)
                            else:
                                pending_content = content
                                last_content_time = now
                last_msg_count = len(current_msgs)

            is_waiting = agent.state.is_waiting_for_input()

            if is_waiting and not was_waiting:
                if pending_content:
                    state.last_output = pending_content
                    await on_new_message(state, pending_content)
                    pending_content = ""
                await on_waiting(state)
                # Si enviamos reportes y el agente pregunta si continuar,
                # responderle automáticamente que sí para que siga con los
                # siguientes targets (solo la primera vez)
                if getattr(state, '_reports_pre_sent', False) and not _auto_continued:
                    _auto_continued = True
                    log.info("Auto-continuing job %s after report delivery", state.job_id)
                    agent.state.add_message("user", "continua con el siguiente target")
                    agent.state.resume_from_waiting()
            elif pending_content and (now - last_content_time) >= 1.5:
                state.last_output = pending_content
                await on_new_message(state, pending_content)
                pending_content = ""

            was_waiting = is_waiting

        if pending_content:
            state.last_output = pending_content
            await on_new_message(state, pending_content)

    async def stop_job(self, state: JobState) -> None:
        task = self._tasks.get(state.job_id)
        if task and not task.done():
            task.cancel()
        state.status = JobStatus.STOPPING
