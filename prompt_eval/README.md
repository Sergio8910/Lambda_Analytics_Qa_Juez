# Prompt Eval

Producto **standalone** para evaluar system prompts de agentes LLM.

Recibe un prompt (texto), lo audita exhaustivamente contra ~26 reglas determinísticas + un LLM-as-judge opcional, y devuelve un análisis con score global, scores por dimensión, hallazgos detallados y recomendaciones priorizadas.

## No es el Juez. No es el Verificador.

| Producto | Qué evalúa | Cuándo |
|---|---|---|
| **Juez** (`juez/`) | El **agente completo** (flow + tools + persona) contra escenarios sintéticos | Antes de desplegar |
| **Verificador** (`verificador/`) | El **artefacto producido** (PDF, imagen, ...) contra lo que la BD dice que debería contener | Después de cada ejecución productiva |
| **Prompt Eval** (`prompt_eval/`) — *este* | El **system prompt** mismo, en aislamiento, con reglas + LLM judge | Durante diseño / iteración de prompts |

Son tres productos separados, sin acoplamiento — corren en puertos distintos, tienen sus propias settings y no comparten estado.

## API

### `POST /prompt_eval/evaluate`

Request mínimo:
```json
{ "prompt": "Eres un asistente que ..." }
```

Request completo:
```json
{
  "prompt": "Eres un asistente experto en banca minorista. ...",
  "nombre": "abad-conversacional",
  "expected_language": "es",
  "expected_output_format": "markdown",
  "tools": ["Buscar_Cliente", "Crear_Orden"],
  "domain": "banca retail",
  "incluir_llm_judge": true,
  "llm_model": "gpt-4o-mini"
}
```

Response:
```json
{
  "nombre": "abad-conversacional",
  "score_global": 87.4,
  "veredicto": "bueno",
  "dimensiones": [
    {"dimension": "estructura", "score": 95.0, "weight": 0.25, "findings_count": 1, "findings_by_severity": {"low": 1}},
    {"dimension": "claridad", "score": 100.0, "weight": 0.20, "findings_count": 0, "findings_by_severity": {}},
    {"dimension": "especificidad", "score": 87.0, "weight": 0.20, "findings_count": 1, "findings_by_severity": {"medium": 1}},
    {"dimension": "guardrails", "score": 76.0, "weight": 0.15, "findings_count": 2, "findings_by_severity": {"medium": 2}},
    {"dimension": "manejo_errores", "score": 87.0, "weight": 0.10, "findings_count": 1, "findings_by_severity": {"medium": 1}},
    {"dimension": "estilo", "score": 100.0, "weight": 0.10, "findings_count": 0, "findings_by_severity": {}}
  ],
  "findings": [
    {
      "rule_id": "R031",
      "rule_name": "manejo_pii",
      "dimension": "guardrails",
      "severity": "medium",
      "titulo": "No hay política explícita sobre datos sensibles/PII",
      "descripcion": "El prompt no menciona cómo manejar...",
      "recomendacion": "Agregá: 'Nunca pidas, almacenes o reveles...'",
      "evidencia": null,
      "posicion_aprox": null
    }
  ],
  "findings_resumen": {"medium": 4, "low": 1},
  "metricas": {
    "longitud_chars": 1240,
    "longitud_palabras": 178,
    "longitud_lineas": 32,
    "longitud_estimada_tokens": 310,
    "idioma_detectado": "es",
    "secciones_detectadas": ["Objetivo", "Tono", "Formato", "Restricciones"],
    "placeholders_detectados": [],
    "menciona_tools": ["Buscar_Cliente"]
  },
  "top_recomendaciones": [
    "[R031 · medium] Agregá: 'Nunca pidas, almacenes o reveles información sensible...'",
    "[R021 · medium] Agregá una sección 'Tools' que enumere cuándo usar cada una."
  ],
  "juez_version": 2,
  "llm_judge_aplicado": true,
  "duracion_ms": 1450,
  "meta": {
    "prompt_hash": "a3f7c2...",
    "llm": {"model": "gpt-4o-mini", "skipped": false, "total_tokens": 842},
    "reglas_evaluadas": 5
  }
}
```

### `GET /prompt_eval/rules`

Devuelve el catálogo de reglas determinísticas, los pesos de cada dimensión y los cortes de veredicto. Útil para que el cliente entienda qué se mide.

### `GET /health`

Ping. Reporta si el LLM judge está habilitado (hay `OPENAI_API_KEY`).

