# PLAN EJECUTABLE: TRANSFORMACIÓN DEL JUEZ A PLATAFORMA INTEGRAL

**Fecha**: 21 de abril de 2026  
**Objetivo**: Convertir motor de evaluación de prompts en plataforma escalable, económica y operativa  
**Horizonte**: 4-5 meses a ritmo sostenido  
**Constraint**: Sin límites de personas/tiempo  
**Requerimiento**: Implementar MVP → Escalado → Producto de forma integrada

---

## 1. ANÁLISIS GAP: ESTADO ACTUAL vs REQUERIDO

### Hoy Tenemos ✅

| Componente | Estado | Detalle |
|-----------|--------|--------|
| Motor de evaluación | Funcional | EvaluationEngine + JudgeEngine |
| Métricas | 15+ LLM + heurísticas | DeepEval integrado, timeout, retries |
| API REST | v1 síncrona | POST /v1/evaluate, /v1/generate-cases |
| Generación de casos | 4 modos | Golden, generated, adversarial, autogen |
| Normalizacion | Robusta | Manejo de múltiples formatos |
| Reporting | JSON + PDF | Reporte forense con métricas |
| Tests | 62/65 pass | Suite automatizada |
| Auth | X-API-KEY básica | Sin scopes, sin tenants |

### Nos Falta para MVP ❌

| Componente | Requisito | Impacto |
|-----------|-----------|--------|
| **Jobs async** | Celery + Redis queue | Permitir evaluaciones largas sin timeout HTTP |
| **Multi-tenant** | Aislamiento namespace | Múltiples clientes sin bleed |
| **Versionado** | registry de datasets/suites/versiones | Reproducibilidad y comparación |
| **API v2** | RFC 9457, idempotency, 202 Accepted | Contratos claros |
| **Observabilidad** | OpenTelemetry collector | Trazabilidad por request |
| **Webhooks** | HMAC + CloudEvents | Entrega de resultados async |
| **Rate limits** | Límites por tenant | Evitar abuso |
| **Suites orquestadas** | CI/Daily/Release rings | Ejecutar pruebas inteligentemente |

### Nos Falta para Escalado ⚠️

| Componente | Requisito |
|-----------|-----------|
| Autoscaling workers | Queue management + Prometheus |
| Red teaming automatizado | Promptfoo integrado |
| Pruebas de carga | k6 + Locust pipelines |
| Drift nightly | Jobs periódicos + umbrales |
| Comparación releases | Baseline + deltas |
| SLOs y alertas | Alertmanager setup |
| Multi-región | Control plane en cluster |

### Nos Falta para Producto 🚀

| Componente | Requisito |
|-----------|-----------|
| UI self-service | Dashboard + job management |
| RBAC | Roles y scopes por tenant |
| Billing/showback | Tracking de costos por cliente |
| SDKs | Python, TypeScript, REST |
| Aprobaciones | Workflow de gate de release |
| Auditoría global | Lineage y compliance |

---

## 2. ARQUITECTURA OBJETIVO

### 2.1 Capas (Separación Explícita)

