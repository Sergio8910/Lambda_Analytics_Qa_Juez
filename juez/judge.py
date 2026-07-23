import json
import langwatch
from juez.llm_client import make_chat_client, usando_claude, usando_ordo
from juez.settings import settings


# ==============================
# SETUP LANGWATCH (si está habilitado)
# ==============================

if settings.ENABLE_LANGWATCH and settings.LANGWATCH_API_KEY:
    langwatch.setup(api_key=settings.LANGWATCH_API_KEY)


# ==============================
# OPENAI CLIENT
# ==============================

client = make_chat_client(api_key=settings.OPENAI_API_KEY)


# ==============================
# PROMPT BUILDER
# ==============================

def build_judge_prompt(input_text: str, output_text: str) -> str:
    return f"""
Eres un evaluador experto de aplicaciones con LLM.

Evalúa la RESPUESTA del modelo según:

- relevance (0-10): ¿Responde a lo pedido?
- clarity (0-10): ¿Está bien estructurada y clara?
- correctness (0-10): ¿Es correcta o inventa?
- overall (0-10): Evaluación global

También entrega una recomendación concreta para mejorar el prompt si el score no es perfecto.

Devuelve ÚNICAMENTE JSON válido con esta estructura EXACTA:

{{
  "relevance": int,
  "clarity": int,
  "correctness": int,
  "overall": int,
  "recommendation": "string"
}}

INPUT:
{input_text}

OUTPUT:
{output_text}
""".strip()


# ==============================
# JSON SAFE PARSER
# ==============================

def parse_json_safe(text: str):
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start:end+1])
        raise ValueError("No se pudo parsear JSON del juez")


# ==============================
# MAIN JUDGE FUNCTION
# ==============================

@langwatch.trace(name="ai_eval_judge")
def run_judge(input_text: str, output_text: str) -> dict:
    prompt = build_judge_prompt(input_text, output_text)
    # Autotrack llamadas OpenAI dentro del trace
    if settings.ENABLE_LANGWATCH and not usando_claude() and not usando_ordo():
        langwatch.get_current_trace().autotrack_openai_calls(client)
    completion = client.chat.completions.create(
        model=settings.JUDGE_MODEL,
        messages=[
            {"role": "system", "content": "Eres un juez estricto y objetivo. Devuelve solo JSON."},
            {"role": "user", "content": prompt}
        ],
        temperature=0,
    )
    raw_response = completion.choices[0].message.content
    parsed = parse_json_safe(raw_response)
    return parsed
