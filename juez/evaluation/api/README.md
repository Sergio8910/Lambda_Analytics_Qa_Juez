# Lambda AI Judge API (interna)

## Ejecutar
```bash
python -m uvicorn evaluation.api.server:app --host 0.0.0.0 --port 8000 --reload
```

## Autenticación
Header requerido en endpoints protegidos:
```
X-API-KEY: tu_clave
```
Configura la variable:
```
JUDGE_API_KEY=tu_clave
```

## Health
```bash
curl http://localhost:8000/health
```

## Subir RAG
```bash
curl -X POST http://localhost:8000/v1/upload-rag \
  -H "X-API-KEY: tu_clave" \
  -F "file=@RAGs/supermercado.json"
```

## Generar casos
```bash
curl -X POST http://localhost:8000/v1/generate-cases \
  -H "X-API-KEY: tu_clave" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt_base": "Asistente corporativo",
    "n_cases": 30
  }'
```

## Analizar workflow n8n (JSON exportado)
```bash
curl -X POST http://localhost:8000/v1/n8n/analyze \
  -H "X-API-KEY: tu_clave" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow": {
      "name": "Demo workflow",
      "nodes": [],
      "connections": {}
    },
    "include_graph": true
  }'
```

Por defecto la respuesta ya incluye `analysis.diagnosis`, que es el veredicto autónomo generado por el propio Juez.

Parámetros útiles:
- `include_graph`: incluye o no el grafo completo del workflow en la respuesta.
- `include_diagnosis`: permite apagar el diagnóstico autónomo si solo quieres el scorecard estático.
- `diagnosis_mode`: `auto`, `llm` o `fallback`.
- `diagnosis_model`: modelo a usar para el diagnóstico LLM cuando `OPENAI_API_KEY` esté configurada.

## Mini pantalla web para revisar workflows n8n
Abre esta ruta en el navegador cuando levantes la API:

```text
http://localhost:8000/ui/n8n-reviewer
```

La pantalla permite:
- subir un JSON exportado de n8n
- elegir modo de diagnóstico
- mandar la evaluación al propio Juez
- ver scorecard, veredicto, hallazgos y JSON crudo

## Analizar workflow n8n desde archivo
```bash
curl -X POST "http://localhost:8000/v1/n8n/analyze-file?include_graph=true&include_diagnosis=true&diagnosis_mode=auto" \
  -H "X-API-KEY: tu_clave" \
  -F "file=@workflow-n8n.json"
```

## Evaluar (replay)
```bash
curl -X POST http://localhost:8000/v1/evaluate \
  -H "X-API-KEY: tu_clave" \
  -H "Content-Type: application/json" \
  -d '{
    "spec": {
      "run_id": "demo",
      "metrics": [
        {"name":"task_success_deterministic","threshold":0.7,"enabled":true}
      ]
    },
    "mode": "replay",
    "conversation": [
      {"role":"user","content":"Hola"},
      {"role":"assistant","content":"Hola, ¿en qué puedo ayudarte?"}
    ],
    "n_cases": 30,
    "audit_mode": "balanced"
  }'
```

## Evaluar autogen (HTTP externo)
```bash
curl -X POST http://localhost:8000/v1/autogen/evaluate \
  -H "X-API-KEY: tu_clave" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "demo-agent",
    "prompt_base": "Responde en español y sin inventar.",
    "n_cases": 30,
    "metrics": ["task_success_deterministic","format_compliance"],
    "audit_mode": "balanced",
    "seed": 7,
    "agent_http": {
      "url": "http://localhost:9000/agent",
      "headers": {},
      "timeout_ms": 8000
    }
  }'
```

## Auto-evaluate (sin agente HTTP)
```bash
curl -X POST http://localhost:8000/v1/auto-evaluate \
  -H "X-API-KEY: tu_clave" \
  -H "Content-Type: application/json" \
  -d '{
    "run_id": "auto-eval-demo",
    "prompt_base": "Responde en español y sin inventar.",
    "n_cases": 30,
    "seed": 7,
    "rag_id": "supermercado.json"
  }'
```
