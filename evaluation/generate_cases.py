from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import sys

from openai import OpenAI

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("DEEPEVAL_TELEMETRY", "false")
os.environ.setdefault("POSTHOG_DISABLED", "1")
os.environ.setdefault("OPENAI_LOG", "error")
os.environ.setdefault("DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE", "300")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("deepeval").setLevel(logging.WARNING)

from settings import settings
from deepeval_judge import evaluate_response
from agent import run_agent
from evaluation.run_evaluation import run_all_cases

DEFAULT_OUTPUT = Path(__file__).with_name("generated_cases.json")
GENERATOR_MODEL = os.getenv("GENERATOR_MODEL", os.getenv("AGENT_MODEL", settings.JUDGE_MODEL))


def _parse_json_safe(text: str) -> List[Dict[str, Any]]:
    text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, list) else []
            except Exception:
                return []
    return []


def _build_prompt(topics: List[str], per_topic: int) -> str:
    topics_text = "\n".join(f"- {t}" for t in topics)
    return f"""
You are an evaluation case writer for LLM agents.
Generate realistic test cases for an agent that may or may not use RAG.
Return ONLY a JSON array. Do not include code fences or extra text.
Each element must be an object with fields:
  - input (string): the exact user message
  - expected_behavior (string): what a good agent should do/answer
  - context (array of strings): retrieval snippets; use [] if not needed; otherwise 1-3 short sentences
Constraints:
  - Provide exactly {per_topic} cases per topic.
  - Keep contexts short (<40 words each).
  - Avoid personally identifiable information.
Topics:
{topics_text}
""".strip()


