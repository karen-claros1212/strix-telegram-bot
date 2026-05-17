from __future__ import annotations

from pathlib import Path


def build_instruction(text: str, attachments: list[Path]) -> str:
    lines: list[str] = []
    lines.append("# Strix Scan Instructions")
    lines.append("")
    lines.append("## Contexto")
    if text.strip():
        lines.append(text.strip())
    else:
        lines.append("Sin texto adicional del usuario.")

    if attachments:
        lines.append("")
        lines.append("## Archivos adjuntos")
        for file_path in attachments:
            lines.append(f"- {file_path.name}")
            lines.append(f"  Ruta en sandbox: /workspace/{file_path.name}")

    lines.append("")
    lines.append("## Reglas")
    lines.append("- Respetar alcance solicitado en el mensaje.")
    lines.append("- Si el target es un archivo, analizar su contenido y estructura.")
    lines.append("- Entregar hallazgos con severidad y recomendaciones.")

    return "\n".join(lines)
