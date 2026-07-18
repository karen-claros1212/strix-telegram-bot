"""Tool-specific Telegram renderers mirroring official Strix TUI renderers.

Each renderer produces plain-text Telegram messages that visually correspond
to what the TUI shows for each tool, adapted for Telegram's monospace plain-text.

See: strix/interface/tui/renderers/ for the official TUI renderers.
"""

from __future__ import annotations

import json
from typing import Any

_TOOL_RENDERERS: dict[str, Any] = {}


def register(name: str):
    """Register a renderer function for a tool name."""
    def decorator(fn):
        _TOOL_RENDERERS[name] = fn
        return fn
    return decorator


def render_tool_event(
    tool_name: str,
    status: str,
    args: dict[str, Any] | None = None,
    result: Any = None,
) -> str:
    """Render a tool event as Telegram plain text.

    Returns a formatted string for the tool event.
    Falls back to generic rendering if no specific renderer is registered.
    """
    renderer = _TOOL_RENDERERS.get(tool_name)
    if renderer:
        try:
            return renderer(status, args or {}, result)
        except Exception:
            pass
    return _render_default(tool_name, status, args or {}, result)


def _render_default(tool_name: str, status: str, args: dict, result: Any) -> str:
    """Generic fallback mirroring TUI _render_default_tool_widget.

    TUI format:
        → Using tool tool_name
          key: value
          ...
        Result: result_str      (if completed/failed/error)
        ● In progress...        (if running)
        ✓ Done                  (if completed, no result)
        ✗ Failed                (if failed, no result)
    """
    lines = [f"→ Using tool {tool_name}"]

    for k, v in args.items():
        str_v = str(v)
        lines.append(f"  {k}: {str_v}")

    if status in ("completed", "failed", "error") and result is not None:
        result_str = str(result)
        lines.append(f"Result: {result_str}")
    else:
        icon = {
            "running": "● In progress...",
            "completed": "✓ Done",
            "failed": "✗ Failed",
            "error": "✗ Error",
        }.get(status, "○ Unknown")
        lines.append(icon)

    return "\n".join(lines)


# ── Shell renderer (mirrors shell_renderer.py) ──────────────────

@register("execute_command")
@register("exec_command")
@register("execute_bash_command")
def _render_shell(status: str, args: dict, result: Any) -> str:
    """Shell renderer mirroring TUI shell_renderer.py.

    TUI limits: MAX_OUTPUT_LINES=50, MAX_LINE_LENGTH=200.
    Truncation marker: '... N lines truncated ...' when lines omitted.
    """
    cmd = args.get("command", "") or args.get("cmd", "") or args.get("input", "")

    if status == "running":
        lines = [">> shell"]
        if cmd:
            lines.append(f"   $ {cmd[:200]}")
        lines.append("   ● In progress...")
        return "\n".join(lines)

    if status == "completed" and isinstance(result, dict):
        exit_code = result.get("exit_code", result.get("exitcode", "?"))
        output = result.get("output", "") or result.get("stdout", "")
        lines = [">> shell"]
        if cmd:
            lines.append(f"   $ {cmd[:200]}")
        lines.append(f"   exit: {exit_code}")
        if output:
            truncated = _truncate_output(output, max_lines=50, max_line_len=200)
            lines.append(f"   {truncated}")
        return "\n".join(lines)

    if status == "failed":
        lines = [">> shell ✗ Failed"]
        if cmd:
            lines.append(f"   $ {cmd[:200]}")
        if isinstance(result, str):
            lines.append(f"   {result[:200]}")
        return "\n".join(lines)

    return _render_default("shell", status, args, result)


# ── Proxy renderer (mirrors proxy_renderer.py) ──────────────────

@register("proxy_fetch")
@register("web_fetch")
@register("fetch")
def _render_proxy(status: str, args: dict, result: Any) -> str:
    method = args.get("method", "GET")
    url = args.get("url", "") or args.get("target_url", "")

    if status == "running":
        lines = [">> fetch"]
        if url:
            lines.append(f"   {method} {_truncate_str(url, 100)}")
        lines.append("   ... descargando")
        return "\n".join(lines)

    if status == "completed" and isinstance(result, dict):
        status_code = result.get("status_code", result.get("status", "?"))
        body = result.get("body", "") or result.get("content", "")
        lines = [">> fetch"]
        if url:
            lines.append(f"   {method} {_truncate_str(url, 100)}")
        lines.append(f"   status: {status_code}")
        if body:
            lines.append(f"   body: {_truncate_str(body, 200)}")
        return "\n".join(lines)

    return _render_default("fetch", status, args, result)


