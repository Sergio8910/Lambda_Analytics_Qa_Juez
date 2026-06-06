"""CLI interactivo de Prompt Eval.

Pega un prompt en la terminal, opcionalmente respondé un par de preguntas
de contexto (idioma, tools, etc.) y el evaluador escribe el reporte en un
.txt dentro de `outputs/prompt_eval/`.

Uso:
    python -m prompt_eval.cli                 # interactivo
    python -m prompt_eval.cli ruta/prompt.txt # lee el prompt de un archivo
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .evaluator import evaluate_prompt
from .models import (
    DIMENSION_WEIGHTS,
    Dimension,
    PromptEvalRequest,
    PromptEvalResult,
    Severity,
)


# ─── Utilidades de terminal ──────────────────────────────────────────────────


def _force_utf8() -> None:
    """Windows + CMD ahoga con tildes. Forzamos UTF-8 si se puede."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _ask_str(prompt: str, default: str = "") -> str:
    sufijo = f" [{default}]" if default else ""
    val = input(f"  {prompt}{sufijo}: ").strip()
    return val or default


def _ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
    default_str = "Y/n" if default_yes else "y/N"
    raw = input(f"  {prompt} [{default_str}]: ").strip().lower()
    if not raw:
        return default_yes
    return raw in ("y", "yes", "s", "si", "sí")


def _leer_prompt_multilinea() -> str:
    """Lee un prompt multilinea desde stdin.

    El usuario pega el texto, termina con una línea que contenga sólo `EOF`
    (sin espacios). Funciona en Windows, Linux y Mac sin depender de Ctrl-D.
    """
    print()
    print("  Pegá el system prompt a evaluar.")
    print("  Cuando termines, escribí una línea con sólo 'EOF' y enter.")
    print("  " + "─" * 60)
    lineas: List[str] = []
    while True:
        try:
            ln = input()
        except EOFError:
            break
        if ln.strip() == "EOF":
            break
        lineas.append(ln)
    return "\n".join(lineas).strip()


# ─── Render del reporte ──────────────────────────────────────────────────────


_SEV_ORDER = [
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
    Severity.INFO,
]


_VEREDICTO_EMOJI = {
    "excelente": "★★★★★",
    "bueno": "★★★★",
    "aceptable": "★★★",
    "deficiente": "★★",
    "critico": "★",
}


