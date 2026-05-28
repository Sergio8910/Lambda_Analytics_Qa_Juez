from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


def _prompt(label: str, default: Optional[str] = None) -> str:
    if default is not None:
        text = input(f"{label} [{default}]: ").strip()
        return text or default
    return input(f"{label}: ").strip()


def _prompt_int(label: str, default: int) -> int:
    raw = _prompt(label, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 3]


def _score_rag(prompt: str, rag_text: str) -> int:
    return len(set(_tokenize(prompt)) & set(_tokenize(rag_text)))


def _list_rags() -> list[str]:
    base = Path("RAGs")
    if not base.exists():
        return []
    return sorted(
        [
            str(p.relative_to(base))
            for p in base.rglob("*")
            if p.is_file() and p.suffix.lower() in {".json", ".txt"}
        ]
    )


def _resolve_rag_input(value: str) -> Optional[str]:
    raw = (value or "").strip()
    if not raw:
        return None

    base = Path("RAGs").resolve()
    candidate = Path(raw)
    if candidate.exists():
        full = candidate.resolve()
        if str(full).startswith(str(base)):
            return str(full.relative_to(base))
        return None

    name = raw
    if not name.endswith((".json", ".txt")):
        name_candidates = [f"{name}.json", f"{name}.txt"]
    else:
        name_candidates = [name]

    matches: list[Path] = []
    for p in base.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in {".json", ".txt"}:
            continue
        for cand in name_candidates:
            if p.name.lower() == cand.lower():
                matches.append(p)
                break

    if not matches:
        return None
    if len(matches) == 1:
        return str(matches[0].relative_to(base))

    print("Se encontraron varios RAGs con ese nombre:")
    for i, m in enumerate(matches, start=1):
        print(f"{i}) {m.relative_to(base)}")
    sel = _prompt_int("Elige numero", 1)
    sel = max(1, min(len(matches), sel))
    return str(matches[sel - 1].relative_to(base))


def _post_json(url: str, payload: dict, api_key: str) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if api_key.strip():
        headers["X-API-KEY"] = api_key.strip()

    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def main() -> int:
    print("===JUEZ===")

    base_url = _prompt("Base URL", "http://127.0.0.1:8000")
    api_key = _prompt("API_security_key")
    run_id = _prompt("ID", "auto-cli-01")
    prompt_base = _prompt("Prompt")

    rag_file = ""
    rags = _list_rags()
    if rags:
        print("RAGs disponibles (en RAGs/ y subcarpetas):")
        for name in rags:
            print(f"- {name}")
        while True:
            raw_rag = _prompt("Rag (opcional, vacio = sin RAG)", "")
            resolved = _resolve_rag_input(raw_rag)
            if raw_rag.strip() == "":
                rag_file = ""
                break
            if resolved:
                rag_file = resolved
                break
            print("No se encontro ese RAG. Escribe nombre o ruta relativa dentro de RAGs/.")
    else:
        print("No hay RAGs disponibles; se evaluara solo con prompt.")

    focus = _prompt("Enfoque Deseado (opcional, vacio = ninguno)", "")
    n_cases = _prompt_int("N° Casos", 10)
    # Base fija para acelerar sin exponer este parametro en el input.
    concurrency = 3

    evaluation_profile = "rag_quality" if rag_file else "balanced"

    payload = {
        "run_id": run_id,
        "prompt_base": prompt_base,
        "n_cases": n_cases,
        "include_summary": True,
        "evaluation_profile": evaluation_profile,
        "concurrency": concurrency,
    }
    if rag_file:
        payload["rag_file"] = rag_file
    if focus.strip():
        payload["focus"] = focus.strip()

    url = base_url.rstrip("/") + "/v1/auto-evaluate"
    try:
        resp = _post_json(url, payload, api_key=api_key)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"Error HTTP {exc.code}: {body}")
        return 1
    except Exception as exc:
        print(f"Error al llamar la API: {exc}")
        return 1

    report = resp.get("report", {})
    summary = report.get("summary", {}) or {}
    exec_summary = summary.get("executive_summary", {}) or {}
    by_metric = summary.get("by_metric_failures", {}) or {}
    recommendations = summary.get("recommendations", []) or []

    pass_rate = summary.get("pass_rate")
    if isinstance(pass_rate, (float, int)):
        pass_rate_text = f"{float(pass_rate) * 100:.2f}%"
    else:
        pass_rate_text = "n/a"

    top_metric = "n/a"
    if by_metric:
        top_metric = sorted(by_metric.items(), key=lambda x: x[1], reverse=True)[0][0]

    print("")
    print("===RESUMEN===")
    print(f"Run ID: {summary.get('run_id', run_id)}")
    print(f"Total casos: {summary.get('total_cases', 'n/a')}")
    print(f"Casos OK: {summary.get('passed_cases', 'n/a')}")
    print(f"Casos fallidos: {summary.get('failed_cases', 'n/a')}")
    print(f"Pass rate: {pass_rate_text}")
    print(f"Confiabilidad: {summary.get('reliability_score', 'n/a')}")
    print(f"Veredicto ejecutivo: {exec_summary.get('verdict', 'n/a')}")
    print(f"Riesgo: {exec_summary.get('risk_level', 'n/a')}")
    print(f"Metrica con mas fallos: {top_metric}")

    if recommendations:
        print("")
        print("Recomendaciones prioritarias:")
        for rec in recommendations[:3]:
            print(f"- {rec}")

    print("")
    print("Archivos generados en outputs/:")
    print(f"- {run_id}.json")
    print(f"- {run_id}_api.json")
    print(f"- {run_id}_summary.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