```
┌─────────────────────────────────────────────────────────────┐
│                    API GATEWAY (FastAPI)                    │
│    • OAuth 2.0/JWT • X-API-KEY with scopes • Rate limit     │
│    • Request ID • Tenant ID propagation • Idempotency       │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        │                             │
    ┌───▼──────────────────┐   ┌─────▼──────────────────┐
    │  CONTROL PLANE (v2)  │   │  SYNC PLANE (smoke)    │
    │  • Auth/RBAC         │   │  • /v2/quick-eval      │
    │  • Registry          │   │  • <1s SLA             │
    │  • Job orchestration │   │  • No async overhead   │
    │  • Webhooks          │   │                        │
    │  • Metadata DB       │   │                        │
    └────────┬─────────────┘   └────────────────────────┘
             │
    ┌────────▼──────────────────────────────────┐
    │     JOB QUEUE (Celery + Redis)            │
    │  • Durable, versionado, con retry        │
    │  • Separado por prioridad/tenant         │
    │  • Deadletter para fallos críticos       │
    └─┬──────────────────┬──────────┬─────────┬┘
      │                  │          │         │
  ┌───▼──┐         ┌─────▼────┐ ┌──▼──┐  ┌──▼─────┐
  │ FUNC │         │   RAG    │ │ SEC │  │ PERF   │
  │WORKER│         │  WORKER  │ │WORK │  │WORKER  │
  └─┬────┘         └──────────┘ └─────┘  └────────┘
    │
┌───▼─────────────────────────────────────────────┐
│         EVIDENCE PLANE (Observabilidad)        │
│  • OpenTelemetry Collector                     │
│  • Prometheus (métricas)                       │
│  • Traces backend (Jaeger/Datadog)             │
│  • Logs backend (structured)                   │
│  • Artefactos (Object Storage)                 │
└───────────────────────────────────────────────┘
```

### 2.2 Componentes No Negociables

#### Control Plane (PostgreSQL + Redis)

```python
# Tablas Mínimas
jobs(
    job_id: UUID,
    tenant_id: str,
    kind: enum(quality, rag, security, perf, chaos),
    status: enum(queued, running, succeeded, failed),
    spec_version: str,
    model_version: str,
    rag_index_version: str,
    dataset_version: str,
    created_at, started_at, completed_at,
    metadata: json  # versionado completo
)

suites(
    suite_id: str,
    tenant_id: str,
    version: str,
    datasets: str[],  # referencias
    scenarios: str[],
    thresholds: json,
    created_at
)

datasets(
    dataset_id: str,
    tenant_id: str,
    version: str,
    cases_count: int,
    hash: str,  # para reproducibilidad
    created_at
)

evaluation_results(
    job_id: UUID,
    case_id: str,
    metrics: json,  # flattened para queries
    trace_id: str,  # link a OpenTelemetry
    cost_usd: float
)
```

#### Execution Plane (Workers Especializados)

**Worker Funcional** (calidad semántica):
- Ejecuta test cases contra agente
- Aplica DeepEval (answer_relevancy, task_success, faithfulness)
- Valida formato, completitud
- Calcula consistency

**Worker RAG** (integridad retrieval):
- Recupera documentos con scorer
- Valida relevancia (precision@k)
- Detecta exfiltración
- Mide coverage de queries

**Worker Seguridad** (red-teaming):
- Prompt injection (directo, indirecto)
- Multi-turn jailbreak
- Exfiltración de contexto
- Tool abuse, sensitive data leakage

**Worker Performance** (carga y chaos):
- k6: smoke, load, stress, burst
- Chaos: latency injection, dependency failures
- Mide p50/p95/p99, throughput, error rates
- Autoscaling triggers

**Worker Drift** (monitoreo continuo):
- Jobs nocturnos vs baseline
- PSI/Jensen-Shannon en inputs/outputs
- Costo marginal por versión
- Alertas en umbrales

#### Evidence Plane (Observabilidad Abierta)

OpenTelemetry spans por operación:
```
request_http (root)
├── job_submit
├── job_orchestrate
├── job_execution (cada worker)
│   ├── retrieval (si RAG)
│   ├── model_call
│   ├── tool_call (si agente)
│   └── metric_evaluation
└── webhook_delivery
```

---

## 3. LAS 3 SUITES

### Suite 1: SMOKE (CI, ~5-10 min)

**Objetivo**: Validación rápida pre-merge

**Escenarios**:
- 5-10 casos determinísticos de happy path
- Exactitud (no LLM-based)
- Formato JSON válido
- Latencia < 2s

**Métricas**:
- Exact match
- Contract compliance
- p50 latency

**Gate**: PASS/FAIL binario

### Suite 2: QUALITY (Daily, ~30-45 min)

