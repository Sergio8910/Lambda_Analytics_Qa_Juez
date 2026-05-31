# Lambda Analytics — Juez + Verificador

Repositorio monorepo de Lambda Analytics que aloja dos productos independientes pero complementarios: el **Juez**, un sistema de evaluación automática de agentes IA (n8n, ElevenLabs, pipelines multi-agente), y el **Verificador**, un servicio FastAPI standalone que audita los artefactos (PDFs por ahora) que esos agentes generan en producción. Ambos productos viven en este repo por conveniencia operativa, pero no comparten imports: cada uno puede desplegarse, testearse y versionarse por separado.

## Status

![status](https://img.shields.io/badge/status-alpha-orange)
![tests](https://img.shields.io/badge/tests-267%20passing-brightgreen)

## Arquitectura

Los dos productos se comunican por HTTP únicamente en el modo de evaluación end-to-end sintético. Fuera de ese modo, no se hablan.

```
                          +-----------------------------+
                          |     juez/  (evaluador)      |
                          |  - análisis estático        |
                          |  - contra-agente            |
                          |  - reportes / scoring       |
                          +--------------+--------------+
                                         |
                            HTTP (solo en modo --e2e)
                                         |
                                         v
                          +-----------------------------+
                          |   verificador/  (auditor)   |
                          |  puerto 8001 (default)      |
                          |  POST /verificador/verify   |
                          |  GET  /verificador/verify/  |
                          |  GET  /health               |
                          +-----------------------------+
```

- **juez/**: corre como CLI o API REST (default port 8002 si se levanta `juez/start_api.py`).
- **verificador/**: corre como servicio HTTP independiente con uvicorn (default port 8001).
- En modo `--e2e` el Juez genera un PDF sintético, lo envía al Verificador en base64 y consume el verdict para mezclarlo con el score conversacional.

## Quickstart

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # Linux/Mac
pip install -r requirements.txt
cp .env.example .env
# editar .env con tus credenciales
```

## Modos de evaluación

| Modo | Comando | Side effects | Costo |
|---|---|---|---|
| Validación | `python juez/evaluar_n8n.py <url> --validacion` | Health check sobre webhooks | $0 |
| Completo | `python juez/evaluar_n8n.py <url> --completo` | Llamadas reales al webhook n8n | Tokens GPT-4 |
| Completo + e2e sintético | `python juez/evaluar_n8n.py <url> --completo --e2e` | Webhook real + tokens MockAgent | Tokens GPT-4 + gpt-4o-mini |
| Completo + e2e con BD real | `python juez/evaluar_n8n.py <url> --completo --e2e --e2e-real-inventario-id 9` | Webhook real + 1 SELECT a BD prod + tokens | Tokens + 1 SELECT |

Flags adicionales relevantes: `--e2e-cases K` (cuántos casos sintéticos disparar, default 1), `--e2e-model` (override del modelo del MockAgent, default `gpt-4o-mini`), `--ci-mode` y `--ci-threshold` (modo no-interactivo para CI).

## Estructura del repo

```
.
├── juez/                   # Evaluador de agentes (CLI + módulos de análisis)
│   ├── evaluation/         # Motor de evaluación + contra-agente + sintético
│   ├── api/                # API REST del Juez (FastAPI async + JobStore)
│   ├── evaluar_n8n.py      # CLI para flows de n8n
│   ├── evaluar_elevenlabs.py  # CLI para agentes de ElevenLabs
│   ├── evaluar_pipeline.py # CLI para pipelines multi-agente
│   ├── scripts/            # Smoke tests, auto-eval, cliente HTTP
│   └── METHODOLOGY.md      # Metodología de scoring
├── verificador/            # Servicio FastAPI que audita artefactos (puerto 8001)
│   ├── app.py              # Entry point uvicorn
│   ├── router.py           # Endpoints /verify, /health
│   ├── verifier.py         # Orquestador cliente + fuente + inspector
│   ├── inspectors/         # PDF; futuro: image / video / audio
│   ├── sources/            # drive, inline
│   ├── clientes/           # abad, abad_synthetic
│   ├── retention.py        # Purge de verifications viejas
│   └── scripts/            # cleanup_old_verifications.py
├── outputs/                # Reportes + SQLite local (gitignored)
├── docker/                 # Dockerfiles + infra
├── .github/workflows/      # CI (ci.yml)
├── requirements.txt
├── pytest.ini
├── README.md
└── .env.example
```

## Comandos esenciales

```bash
# Smoke E2E del modo sintético (sin BD, sin webhook real)
python juez/scripts/e2e_synthetic_smoke.py

# Listar inventarios reales disponibles para usar con --e2e-real-inventario-id
python -c "from juez.evaluation.contra_agente.synthetic.real_db_source import listar_inventarios_disponibles
for i in listar_inventarios_disponibles(): print(i)"

# Evaluar un flow de n8n en modo completo
python juez/evaluar_n8n.py https://n8n-dev.example.com/workflow/ID --completo

# Levantar el Verificador local
uvicorn verificador.app:app --port 8001 --reload

# Correr la suite completa de tests (267 tests)
python -m pytest verificador/tests/ juez/evaluation/contra_agente/synthetic/tests/ \
  juez/evaluation/contra_agente/tests/ juez/evaluation/tests/ \
  -p no:xdist -p no:rerunfailures
```

## Garantías operacionales (campo minado)

El sistema fue diseñado para correr sobre infraestructura productiva de clientes sin riesgo. Las siguientes garantías están enforced por código y tests:

- Cero `INSERT` / `UPDATE` / `DELETE` contra la BD productiva de clientes.
- Cero llamadas al webhook real de n8n en modo e2e sintético (se interceptan en el pool).
- Cero uploads a Drive, S3 ni a cualquier almacenamiento de archivos externo.
- Solo `SELECT` read-only contra la BD del cliente, con `SET TRANSACTION READ ONLY` y `statement_timeout` corto (5000 ms default).
- El cliente del Juez para la BD productiva está restringido a un allowlist explícito de tablas.
- Los tokens de modelos AI (OpenAI) son el único costo aceptable en cualquier modo.

## Tests

```bash
python -m pytest verificador/tests/ juez/evaluation/contra_agente/synthetic/tests/ \
  juez/evaluation/contra_agente/tests/ juez/evaluation/tests/ \
  -p no:xdist -p no:rerunfailures
```

Suite actual: 267 tests pasando (Juez + Verificador + sintético).

## Documentación interna

- [juez/README.md](juez/README.md) — Evaluador de agentes, contra-agente, modo e2e.
- [verificador/README.md](verificador/README.md) — Auditor de artefactos, retention, logging.
- [juez/METHODOLOGY.md](juez/METHODOLOGY.md) — Metodología de scoring del Juez.