def generate_cases(topics: List[str], per_topic: int) -> List[Dict[str, Any]]:
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    prompt = _build_prompt(topics, per_topic)
    completion = client.chat.completions.create(
        model=GENERATOR_MODEL,
        messages=[
            {"role": "system", "content": "You create concise LLM eval cases and return only JSON."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    raw = completion.choices[0].message.content or ""
    return _parse_json_safe(raw)


def generate_cases_from_inventory(path: Path) -> List[Dict[str, Any]]:
    contexto: List[str] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            inv = data.get("supermercado_euro", {})
            contexto = inv.get("contexto", []) or []
        except Exception:
            contexto = []
    return [
        {
            "input": "¿Qué productos de frutas y verduras tienen y sus precios?",
            "expected_behavior": "Listar frutas y verduras disponibles con precios del inventario.",
            "context": contexto,
        },
        {
            "input": "¿Cuáles son las promociones activas?",
            "expected_behavior": "Mencionar promociones vigentes tal como figuran en el inventario.",
            "context": contexto,
        },
        {
            "input": "¿Qué métodos de pago aceptan y cuáles son los horarios?",
            "expected_behavior": "Indicar métodos de pago y horarios exactos del inventario.",
            "context": contexto,
        },
        {
            "input": "Necesito leche y yogurt, ¿qué opciones tienen y precios?",
            "expected_behavior": "Responder con opciones de lácteos y sus precios según inventario.",
            "context": contexto,
        },
        {
            "input": "¿Tienen detergente líquido y papel higiénico? ¿Cuánto cuestan?",
            "expected_behavior": "Responder con productos de limpieza y precios según inventario.",
            "context": contexto,
        },
    ]


def _gen_user_message(
    client: OpenAI,
    topic: str,
    turn: int,
    history: List[Dict[str, str]],
    categories: List[str],
    products: List[str],
) -> str:
    history_text = "\n".join(f"{h['role']}: {h['content']}" for h in history[-6:])
    cats_text = ", ".join(categories) if categories else "productos generales"
    sample_products = ", ".join(products[:20]) if products else "detergente, leche, yogurt"
    prompt = (
        "Simula a un cliente del Supermercado Euro en una conversación continua. "
        f"Tema: {topic}. Turno {turn}. "
        "Reglas: responde SOLO con el próximo mensaje del usuario, en español, "
        "sin comillas ni explicaciones. Mantén coherencia con el historial, "
        "no repitas exactamente lo ya preguntado y avanza la conversación. "
        f"Categorías posibles: {cats_text}. "
        f"Ejemplos de productos reales: {sample_products}. "
        "Pide productos, precios, promociones u horarios de manera natural. "
        "No inventes categorías fuera del inventario (evita 'ecológicos', 'cuidado de la piel', "
        "'tamaño familiar' si no aparecen en la lista)."
    )
    completion = client.chat.completions.create(
        model=GENERATOR_MODEL,
        messages=[
            {
                "role": "system",
                "content": "Eres un cliente real. Solo escribe el siguiente mensaje del usuario.",
            },
            {"role": "user", "content": prompt},
            {"role": "user", "content": f"Historial reciente:\n{history_text}"},
        ],
        temperature=0.7,
    )
    return (completion.choices[0].message.content or "").strip()


def simulate_dialog(turns: int, topic: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    history: List[Dict[str, str]] = []
    evaluations: List[Dict[str, Any]] = []
    inv_path = ROOT_DIR / "inventarios.json"
    context: List[str] = []
    categories: List[str] = []
    products: List[str] = []
    if inv_path.exists():
        try:
            data = json.loads(inv_path.read_text(encoding="utf-8"))
            context = data.get("supermercado_euro", {}).get("contexto", []) or []
            for line in context:
                if ":" in line:
                    categories.append(line.split(":", 1)[0].strip())
                    items = line.split(":", 1)[1]
                    for part in items.split(","):
                        name = part.strip().split("$")[0].strip()
                        if name and name not in products:
                            products.append(name)
        except Exception:
            context = []
            categories = []
            products = []

    for t in range(1, turns + 1):
        user_msg = _gen_user_message(client, topic, t, history, categories, products)
        if not user_msg:
            fallback = [
                "Hola, ¿qué productos de limpieza tienen y sus precios?",
                "¿Tienen leche y yogurt? ¿Cuánto cuestan?",
                "¿Qué promociones tienen hoy?",
                "¿Cuáles son los horarios y métodos de pago?",
                "¿Tienen fruta fresca y verduras? ¿Precios?",
                "Gracias, eso es todo por ahora.",
            ]
            user_msg = fallback[min(t - 1, len(fallback) - 1)]
        history.append({"role": "user", "content": user_msg})

        agent_reply = run_agent(user_msg).get("response", "")
        history.append({"role": "assistant", "content": agent_reply})

        expected = (
            f"Responde de forma directa y concisa a la solicitud. "
            f"No repitas la pregunta. Usuario: {user_msg}"
        )
        report = evaluate_response(
            user_input=user_msg,
            model_output=agent_reply,
            expected_behavior=expected,
            context=context,
        )
        evaluations.append(
            {
                "turn": t,
                "user": user_msg,
                "assistant": agent_reply,
                "metrics": report["metrics"],
                "recommendation": report["recommendation"],
            }
        )

    summary = {
        "turns": turns,
        "topic": topic,
        "passes": sum(all(m["success"] for m in ev["metrics"]) for ev in evaluations),
        "fails": sum(any(not m["success"] for m in ev["metrics"]) for ev in evaluations),
    }
    return evaluations, summary


def _print_metrics(ev: Dict[str, Any]) -> None:
    for m in ev["metrics"]:
        status = "OK" if m["success"] else "FALLO"
        score = "n/a" if m["score"] is None else f"{m['score']:.3f}"
        thr = "n/a" if m["threshold"] is None else f"{m['threshold']:.3f}"
        print(f"  - {m['name']}: {status} puntaje={score} umbral={thr}")
        if m.get("reason"):
            print(f"    motivo: {m['reason']}")


def _print_dialog_report(evaluations: List[Dict[str, Any]], summary: Dict[str, Any]) -> None:
    print(f"Resumen diálogo: {summary}")
    print("")
    for ev in evaluations:
        status = "OK" if all(m["success"] for m in ev["metrics"]) else "FALLO"
        print(f"Turno {ev['turn']} [{status}]")
        print(f"Usuario: {ev['user']}")
        print(f"Asistente: {ev['assistant']}")
        _print_metrics(ev)
        print("Recomendación:")
        print(ev["recommendation"])
        print("-" * 60)
    total_turns = summary.get("turns", len(evaluations))
    passes = summary.get("passes", 0)
    fails = summary.get("fails", 0)
    print(f"Resultado: {passes}/{total_turns} turnos OK; {fails} fallaron.")


def save_cases(cases: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=True, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate DeepEval test cases using OpenAI.")
    parser.add_argument("--topic", action="append", dest="topics", help="Topic to generate cases for. Can repeat.")
    parser.add_argument("--per-topic", type=int, default=2, help="Cases per topic (default: 2)")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT), help="Output JSON path")
    parser.add_argument("--simulate-dialog", type=int, default=0, help="Simulate N user/assistant turns and evaluate.")
    parser.add_argument("--dialog-topic", type=str, default="general QA", help="Topic for simulated dialog.")
    parser.add_argument("--from-inventory", action="store_true", help="Generate cases from inventarios.json")
    parser.add_argument("--run-eval", action="store_true", help="Run evaluation after generating cases")
    args = parser.parse_args()

    if args.simulate_dialog > 0:
        evaluations, summary = simulate_dialog(args.simulate_dialog, args.dialog_topic)
        _print_dialog_report(evaluations, summary)
        return 0

    if args.from_inventory:
        inv_path = ROOT_DIR / "inventarios.json"
        cases = generate_cases_from_inventory(inv_path)
    else:
        topics = args.topics or ["RAG basics", "General Q&A"]
        cases = generate_cases(topics, args.per_topic)
    if not cases:
        print("No cases generated; check API key or model.")
        return 1

    save_cases(cases, Path(args.output))
    print(f"Generated {len(cases)} cases to {args.output}")
    if args.run_eval:
        run_all_cases(export=False, print_report=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())