**Objetivo**: Evaluación semántica completa

**Escenarios**:
- 50 casos mix (happy path, edge, ambiguous)
- Golden reference set + generated
- LLM-based (DeepEval)
- Deterministic heuristics

**Métricas**:
- answer_relevancy, faithfulness, task_success (LLM)
- completeness, format_compliance, unsupported_claims (heuristics)
- consistency (cross-case)
- latency p95

**Baseline Comparison**: vs previous release
- Delta quality (±0.05 threshold)
- Delta latency (±200ms)
- Delta cost

**Gate**: Pass if quality >= baseline AND no new critical failures

### Suite 3: RAG (Daily, ~20-30 min)

**Objetivo**: Integridad del retrieval-augmented generation

**Escenarios**:
- 40 casos RAG-specific
- Poisoning: document injection
- Exfiltration: ask for sensitive data
- Hallucination: contradictions vs context
- Coverage: ¿se recuperan los docs relevantes?

**Métricas**:
- retrieval_precision@k
- retrieval_recall@k
- faithfulness (claims vs context)
- rag_document_safety (no fuga de IP/PII)
- grounding_coverage %

**Gate**: 
- Zero exfiltration findings (critical)
- faithfulness >= 0.90
- precision@5 >= 0.85

---

## 4. API v2: CONTRATOS CONCRETOS

### 4.1 Crear Job (Async)

```http
POST /v2/evaluation-jobs HTTP/1.1
Authorization: Bearer <jwt_token>
Idempotency-Key: <uuid>
X-Tenant-ID: acme
Content-Type: application/json

{
  "kind": "quality",
  "target": {
    "app_id": "customer-support-v12",
    "environment": "staging",
    "base_url": "https://staging.example.com/api",
    "auth_ref": "secret://targets/staging-token"
  },
  "suite": {
    "dataset_id": "golden-qa-v3",
    "scenario": "quality",
    "sample_size": 50,
    "seed": 42
  },
  "execution": {
    "mode": "async",
    "max_parallelism": 4,
    "deadline_s": 1800
  },
  "notification": {
    "webhook": "https://acme.example.com/webhooks/eval",
    "events": ["job.completed", "job.failed", "job.threshold_breached"]
  },
  "metadata": {
    "model_version": "gpt-4o-2026-04-01",
    "rag_index_version": "kb-2026-04-21",
    "prompt_version": "cust-support-v12",
    "suite_version": "release-gate-v8"
  }
}

HTTP/1.1 202 Accepted
Content-Type: application/json

{
  "job_id": "job_01JS4P3EN7B5J2M7V8X9Y0Z123",
  "status": "queued",
  "submitted_at": "2026-04-21T15:30:00Z",
  "status_url": "/v2/jobs/job_01JS4P3EN7B5J2M7V8X9Y0Z123",
  "tenant_id": "acme"
}
```

### 4.2 Consultar Estado (Polling)

```http
GET /v2/jobs/job_01JS4P3EN7B5J2M7V8X9Y0Z123 HTTP/1.1
Authorization: Bearer <jwt_token>

HTTP/1.1 200 OK

{
  "job_id": "job_01JS4P3EN7B5J2M7V8X9Y0Z123",
  "status": "running",
  "phase": "quality_evaluation",
  "progress": {
    "completed_cases": 28,
    "total_cases": 50,
    "percent": 56.0
  },
  "timing": {
    "queued_at": "2026-04-21T15:30:00Z",
    "started_at": "2026-04-21T15:30:15Z",
    "last_heartbeat_at": "2026-04-21T15:35:42Z"
  },
  "partial_report_url": "/v2/jobs/job_01JS4P3EN7B5J2M7V8X9Y0Z123/report"
}
```

### 4.3 Obtener Reporte Final

