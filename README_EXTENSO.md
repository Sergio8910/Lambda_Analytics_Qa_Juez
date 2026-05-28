# Juez — README Extenso (Arquitectura y Funcionamiento)

Este documento describe en detalle la arquitectura, los módulos, las funciones y el flujo completo del proyecto **Juez**. Está pensado para mantenimiento, auditoría y onboarding.

## Resumen ejecutivo
Juez es un **sistema de evaluación de agentes LLM** que:
1. Ejecuta casos de prueba contra un agente objetivo.
2. Normaliza la salida del agente a un formato estándar.
3. Evalúa con métricas LLM (DeepEval) y heurísticas determinísticas.
4. Produce reportes detallados JSON (y opcionalmente PDF forense).
5. Permite autogeneración de casos (autogen) sin LLM.
6. Expone endpoints API internos para evaluación y generación.

## Capas principales
1. **Core Engine**: orquesta ejecución, métricas y reportes.
2. **Adapters/Runner**: integra agentes y normaliza outputs.
3. **Métricas y Registry**: catálogo único para ejecución.
4. **Generación de casos**: golden, contra-agent, autogen.
5. **Feedback profesor**: recomendaciones accionables.
6. **Scorecard / Fase 2**: dimensiones, anti-gaming, executive summary.
7. **Reporting**: JSON forense y PDF opcional.
8. **API**: endpoints internos (FastAPI).

---

# 1) Core Engine

## `evaluation/core/engine.py`
- **`EvaluationEngine`**: orquestador principal. Dada una `EvaluationSpec`:
  - Carga casos.
  - Ejecuta el runner.
  - Ejecuta métricas (vía registry).
  - Aplica gating.
  - Genera summary y reportes.

## `evaluation/core/engine_impl.py`
Funciones internas de la evaluación:
- `_has_clarifying_question()`: detecta si el agente preguntó para aclarar.
- `_output_uses_context()`: heurística de uso real del contexto.
- `_is_ambiguous_input()`: detecta ambigüedad en input.
- `_quantize()`: cuantiza scores para consistencia.
- `sanitize_encoding()`: limpieza de encoding (legacy).
- `_sanitize_list()`: sanitiza listas de strings.
- `_is_success()`: comparación score/threshold (float-safe).
- `_is_infra_error()`: detecta errores de infraestructura.
- `_infra_skip_reason()`: etiqueta razón del skip infra.
- `_english_ratio()`: ratio de inglés en texto.
- `_translate_reason()`: traducción determinística reason->reason_es.
- `_metric_worker()`: ejecuta métrica con retries/timeout/average_runs.
- `_tokenizar()`: tokenización básica.
- `_normalizar()`: normalización de texto.
- `_normalizar_claim()`: normalización de claims.
- `extract_claims()`: extrae afirmaciones del output.
- `score_claims_against_context()`: evalúa claims vs contexto.
- **`JudgeEngine`**: wrapper de compatibilidad que delega al engine actual.

## `evaluation/core/models.py`
Modelo de **NormalizedRun** (input universal):
- `AgentInfo`: metadata del agente (kind, name, version).
- `CaseInfo`: metadata del caso.
- `ConversationTurn`: turno de conversación.
- `InputInfo`: input normalizado.
- `ContextInfo`: contexto disponible (retrieval, tools).
- `ExecutionTrace`: latencia, tokens, etc.
- `ExecutionInfo`: output_text/json y tool calls.
- `ContractInfo`: contrato del output.
- `NormalizedRun`: objeto completo, base para métricas.

## `evaluation/core/contracts.py`
Contratos por defecto y por tag:
- `resolve_contract()`: combina contrato default + overrides.
- `to_contract_info()`: convierte TaskContract a ContractInfo.

---

# 2) Specs y modelos de reporte

## `evaluation/report_models.py`
Incluye todos los modelos Pydantic principales:
- `EvaluationSpec`: configuración general.
- `MetricSpec`: define métrica (nombre, threshold, config).
- `TestCase`: representa cada caso.
- `MetricResult`: resultado por métrica.
- `ClaimAnalysis`, `PromptImprovement`, `RagAudit`, `CaseFeedback`.
- `CaseReport`: reporte completo por caso.
- `RunSummary`, `RunReport`: resumen y reporte total.

