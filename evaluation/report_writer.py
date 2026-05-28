from __future__ import annotations

import json
from pathlib import Path

from .report_models import RunReport
from .utils.text_normalization import repair_recursive


def save_json(report: RunReport, path: str) -> None:
    ruta = Path(path)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8") as f:
        data = report.model_dump()
        if hasattr(report.summary, "to_dict"):
            data["summary"] = report.summary.to_dict()
        data = repair_recursive(data)
        json.dump(data, f, ensure_ascii=False, indent=2)


def pretty_print_summary(report: RunReport) -> None:
    resumen = report.summary
    print("Resumen de ejecución")
    print(f"- Run ID: {resumen.run_id}")
    print(f"- Casos totales: {resumen.total_cases}")
    print(f"- Casos OK: {resumen.passed_cases}")
    print(f"- Casos fallidos: {resumen.failed_cases}")
    print(f"- Pass rate: {resumen.pass_rate:.2f}")
    if resumen.by_metric_failures:
        print("Fallos por métrica:")
        for k, v in sorted(resumen.by_metric_failures.items(), key=lambda x: x[1], reverse=True):
            print(f"  {k}: {v}")
    if resumen.by_tag_failures:
        print("Fallos por etiqueta:")
        for k, v in sorted(resumen.by_tag_failures.items(), key=lambda x: x[1], reverse=True):
            print(f"  {k}: {v}")
    if resumen.by_tag_pass_rate:
        print("Pass rate por etiqueta:")
        for k, v in sorted(resumen.by_tag_pass_rate.items(), key=lambda x: x[0]):
            print(f"  {k}: {v:.2f}")
    if resumen.skipped_by_metric:
        print("Métricas omitidas (skipped):")
        for k, v in sorted(resumen.skipped_by_metric.items(), key=lambda x: x[1], reverse=True):
            print(f"  {k}: {v}")
    if resumen.recommendations:
        print("Recomendaciones:")
        for r in resumen.recommendations:
            print(f"- {r}")
