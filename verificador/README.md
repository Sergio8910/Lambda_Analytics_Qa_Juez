# Verificador

Servicio post-ejecución que audita artefactos (PDFs, imágenes, etc.) generados por flows productivos contra lo que la BD del cliente dice que deberían contener.

**No es el Juez.** El Juez evalúa al agente y su configuración. El Verificador audita el artefacto real que produce el sistema completo, **después** de que el flow corrió en producción. Son productos complementarios.

## Restricciones operativas (campo minado)

- **Read-only sobre todo lo que no sea su propia BD.** Nunca escribe a Drive, BD de clientes, ni dispara flows.
- **Credenciales solo en `.env`** local (gitignored). Nunca en código, logs o respuestas HTTP.
- **El nodo n8n que llama al verificador debe ir con `continueOnFail`** para que un fallo del verificador nunca afecte producción.
- **Conexión a BD de cliente con usuario dedicado read-only** (ej. `verificador_ro`), nunca con superuser.
- **Logs estructurados sin PII**: solo IDs, conteos, verdicts. Nunca payloads ni queries con valores.
- **PDFs nunca se almacenan**. Se descargan en memoria, se inspeccionan, se descartan.

## Cómo correr localmente

```bash
# Carga variables del .env del root
uvicorn verificador.app:app --port 8001 --reload

# Smoke
curl http://localhost:8001/health
```

## Variables de entorno

| Var | Requerido | Default | Uso |
|---|---|---|---|
| `DATABASE_URL` | Sí (cuando se conecte storage) | — | BD propia, schema `verificador` |
| `ABAT_DB_URL` | Sí (para cliente Abad) | — | BD read-only de Abad |
| `GOOGLE_OAUTH_TOKEN` | Sí (para fuente Drive) | — | Descarga de archivos en Drive |
| `VERIFICADOR_API_KEY` | Sí | — | Header `X-Verifier-Key` para autenticar webhooks |
| `VERIFICADOR_PORT` | No | `8001` | Puerto de la app |
| `VERIFICADOR_DRIVE_TIMEOUT_S` | No | `60` | Timeout de descarga de Drive |
| `VERIFICADOR_DRIVE_RETRY_DELAY_S` | No | `3` | Backoff base para retries por eventual consistency |
| `VERIFICADOR_DRIVE_RETRY_MAX` | No | `3` | Reintentos máximos de descarga |
| `VERIFICADOR_CLIENT_DB_TIMEOUT_MS` | No | `5000` | Statement timeout sobre BD del cliente |
| `VERIFICADOR_LOG_LEVEL` | No | `INFO` | Nivel de logging |

## Estructura

```
verificador/
├── app.py                # FastAPI standalone, levanta en :8001
├── router.py             # POST /verify, GET /verify/{id}, GET /health
├── verifier.py           # Orquestador: cliente + fuente + inspector + storage
├── schemas.py            # Pydantic v2
├── settings.py           # env vars
├── storage.py            # SQLAlchemy, tabla verificador.verifications
├── jobs.py               # JobStore async (copia de api/jobs.py)
├── sources/              # De dónde se descarga el artefacto (Drive, futuro: S3, HTTP)
├── clientes/             # Adapter por cliente (Abad hoy, otros mañana)
└── inspectors/           # Por tipo de artefacto (PDF hoy, image/video/audio mañana)
```

## Cliente `abad_synthetic` + source `inline`

El Verificador soporta un modo sintético usado por el Juez durante evaluaciones e2e. En este modo no consulta ninguna BD productiva: recibe el snapshot esperado dentro del `metadata` del request y audita el PDF que el Juez le manda en base64 inline.

El payload tiene esta forma:

```json
{
  "cliente": "abad_synthetic",
  "artifact_type": "pdf",
  "source": {
    "type": "inline",
    "blob_base64": "JVBERi0xLjQK..."
  },
  "metadata": {
    "expected_snapshot": {
      "inventario_id": 9,
      "ambientes": [
        {"nombre": "cocina", "items": [{"descripcion": "nevera", "fotos": 1}]}
      ]
    }
  }
}
```

Flujo:

1. El Juez genera el PDF sintético con `pdf_builder` y lo serializa en base64.
2. Llama `POST /verificador/verify` con `cliente: "abad_synthetic"` y el snapshot en `metadata`.
3. El cliente `abad_synthetic` lee el snapshot del metadata (no toca BD), el source `inline` decodifica el blob, el inspector PDF lo audita.
4. La respuesta incluye verdict + diagnósticos que el Juez incorpora a su score final.

## Política de retention

La tabla `verificador.verifications` crece sin límite por diseño. Para evitar que la BD del Verificador se infle, se incluye un script de purga manual o agendable por cron.

```bash
# Preview: cuenta filas afectadas, no borra nada
python verificador/scripts/cleanup_old_verifications.py --days 90 --dry-run

# Borrar de verdad (pide confirmación interactiva)
python verificador/scripts/cleanup_old_verifications.py --days 90

# Borrar de verdad sin prompt (cron / CI)
python verificador/scripts/cleanup_old_verifications.py --days 90 --yes
```

El script es idempotente: correrlo dos veces seguidas con el mismo `--days` no tiene efecto adicional la segunda vez. Default `--days 90`.

## Logging estructurado

Por default el Verificador loguea en texto plano. Setear la variable de entorno `LOG_FORMAT=json` para emitir una línea JSON por record, con campos canónicos (`ts`, `level`, `logger`, `msg`, `file`, `line`) más cualquier atributo extra que se pase vía `logger.info(..., extra={...})`.

En ambos formatos, los valores asociados a claves sensibles se redactan automáticamente en el output. Las claves cubiertas (case-insensitive) son: `password`, `token`, `key`, `secret`, `authorization`, `api_key`. El patrón reemplaza secuencias del tipo `token=xxx` o `password: xxx` por `token=***REDACTED***`.

Ejemplo de salida con `LOG_FORMAT=json`:

```json
{"ts": "2026-05-30T18:42:11.220+00:00", "level": "INFO", "logger": "verificador.router", "msg": "verify ok", "file": "router.py", "line": 87, "verification_id": "verif_abc123"}
```

## Estado actual

MVP en desarrollo. Ver [el plan completo](../../.claude/plans/ok-como-podemos-empezar-flickering-ripple.md) para alcance, riesgos y validación pre-producción.