## `evaluation/report_writer.py`
Funciones:
- `save_json()`: guarda reporte JSON.
- `pretty_print_summary()`: imprime resumen humano.

---

# 3) Runner y normalización

## `evaluation/contracts.py`
Modelos runtime para ejecución:
- `ToolCall`, `Usage`.
- `AgentEnvelope`: salida normalizada universal.
- `RunnerResult`: resultado del runner.

## `evaluation/normalize.py`
Normalización universal del output:
- `_as_list()`: fuerza lista.
- `_extract_text_from_message()`: extrae texto de dicts.
- `_extract_tool_calls()`: extrae tool calls.
- `normalize_agent_result()`: output final normalizado.

## `evaluation/runner.py`
Funciones clave:
- `_invoke_agent_raw()`: invoca agente real.
- `_process_worker()`: worker para timeout duro.
- `_run_with_thread()` / `_run_with_timeout()`: ejecución robusta.
- `_build_agent_eval_input()`: construye input estándar para el agente.
- `_normalize_input()`: normaliza el input.
- `run_agent()`: función principal (acepta string o TestCase).

---

# 4) Registry y métricas

## `evaluation/metric_registry.py`
Catálogo único de métricas:
Incluye runners para:
- LLM: `answer_relevancy`, `instruction_adherence`, `task_success`, `faithfulness`, `contextual_precision`, `hallucination`.
- Determinísticas: `task_success_deterministic`, `unsupported_claims`, `completeness`, `format_compliance`, `latency_budget`, `consistency`.
- Placeholders: `tool_call_validity`.

Función clave:
- `resolve_metric_specs()`: deduplica y prepara métricas finales.

## Métricas específicas
Carpeta `evaluation/metrics/`:
- `hybrid_instruction_adherence.py`: métrica híbrida.
- `contradiction_based_hallucination.py`: hallucination real basada en contradicciones.
- `heuristics/`:
  - `unsupported_claims.py`
  - `format_compliance.py`
  - `completeness.py`
  - `latency_budget.py`

---

# 5) Generación de casos

## `evaluation/case_factory.py`
Construcción de casos:
- `load_golden_cases()`: lee golden JSON.
- `_build_generated_cases()`: genera casos basados en prompt.
- `_build_adversarial_cases()`: adversarial.
- `build_cases()`: router.

## `evaluation/case_generator.py`
Generador “Contra-Agente”:
Funciones:
- `_base_distribution()`, `_scale_distribution()`
- `_domain_samples()`
- `_expected_behavior_text()`
- `_build_input()`
- `_extract_facts()`, `_fact_to_question()`
- `_infer_entities_from_input()`
- `_build_task_contract_for_tag()`
- `generate_cases()`

## `evaluation/metamorphic.py`
Variantes metamórficas:
- `generate_paraphrase()`, `inject_typos_noise()`, `reorder_or_split()`, `build_variants()`.

---

# 6) Autogen (Auto-eval sin LLM)

Carpeta `evaluation/autogen/`:
- `prompt_analyzer.py`: `analyze_prompt()` produce `PromptProfile`.
- `case_generator.py`: `generate_cases()` y `build_cases()`.
- `context_generator.py`: `generate_context_for_case()` (3–8 chunks, distractor).
- `context_synth.py`: `synthesize_context()` para autogen HTTP.
- `agent_client.py`: cliente HTTP para agente externo.
- `schemas.py`: `PromptProfile`, `AutoGenRequest`, `AutoGenSummary`.
- `__init__.py`: `run_auto_eval()` pipeline completo.

---

# 7) Feedback “Profesor”

## `evaluation/feedback_generator.py`
Genera feedback accionable:
Funciones:
- Normalización y tokenización.
- Detección de idioma.
- Parseo de subpreguntas.
- Extracción de entidades/unidades.
- Snippets cercanos.
- `build_case_feedback()` produce:
  - `question_by_question`
  - `rag_audit`
  - `prompt_improvement`

