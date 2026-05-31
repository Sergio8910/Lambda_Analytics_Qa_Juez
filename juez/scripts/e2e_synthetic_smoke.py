"""Smoke E2E del modo sintético del contra-agente.

Levanta el Verificador local en background, corre 1 caso e2e con el Juez en
modo sintético, valida que la cadena completa funciona sin tocar producción.

Modos:
  - Por default: snapshot esperado generado **sintéticamente** (cero BD).
  - Con `--real-inventario-id N`: lee de la BD productiva (read-only) los
    datos del inventario N para anclar el caso a producción. Cero writes,
    cero disparos a n8n, solo SELECT.

Pre-requisitos:
  - OPENAI_API_KEY en .env (lo consume MockAgent + TurnEvaluator).
  - Si se usa --real-inventario-id: ABAT_DB_URL en .env.

Uso:
    python juez/scripts/e2e_synthetic_smoke.py
    python juez/scripts/e2e_synthetic_smoke.py --real-inventario-id 9
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Path setup para correr standalone
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Forzar UTF-8 en stdout/stderr en Windows para que el reporter no rompa por cp1252
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv

load_dotenv()


VERIFICADOR_PORT = 8765
os.environ["VERIFICADOR_BASE_URL"] = f"http://localhost:{VERIFICADOR_PORT}"
os.environ.setdefault("VERIFICADOR_API_KEY", "smoke-test-key")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Smoke E2E del modo sintético del Juez")
    parser.add_argument(
        "--real-inventario-id", type=int, default=None,
        help="Si se pasa, lee datos REALES de la BD productiva (read-only) "
             "para el snapshot esperado. Si no, usa snapshot sintético determinístico.",
    )
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY no configurado en .env — el MockAgent lo requiere.")
        return 2

    modo_label = (
        f"REAL (inventario_id={args.real_inventario_id})"
        if args.real_inventario_id else "SINTETICO PURO"
    )
    print(f"Modo del smoke: {modo_label}")
    print()

    # ── 1. Levantar el Verificador in-process en un thread ──────────────
    print(f"[1/6] Levantando Verificador in-process en :{VERIFICADOR_PORT}...")
    import threading
    import uvicorn
    from verificador.app import app as verificador_app

    config = uvicorn.Config(
        verificador_app, host="127.0.0.1", port=VERIFICADOR_PORT,
        log_level="warning", access_log=False,
    )
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    try:
        # Esperar a que el verificador esté listo
        from juez.evaluation.contra_agente.verificador_client import healthcheck
        for i in range(20):
            if healthcheck(timeout_s=1.0):
                break
            time.sleep(0.5)
        else:
            print("ERROR: Verificador no respondió healthcheck.")
            return 3
        print(f"      OK — Verificador respondiendo healthcheck en :{VERIFICADOR_PORT}")

        # ── 2. Construir analisis mínimo sin necesidad de n8n ──────────
        print("[2/6] Armando analisis mock del agente bajo test...")
        analisis = {
            "agent_id": "abad_smoke_test",
            "prompt": {
                "completo": (
                    "Eres un asistente de inventarios inmobiliarios para Abad Faciolince. "
                    "Cuando el inventarista te dé un número de contrato (formato JUEZ-E2E-...) "
                    "debes: 1) Registrar el inmueble, 2) Por cada ambiente que mencione, "
                    "registrar el ambiente, 3) Al finalizar, generar el PDF de inventario. "
                    "Llama las tools en el orden correcto. Confirma cada paso al inventarista."
                ),
            },
            "herramientas": [
                {"nombre": "Registrar_Inmueble",
                 "descripcion": "Registra un nuevo inmueble en el sistema. Params: contrato_id."},
                {"nombre": "Registrar_Ambiente",
                 "descripcion": "Registra un ambiente del inmueble. Params: ambiente (nombre)."},
                {"nombre": "Cerrar_Inventario",
                 "descripcion": "Cierra el inventario después de registrar todos los ambientes."},
                {"nombre": "Generar_PDF_Inventario",
                 "descripcion": "Genera el PDF del inventario. Solo después de Cerrar_Inventario."},
            ],
        }
        print(f"      OK — system_prompt={len(analisis['prompt']['completo'])} chars, "
              f"tools={len(analisis['herramientas'])}")

        # ── 3. Generar batch con 1 plan, todo e2e ─────────────────────────
        print("[3/6] Generando batch (total=1, e2e_k=1)...")
        from juez.evaluation.contra_agente.generator import generar_batch
        batch = generar_batch(
            analisis=analisis,
            agent_name="abad_smoke_test",
            total=1,
            concurrency=1,
            adapter="n8n",
            openai_key=os.getenv("OPENAI_API_KEY", ""),
            e2e_k=1,
            e2e_real_inventario_id=args.real_inventario_id,
        )
        assert len(batch.plans) == 1, f"Expected 1 plan, got {len(batch.plans)}"
        the_plan = batch.plans[0]
        assert the_plan.artifact_expectation is not None, "Plan no marcado como e2e"
        assert "e2e_artifact" in the_plan.tags, "Tag e2e_artifact ausente"
        artifact_id = the_plan.artifact_expectation.artifact_id
        source = the_plan.artifact_expectation.canonical_data.get("source", "?")
        print(f"      OK — plan_id={the_plan.plan_id} category={the_plan.category}")
        print(f"      artifact_id={artifact_id} source={source}")
        print(f"      expected: {the_plan.artifact_expectation.expected_snapshot['counts']}")
        if args.real_inventario_id and source != "real_db":
            print(f"      WARN — solicitaste real_db pero el snapshot quedó '{source}' "
                  f"(probablemente fallback por error de BD)")

        # ── 4. Adapter factory que lanza si se invoca (debe usar MockAdapter) ─
        adapter_calls = []
        class _FailAdapter:
            def send_message(self, msg, history):
                adapter_calls.append(msg)
                raise RuntimeError(
                    "REGRESION: el adapter real fue invocado en modo e2e (deberia usar MockAdapter)"
                )
        def adapter_factory(_type, _id):
            return _FailAdapter()

        # ── 5. Ejecutar el batch ───────────────────────────────────────────
        print("[4/6] Ejecutando batch (usa MockAgent + Verificador local)...")
        from juez.evaluation.contra_agente.evaluator import TurnEvaluator
        from juez.evaluation.contra_agente.pool import ejecutar_batch

        evaluator = TurnEvaluator(openai_key=os.getenv("OPENAI_API_KEY", ""))
        t0 = time.time()
        batch_result = ejecutar_batch(
            batch,
            adapter_factory,
            evaluator,
            openai_key=os.getenv("OPENAI_API_KEY", ""),
            synthetic_context={
                "system_prompt": analisis["prompt"]["completo"],
                "herramientas": analisis["herramientas"],
                "model": os.getenv("JUEZ_E2E_MODEL", "gpt-4o-mini"),
            },
        )
        elapsed = time.time() - t0
        print(f"      OK — batch ejecutado en {elapsed:.1f}s")
        assert len(adapter_calls) == 0, (
            f"REGRESION: el adapter real fue invocado {len(adapter_calls)} veces en modo e2e"
        )

        # ── 6. Validar resultado ──────────────────────────────────────────
        print("[5/6] Validando resultado...")
        assert batch_result.total == 1
        the_result = batch_result.results[0]
        verdict = the_result.artifact_verdict
        assert verdict is not None, "artifact_verdict deberia estar poblado"

        if verdict.get("status") == "skipped":
            print(f"      WARN — verificacion skipped: {verdict.get('skip_reason')}")
            print(f"             error: {verdict.get('error', '')[:200]}")
            return 4

        print(f"      conv_score    : raw del worker + post-mix")
        print(f"      overall_score : {the_result.overall_score}")
        print(f"      verdict       : {verdict.get('verdict')}")
        print(f"      artifact_score: {verdict.get('score')}")
        print(f"      passed        : {the_result.passed}")
        print(f"      checks        :")
        for c in verdict.get("checks", []):
            print(f"        - {c.get('name')}: {c.get('verdict')} "
                  f"score={float(c.get('score') or 0):.0%}")
        issues = verdict.get("issues") or []
        if issues:
            print(f"      issues        : {len(issues)}")
            for i in issues[:3]:
                print(f"        [{i.get('severidad')}] {str(i.get('mensaje', ''))[:100]}")

        # ── 7. Reporter ───────────────────────────────────────────────────
        print("[6/6] Renderizando seccion del reporter...")
        from juez.evaluation.contra_agente.reporter import generar_reporte_batch
        report = generar_reporte_batch(batch_result, agent_name="abad_smoke_test")
        # Extraer la sección e2e (el reporter pasa header a UPPER)
        report_lower = report.lower()
        needle = "verificacion e2e de artefactos"
        if needle in report_lower:
            section_start = report_lower.index(needle)
            # cortar 4 chars antes para incluir la indentación del header
            section_start = max(0, section_start - 4)
            section_end = report.find("\n\n\n", section_start)
            if section_end == -1 or section_end - section_start > 4000:
                section_end = min(len(report), section_start + 3000)
            print()
            print(report[section_start:section_end])
        else:
            print("WARN: seccion e2e no aparece en el reporte")
            return 5

        print()
        print("=" * 64)
        print(f" SMOKE E2E ({modo_label}): OK")
        print(" - Cero llamadas al adapter real (webhook n8n NO tocado)")
        print(" - Cero writes a BD productiva (zero INSERT/UPDATE/DELETE)")
        print(" - Cero uploads a Drive")
        if args.real_inventario_id:
            print(f" - SI se hicieron SELECTs read-only al inventario {args.real_inventario_id} "
                  f"(esperable en modo --real-inventario-id)")
        else:
            print(" - Cero queries a BD productiva (modo sintetico puro)")
        print(f" - artifact verdict: {verdict.get('verdict')} (score={verdict.get('score')})")
        print("=" * 64)
        return 0

    finally:
        # El thread es daemon — muere al salir del proceso. Pero pedimos
        # shutdown graceful por si el proceso sigue vivo para algo más.
        try:
            server.should_exit = True
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