# ── Filesystem renderer (mirrors filesystem_renderer.py) ─────────

@register("apply_patch")
def _render_patch(status: str, args: dict, result: Any) -> str:
    patch_text = _extract_patch_text(args)

    if status == "running":
        lines = [">> patch"]
        ops = _parse_patch_ops(patch_text)
        if ops:
            for kind, path in ops[:5]:
                label = {"add": "create", "update": "edit", "delete": "delete"}.get(kind, "file")
                lines.append(f"   {label} {_truncate_str(path, 60)}")
        else:
            lines.append("   procesando...")
        return "\n".join(lines)

    if status == "completed":
        lines = [">> patch"]
        ops = _parse_patch_ops(patch_text)
        if ops:
            for kind, path in ops[:10]:
                label = {"add": "create", "update": "edit", "delete": "delete"}.get(kind, "file")
                lines.append(f"   {label} {_truncate_str(path, 60)}")
        elif isinstance(result, str) and result.strip():
            lines.append(f"   {result.strip()[:200]}")
        return "\n".join(lines)

    return _render_default("patch", status, args, result)


@register("view_image")
def _render_view_image(status: str, args: dict, result: Any) -> str:
    path = args.get("path", "")

    if status == "running":
        lines = [">> view image"]
        if path:
            lines.append(f"   {_truncate_str(path, 60)}")
        return "\n".join(lines)

    if status == "completed":
        lines = [">> view image"]
        if path:
            lines.append(f"   {_truncate_str(path, 60)}")
        if isinstance(result, str) and result.strip():
            err = result.strip()
            if any(err.lower().startswith(p) for p in ("image path", "unable to read", "not a supported")):
                lines.append(f"   error: {err[:100]}")
            else:
                lines.append("   OK")
        else:
            lines.append("   OK")
        return "\n".join(lines)

    return _render_default("view_image", status, args, result)


# ── Reporting renderer (mirrors reporting_renderer.py) ───────────

@register("create_vulnerability_report")
def _render_vuln_report(status: str, args: dict, result: Any) -> str:
    if status == "running":
        title = args.get("title", "")
        lines = [">> vulnerabilidad"]
        if title:
            lines.append(f"   {title}")
        lines.append("   ... reportando")
        return "\n".join(lines)

    if status == "completed":
        title = args.get("title", "Sin titulo")
        severity = ""
        cvss_score = None
        if isinstance(result, dict):
            severity = result.get("severity", "")
            cvss_score = result.get("cvss_score")

        sev_icons = {
            "critical": "CRITICO",
            "high": "ALTO",
            "medium": "MEDIO",
            "low": "BAJO",
            "info": "INFO",
        }
        sev_label = sev_icons.get(severity.lower(), severity.upper()) if severity else ""

        lines = [f">> vulnerabilidad [{sev_label}]" if sev_label else ">> vulnerabilidad"]
        lines.append(f"   {title}")
        if cvss_score is not None:
            lines.append(f"   CVSS: {cvss_score}")
        target = args.get("target", "")
        endpoint = args.get("endpoint", "")
        if endpoint:
            lines.append(f"   {args.get('method', '')} {_truncate_str(endpoint, 80)}")
        elif target:
            lines.append(f"   {_truncate_str(target, 80)}")
        cve = args.get("cve", "")
        cwe = args.get("cwe", "")
        if cve:
            lines.append(f"   CVE: {cve}")
        if cwe:
            lines.append(f"   CWE: {cwe}")
        return "\n".join(lines)

    return _render_default("vulnerabilidad", status, args, result)


# ── Thinking renderer (mirrors thinking_renderer.py) ─────────────

@register("thinking")
def _render_thinking(status: str, args: dict, result: Any) -> str:
    thought = args.get("thought", "") or args.get("content", "")
    if thought:
        return f">> pensando\n   {_truncate_str(thought, 300)}"
    return ">> pensando"


# ── Notes renderer (mirrors notes_renderer.py) ───────────────────

