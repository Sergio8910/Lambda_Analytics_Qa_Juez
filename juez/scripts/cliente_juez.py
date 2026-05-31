from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Optional


def _prompt(label: str, default: Optional[str] = None) -> str:
    if default is not None:
        raw = input(f"{label} [{default}]: ").strip()
        return raw or default
    return input(f"{label}: ").strip()


def _prompt_int(label: str, default: int) -> int:
    raw = _prompt(label, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _post_json(url: str, payload: dict, api_key: str, timeout_s: int = 120) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-API-KEY": api_key.strip(),
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _upload_rag(base_url: str, api_key: str, rag_path: Path) -> dict:
    if not rag_path.exists() or not rag_path.is_file():
        raise FileNotFoundError(f"No existe archivo RAG: {rag_path}")
    if rag_path.suffix.lower() not in {".json", ".txt"}:
        raise ValueError("El RAG debe ser .json o .txt")

    url = base_url.rstrip("/") + "/v1/upload-rag"
    boundary = f"----JUEZBoundary{uuid.uuid4().hex}"
    mime_type = mimetypes.guess_type(str(rag_path))[0] or "application/octet-stream"
    file_name = rag_path.name
    file_bytes = rag_path.read_bytes()

    body_start = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8")
    body_end = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = body_start + file_bytes + body_end

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-API-KEY": api_key.strip(),
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _save_local_outputs(run_id: str, response: dict) -> tuple[Path, Optional[Path]]:
    out_dir = Path("outputs_client")
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"{run_id}_response.json"
    json_path.write_text(
        json.dumps(response, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_path: Optional[Path] = None
    narrative = response.get("report", {}).get("summary", {}).get("narrative_summary")
    if isinstance(narrative, str) and narrative.strip():
        summary_path = out_dir / f"{run_id}_summary.txt"
        summary_path.write_text(narrative, encoding="utf-8")

    return json_path, summary_path


def main() -> int:
    print("===JUEZ CLIENTE===")
    base_url = _prompt("Base URL", "http://127.0.0.1:8000")
    api_key = _prompt("API_security_key")
    run_id = _prompt("ID", "cliente-api-01")
    prompt_base = _prompt("Prompt")
    rag_input = _prompt("Rag (ruta local opcional, vacio = sin RAG)", "")
    focus = _prompt("Enfoque Deseado (opcional, vacio = ninguno)", "")
    n_cases = _prompt_int("N Casos", 10)

    # Base fija para clientes externos.
    concurrency = 3

    if not api_key.strip():
        print("Error: API_security_key es obligatoria.")
        return 1

    rag_id: Optional[str] = None
    if rag_input.strip():
        try:
            upload = _upload_rag(base_url, api_key, Path(rag_input.strip()))
            rag_id = str(upload.get("rag_id", "")).strip() or None
            print(f"RAG cargado correctamente. rag_id={rag_id}")
        except Exception as exc:
            print(f"Error subiendo RAG: {exc}")
            return 1

    payload = {
        "run_id": run_id,
        "prompt_base": prompt_base,
        "n_cases": n_cases,
        "seed": 123,
        "include_summary": True,
        "save_outputs": True,
        "concurrency": concurrency,
    }
    if rag_id:
        payload["rag_id"] = rag_id
    if focus.strip():
        payload["focus"] = focus.strip()

    # El tiempo crece con n_cases y métricas LLM; evitamos timeout prematuro del cliente.
    timeout_s = max(180, n_cases * 40)
    print(f"Ejecutando evaluación... (timeout cliente: {timeout_s}s)")

    try:
        response = _post_json(
            base_url.rstrip("/") + "/v1/auto-evaluate",
            payload,
            api_key,
            timeout_s=timeout_s,
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"Error HTTP {exc.code}: {detail}")
        return 1
    except Exception as exc:
        print(f"Error ejecutando auto-evaluate: {exc}")
        return 1

    report = response.get("report", {})
    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    exec_summary = summary.get("executive_summary", {}) if isinstance(summary, dict) else {}
    by_metric = summary.get("by_metric_failures", {}) if isinstance(summary, dict) else {}
    recommendations = summary.get("recommendations", []) if isinstance(summary, dict) else []

    pass_rate = summary.get("pass_rate")
    if isinstance(pass_rate, (float, int)):
        pass_rate_text = f"{float(pass_rate) * 100:.2f}%"
    else:
        pass_rate_text = "n/a"

    top_metric = "n/a"
    if isinstance(by_metric, dict) and by_metric:
        top_metric = sorted(by_metric.items(), key=lambda x: x[1], reverse=True)[0][0]

    json_path, summary_path = _save_local_outputs(run_id, response)

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
    if isinstance(recommendations, list) and recommendations:
        print("")
        print("Recomendaciones prioritarias:")
        for rec in recommendations[:3]:
            print(f"- {rec}")

    print("")
    print("Archivos cliente guardados:")
    print(f"- {json_path}")
    if summary_path:
        print(f"- {summary_path}")
    else:
        print("- (sin resumen narrativo local)")
    print("Archivos servidor guardados en outputs/ del servidor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
