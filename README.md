# Juez — Lambda Analytics

Sistema interno de evaluación automatizada de agentes IA (ElevenLabs, n8n, pipelines).

## Documentación

- **[EL_JUEZ.txt](EL_JUEZ.txt)** — Documento maestro: qué es, qué evalúa y cómo se usa.
- **[API.txt](API.txt)** — Documentación completa de la API REST async.
- **[METHODOLOGY.md](METHODOLOGY.md)** — Metodología técnica de evaluación de agentes de voz.
- **[README_EXTENSO.md](README_EXTENSO.md)** — Arquitectura interna del motor `evaluation/`.
- **[PLAN_TRANSFORMACION_EJECUTABLE.md](PLAN_TRANSFORMACION_EJECUTABLE.md)** — Plan de evolución a plataforma escalable.
- **[planeacion_juez.txt](planeacion_juez.txt)** — Visión n8n-céntrica de orquestación.

## Uso rápido

CLI:
```bash
python evaluar_elevenlabs.py   # agentes ElevenLabs
python evaluar_n8n.py          # workflows n8n
python evaluar_pipeline.py     # pipelines combinados
python deepeval_judge.py       # evaluador genérico DeepEval
```

API:
```bash
python start_api.py
# luego ver API.txt para endpoints /api/v1/*
```