```http
GET /v2/jobs/job_01JS4P3EN7B5J2M7V8X9Y0Z123/report HTTP/1.1
Authorization: Bearer <jwt_token>

HTTP/1.1 200 OK

{
  "job_id": "job_01JS4P3EN7B5J2M7V8X9Y0Z123",
  "suite": "quality",
  "status": "succeeded",
  "summary": {
    "total_cases": 50,
    "passed_cases": 47,
    "failed_cases": 3,
    "skipped_cases": 0,
    "pass_rate": 0.94,
    "scores": {
      "answer_relevancy": 0.88,
      "faithfulness": 0.92,
      "task_success": 0.90,
      "completeness": 0.94,
      "format_compliance": 1.0,
      "consistency": 0.91
    },
    "latency_ms": {
      "p50": 480,
      "p95": 1240,
      "p99": 2840
    },
    "cost_usd": {
      "total": 24.50,
      "per_case": 0.49
    }
  },
  "comparison_vs_baseline": {
    "quality_delta": 0.02,
    "latency_p95_delta_ms": 120,
    "cost_delta_pct": 5.2,
    "release_gate": "pass"
  },
  "failures": [
    {
      "case_id": "quality-042",
      "input": "...",
      "expected": "...",
      "actual": "...",
      "metrics": {
        "answer_relevancy": 0.65,
        "task_success": 0.40
      },
      "root_cause": "modelo no reconoció entidad",
      "feedback": "revisar training data para esta categoría"
    }
  ],
  "exported_artifacts": {
    "full_json": "/artifacts/job_01JS4P3EN7B5J2M7V8X9Y0Z123/report.json",
    "traces": "/artifacts/job_01JS4P3EN7B5J2M7V8X9Y0Z123/traces.json",
    "pdf_executive": "/artifacts/job_01JS4P3EN7B5J2M7V8X9Y0Z123/executive.pdf"
  }
}
```

### 4.4 Quick Eval Síncrono (Smoke)

Para smoke tests que deben ser rápidos (<1s):

```http
POST /v2/quick-eval HTTP/1.1
Authorization: Bearer <jwt_token>
X-Tenant-ID: acme
Content-Type: application/json

{
  "target": {"app_id": "support", "base_url": "https://api.example.com"},
  "case": {
    "id": "smoke-001",
    "input": "¿Cuál es tu nombre?",
    "expected_behavior": "responder nombre asistente",
    "retrieval_context": []
  },
  "metrics": ["exact_match", "format_compliance"]
}

HTTP/1.1 200 OK

{
  "case_id": "smoke-001",
  "agent_response": "Soy Asistente Lambda",
  "metrics": {
    "exact_match": 1.0,
    "format_compliance": 1.0
  },
  "latency_ms": 340,
  "passed": true
}
```

### 4.5 Errores (RFC 9457)

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/problem+json