## Reglas (26 determinísticas + LLM judge opcional)

| Dimensión | Peso | Reglas |
|---|---|---|
| **estructura** | 25% | R001 rol, R002 objetivo, R003 formato salida, R004 restricciones, R005 ejemplos few-shot |
| **claridad** | 20% | R010 lenguaje vago, R011 imperativos, R012 repeticiones, R013 longitud, R014 consistencia idioma |
| **especificidad** | 20% | R006 tono, R007 audiencia, R020 placeholders, R021 alineación tools↔prompt, R022 longitud respuesta |
| **guardrails** | 15% | R030 off-topic, R031 PII, R032 prompt injection, R033 human handoff, R034 anti-hallucination |
| **manejo_errores** | 10% | R040 info faltante, R041 errores de tool, R042 usuario hostil |
| **estilo** | 10% | R050 secciones, R051 mayúsculas, R052 emojis |

### Cómo se calcula el score

- Cada dimensión empieza en **100**.
- Cada finding resta `severity_penalty` puntos a su dimensión (clip en 0):
  - `critical: 70`, `high: 28`, `medium: 13`, `low: 5`, `info: 0`.
- **Score global** = promedio ponderado de las dimensiones con sus pesos.
- **Veredicto**: ≥90 excelente · ≥75 bueno · ≥60 aceptable · ≥40 deficiente · <40 crítico.

### LLM-as-judge (opcional)

Si `incluir_llm_judge=true` (default) y hay `OPENAI_API_KEY`, además se llama a un modelo (default `gpt-4o-mini`) que busca cosas que las regex no pueden ver:

- Contradicciones internas (instrucciones que se anulan).
- Ambigüedades semánticas reales.
- Coherencia rol ↔ objetivo ↔ tono ↔ formato.
- Lagunas no obvias dada la categoría del agente.

Si el LLM falla o no hay key, la respuesta igual se entrega con `llm_judge_aplicado: false` — el endpoint nunca cae por culpa del judge.

## Cómo correr localmente

```powershell
# Carga variables del .env del root
uvicorn prompt_eval.app:app --port 8002 --reload

# Smoke en-proceso (sin levantar uvicorn)
python -m prompt_eval.scripts.smoke

# Tests
python -m pytest prompt_eval/tests/ -q
```

```bash
# Llamada típica
curl -X POST http://localhost:8002/prompt_eval/evaluate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Eres un asistente que ayuda a usuarios."}'
```

## Variables de entorno

| Var | Requerido | Default | Uso |
|---|---|---|---|
| `PROMPT_EVAL_PORT` | No | `8002` | Puerto de la app |
| `PROMPT_EVAL_API_KEY` | No | — | Si se setea, requiere header `X-API-Key` |
| `OPENAI_API_KEY` | No (recomendado) | — | Habilita el LLM judge |
| `PROMPT_EVAL_JUDGE_MODEL` | No | `gpt-4o-mini` | Modelo del LLM judge |
| `PROMPT_EVAL_JUDGE_TIMEOUT_S` | No | `30` | Timeout del LLM judge |
| `PROMPT_EVAL_LOG_LEVEL` | No | `INFO` | Nivel de logging |

## Estructura

```
prompt_eval/
├── app.py              # FastAPI standalone, default :8002
├── router.py           # POST /evaluate, GET /rules, GET /health
├── evaluator.py        # Orquestador (reglas + LLM judge + scoring)
├── rules.py            # 26 reglas determinísticas
├── llm_judge.py        # LLM judge opcional, tolerante a fallos
├── models.py           # Pydantic Request/Response/Finding/Dimension/Severity
├── settings.py         # Env vars (auto-suficiente, no importa Juez ni Verificador)
├── scripts/
│   └── smoke.py        # Smoke en-proceso
└── tests/
    ├── test_rules.py   # 46 tests unitarios de reglas
    └── test_api.py     # 7 tests del endpoint HTTP
```

## Filosofía

- **Síncrono**: el análisis es rápido (~50-500ms sin LLM, ~2-8s con LLM). No vale un job store.
- **Aditivo**: no toca el Juez ni el Verificador. Si se rompe, los otros productos siguen.
- **Tolerante**: si una regla individual lanza, se reporta como finding y el resto continúa. Si el LLM falla, se devuelve solo lo determinístico.
- **Transparente**: el endpoint `/rules` expone el catálogo completo y los pesos.
