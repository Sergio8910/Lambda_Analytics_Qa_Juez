from __future__ import annotations

import argparse
import json
import os
import io
import contextlib
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Carga variables de entorno antes de importar DeepEval.
load_dotenv()

# Desactiva telemetria antes de cargar DeepEval (si aplica).
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
os.environ.setdefault("DEEPEVAL_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DEEPEVAL_TELEMETRY", "false")

from .case_factory import build_cases
from .case_generator import generate_cases
from .metamorphic import build_variants
from .report_models import (
    ContraAgentSpec,
    EvaluationSpec,
    InstructionPolicy,
    TaskContract,
    TestCase,
)
from .report_writer import pretty_print_summary
from .utils_json import render_case_json, render_run_json
from .runner import run_agent
from .utils.text_normalization import repair_recursive
from .api_schema import build_api_report
from .contracts import RunnerResult
from .reporting.forensic import build_forensic_report


def _load_payload(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No se encontro el archivo de spec: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return repair_recursive(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON invalido en {path}: {exc}") from exc


def _build_cases_from_payload(spec: EvaluationSpec, payload: dict) -> List[TestCase]:
    if "cases" in payload:
        return [TestCase(**c) for c in payload.get("cases", [])]
    return build_cases(spec)


def _requires_llm_metrics(spec: EvaluationSpec) -> bool:
    llm_metrics = {
        "answer_relevancy",
        "instruction_adherence",
        "task_success",
        "faithfulness",
        "contextual_precision",
        "hallucination",
    }
    return any(m.enabled and m.name in llm_metrics for m in spec.metrics)


def _build_eval_spec_from_contra(contra: ContraAgentSpec) -> EvaluationSpec:
    instruction_policy = InstructionPolicy(
        language=contra.language,
        no_markdown=contra.output_contract.no_markdown,
    )
    task_contract = TaskContract(
        must_include=contra.output_contract.must_include,
        must_not_include=contra.output_contract.must_not_include,
        require_clarifying_question_if_ambiguous=(
            contra.output_contract.require_clarifying_question_if_ambiguous
        ),
        output_format=contra.output_contract.output_format,
        json_schema=contra.output_contract.json_schema,
        language=contra.output_contract.language,
        truth_source=contra.output_contract.truth_source,
    )
    return EvaluationSpec(
        run_id=contra.run_id,
        mode="deterministic",
        num_tests=contra.num_cases,
        seed=contra.seed,
        prompt_base=contra.prompt_base,
        metrics=contra.judge_profile.metrics,
        grading_mode=contra.grading_mode,
        gating_metrics=contra.gating_metrics,
        diagnostic_metrics=contra.diagnostic_metrics,
        instruction_policy=instruction_policy,
        task_contract_default=task_contract,
        allow_empty_retrieval=contra.rag.allow_empty_retrieval,
        enable_metamorphic=False,
        metamorphic_variants_per_case=0,
        latency_budget_ms=contra.latency_budget_ms,
        llm_preflight=contra.judge_profile.llm_preflight,
        llm_metric_timeout_s=contra.judge_profile.llm_metric_timeout_s,
        llm_fail_fast_on_infra=contra.judge_profile.llm_fail_fast_on_infra,
        llm_config=contra.llm_config,
        agent_type=contra.agent_type,
        scorecard_weights=contra.scorecard_weights,
        scorecard_gates=contra.scorecard_gates,
        anti_gaming_config=contra.anti_gaming_config,
        audit_mode=contra.audit_mode,
        scorecard_min_pass_rate=contra.scorecard_min_pass_rate,
        reliability_min=contra.reliability_min,
    )


def _print_generated_cases(cases: List[TestCase], stats: dict) -> None:
    print("Casos generados:")
    for c in cases:
        rc = f" | retrieval_context={len(c.retrieval_context)}" if c.retrieval_context else ""
        print(f"- {c.case_id} | tags={c.tags} | input={c.input}{rc}")
    print("Estadisticas de generacion:")
    for k, v in sorted(stats.items(), key=lambda x: x[0]):
        print(f"- {k}: {v}")


def _print_debug_case(
    spec: EvaluationSpec,
    cases: List[TestCase],
    report,
    case_id: str,
    verbose: bool,
) -> None:
    report_case = next((c for c in report.cases if c.case_id == case_id), None)
    case = next((c for c in cases if c.case_id == case_id), None)
    if not report_case or not case:
        print(f"No se encontro el caso {case_id}.")
        return
    rr = run_agent(spec, case)
    retrieval_count = len(case.retrieval_context or case.context or [])
    system_prompt_injected = bool(spec.prompt_base)
    gating = spec.gating_metrics
    if spec.grading_mode == "rubric" and not gating:
        gating = [
            "task_success_deterministic",
            "unsupported_claims",
            "format_compliance",
            "latency_budget",
        ]
    if gating:
        enabled = {m.name for m in spec.metrics if m.enabled}
        gating = [g for g in gating if g in enabled]
    llm_metrics = {
        "answer_relevancy",
        "instruction_adherence",
        "task_success",
        "faithfulness",
        "contextual_precision",
        "hallucination",
    }
    passed_llm = all(m.success for m in report_case.metrics if m.name in llm_metrics)
    passed_rubric = all(m.success for m in report_case.metrics if m.name in (gating or [])) if gating else report_case.passed
    print("Debug de caso:")
    print(f"- case_id: {case.case_id}")
    print(f"- tags: {case.tags}")
    print(f"- severity: {case.severity}")
    print(f"- grading_mode: {spec.grading_mode}")
    if gating:
        print(f"- gating_metrics: {gating}")
    if spec.grading_mode == "rubric":
        print(f"- passed_llm: {passed_llm}")
        print(f"- passed_rubric: {passed_rubric}")
    print(f"- retrieval_context_count: {retrieval_count}")
    print(f"- system_prompt_inyectado: {system_prompt_injected}")
    print("agent_output:")
    print(rr.output_text)
    if report_case.turns:
        for t in report_case.turns:
            print(f"Turno {t.turn_index} | input={t.user_input}")
            print(f"output={t.agent_output}")
            print(f"metrics={len(t.metrics)}")
    else:
        print(f"metrics={len(report_case.metrics)}")
        for m in report_case.metrics:
            if m.reason_es:
                print(f"* {m.name} success={m.success} score={m.score} reason_es={m.reason_es}")
                print(f"  reason={m.reason or m.error}")
            else:
                print(f"* {m.name} success={m.success} score={m.score} reason={m.reason or m.error}")
        det = next((m for m in report_case.metrics if m.name == "task_success_deterministic"), None)
        if det:
            print(
                f"task_success_deterministic: score={det.score} success={det.success} reason={det.reason}"
            )
        if gating:
            print("gating_metrics_resultado:")
            for m in report_case.metrics:
                if m.name in gating:
                    print(
                        f"- {m.name} score={m.score} threshold={m.threshold} success={m.success}"
                    )
    if not verbose:
        return
    if report_case.feedback:
        print("feedback.overall.primary_fail_reasons:")
        for r in report_case.feedback.overall.primary_fail_reasons[:3]:
            print(f"- {r}")
        print("feedback.prompt_improvement.summary:")
        for s in report_case.feedback.prompt_improvement.summary:
            print(f"- {s}")
        edits = report_case.feedback.prompt_improvement.suggested_edits
        if edits:
            print("feedback.prompt_improvement.edits:")
            p0 = [e for e in edits if e.priority == "P0"]
            show = p0[:2] if p0 else edits[:2]
            for e in show:
                print(f"- {e.priority} | {e.edit_type} | {e.proposed_text}")
        if report_case.feedback.question_by_question:
            print("feedback.question_by_question:")
            for q in report_case.feedback.question_by_question[:3]:
                print(
                    f"- pregunta={q.question} | answered={q.answered} | verdict={q.verdict} "
                    f"| snippet={q.answer_snippet[:80]}"
                )
        if report_case.feedback.rag_audit and report_case.feedback.rag_audit.contradictions:
            print("feedback.rag_audit.contradictions:")
            for c in report_case.feedback.rag_audit.contradictions[:3]:
                print(f"- {c}")
        if report_case.feedback.rag_audit:
            print("feedback.rag_audit.summary:")
            for s in report_case.feedback.rag_audit.summary[:3]:
                print(f"- {s}")
            if report_case.feedback.rag_audit.issues:
                print("feedback.rag_audit.issues:")
                for i in report_case.feedback.rag_audit.issues[:3]:
                    snippet = (i.output_snippet or "").replace("\n", " ")
                    print(
                        f"- type={i.type} question_ref={i.question_ref} snippet={snippet[:80]}"
                    )
        if report_case.feedback.prompt_improvement.prompt_patch.text:
            print("feedback.prompt_patch.text:")
            print(report_case.feedback.prompt_improvement.prompt_patch.text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ejecutor local del evaluador.")
    parser.add_argument("--spec", required=True, help="Ruta al JSON de spec.")
    parser.add_argument("--out", required=True, help="Ruta de salida del reporte JSON.")
    parser.add_argument("--debug-case", default="", help="Imprime detalle de un caso por ID.")
    parser.add_argument(
        "--debug-case-verbose",
        action="store_true",
        help="Imprime debug textual detallado sin duplicar secciones.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Alias de --print-run-json (imprime el RunReport completo en JSON).",
    )
    parser.add_argument(
        "--print-run-json",
        action="store_true",
        help="Imprime el RunReport completo en JSON a stdout.",
    )
    parser.add_argument(
        "--print-case-json",
        default="",
        help="Imprime un CaseReport en JSON (por case_id).",
    )
    parser.add_argument(
        "--json-indent",
        type=int,
        default=2,
        help="Indentación para JSON en stdout.",
    )
    parser.add_argument(
        "--json-redact-secrets",
        action="store_true",
        default=True,
        help="Redacta secretos en JSON impreso por consola.",
    )
    parser.add_argument(
        "--no-json-redact-secrets",
        dest="json_redact_secrets",
        action="store_false",
        help="No redactar secretos en JSON impreso por consola.",
    )
    parser.add_argument(
        "--print-generated",
        action="store_true",
        help="Imprime los casos generados (solo modo contra_agent).",
    )
    parser.add_argument(
        "--dump-normalized-run",
        action="store_true",
        help="Incluye normalized_run por caso en el JSON final.",
    )
    parser.add_argument(
        "--api-out",
        default="",
        help="Ruta de salida para modo API (JSON estable).",
    )
    parser.add_argument(
        "--pdf-out",
        default="",
        help="Ruta de salida para PDF ejecutivo forense.",
    )
    parser.add_argument(
        "--agent-type",
        default="",
        help="Override de agent_type para scorecard.",
    )
    parser.add_argument(
        "--audit-mode",
        choices=["balanced", "enterprise"],
        default="",
        help="Modo de auditorÃ­a (balanced o enterprise).",
    )
    parser.add_argument(
        "--scorecard-config",
        default="",
        help="Ruta JSON con scorecard_weights y scorecard_gates.",
    )
    parser.add_argument(
        "--autogen",
        action="store_true",
        help="Genera casos automáticamente y evalúa por HTTP.",
    )
    parser.add_argument(
        "--prompt-base-file",
        default="",
        help="Ruta a archivo con prompt_base para autogen.",
    )
    parser.add_argument(
        "--agent-http-url",
        default="",
        help="URL del agente externo por HTTP.",
    )
    parser.add_argument(
        "--n-cases",
        type=int,
        default=30,
        help="Número de casos autogen (1-50).",
    )
    parser.add_argument(
        "--strict-infra",
        action="store_true",
        help="Si está activo, fallos de infraestructura LLM sí fallan el caso.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="No imprime logs de progreso; solo JSON final si corresponde.",
    )
    args = parser.parse_args()

    payload = _load_payload(Path(args.spec))
    contra_block = payload.get("contra_agent")
    if isinstance(contra_block, dict) and contra_block.get("enabled", False):
        contra_data = dict(contra_block)
        contra_data.pop("enabled", None)
        contra_spec = ContraAgentSpec(**contra_data)
        os.environ.setdefault("EVAL_MODEL", contra_spec.judge_profile.judge_model)
        spec = _build_eval_spec_from_contra(contra_spec)
        cases, stats = generate_cases(contra_spec)
        if args.print_generated and not args.quiet:
            _print_generated_cases(cases, stats.by_category)
    else:
        spec_data = payload.get("spec", payload)
        spec = EvaluationSpec(**spec_data)
        cases = _build_cases_from_payload(spec, payload)
        if spec.cases_file:
            cases_path = Path(spec.cases_file)
            if not cases_path.is_absolute():
                cases_path = Path(cases_path)
            cases_payload = _load_payload(cases_path)
            if isinstance(cases_payload, list):
                cases = [TestCase(**c) for c in cases_payload]
            elif isinstance(cases_payload, dict) and "cases" in cases_payload:
                cases = [TestCase(**c) for c in cases_payload.get("cases", [])]

    if args.agent_type:
        spec.agent_type = args.agent_type
    if args.audit_mode:
        spec.audit_mode = args.audit_mode
    if args.scorecard_config:
        sc_payload = _load_payload(Path(args.scorecard_config))
        spec.scorecard_weights = sc_payload.get("scorecard_weights", spec.scorecard_weights)
        spec.scorecard_gates = sc_payload.get("scorecard_gates", spec.scorecard_gates)

    if _requires_llm_metrics(spec) and not os.getenv("OPENAI_API_KEY"):
        raise SystemExit(
            "OPENAI_API_KEY no esta configurada. Revisa tu .env o variables de entorno."
        )
    if _requires_llm_metrics(spec) and spec.llm_preflight:
        from .preflight_openai import run_preflight

        if run_preflight() != 0:
            raise SystemExit(
                "No hay conectividad con OpenAI. Revisa red/proxy/VPN/SSL."
            )

    if args.strict_infra:
        spec.strict_infra = True

    if args.autogen:
        prompt_base = spec.prompt_base
        if args.prompt_base_file:
            prompt_base = Path(args.prompt_base_file).read_text(encoding="utf-8")
        if not prompt_base:
            raise SystemExit("prompt_base requerido para --autogen.")

        if args.agent_http_url:
            from .autogen.prompt_analyzer import analyze_prompt
            from .autogen.context_synth import synthesize_context
            from .autogen.case_generator import build_cases as build_autogen_cases
            from .autogen.agent_client import AgentHttpClient
            from .core.engine import EvaluationEngine

            _ = analyze_prompt(prompt_base)
            nodes = synthesize_context(seed=spec.seed, n_nodes=6)
            cases, context_map = build_autogen_cases(
                prompt_base=prompt_base,
                retrieval_nodes=nodes,
                n_cases=max(1, min(50, args.n_cases)),
                seed=spec.seed,
            )
            client = AgentHttpClient(
                args.agent_http_url, headers={}, timeout_ms=int(spec.agent_timeout_s * 1000)
            )

            def _runner(tc: TestCase):
                ctx_nodes = context_map.get(tc.case_id, nodes)
                result = client.call(
                    {
                        "input": tc.input,
                        "prompt_base": prompt_base,
                        "retrieval_context": ctx_nodes,
                        "case_id": tc.case_id,
                    }
                )
                return RunnerResult(
                    output_text=result.output,
                    retrieval_context=[n["text"] for n in ctx_nodes],
                    latency_ms=result.latency_ms,
                    error=result.error,
                )

            engine = EvaluationEngine(spec)
            if args.quiet:
                with contextlib.redirect_stdout(io.StringIO()):
                    report = engine.evaluate_run(
                        cases, _runner, dump_normalized_run=args.dump_normalized_run
                    )
            else:
                report = engine.evaluate_run(
                    cases, _runner, dump_normalized_run=args.dump_normalized_run
                )
        else:
            from .autogen import run_auto_eval

            report = run_auto_eval(
                prompt_base=prompt_base,
                metrics=spec.metrics,
                n_cases=max(1, min(50, args.n_cases)),
                seed=spec.seed,
                run_id=spec.run_id,
            )
    elif spec.enable_metamorphic:
        all_cases: List[TestCase] = []
        for tc in cases:
            all_cases.append(tc)
            all_cases.extend(build_variants(tc, spec.metamorphic_variants_per_case, spec.seed))
        cases = all_cases
        from .core.engine import EvaluationEngine

        engine = EvaluationEngine(spec)
        if args.quiet:
            with contextlib.redirect_stdout(io.StringIO()):
                report = engine.evaluate_run(
                    cases, lambda x: run_agent(spec, x), dump_normalized_run=args.dump_normalized_run
                )
        else:
            report = engine.evaluate_run(
                cases, lambda x: run_agent(spec, x), dump_normalized_run=args.dump_normalized_run
            )
    forensic_report = build_forensic_report(report, spec, spec.audit_mode, args.spec)
    # Serializa manteniendo el schema original de RunReport
    report_dict = report.model_dump()
    if hasattr(report.summary, "to_dict"):
        report_dict["summary"] = report.summary.to_dict()
    summary_dict = report_dict.get("summary", {})
    if isinstance(summary_dict, dict):
        summary_dict["forensic_report"] = forensic_report
        report_dict["summary"] = summary_dict
    report_dict = repair_recursive(report_dict)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(report_dict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.pdf_out:
        from .reporting.forensic import render_forensic_pdf

        render_forensic_pdf(forensic_report, args.pdf_out)
    if args.api_out:
        api_report = build_api_report(report)
        api_path = Path(args.api_out)
        api_path.parent.mkdir(parents=True, exist_ok=True)
        api_path.write_text(
            json.dumps(api_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    suppress_summary = args.print_run_json or args.print_json
    if not args.quiet and not suppress_summary:
        pretty_print_summary(report)
    if not args.quiet and args.debug_case and (not args.print_case_json or args.debug_case_verbose):
        _print_debug_case(spec, cases, report, args.debug_case, args.debug_case_verbose)

    if args.print_case_json:
        try:
            case_json = render_case_json(
                report,
                args.print_case_json,
                indent=args.json_indent,
                redact=args.json_redact_secrets,
            )
        except KeyError as exc:
            print(str(exc))
            return 2
        print("===BEGIN_JSON===")
        print(case_json)
        print("===END_JSON===")
        return 0

    if args.print_run_json or args.print_json:
        run_json = render_run_json(
            report, indent=args.json_indent, redact=args.json_redact_secrets
        )
        print("===BEGIN_JSON===")
        print(run_json)
        print("===END_JSON===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