def _render_reporte(res: PromptEvalResult, prompt_original: str) -> str:
    """Construye el texto del reporte que va al .txt."""
    lineas: List[str] = []
    push = lineas.append
    sep = "═" * 72
    sub = "─" * 72

    push(sep)
    push("                       PROMPT EVAL — REPORTE")
    push(sep)
    push(f"Nombre              : {res.nombre or '(sin nombre)'}")
    push(f"Generado            : {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    push(f"Duración            : {res.duracion_ms} ms")
    push(f"LLM judge aplicado  : {'sí' if res.llm_judge_aplicado else 'no'}")
    if res.meta.get("llm", {}).get("model"):
        push(f"LLM modelo          : {res.meta['llm']['model']}")
        if res.meta["llm"].get("total_tokens"):
            push(f"LLM tokens          : {res.meta['llm']['total_tokens']}")
    push(f"Prompt hash         : {res.meta.get('prompt_hash', '?')}")
    push("")

    push(sub)
    push("                          RESULTADO GLOBAL")
    push(sub)
    push(f"  Score global  : {res.score_global:5.1f} / 100  {_VEREDICTO_EMOJI.get(res.veredicto, '')}")
    push(f"  Veredicto     : {res.veredicto.upper()}")
    resumen_sev = ", ".join(
        f"{k}={v}" for k, v in sorted(res.findings_resumen.items())
    ) or "(sin findings)"
    push(f"  Findings      : {len(res.findings)}   ({resumen_sev})")
    push("")

    push(sub)
    push("                       SCORE POR DIMENSIÓN")
    push(sub)
    push(f"  {'Dimensión':<18} {'Score':>6}  {'Peso':>5}  {'Findings':>9}")
    for d in res.dimensiones:
        push(
            f"  {d.dimension.value:<18} {d.score:6.1f}  {d.weight*100:4.0f}%  {d.findings_count:>9}"
        )
    push("")

    push(sub)
    push("                       MÉTRICAS DEL PROMPT")
    push(sub)
    m = res.metricas
    push(f"  Longitud (chars)     : {m.longitud_chars}")
    push(f"  Longitud (palabras)  : {m.longitud_palabras}")
    push(f"  Longitud (líneas)    : {m.longitud_lineas}")
    push(f"  Tokens estimados     : {m.longitud_estimada_tokens}")
    push(f"  Idioma detectado     : {m.idioma_detectado}")
    if m.secciones_detectadas:
        push(f"  Secciones detectadas : {', '.join(m.secciones_detectadas)}")
    if m.placeholders_detectados:
        push(f"  Placeholders         : {', '.join(m.placeholders_detectados)}")
    if m.menciona_tools:
        push(f"  Tools mencionadas    : {', '.join(m.menciona_tools)}")
    push("")

    # Findings agrupados por dimensión, ordenados por severidad
    findings_por_dim: dict = {}
    for f in res.findings:
        findings_por_dim.setdefault(f.dimension, []).append(f)

    push(sub)
    push("                       HALLAZGOS DETALLADOS")
    push(sub)
    if not res.findings:
        push("  (sin hallazgos — el prompt pasó todas las reglas)")
        push("")
    else:
        for dim in Dimension:
            items = findings_por_dim.get(dim, [])
            if not items:
                continue
            push(f"\n  ── {dim.value.upper()} ({len(items)} hallazgo(s)) " + "─" * (40 - len(dim.value)))
            items_ordenados = sorted(items, key=lambda f: _SEV_ORDER.index(f.severity))
            for i, f in enumerate(items_ordenados, 1):
                push(f"\n  [{i}] {f.rule_id} · {f.severity.value.upper()} · {f.titulo}")
                push(f"      Descripción : {f.descripcion}")
                if f.recomendacion:
                    push(f"      Acción      : {f.recomendacion}")
                if f.evidencia:
                    snippet = f.evidencia[:200].replace("\n", " ⏎ ")
                    push(f"      Evidencia   : {snippet}")
        push("")

    push(sub)
    push("                    TOP RECOMENDACIONES (PRIORIZADAS)")
    push(sub)
    if not res.top_recomendaciones:
        push("  (sin recomendaciones priorizadas)")
    else:
        for i, reco in enumerate(res.top_recomendaciones, 1):
            push(f"  {i}. {reco}")
    push("")

    push(sep)
    push("                          PROMPT EVALUADO")
    push(sep)
    push(prompt_original)
    push(sep)
    return "\n".join(lineas)


# ─── Main ────────────────────────────────────────────────────────────────────


def _leer_prompt_de_archivo(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        print(f"  ✗ Archivo no encontrado: {p}")
        sys.exit(2)
    return p.read_text(encoding="utf-8").strip()


def _output_path(nombre: Optional[str]) -> Path:
    out_dir = Path("outputs") / "prompt_eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    slug = (nombre or "prompt").lower()
    slug = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in slug)[:40]
    return out_dir / f"{ts}_{slug}.txt"


def main(argv: Optional[List[str]] = None) -> int:
    _force_utf8()
    argv = list(argv if argv is not None else sys.argv[1:])

    print("\n" + "═" * 60)
    print("  PROMPT EVAL — evaluador interactivo")
    print("═" * 60)

    # 1. Obtener el prompt
    if argv and not argv[0].startswith("-"):
        prompt = _leer_prompt_de_archivo(argv[0])
        print(f"\n  Prompt leído de: {argv[0]} ({len(prompt)} chars)")
    else:
        prompt = _leer_prompt_multilinea()

    if not prompt:
        print("\n  ✗ El prompt está vacío. Abortando.")
        return 2

    # 2. Contexto opcional
    print("\n" + "─" * 60)
    print("  CONTEXTO OPCIONAL (Enter para omitir cada uno)")
    print("─" * 60)
    nombre = _ask_str("Nombre del agente / prompt", "")
    expected_language = _ask_str("Idioma esperado (es/en/pt/fr)", "")
    expected_output_format = _ask_str("Formato esperado (json/markdown/plain/yaml/html)", "")
    tools_raw = _ask_str("Tools (separadas por coma, ej. 'Buscar,Crear')", "")
    domain = _ask_str("Dominio (free text, ej. 'banca')", "")
    incluir_llm = _ask_yes_no("¿Incluir LLM judge?", default_yes=bool(os.getenv("OPENAI_API_KEY")))

    tools = [t.strip() for t in tools_raw.split(",") if t.strip()] if tools_raw else []

    # 3. Evaluar
    print("\n  Evaluando…")
    req = PromptEvalRequest(
        prompt=prompt,
        nombre=nombre or None,
        expected_language=expected_language or None,  # type: ignore[arg-type]
        expected_output_format=expected_output_format or None,  # type: ignore[arg-type]
        tools=tools,
        domain=domain or None,
        incluir_llm_judge=incluir_llm,
    )
    try:
        res = evaluate_prompt(req)
    except Exception as exc:
        print(f"\n  ✗ Error evaluando: {type(exc).__name__}: {exc}")
        return 1

    # 4. Render + persistir
    reporte = _render_reporte(res, prompt)
    out_path = _output_path(nombre)
    out_path.write_text(reporte, encoding="utf-8")

    # 5. Resumen en pantalla
    print()
    print("─" * 60)
    print("  RESULTADO")
    print("─" * 60)
    print(f"  Score global  : {res.score_global:5.1f} / 100")
    print(f"  Veredicto     : {res.veredicto.upper()}")
    print(f"  Findings      : {len(res.findings)}")
    print(f"  LLM judge     : {'sí' if res.llm_judge_aplicado else 'no'}")
    print(f"  Reporte       : {out_path}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
