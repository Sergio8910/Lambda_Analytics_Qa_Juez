# Juez — Evaluador de agentes IA

## Qué es el Juez

El Juez es un sistema de evaluación automática de agentes IA. Recibe la configuración de un agente (prompt, tools, schema, flow de n8n o ElevenLabs) y produce un diagnóstico en dos planos: un **análisis estático** sobre el diseño del agente y un **análisis dinámico** mediante un contra-agente adversarial que conversa con él, mide su comportamiento y entrega un score por dimensión. Opcionalmente, en modo e2e, también audita los artefactos (PDFs) que el agente generaría en producción, mediante una integración HTTP con el Verificador.

## Análisis estático

El analizador estático no ejecuta el agente: lee su configuración y la puntúa en estas dimensiones:

- `calidad_prompt` — claridad, completitud y especificidad del system prompt.
- `tools_integraciones` — definición de tools, schemas, manejo de errores.
- `seguridad` — guardrails, sanitización de inputs, prevención de prompt injection.
- `observabilidad` — logging, trazas, métricas instrumentadas.
- `mantenibilidad` — estructura, separación de responsabilidades, documentación.
- `alineacion_tools` — coherencia entre lo que el prompt promete y lo que las tools realmente exponen.

## Contra-agente

El contra-agente genera conversaciones adversariales agrupadas en 8 categorías:

- `happy_path` — flujo cooperativo, el usuario entrega toda la información necesaria.
- `herramienta` — fuerza al agente a invocar una tool con datos concretos.
- `multi_turno` — prueba memoria de contexto entre 4-6 turnos.
- `limite` — pedidos fuera del dominio del agente pero plausibles.
- `caos` — preguntas absurdas o sin sentido dentro del contexto.
- `agresivo` — usuarios frustrados con un motivo de queja específico del dominio.
- `seguridad` — intentos de manipulación o extracción de información sensible.
- `contexto_multiple` — información ambigua o incompleta que obliga a desambiguar.

Cada conversación se evalúa turno a turno por un LLM-juez que mide adherencia al prompt, uso correcto de tools, manejo del usuario y consistencia.

## Modo e2e sintético

Cuando se pasa `--e2e`, el Juez activa una cadena de evaluación end-to-end sin tocar producción:

1. El **generator** marca K planes (default 1, configurable con `--e2e-cases`) con el campo `artifact_expectation`, declarando qué artefacto se espera al final.
2. El **pool** intercepta los planes marcados y reemplaza el adapter productivo por el `MockAdapter`. Cero llamadas al webhook real de n8n para esos planes.
3. Un **MockAgent** (mini-LLM con function calling, `gpt-4o-mini` por default) conversa contra el contra-agente usando un **MockToolRunner** que simula las tools del agente real, terminando en una llamada a `pdf_builder` que construye un PDF sintético.
4. El PDF se envía al **Verificador** vía HTTP (`POST /verificador/verify` con `source.type: "inline"` y `blob_base64`), junto con el `expected_snapshot` en `metadata`. El Verificador audita el PDF y devuelve verdict + diagnósticos.
5. El score final mezcla conversación y artefacto:

   ```
   overall = conv_score * (1 - weight) + artifact_score * weight
   ```

   Con `weight` default `0.30`. Si el Verificador no responde o falla, el peso del artefacto se reasigna al de conversación (degradación elegante).

## Modo BD real

Con `--e2e-real-inventario-id N` el snapshot esperado del PDF se ancla a un inventario real de la BD productiva del cliente. La conexión es read-only con `statement_timeout` corto y un allowlist explícito de tablas — el Verificador sigue recibiendo el snapshot vía `metadata`, sin tocar la BD del cliente.

Ejemplo:

```bash
python juez/evaluar_n8n.py https://n8n-dev.example.com/workflow/ID \
  --completo --e2e --e2e-real-inventario-id 9
```

## Variables de entorno

| Var | Requerido | Default | Uso |
|---|---|---|---|
| `OPENAI_API_KEY` | Sí | — | LLM-juez, MockAgent, generator |
| `N8N_BASE_URL` | Opcional | — | Base de la API de n8n para descargar flows |
| `N8N_API_KEY` | Opcional | — | Credencial de la API de n8n |
| `ELEVENLABS_API_KEY` | Opcional | — | Descarga de agent configs de ElevenLabs |
| `ABAT_DB_URL` | Si se usa `--e2e-real-inventario-id` | — | BD read-only del cliente Abad |
| `VERIFICADOR_BASE_URL` | Si se usa `--e2e` | `http://localhost:8001` | Endpoint del Verificador |
| `VERIFICADOR_API_KEY` | Si se usa `--e2e` | — | Header `X-Verifier-Key` |
| `JUEZ_E2E_MODEL` | No | `gpt-4o-mini` | Modelo del MockAgent en e2e |
| `JUEZ_E2E_HEALTH_TIMEOUT_S` | No | `10` | Timeout del health check al Verificador antes de los e2e |
| `JUDGE_MODEL` | No | `gpt-4o-mini` | Modelo del LLM-juez por turno |

## Comandos útiles

```bash
# Listar inventarios disponibles (para usar con --e2e-real-inventario-id)
python -c "from juez.evaluation.contra_agente.synthetic.real_db_source import listar_inventarios_disponibles
for i in listar_inventarios_disponibles(): print(i)"

# Smoke E2E del modo sintético (snapshot generado por código, cero BD)
python juez/scripts/e2e_synthetic_smoke.py

# Smoke E2E con BD real (1 SELECT read-only contra prod)
python juez/scripts/e2e_synthetic_smoke.py --real-inventario-id 9

# Evaluar un flow de n8n en modo completo + e2e
python juez/evaluar_n8n.py https://n8n-dev.example.com/workflow/ID --completo --e2e
```

## Estructura interna de `juez/evaluation/contra_agente/synthetic/`

| Módulo | Descripción |
|---|---|
| `snapshot_factory.py` | Construye el `expected_snapshot` (datos canónicos del PDF a generar). |
| `mock_tools.py` | Simulador de tools del agente real (lookups, queries, transformaciones). |
| `mock_agent.py` | Mini-LLM con function calling que actúa como el agente productivo. |
| `pdf_builder.py` | Genera el PDF sintético a partir del snapshot. |
| `adapter.py` | `MockAdapter` que reemplaza al adapter productivo en planes e2e. |
| `real_db_source.py` | Lectura read-only de la BD del cliente para anclar snapshots a producción. |
| `cost_meter.py` | Contabiliza tokens y llamadas para reporting de costo. |
| `pdf_cache.py` | Cache local de PDFs sintéticos para no regenerar entre runs. |

## Tests

```bash
python -m pytest \
  juez/evaluation/contra_agente/synthetic/tests/ \
  juez/evaluation/contra_agente/tests/ \
  juez/evaluation/tests/ \
  -p no:xdist -p no:rerunfailures
```
