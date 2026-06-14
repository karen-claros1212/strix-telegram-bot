from __future__ import annotations

import platform
import re
import subprocess
from typing import Any

from strix_telegram_bot.telegram import send_message, edit_message, answer_callback
from strix_telegram_bot.ui.keyboards import back_to_menu
from strix_telegram_bot.ui.messages import health_text
from strix_telegram_bot.security import authorized_only

_STRIX_MIN_VERSION = (1, 0, 4)
_PYTHON_MIN_VERSION = (3, 12)


def _parse_version(ver_str: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", ver_str)
    if match:
        return tuple(int(g) for g in match.groups())
    return (0, 0, 0)


def _version_warning(ver: str, py_ver: str) -> str:
    warnings: list[str] = []
    strix_ver = _parse_version(ver)
    if strix_ver < _STRIX_MIN_VERSION:
        warnings.append(
            f"STRIX {ver} está desactualizada. Mínimo recomendado: "
            f"{'.'.join(str(v) for v in _STRIX_MIN_VERSION)}"
        )
    py_parsed = _parse_version(py_ver)
    if py_parsed < _PYTHON_MIN_VERSION:
        warnings.append(
            f"Python {py_ver} está por debajo del mínimo de STRIX: "
            f"{'.'.join(str(v) for v in _PYTHON_MIN_VERSION)}"
        )
    return "\n".join(warnings)


@authorized_only
def cmd_health(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    _send_health(bot, chat_id)


@authorized_only
def cmd_version(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    try:
        ver = subprocess.run(
            ["strix", "--version"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        ver = "unknown"
    py_ver = platform.python_version()
    warning = _version_warning(ver, py_ver)
    text = f"Versión STRIX: {ver}\nPython: {py_ver}"
    if warning:
        text += "\n\n⚠️ ADVERTENCIA:\n" + warning
    send_message(
        bot, chat_id, text,
        reply_markup=back_to_menu(),
    )


@authorized_only
def cmd_uptime(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    import os
    import time
    try:
        boot = psutil_boot_time()
        uptime_sec = time.time() - boot
    except Exception:
        uptime_sec = 0

    h, r = divmod(int(uptime_sec), 3600)
    m, s = divmod(r, 60)
    uptime_str = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"

    send_message(
        bot, chat_id,
        f"Tiempo activo del sistema: {uptime_str}",
        reply_markup=back_to_menu(),
    )


@authorized_only
def callback_health(bot: Any, update: dict) -> None:
    cb = update.get("callback_query", {})
    chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
    msg_id = cb.get("message", {}).get("message_id", "")
    _send_health(bot, chat_id, msg_id)
    answer_callback(bot, cb.get("id", ""))


def _send_health(bot, chat_id, msg_id=None) -> None:
    try:
        ver = subprocess.run(
            ["strix", "--version"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "unknown"
    except Exception:
        ver = "unknown"

    py_ver = platform.python_version()
    warning = _version_warning(ver, py_ver)

    from strix_telegram_bot.jobs.job_store import JobStore
    store = JobStore()
    active_count = len(store.list_active())

    text = health_text(
        strix_version=ver,
        python_version=py_ver,
        uptime="N/A",
        active_jobs=active_count,
        caido_status="N/A",
    )
    if warning:
        text += "\n\n⚠️ ADVERTENCIA:\n" + warning

    if msg_id:
        edit_message(bot, chat_id, msg_id, text, reply_markup=back_to_menu())
    else:
        send_message(bot, chat_id, text, reply_markup=back_to_menu())


def psutil_boot_time() -> float:
    import os
    import struct
    try:
        with open("/proc/stat", "r") as f:
            for line in f:
                if line.startswith("btime"):
                    return float(line.strip().split()[1])
    except Exception:
        pass
    try:
        import ctypes
        libc = ctypes.CDLL("libc.dylib")
        tv_sec = ctypes.c_long()
        tv_usec = ctypes.c_long()
        mib = (ctypes.c_int * 2)(1, 3)
        size = ctypes.c_size_t(ctypes.sizeof(tv_sec))
        libc.sysctl(mib, 2, ctypes.byref(tv_sec), ctypes.byref(size), None, 0)
        return float(tv_sec.value)
    except Exception:
        return 0.0


def _chat_id(update: dict) -> int:
    return (
        update.get("message", {}).get("chat", {}).get("id", "")
        or update.get("callback_query", {})
        .get("message", {})
        .get("chat", {})
        .get("id", 0)
    )