{
  "type": "https://api.example.com/problems/rate-limit-exceeded",
  "title": "Rate limit exceeded",
  "status": 429,
  "detail": "Tenant acme exceeded 600 requests/minute on /v2/evaluation-jobs",
  "instance": "/v2/evaluation-jobs",
  "request_id": "req_01JS4P2X2M7N1AA9X4R6D7",
  "tenant_id": "acme",
  "error_code": "RATE_LIMIT_EXCEEDED",
  "retry_after_s": 42
}
```

---

## 5. ESTRUCTURA DE DIRECTORIOS POST-TRANSFORMACIÓN

```
evaluation/
├── api/
│   ├── v1/                          # Legacy (se retira en Product)
│   │   └── app.py
│   ├── v2/                          # NEW
│   │   ├── __init__.py
│   │   ├── app.py                   # FastAPI main
│   │   ├── auth.py                  # OAuth + API key validation
│   │   ├── handlers/
│   │   │   ├── jobs.py              # POST /v2/evaluation-jobs, GET /v2/jobs/{id}
│   │   │   ├── quick.py             # POST /v2/quick-eval
│   │   │   └── artifacts.py         # GET /v2/jobs/{id}/report, traces
│   │   └── schemas.py               # Pydantic models (RFC 9457)
│   ├── middleware/
│   │   ├── auth.py
│   │   ├── rate_limit.py
│   │   ├── tenant_propagation.py
│   │   └── request_id.py
│   └── webhooks.py                  # CloudEvents + HMAC
│
├── control_plane/
│   ├── __init__.py
│   ├── models.py                    # SQLAlchemy: Job, Suite, Dataset, Result
│   ├── registry.py                  # JobRegistry, DatasetRegistry
│   ├── orchestrator.py              # JobOrchestrator (celery task dispatcher)
│   ├── versiontag.py               # Manejo de versiones
│   └── secrets.py                   # Secret manager integration
│
├── execution/
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── functional.py            # Calidad semántica
│   │   ├── rag.py                   # Integridad RAG
│   │   ├── security.py              # Red-teaming
│   │   ├── performance.py           # Carga y chaos
│   │   └── drift.py                 # Monitoreo continuidad
│   ├── celery_app.py                # Celery configuration
│   └── tasks.py                     # @app.task definitions
│
├── evidence/
│   ├── __init__.py
│   ├── telemetry.py                 # OpenTelemetry setup
│   ├── exporters.py                 # Prometheus, Jaeger, Datadog
│   └── spans.py                     # Helper para crear spans
│
├── suites/                          # Definiciones de suites
│   ├── __init__.py
│   ├── smoke.py                     # Suite 1
│   ├── quality.py                   # Suite 2
│   ├── rag.py                       # Suite 3
│   └── comparison.py                # Baseline + delta logic
│
├── core/
│   ├── engine.py                    # EvaluationEngine (refactored para async)
│   └── engine_impl.py               # JudgeEngine (sin cambios mayoress)
│
├── metrics/
│   ├── ...                          # (existente)
│   └── comparator.py                # NEW: Comparar releases
│
└── tests/
    ├── test_api_v2.py               # API contracts
    ├── test_workers.py              # Each worker type
    ├── test_suites.py               # Suite logic
    └── test_integration.py          # End-to-end
