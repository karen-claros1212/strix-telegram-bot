from __future__ import annotations

from typing import Any

from strix_telegram_bot.jobs.job_store import JobStore
from strix_telegram_bot.telegram import edit_message, send_message
from strix_telegram_bot.ui.keyboards import (
    _btn,
    _cb,
    back_to_menu,
    build_inline_keyboard,
    job_panel,
    parse_callback,
)
from strix_telegram_bot.ui.messages import escape_md, job_status_text

_PAGE_SIZE = 10


def cmd_jobs(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    _list_jobs(bot, chat_id)


def cmd_status(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    bridge = getattr(bot, "_bridge", None)
    if bridge and bridge.is_running:
        status = bridge.to_status_dict()
        text = job_status_text(status)
        send_message(bot, chat_id, text, reply_markup=job_panel(running=True))
        return
    store = JobStore()
    active = store.list_active()
    if not active:
        send_message(bot, chat_id, "No hay trabajos activos.", reply_markup=back_to_menu())
        return
    job = active[0]
    text = job_status_text(job)
    send_message(bot, chat_id, text, reply_markup=job_panel(running=job.is_active))


def cmd_stop(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    bridge = getattr(bot, "_bridge", None)
    if bridge and bridge.is_running:
        # Non-blocking: stop runs in a background thread; result is logged honestly.
        send_message(bot, chat_id, "Deteniendo escaneo...", reply_markup=back_to_menu())
        bridge.stop_scan_async()
    else:
        send_message(bot, chat_id, "No hay escaneo activo.", reply_markup=back_to_menu())


def callback_jobs(bot: Any, update: dict) -> None:
    cb = update.get("callback_query", {})
    data = cb.get("data", "")
    chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
    msg_id = cb.get("message", {}).get("message_id", "")
    parts = parse_callback(data)

    if len(parts) < 2:
        return

    action = parts[1]

    if action == "list":
        _list_jobs(bot, chat_id, msg_id)

    elif action == "agents":
        bridge = getattr(bot, "_bridge", None)
        if bridge and bridge.is_running:
            agents = bridge.list_agents()
            if agents:
                from strix_telegram_bot.ui.keyboards import agent_selector
                edit_message(
                    bot, chat_id, msg_id, "Selecciona un agente:",
                    reply_markup=agent_selector(agents),
                )
            else:
                edit_message(bot, chat_id, msg_id, "No hay agentes.", reply_markup=back_to_menu())
        else:
            edit_message(bot, chat_id, msg_id, "Bridge no disponible.", reply_markup=back_to_menu())

    elif action == "stop":
        bridge = getattr(bot, "_bridge", None)
        if bridge and bridge.is_running:
            # Non-blocking + honest: "Deteniendo..." (in progress), not "Detenido" (done).
            edit_message(bot, chat_id, msg_id, "Deteniendo escaneo...", reply_markup=back_to_menu())
            bridge.stop_scan_async()
        else:
            edit_message(
                bot, chat_id, msg_id, "No hay escaneo activo.", reply_markup=back_to_menu()
            )

    elif action == "chat":
        bridge = getattr(bot, "_bridge", None)
        if bridge and bridge.is_running:
            agents = bridge.list_agents()
            if agents:
                if len(agents) == 1:
                    _show_agent_chat(bot, chat_id, msg_id, bridge, agents[0]["id"])
                else:
                    from strix_telegram_bot.ui.keyboards import agent_selector
                edit_message(
                    bot, chat_id, msg_id, "Selecciona un agente:",
                    reply_markup=agent_selector(agents),
                )
            else:
                edit_message(bot, chat_id, msg_id, "No hay agentes.", reply_markup=back_to_menu())
        else:
            edit_message(bot, chat_id, msg_id, "Bridge no disponible.", reply_markup=back_to_menu())

    elif action == "tree":
        bridge = getattr(bot, "_bridge", None)
        if bridge and bridge.is_running:
            _show_agent_tree(bot, chat_id, msg_id, bridge)
        else:
            edit_message(
                bot, chat_id, msg_id, "No hay escaneo activo.", reply_markup=back_to_menu()
            )

    elif action == "vulns":
        bridge = getattr(bot, "_bridge", None)
        if bridge and bridge.is_running:
            _show_vulnerabilities(bot, chat_id, msg_id, bridge)
        else:
            edit_message(
                bot, chat_id, msg_id, "No hay escaneo activo.", reply_markup=back_to_menu()
            )

    elif action == "vulndetail":
        if len(parts) < 3:
            return
        vuln_id = parts[2]
        bridge = getattr(bot, "_bridge", None)
        if bridge:
            _show_vuln_detail(bot, chat_id, msg_id, bridge, vuln_id)

    elif action == "vulndetailpage":
        if len(parts) < 4:
            return
        vuln_id = parts[2]
        try:
            section = int(parts[3])
        except ValueError:
            return
        bridge = getattr(bot, "_bridge", None)
        if bridge:
            _show_vuln_detail(bot, chat_id, msg_id, bridge, vuln_id, section=section)

    elif action == "vulnpage":
        if len(parts) < 3:
            return
        cursor = parts[2]
        bridge = getattr(bot, "_bridge", None)
        if bridge:
            _show_vulnerabilities(bot, chat_id, msg_id, bridge, before_vuln_id=cursor)

    elif action == "chatpage":
        agent_id = parts[2] if len(parts) > 2 else ""
        cursor = parts[3] if len(parts) > 3 else "__latest__"
        bridge = getattr(bot, "_bridge", None)
        if bridge and agent_id:
            _show_agent_chat(bot, chat_id, msg_id, bridge, agent_id, before_event_id=cursor)

    elif action == "status":
        bridge = getattr(bot, "_bridge", None)
        if bridge and bridge.is_running:
            status = bridge.to_status_dict()
            text = job_status_text(status)
            edit_message(bot, chat_id, msg_id, text, reply_markup=job_panel(running=True))
        else:
            store = JobStore()
            active = store.list_active()
            if active:
                job = active[0]
                text = job_status_text(job)
                edit_message(bot, chat_id, msg_id, text,
                            reply_markup=job_panel(running=job.is_active))
            else:
                edit_message(bot, chat_id, msg_id, "No hay trabajos activos.",
                            reply_markup=back_to_menu())

    elif action == "stop_agent":
        if len(parts) < 3:
            return
        agent_id = parts[2]
        bridge = getattr(bot, "_bridge", None)
        if bridge and bridge.is_running:
            ok = bridge.stop_agent(agent_id)
            msg = (
                f"Agente {agent_id[:8]} detenido." if ok
                else f"No se pudo detener {agent_id[:8]}."
            )
            edit_message(
                bot, chat_id, msg_id, msg, reply_markup=back_to_menu()
            )
        else:
            edit_message(
                bot, chat_id, msg_id, "No hay escaneo activo.", reply_markup=back_to_menu()
            )


def _list_jobs(bot, chat_id, msg_id=None) -> None:
    store = JobStore()
    jobs = store.list_recent(limit=10)
    if not jobs:
        text = "No hay trabajos aun."
        kb = back_to_menu()
    else:
        lines = ["Trabajos recientes:"]
        for j in jobs:
            lines.append(
                f"{j.phase.value} {escape_md(j.run_name[:30])} [{j.mode.value}] {j.elapsed}"
            )
        text = "\n".join(lines)
        kb = back_to_menu()
    if msg_id:
        edit_message(bot, chat_id, msg_id, text, reply_markup=kb)
    else:
        send_message(bot, chat_id, text, reply_markup=kb)


def _chat_id(update: dict) -> int:
    return (
        update.get("message", {}).get("chat", {}).get("id", "")
        or update.get("callback_query", {}).get("message", {}).get("chat", {}).get("id", 0)
    )


# ── Chat view ──────────────────────────────────────────────────


def _build_render_signature(agent_id: str, cursor: str, timeline: list[dict],
                            agent_status: str = "") -> str:
    """Firma estable: incluye todos los eventos de la pagina + estado del agente."""
    parts = [agent_id, cursor, agent_status]
    for e in timeline:
        parts.append(f"{e.get('id','')}:{e.get('version',0)}")
    return "|".join(parts)


def _is_terminal_transition(old_sig: str, new_sig: str) -> bool:
    """Detecta transiciones de estado que requieren refresh inmediato."""
    # Si la firma vieja no existe, no es transicion
    if not old_sig or not new_sig:
        return False
    # Comparar versiones: si algun evento paso de streaming→final o tool completed/failed
    def _parse_versions(sig: str) -> dict[str, int]:
        result = {}
        for part in sig.split("|")[2:]:  # skip agent_id, cursor, agent_status
            if ":" in part:
                eid, ver = part.split(":", 1)
                try:
                    result[eid] = int(ver)
                except ValueError:
                    pass
        return result

    old_v = _parse_versions(old_sig)
    new_v = _parse_versions(new_sig)
    if not old_v or not new_v:
        return True  # estructura diferente, refrescar

    return old_v != new_v  # cualquier cambio de version → refrescar


def _show_agent_chat(bot: Any, chat_id: str, msg_id: str, bridge, agent_id: str,
                     before_event_id: str = "__latest__") -> None:
    timeline = bridge.agent_timeline(agent_id)
    tree = bridge.get_agent_tree()
    agent_info = {}
    if tree and agent_id in tree.get("agents", {}):
        agent_info = tree["agents"][agent_id]

    # Resolve cursor
    if before_event_id == "__latest__":
        page = timeline[-_PAGE_SIZE:] if timeline else []
    else:
        anchor_idx = None
        for i, ev in enumerate(timeline):
            if ev.get("id") == before_event_id:
                anchor_idx = i
                break
        if anchor_idx is not None:
            start = max(0, anchor_idx - _PAGE_SIZE)
            page = timeline[start:anchor_idx]
        else:
            page = timeline[-_PAGE_SIZE:] if timeline else []

    # Render signature — skip edit if unchanged (except terminal transitions)
    agent_status = agent_info.get("status", "unknown")
    signature = _build_render_signature(agent_id, before_event_id, page, agent_status)
    last_sig = getattr(bot, "_last_chat_signature", "")
    is_terminal = _is_terminal_transition(last_sig, signature)
    if signature == last_sig:
        return
    if not is_terminal:
        now = __import__("time").time()
        _last_chat_refresh = getattr(bot, "_last_chat_refresh", 0.0)
        if now - _last_chat_refresh < 3.0:
            return
    bot._last_chat_signature = signature

    _MAX_CHAT_MSG = 4000

    lines = [
        f"Chat — {agent_info.get('name', agent_id)[:30]}",
        f"Estado: {agent_info.get('status', 'unknown')}",
        "",
    ]

    if not page:
        lines[-1] = "Sin actividad registrada."
    else:
        for ev in page:
            ev_type = ev.get("type", "")
            data = ev.get("data", {})
            chunk: list[str] = []
            if ev_type == "chat":
                role = data.get("role", "")
                content = data.get("content", "")
                streaming = data.get("metadata", {}).get("streaming", False)
                if streaming:
                    chunk.append(">> STRIX (escribiendo)")
                    chunk.append(f"   {content}")
                elif role == "user":
                    chunk.append(">> Tu")
                    chunk.append(f"   {content}")
                elif role == "assistant":
                    chunk.append(">> STRIX")
                    chunk.append(f"   {content}")
            elif ev_type == "tool":
                from strix_telegram_bot.strix.telegram_renderers import render_tool_event
                name = data.get("tool_name", "tool")
                status = data.get("status", "")
                args = data.get("args", {})
                result = data.get("result")
                text = render_tool_event(name, status, args, result)
                chunk.append(text)

            candidate = "\n".join(lines) + ("\n" if lines[-1] else "") + "\n".join(chunk)
            if len(candidate) > _MAX_CHAT_MSG:
                break
            lines.extend(chunk)

    text = "\n".join(lines)

    # Navigation: cursor-based pagination
    nav_buttons = []
    first_in_page = page[0] if page else None

    if first_in_page and (before_event_id != "__latest__" or len(timeline) > _PAGE_SIZE):
        prev_id = first_in_page.get("id", "")
        nav_buttons.append(_btn("Anteriores", _cb("job", "chatpage", agent_id, prev_id)))

    if before_event_id != "__latest__":
        nav_buttons.append(_btn("Recientes", _cb("job", "chatpage", agent_id, "__latest__")))

    kb_rows = [nav_buttons] if nav_buttons else []
    kb_rows.append([
        _btn("Scan", _cb("job", "status")),
        _btn("Agentes", _cb("job", "agents")),
    ])
    kb_rows.append([_btn("Detener agente", _cb("job", "stop_agent", agent_id))])
    kb_rows.append([_btn("Volver", _cb("menu", "main"))])
    kb = build_inline_keyboard(kb_rows)

    edit_message(bot, chat_id, msg_id, text, reply_markup=kb, parse_mode=None)

    bot._active_chat_agent_id = agent_id
    bot._active_chat_message_id = msg_id
    bot._active_chat_chat_id = chat_id
    bot._active_chat_cursor = before_event_id


# ── Agent tree ──────────────────────────────────────────────────


def _show_agent_tree(bot: Any, chat_id: str, msg_id: str, bridge) -> None:
    tree = bridge.get_agent_tree()
    if not tree or not tree.get("agents"):
        edit_message(bot, chat_id, msg_id, "No hay agentes.", reply_markup=back_to_menu())
        return

    agents = tree["agents"]
    root_id = bridge.root_agent_id or ""

    def _build_lines(aid: str, depth: int = 0) -> list[str]:
        info = agents.get(aid, {})
        prefix = "  " * depth + ("└ " if depth > 0 else "")
        name = info.get("name", aid)[:20]
        status = info.get("status", "?")
        icon = {
            "running": "▶", "waiting": "⏳", "completed": "✅",
            "stopped": "⏹", "failed": "❌",
        }.get(status, "?")
        result = [f"{prefix}{icon} {name} ({status})"]
        children = [k for k, v in agents.items() if v.get("parent_id") == aid and k != aid]
        for child in children:
            result.extend(_build_lines(child, depth + 1))
        return result

    lines = ["Arbol de agentes:", ""]
    if root_id and root_id in agents:
        lines.extend(_build_lines(root_id))
    else:
        for aid in agents:
            if agents[aid].get("parent_id") is None:
                lines.extend(_build_lines(aid))

    text = "\n".join(lines)

    agent_list = list(agents.keys())[:8]
    stop_rows = []
    for i in range(0, len(agent_list), 2):
        row = []
        for aid in agent_list[i:i+2]:
            name = agents[aid].get("name", aid)[:8]
            row.append(_btn(f"X {name}", _cb("job", "stop_agent", aid)))
        stop_rows.append(row)

    kb_rows = stop_rows
    kb_rows.append([_btn("Chat", _cb("job", "chat"))])
    kb_rows.append([_btn("Volver", _cb("menu", "main"))])
    kb = build_inline_keyboard(kb_rows)
    edit_message(bot, chat_id, msg_id, text, reply_markup=kb, parse_mode=None)

    bot._active_chat_agent_id = None


# ── Vulnerabilities ────────────────────────────────────────────


def _show_vulnerabilities(bot: Any, chat_id: str, msg_id: str, bridge,
                         before_vuln_id: str = "__latest__") -> None:
    vulns = bridge.get_vulnerabilities()
    if not vulns:
        edit_message(
            bot, chat_id, msg_id, "Sin vulnerabilidades detectadas.",
            reply_markup=back_to_menu(),
        )
        return

    per_page = 5

    if before_vuln_id == "__latest__":
        page = vulns[-per_page:] if vulns else []
    else:
        anchor_idx = None
        for i, v in enumerate(vulns):
            if v.get("id") == before_vuln_id:
                anchor_idx = i
                break
        if anchor_idx is not None:
            start = max(0, anchor_idx - per_page)
            page = vulns[start:anchor_idx]
        else:
            page = vulns[-per_page:] if vulns else []

    total = len(vulns)
    first_in_page = page[0] if page else None

    lines = [f"Vulnerabilidades ({total}):", ""]
    for v in page:
        vid = v.get("id", "?")
        severity = v.get("severity", "info").upper()
        title = v.get("title", "")[:60]
        agent = v.get("agent_name", "")[:16]
        lines.append(f"[{severity}] {title}")
        if agent:
            lines.append(f"  Agente: {agent}")
        lines.append(f"  Detalle: /vuln_{vid}")

    text = "\n".join(lines)

    nav = []
    if first_in_page and before_vuln_id != "__latest__":
        nav.append(_btn("Anteriores", _cb("job", "vulnpage", "before", first_in_page["id"])))
    if before_vuln_id != "__latest__":
        nav.append(_btn("Ultimas", _cb("job", "vulnpage", "latest")))

    kb_rows = [nav] if nav else []
    for v in page[:5]:
        vid = v.get("id", "")
        title = v.get("title", "")[:30]
        kb_rows.append([_btn(f"{title}", _cb("job", "vulndetail", vid))])
    kb_rows.append([_btn("Arbol", _cb("job", "tree")), _btn("Chat", _cb("job", "chat"))])
    kb_rows.append([_btn("Volver", _cb("menu", "main"))])
    kb = build_inline_keyboard(kb_rows)
    edit_message(bot, chat_id, msg_id, text, reply_markup=kb, parse_mode=None)


def _show_vuln_detail(bot: Any, chat_id: str, msg_id: str, bridge, vuln_id: str,
                      section: int = 0) -> None:
    vulns = bridge.get_vulnerabilities()
    vuln = next((v for v in vulns if v.get("id") == vuln_id), None)
    if not vuln:
        edit_message(
            bot, chat_id, msg_id, "Vulnerabilidad no encontrada.",
            reply_markup=back_to_menu(),
        )
        return

    # Build sections for long content
    sections = []
    severity = vuln.get("severity", "info").upper()
    title = vuln.get("title", "Sin titulo")
    agent = vuln.get("agent_name", "")[:32]
    endpoint = vuln.get("endpoint", "")[:200]
    cvss = vuln.get("cvss", "")
    cvss_breakdown = vuln.get("cvss_breakdown", {})
    cwe = ", ".join(vuln.get("cwe_ids", [])[:5])
    cve = ", ".join(vuln.get("cve_ids", [])[:5])
    method = vuln.get("method", "")
    target = vuln.get("target", "")

    # Header section
    header = [f"VULNERABILIDAD: {title}", f"Severidad: {severity}"]
    if agent:
        header.append(f"Agente: {agent}")
    if method and target:
        header.append(f"{method} {target}")
    elif endpoint:
        header.append(f"Endpoint: {endpoint}")
    if cvss:
        header.append(f"CVSS: {cvss}")
        if cvss_breakdown:
            cvss_parts = ", ".join(f"{k}={v}" for k, v in cvss_breakdown.items())
            header.append(f"  Breakdown: {cvss_parts}")
    if cwe:
        header.append(f"CWE: {cwe}")
    if cve:
        header.append(f"CVE: {cve}")
    header.append("")
    sections.append("\n".join(header))

    # Content sections (each may be long → split if needed)
    desc = vuln.get("description", "")
    impact = vuln.get("impact", "")
    tech = vuln.get("technical_analysis", "")
    evidence = vuln.get("evidence", "")
    poc_desc = vuln.get("poc_description", "")
    poc_code = vuln.get("poc_script_code", "")
    remediation = vuln.get("remediation_steps", "")
    assumptions = vuln.get("assumptions", "")
    fix_effort = vuln.get("fix_effort", "")

    def _add_section(label: str, content: str):
        if not content or not content.strip():
            return
        text = f"{label}:\n{content[:4000]}"
        # Split into 4000-char chunks if needed
        for i in range(0, len(text), 4000):
            sections.append(text[i:i+4000])

    _add_section("DESCRIPCION", desc)
    _add_section("IMPACTO", impact)
    _add_section("ANALISIS TECNICO", tech)
    _add_section("EVIDENCIA", evidence)
    if poc_desc:
        poc_text = f"PoC — Descripcion:\n{poc_desc[:2000]}"
        if poc_code:
            poc_text += f"\n\nPoC — Codigo:\n{poc_code[:2000]}"
        _add_section("", poc_text)
    _add_section("REMEDIACION", remediation)
    _add_section("SUPUESTOS", assumptions)
    if fix_effort:
        _add_section("ESFUERZO DE CORRECCION", fix_effort)

    # Dependency metadata
    dep = vuln.get("dependency_metadata", {}) or {}
    if dep:
        dep_name = dep.get("name", "")
        dep_version = dep.get("version", "")
        if dep_name:
            sections.append(
                f"DEPENDENCIA: {dep_name}" + (f" v{dep_version}" if dep_version else "")
            )

    # Code locations
    locations = vuln.get("code_locations", []) or []
    if locations:
        loc_lines = ["UBICACIONES DE CODIGO:"]
        for loc in locations[:10]:
            if isinstance(loc, dict):
                loc_lines.append(f"  {loc.get('file','?')}:{loc.get('line','?')}")
            else:
                loc_lines.append(f"  {loc}")
        sections.append("\n".join(loc_lines))

    # Side effects
    side_effects = vuln.get("side_effects", []) or []
    if side_effects:
        se_lines = ["EFECTOS COLATERALES:"]
        for se in side_effects[:5]:
            se_lines.append(f"  - {se}")
        sections.append("\n".join(se_lines))

    total_sections = len(sections)
    current = sections[section] if section < total_sections else ""

    if total_sections > 1:
        header_text = f"[{section + 1}/{total_sections}] {title}\n\n{current}"
    else:
        header_text = current

    nav = []
    if section > 0:
        nav.append(_btn("Anterior", _cb("job", "vulndetailpage", vuln_id, str(section - 1))))
    if section + 1 < total_sections:
        nav.append(_btn("Siguiente", _cb("job", "vulndetailpage", vuln_id, str(section + 1))))

    kb_rows = [nav] if nav else []
    kb_rows.append([_btn("Lista", _cb("job", "vulns")), _btn("Scan", _cb("job", "status"))])
    kb = build_inline_keyboard(kb_rows)
    edit_message(bot, chat_id, msg_id, header_text, reply_markup=kb, parse_mode=None)


_show_agent_chat.__module__ = "strix_telegram_bot.commands.jobs"