---

# 8) Scorecard y Fase 2

## `evaluation/scorecard/dimensions.py`
- `build_dimensions()`: agrega métricas a dimensiones.

## `evaluation/scorecard/scorecard.py`
- `compute_scorecard()`: score final ponderado.

## `evaluation/scorecard/anti_gaming.py`
- `evaluate_anti_gaming()`: flags de prompt-gaming.

## `evaluation/scorecard/agent_types.py`
- `resolve_agent_type()`: política por tipo de agente.

---

# 9) Reporting forense

## `evaluation/reporting/forensic.py`
Genera reporte forense y PDF:
- `build_forensic_report()`: JSON forense (meta, risks, failures).
- `render_forensic_pdf()`: PDF ejecutivo (reportlab opcional).

---

# 10) API interna

## `evaluation/api/app.py`
Endpoints reales:
- `GET /health`
- `POST /v1/generate-cases`
- `POST /v1/evaluate`
- `POST /v1/autogen/evaluate`
- `POST /v1/auto-evaluate`

## `evaluation/api/server.py`
Stub mínimo para uvicorn:
- `GET /`
- `POST /v1/auto-evaluate`
- `POST /v1/evaluate`

## `evaluation/api/schemas.py`
Modelos Pydantic para API.

---

# 11) CLI principal

## `evaluation/local_harness.py`
CLI principal de ejecución:
- `main()`: carga spec, genera casos, ejecuta engine.
- Soporta JSON completo, API output y PDF (opcional).
- Soporta `--autogen`, `--prompt-base-file`, `--agent-http-url`.

## `evaluation/smoke_run.py`
Ejecuta smoke rápido (sin LLM).

## `evaluation/preflight_openai.py`
Preflight OpenAI para full/full-lite/full-prod.

---

# 12) Utilidades

## `evaluation/utils/text_normalization.py`
- `repair_text()` y `repair_recursive()` para mojibake.

## `evaluation/utils_json.py`
Redacción y JSON rendering:
- `redact_secrets()`, `dump_json()`, `render_case_json()`, `render_run_json()`.

---

# 13) Tests
Carpeta `evaluation/tests/` incluye:
- Validaciones de claims, normalización, métricas, scorecard, API, autogen.
Todos los tests están diseñados para ser determinísticos y sin necesidad de LLM real.

---

# 14) Archivos de configuración (testdata)
Carpeta `evaluation/testdata/`:
Incluye specs:
`spec_default.json`, `spec_smoke.json`, `spec_full_lite.json`, `spec_full_prod.json`,
`spec_contra_agent.json`, `spec_contra_agent_stability_debug.json`,
`spec_strategic_quality_test.json`.

También:
`golden_cases_v1.json`, `cases_strategic_quality.json`, `mock_strategic_agent.py`.

---

# Flujos principales

## Flujo evaluación (local_harness)
1. Cargar spec.
2. Construir casos (golden / contra / autogen / cases_file).
3. Ejecutar runner.
4. Evaluar métricas via registry.
5. Agregar scorecard/dimensiones/feedback.
6. Generar JSON (y PDF opcional).

## Flujo autogen
1. Analizar prompt_base.
2. Generar casos sintéticos.
3. Generar retrieval_context sintético.
4. Ejecutar engine.
5. Agregar autogen_summary.

---

# Cómo correr
Smoke:
```bash
python -m evaluation.smoke_run
```

Local harness:
```bash
python -m evaluation.local_harness --spec evaluation/testdata/spec_default.json --out outputs/run_report.json
```

API real:
```bash
python -m uvicorn evaluation.api.app:app --host 127.0.0.1 --port 8000 --reload
```

API stub:
```bash
python -m uvicorn evaluation.api.server:app --host 127.0.0.1 --port 8000 --reload
```

---

# Nota final
Este README es descriptivo y mantiene el contrato actual. Si necesitas versiones reducidas (operación, quickstart, dev), puedo generarlas.

