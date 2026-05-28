from __future__ import annotations

from typing import Any, Dict, List

import json


def _serialize_dimension_evidence(ev: Any) -> Dict[str, Any]:
    if isinstance(ev, dict):
        return {
            "metric": ev.get("name") or ev.get("metric") or ev.get("metric_name"),
            "score": ev.get("score"),
            "success": ev.get("success"),
            "std_dev": ev.get("std_dev"),
        }
    return {
        "metric": getattr(ev, "name", None) or getattr(ev, "metric_name", None),
        "score": getattr(ev, "score", None),
        "success": getattr(ev, "success", None),
        "std_dev": getattr(ev, "std_dev", None),
    }


def _serialize_dimensions(dimensions: Any) -> Dict[str, Any]:
    if not dimensions:
        return {}
    out: Dict[str, Any] = {}
    for key, val in dimensions.items():
        if isinstance(val, dict):
            evidence = val.get("evidence", [])
            out[key] = {
                "score": val.get("score"),
                "evidence": [_serialize_dimension_evidence(e) for e in evidence],
                "notes": list(val.get("notes") or []),
            }
        else:
            evidence = getattr(val, "evidence", []) or []
            out[key] = {
                "score": getattr(val, "score", None),
                "evidence": [_serialize_dimension_evidence(e) for e in evidence],
                "notes": list(getattr(val, "notes", []) or []),
            }
    return out


def _serialize_scorecard(scorecard: Any) -> Dict[str, Any]:
    if not scorecard:
        return {}
    if isinstance(scorecard, dict):
        return {
            "overall_score": scorecard.get("overall_score"),
            "weights": dict(scorecard.get("weights") or {}),
            "eligible_dimensions": list(scorecard.get("eligible_dimensions") or []),
            "scorecard_passed": scorecard.get("scorecard_passed"),
            "gates": dict(scorecard.get("gates") or {}),
            "notes": list(scorecard.get("notes") or []),
        }
    return {
        "overall_score": getattr(scorecard, "overall_score", None),
        "weights": dict(getattr(scorecard, "weights", {}) or {}),
        "eligible_dimensions": list(getattr(scorecard, "eligible_dimensions", []) or []),
        "scorecard_passed": getattr(scorecard, "scorecard_passed", None),
        "gates": dict(getattr(scorecard, "gates", {}) or {}),
        "notes": list(getattr(scorecard, "notes", []) or []),
    }


def _serialize_anti_gaming(anti: Any) -> Dict[str, Any]:
    if not anti:
        return {}
    if isinstance(anti, dict):
        flags = anti.get("flags") or []
        penalty = anti.get("penalty")
        return {
            "flags": [
                {
                    "code": f.get("code"),
                    "severity": f.get("severity"),
                    "reason": f.get("reason"),
                }
                if isinstance(f, dict)
                else {
                    "code": getattr(f, "code", None),
                    "severity": getattr(f, "severity", None),
                    "reason": getattr(f, "reason", None),
                }
                for f in flags
            ],
            "penalty": penalty if isinstance(penalty, dict) else penalty,
            "notes": list(anti.get("notes") or []),
        }
    flags = getattr(anti, "flags", []) or []
    penalty = getattr(anti, "penalty", None)
    return {
        "flags": [
            {
                "code": getattr(f, "code", None),
                "severity": getattr(f, "severity", None),
                "reason": getattr(f, "reason", None),
            }
            for f in flags
        ],
        "penalty": penalty,
        "notes": list(getattr(anti, "notes", []) or []),
    }


def ensure_json_safe(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return obj
    except TypeError as exc:
        raise RuntimeError("API output contains non-serializable objects") from exc

from .report_models import RunReport


def build_api_report(report: RunReport) -> Dict[str, Any]:
    cases = []
    for case in report.cases:
        metrics = {m.name: m.model_dump(mode="json") for m in case.metrics}
        cases.append(
            {
                "case_id": case.case_id,
                "agent_type": case.agent_type,
                "metrics": metrics,
                "dimensions": _serialize_dimensions(case.dimensions),
                "scorecard": _serialize_scorecard(case.scorecard),
                "anti_gaming": _serialize_anti_gaming(case.anti_gaming),
                "gating_metrics_resultado": case.gating_metrics_resultado or [],
            }
        )
    api_report = {
        "run_id": report.summary.run_id,
        "cases": cases,
        "summary": report.summary.to_dict()
        if hasattr(report.summary, "to_dict")
        else report.summary.model_dump(mode="json"),
    }
    return ensure_json_safe(api_report)