@register("create_note")
def _render_create_note(status: str, args: dict, result: Any) -> str:
    title = args.get("title", "")
    content = args.get("content", "")
    category = args.get("category", "general")

    lines = [">> nota"]
    if title:
        lines.append(f"   {title} ({category})")
    if content:
        lines.append(f"   {_truncate_str(content, 150)}")
    return "\n".join(lines)


@register("update_note")
def _render_update_note(status: str, args: dict, result: Any) -> str:
    title = args.get("title", "")
    lines = [">> nota actualizada"]
    if title:
        lines.append(f"   {title}")
    return "\n".join(lines)


@register("delete_note")
def _render_delete_note(status: str, args: dict, result: Any) -> str:
    return ">> nota eliminada"


@register("list_notes")
def _render_list_notes(status: str, args: dict, result: Any) -> str:
    if status == "completed" and isinstance(result, dict) and result.get("success"):
        notes = result.get("notes", []) or []
        total = result.get("total_count", len(notes))
        lines = [">> notas"]
        if total == 0:
            lines.append("   sin notas")
        else:
            for note in notes[:10]:
                t = note.get("title", "(sin titulo)")
                cat = note.get("category", "general")
                lines.append(f"   - {t} ({cat})")
            if total > 10:
                lines.append(f"   +{total - 10} mas")
        return "\n".join(lines)
    return ">> notas"


@register("get_note")
def _render_get_note(status: str, args: dict, result: Any) -> str:
    if status == "completed" and isinstance(result, dict) and result.get("success"):
        note = result.get("note", {}) or {}
        title = note.get("title", "(sin titulo)")
        cat = note.get("category", "general")
        content = note.get("content", "")
        lines = [">> nota"]
        lines.append(f"   {title} ({cat})")
        if content:
            lines.append(f"   {_truncate_str(content, 200)}")
        return "\n".join(lines)
    return ">> nota"


# ── Todo renderer (mirrors todo_renderer.py) ─────────────────────

@register("create_todo")
@register("list_todos")
@register("update_todo")
@register("mark_todo_done")
@register("mark_todo_pending")
@register("delete_todo")
def _render_todo(status: str, args: dict, result: Any) -> str:
    tool_label = {
        "create_todo": "todo creado",
        "list_todos": "todos",
        "update_todo": "todo actualizado",
        "mark_todo_done": "todo completado",
        "mark_todo_pending": "todo reabierto",
        "delete_todo": "todo eliminado",
    }
    label = "todo"
    for k, v in tool_label.items():
        if k in args or (result and isinstance(result, dict) and result.get("tool") == k):
            label = v
            break

    if status == "completed" and isinstance(result, dict) and result.get("success"):
        todos = result.get("todos", []) or []
        lines = [f">> {label}"]
        if not todos:
            lines.append("   sin todos")
        else:
            markers = {"pending": "[ ]", "in_progress": "[~]", "done": "[+]"}
            for todo in todos:
                st = todo.get("status", "pending")
                marker = markers.get(st, "[ ]")
                title = todo.get("title", "(sin titulo)")
                lines.append(f"   {marker} {title}")
        return "\n".join(lines)

    return f">> {label}"


# ── Web search renderer (mirrors web_search_renderer.py) ────────

@register("web_search")
def _render_web_search(status: str, args: dict, result: Any) -> str:
    query = args.get("query", "") or args.get("q", "")

    if status == "running":
        lines = [">> busqueda"]
        if query:
            lines.append(f"   \"{query}\"")
        lines.append("   ... buscando")
        return "\n".join(lines)

    if status == "completed" and isinstance(result, dict):
        results = result.get("results", []) or result.get("organic_results", [])
        lines = [">> busqueda"]
        if query:
            lines.append(f"   \"{query}\"")
        for r in results[:5]:
            title = r.get("title", "")
            snippet = r.get("snippet", "") or r.get("description", "")
            if title:
                lines.append(f"   - {title}")
            if snippet:
                lines.append(f"     {_truncate_str(snippet, 100)}")
        if not results:
            lines.append("   sin resultados")
        return "\n".join(lines)

    return _render_default("busqueda", status, args, result)


# ── Load skill renderer (mirrors load_skill_renderer.py) ────────

