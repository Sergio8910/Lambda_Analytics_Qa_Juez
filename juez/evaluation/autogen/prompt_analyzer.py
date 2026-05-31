from __future__ import annotations

import re

from .schemas import PromptProfile


_STOPWORDS = {
    "el",
    "la",
    "los",
    "las",
    "un",
    "una",
    "unos",
    "unas",
    "de",
    "del",
    "y",
    "o",
    "en",
    "para",
    "por",
    "con",
    "sin",
    "que",
    "es",
    "a",
    "al",
    "se",
    "su",
    "sus",
    "tu",
    "tus",
    "mi",
    "mis",
}


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-záéíóúñ0-9]+", text.lower())
    return [t for t in tokens if len(t) > 2 and t not in _STOPWORDS]


def _detect_domain(tokens: list[str]) -> str:
    buckets = {
        "retail": ["supermercado", "inventario", "producto", "precio", "tienda", "compra", "carrito"],
        "support": ["soporte", "tecnico", "técnico", "impresora", "router", "computadora", "error", "problema"],
        "autos": ["auto", "vehiculo", "vehículo", "carro", "mantenimiento", "motor", "aceite", "neumatico"],
        "finance": ["banco", "tarjeta", "cuenta", "transaccion", "transacción", "pago", "credito", "crédito"],
        "travel": ["viaje", "hotel", "vuelo", "reserva", "itinerario", "equipaje"],
    }
    best = "general"
    best_score = 0
    for name, words in buckets.items():
        score = sum(1 for w in words if w in tokens)
        if score > best_score:
            best_score = score
            best = name
    return best


def analyze_prompt(prompt_base: str) -> PromptProfile:
    text = (prompt_base or "").lower()
    tokens = _tokenize(text)
    requires_json = "json" in tokens or "schema" in tokens
    strict_format = "formato" in tokens or "estructura" in tokens
    forbids_markdown = "markdown" in tokens and ("no" in tokens or "sin" in tokens)
    mentions_context = (
        "contexto" in tokens or "retrieval" in tokens or "basado" in tokens or "fuente" in tokens
    )
    language = "unknown"
    if "english" in tokens or "ingles" in tokens or "inglés" in tokens:
        language = "en"
    if "español" in tokens or "espanol" in tokens:
        language = "es"
    output_format_hint = None
    if "producto" in tokens and "precio" in tokens:
        output_format_hint = "Producto: Precio"
    elif requires_json:
        output_format_hint = "JSON"
    elif "bullet" in tokens or "viñetas" in tokens or "vinetas" in tokens:
        output_format_hint = "bullet points"
    strictness = "high" if strict_format and forbids_markdown else "med"
    if "estricto" in tokens or "obligatorio" in tokens:
        strictness = "high"
    domain = _detect_domain(tokens)
    keywords = tokens[:8]
    return PromptProfile(
        language=language,
        requires_json=requires_json,
        forbids_markdown=forbids_markdown,
        context_dependency=mentions_context,
        output_format_hint=output_format_hint,
        strictness=strictness,
        domain=domain,
        keywords=keywords,
    )
