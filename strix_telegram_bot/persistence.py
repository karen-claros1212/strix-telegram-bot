"""Single atomic JSON write primitive shared by every persisted bot state.

All bot-side JSON state (job store, delivery tracker, state manager, Telegram
offset) must go through :func:`atomic_write_json` so a crash mid-write can never
leave a partial/corrupt file. The write is: temp file in the SAME directory (same
filesystem) -> write -> flush -> fsync -> os.replace (atomic rename). A failure
unlinks the temp file and re-raises, leaving the previous file intact.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(
    path: Path | str,
    data: Any,
    *,
    indent: int = 2,
    default: Any = str,
) -> None:
    """Atomically write ``data`` as JSON to ``path``.

    The temp file is created in the same directory as ``path`` so the final
    ``os.replace`` is an atomic rename on the same filesystem. ``fsync`` ensures
    the bytes are on disk before the rename, so a power loss cannot leave a
    half-written file.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=indent, default=default)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(target))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