@register("load_skill")
def _render_load_skill(status: str, args: dict, result: Any) -> str:
    skill_name = args.get("name", "") or args.get("skill", "")

    if status == "running":
        lines = [">> skill"]
        if skill_name:
            lines.append(f"   cargando: {skill_name}")
        return "\n".join(lines)

    if status == "completed":
        lines = [">> skill"]
        if skill_name:
            lines.append(f"   {skill_name}")
        if isinstance(result, dict):
            desc = result.get("description", "")
            tools = result.get("tools", []) or []
            if desc:
                lines.append(f"   {_truncate_str(desc, 120)}")
            if tools:
                lines.append(f"   herramientas: {', '.join(tools[:5])}")
        return "\n".join(lines)

    return _render_default("skill", status, args, result)


# ── Finish renderer (mirrors finish_renderer.py) ────────────────

@register("finish_scan")
@register("agent_finish")
def _render_finish(status: str, args: dict, result: Any) -> str:
    if status == "completed":
        summary = args.get("summary", "") or args.get("message", "")
        report_path = args.get("report_path", "") or args.get("path", "")
        lines = [">> finalizado"]
        if summary:
            lines.append(f"   {_truncate_str(summary, 200)}")
        if report_path:
            lines.append(f"   reporte: {_truncate_str(report_path, 80)}")
        return "\n".join(lines)
    return ">> finalizando..."


# ── Agents graph renderer (mirrors agents_graph_renderer.py) ────

@register("spawn_child_agent")
def _render_spawn(status: str, args: dict, result: Any) -> str:
    child_name = args.get("name", "") or args.get("agent_name", "")
    task = args.get("task", "") or args.get("instruction", "")
    lines = [">> spawn agente"]
    if child_name:
        lines.append(f"   nombre: {child_name}")
    if task:
        lines.append(f"   tarea: {_truncate_str(task, 120)}")
    return "\n".join(lines)


@register("wait_for_message")
def _render_wait(status: str, args: dict, result: Any) -> str:
    return ">> esperando respuesta"


@register("send_message_to_agent")
def _render_send_agent(status: str, args: dict, result: Any) -> str:
    text = args.get("text", "") or args.get("message", "")
    target = args.get("agent_id", "") or args.get("target", "")
    lines = [">> mensaje a agente"]
    if target:
        lines.append(f"   para: {_truncate_str(target, 20)}")
    if text:
        lines.append(f"   {_truncate_str(text, 150)}")
    return "\n".join(lines)


# ── Helpers ──────────────────────────────────────────────────────

def _truncate_str(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def _truncate_output(output: str, max_lines: int = 50, max_line_len: int = 200) -> str:
    """Truncate output mirroring TUI shell_renderer._format_output.

    TUI uses head/tail split: first half from top, last half from bottom,
    with '... N lines truncated ...' marker in between.
    Lines longer than MAX_LINE_LENGTH are truncated with '...'.
    """
    lines = output.splitlines()
    total = len(lines)

    head_count = max_lines // 2
    tail_count = max_lines - head_count - 1

    if total <= max_lines:
        display = lines
        truncated = False
        hidden_count = 0
    else:
        display = lines[:head_count]
        truncated = True
        hidden_count = total - head_count - tail_count

    result_lines = []
    for i, line in enumerate(display):
        line = line.rstrip()
        if len(line) > max_line_len:
            line = line[: max_line_len - 3] + "..."
        result_lines.append(line)

    if truncated:
        result_lines.append(f"... {hidden_count} lines truncated ...")
        tail = lines[-tail_count:]
        for line in tail:
            line = line.rstrip()
            if len(line) > max_line_len:
                line = line[: max_line_len - 3] + "..."
            result_lines.append(line)

    return "\n   ".join(result_lines) if result_lines else ""


def _extract_patch_text(args: dict) -> str:
    raw = args.get("patch")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        inner = raw.get("patch")
        if isinstance(inner, str):
            return inner
    fallback = args.get("input") if isinstance(args, dict) else None
    if isinstance(fallback, str):
        return fallback
    return ""


def _parse_patch_ops(patch_text: str) -> list[tuple[str, str]]:
    """Parse patch text into [(kind, path), ...] for display."""
    ops: list[tuple[str, str]] = []
    markers = {
        "*** Add File: ": "add",
        "*** Update File: ": "update",
        "*** Delete File: ": "delete",
    }
    for line in patch_text.splitlines():
        for prefix, kind in markers.items():
            if line.startswith(prefix):
                path = line[len(prefix):].strip()
                ops.append((kind, path))
                break
    return ops