```

---

## 6. ROADMAP EJECUTABLE

### FASE 0: Preparación (1-2 semanas)

**Objetivo**: Estructurar base para async

**Tareas**:

1. **Setup Docker Compose**
   - PostgreSQL (control plane)
   - Redis (queue)
   - Prometheus (metrics)
   - Jaeger (traces)
   - MinIO (object storage)
   
2. **Crear estructura de directorios**
   - `evaluation/api/v2/`
   - `evaluation/control_plane/`
   - `evaluation/execution/`
   - `evaluation/evidence/`
   - `evaluation/suites/`

3. **Setup Celery**
   - `celery_app.py` con Redis broker
   - Task serialization JSON
   - Retry/backoff config

4. **Schemas RFC 9457**
   - Pydantic models para Job, Report, Error
   - Validación de contratos

5. **Tests de base**
   - Conectividad PostgreSQL
   - Queue connection
   - Metric collection

**Estimación**: 60-80 h/persona

### FASE 1: MVP Async (3-4 semanas)

**Objetivo**: API v2 funcional, multi-tenant, jobs async

**Tareas**:

1. **Control Plane**
   - ✅ Modelos SQLAlchemy (Job, Suite, Dataset)
   - ✅ Registry (registro de qué ejecutar)
   - ✅ JobOrchestrator (dispatcher)
   - ✅ Auth mejorada (OAuth + API keys con scopes)
   - ✅ Tenant namespacing

2. **API v2**
   - ✅ POST /v2/evaluation-jobs (202 Accepted)
   - ✅ GET /v2/jobs/{job_id} (polling)
   - ✅ GET /v2/jobs/{job_id}/report
   - ✅ POST /v2/quick-eval (síncrono)
   - ✅ Idempotency-Key validation
   - ✅ Rate limits por tenant

3. **Workers Básicos**
   - ✅ Functional worker (reutilizar motor existente)
   - ✅ Task result persistence

4. **Observabilidad Base**
   - ✅ OpenTelemetry collector setup
   - ✅ Request ID propagation
   - ✅ Prometheus metrics (job counts, latencies)

5. **Tests API v2**
   - ✅ Contract tests (pytest)
   - ✅ Schemathesis property-based tests
   - ✅ Multi-tenant isolation tests

**Estimación**: 200-300 h/persona

### FASE 2: Suites Orquestadas (4-5 semanas)

**Objetivo**: Smoke, Quality, RAG suites completas y automatizadas

**Tareas**:

1. **Suite SMOKE**
   - ✅ 10 casos determinísticos
   - ✅ Exactitud (JSON validation)
   - ✅ Metrics: exact_match, format_compliance
   - ✅ Task (smoke_run) cada push

2. **Suite QUALITY**
   - ✅ 50 casos mix (happy path + edge)
   - ✅ Golden reference set
   - ✅ LLM metrics (DeepEval integrado)
   - ✅ Heuristic metrics
   - ✅ Consistency check
   - ✅ Baseline comparison
   - ✅ Release gate logic

3. **Suite RAG**
   - ✅ 40 casos RAG-specific
   - ✅ Poisoning scenarios
   - ✅ Exfiltration detection
   - ✅ Faithfulness scoring
   - ✅ Retrieval metrics
   - ✅ Critical gates

4. **Execution Rings**
   - ✅ CI ring: smoke only (5-10 min)
   - ✅ Daily ring: quality + RAG (1 hora)
   - ✅ Release ring: todas + load + chaos (4+ horas)

5. **Comparison Engine**
   - ✅ Baseline persistence
   - ✅ Delta calculation
   - ✅ Release gate evaluation

6. **Tests**
   - ✅ Suite logic tests
   - ✅ Gate tests
   - ✅ Comparison tests

**Estimación**: 250-400 h/persona

### FASE 3: Red-Team + Performance (4-5 semanas)

**Objetivo**: Seguridad automatizada, pruebas de carga, drift

**Tareas**:

1. **Security Worker**
   - ✅ Prompt injection (directo + indirecto)
   - ✅ Multi-turn jailbreak
   - ✅ RAG exfiltration
   - ✅ Sensitive info disclosure
   - ✅ Integration con Promptfoo

2. **Performance Worker**
   - ✅ k6 test scripting
   - ✅ Load profiles (smoke, load, stress, soak)
   - ✅ p50/p95/p99 measurement
   - ✅ Chaos scenarios (latency, network loss)
   - ✅ Autoscaling triggers

3. **Drift Worker**
   - ✅ Nightly jobs vs baseline
   - ✅ Input/output drift detection (PSI, Jensen-Shannon)
   - ✅ Cost drift alerting
   - ✅ Threshold violations

4. **Tests**
   - ✅ Security suite tests
   - ✅ Load test validation
   - ✅ Drift detection tests

**Estimación**: 280-400 h/persona

### FASE 4: Observabilidad + SLOs (3-4 semanas)

**Objetivo**: Dashboards, alerting, SLOs establecidos

**Tareas**:

1. **Dashboards**
   - ✅ Executive (quality, cost, risk per version)
   - ✅ SRE (availability, p95/p99, queue depth, MTTR)
   - ✅ Security (injection success rate, exfiltration findings)
   - ✅ Product (usage per suite, cost per client)

2. **Alerting**
   - ✅ Alertmanager setup
   - ✅ Critical alerts (exfiltration, gate failures)
   - ✅ Warning alerts (quality delta, cost spike)
   - ✅ On-call integration

3. **SLOs**
   - ✅ API availability 99.9%
   - ✅ Smoke test p95 < 1s
   - ✅ Job queuing p95 < 60s
   - ✅ Job failure rate < 1%
   - ✅ MTTR < 30 min

4. **Logging Estructurado**
   - ✅ JSON logs
   - ✅ Tenant/job/request propagation
   - ✅ Stack traces normalizadas

**Estimación**: 150-250 h/persona

### FASE 5: Escalado + Hardening (4-5 semanas)

**Objetivo**: Producción-ready, multi-región, hardened

**Tareas**:

1. **Webhooks + CloudEvents**
   - ✅ HMAC signature
   - ✅ Retry logic
   - ✅ CloudEvents format
   - ✅ Delivery guarantees

2. **Multi-región**
   - ✅ Control plane cluster (PostgreSQL replicated)
   - ✅ Object storage replicated
   - ✅ Regional workers
   - ✅ Failover orchestration

3. **Seguridad**
   - ✅ Secret rotation
   - ✅ Encryption at rest
   - ✅ TLS everywhere
   - ✅ Multi-tenant isolation tests

4. **Data Privacy**
   - ✅ PII redaction en logs/traces
   - ✅ Data retention policies
   - ✅ GDPR compliance checks
   - ✅ Audit trail

5. **Optimización de costos**
   - ✅ Sampling adaptativo
   - ✅ Batch processing
   - ✅ Cache de retrieval
   - ✅ Cost tracking por tenant

**Estimación**: 250-400 h/persona

### FASE 6: Producto (5-7 semanas)

**Objetivo**: Self-service platform, comercializable

**Tareas**:

1. **UI Dashboard**
   - ✅ Job creation wizard
   - ✅ Results explorer
   - ✅ Comparison tool
   - ✅ Cost/billing view
   - ✅ Settings/RBAC

2. **SDK + CLI**
   - ✅ Python SDK
   - ✅ TypeScript SDK
   - ✅ CLI tool
   - ✅ Examples + tutorials

3. **Billing**
   - ✅ Usage tracking
   - ✅ Cost calculation
   - ✅ Showback/chargeback
   - ✅ Billing API

4. **Governance**
   - ✅ Approval workflows
   - ✅ Policy engine
   - ✅ Audit logs
   - ✅ Compliance reports

5. **Onboarding**
   - ✅ Tenant provisioning
   - ✅ Configuration templates
   - ✅ Migration tools
   - ✅ Support portal

**Estimación**: 400-700 h/persona

---

## 7. ESTIMACIÓN TOTAL

| Fase | Semanas | Horas/persona | FTE (8h/día) |
|------|---------|---------------|------------|
| 0: Prep | 1-2 | 60-80 | 0.75-1 |
| 1: MVP | 3-4 | 200-300 | 2.5-3.75 |
| 2: Suites | 4-5 | 250-400 | 3.1-5 |
| 3: Red-team | 4-5 | 280-400 | 3.5-5 |
| 4: Observability | 3-4 | 150-250 | 1.9-3.1 |
| 5: Hardening | 4-5 | 250-400 | 3.1-5 |
| 6: Producto | 5-7 | 400-700 | 5-8.75 |
| **TOTAL** | **24-32** | **1,590-2,530** | **20-31.5** |

**Recomendación de equipo**:
- 2-3 backend engineers (async/workers)
- 1 security engineer (red-teaming)
- 1 platform/SRE (observability, multi-region)
- 1 frontend engineer (UI/SDKs)
- 1 tech lead (arquitectura)

**Timeline**: 6 meses a ritmo sostenido (1 FTE ~= 1 persona)

---

## 8. CHECKLIST CRÍTICO POR FASE

### ✅ MVP (Go/No-Go)

- [ ] PostgreSQL migrations running
- [ ] Celery workers online
- [ ] API v2 /evaluation-jobs: 202 responses
- [ ] Job tracking en DB
- [ ] Reports almacenados
- [ ] Multi-tenant aislamiento (namespace en queues)
- [ ] OAuth + API key validation working
- [ ] Rate limits per tenant
- [ ] Idempotency-Key validation
- [ ] Request ID propagation
- [ ] Smoke suite funcional
- [ ] Quality suite funcional (baseline set)
- [ ] RAG suite funcional
- [ ] Tests API: >95% pass rate
- [ ] Logs estructurados en JSON

### ✅ Escalado (Go/No-Go)

- [ ] Security worker: injection tests passing
- [ ] Performance worker: k6 running
- [ ] Drift worker: nightly jobs
- [ ] Webhooks: HMAC signature + retry
- [ ] Prometheus metrics: collected
- [ ] Dashboards: 4 principales built
- [ ] SLOs: defined + monitored
- [ ] Alertmanager: rules + on-call
- [ ] Multi-tenant: isolation tests pass
- [ ] Cost tracking: por job/tenant
- [ ] Archive pipeline: long-term storage
- [ ] Disaster recovery: tested

### ✅ Producto (Go/No-Go)

- [ ] UI: job creation, results, comparison
- [ ] RBAC: roles, scopes per tenant
- [ ] Billing: usage tracking, showback
- [ ] SDKs: Python, TypeScript working
- [ ] Onboarding: tenant provisioning
- [ ] Approval workflow: implemented
- [ ] Support: docs, examples, FAQ
- [ ] Performance: p95 < 500ms for most endpoints
- [ ] Security: penetration test passed
- [ ] Compliance: GDPR, SOC2 ready

---

## 9. RIESGOS Y MITIGACIÓN

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|-------------|--------|-----------|
| Celery/Redis instability | Media | Alto | Setup sentinel, use AWS managed services en prod |
| Costs explode (LLM overage) | Media | Alto | Sampling adaptativo, límites por tenant, alertas |
| Multi-tenant bleed | Baja | Crítico | Code review, dedicated security tests, continuous audit |
| Observer latency | Media | Medio | Batch collector, async export, local caching |
| Worker starvation | Media | Medio | Separate queues por prioridad, dead-letter queue |
| Seed variability (LLM flakiness) | Media | Medio | Fix seeds, tolerancia ±5%, multiple evaluators para críticos |
| Schema drift (backward compat) | Baja | Medio | Versioning strict, deprecation warnings, migration tests |
| Webhook delivery failures | Media | Bajo | Retry policy, dead-letter, admin resend UI |
| PII leakage | Baja | Crítico | Redaction layer, encryption, audit logs |
| Prompt injection in red-team suite | Baja | Bajo | Sandboxing, isolation, fallback safe agent |

---

## 10. STACK FINAL (SEALED)

```yaml
Backend:
  - FastAPI (async, async_to_sync donde necesario)
  - Celery + Redis (queue)
  - PostgreSQL 14+ (metadata)
  - MinIO/S3 (artifacts)
  - OpenTelemetry Collector

Observability:
  - Prometheus (metrics)
  - Jaeger/Datadog (traces)
  - Structured logging (JSON)
  - Grafana (dashboards)
  - AlertManager (on-call)

Testing:
  - pytest (unit + integration)
  - Schemathesis (contract)
  - k6 (performance)
  - Chaos Mesh o Gremlin (resilience)

Red-Team:
  - Promptfoo (scenarios)
  - Custom suites (RAG exfiltration, injection)

Deployment:
  - Docker (containers)
  - Docker Compose (local dev)
  - Kubernetes (production)
  - Helm (charts)
```

---

## 11. PRÓXIMOS PASOS INMEDIATOS

1. **Esta semana**:
   - Crear rama `feature/platform-v2`
   - Setup Docker Compose (Postgres, Redis, Prometheus, Jaeger)
   - Crear estructura de directorios Phase 0

2. **Semana 2**:
   - Celery app + task skeletons
   - Control plane models (SQLAlchemy)
   - API v2 skeleton (FastAPI)

3. **Semana 3**:
   - First job submission (202 response, DB persistence)
   - Functional worker (reutilizar motor existente)
   - Basic tests

Voy a comenzar con la implementación concreta.
