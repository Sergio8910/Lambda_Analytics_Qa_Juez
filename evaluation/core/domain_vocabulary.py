"""Vocabulario de dominio para métricas heurísticas del Juez.

Algunas métricas (ambigüedad, pregunta aclaratoria, soporte de claims por
categoría) dependen de vocabulario específico del dominio del agente. Antes
estaba hardcodeado para supermercado/e-commerce dentro del engine, lo que
sesgaba la evaluación de agentes de otros dominios (inventario inmobiliario,
delivery, salud, etc.).

Diseño:
  - Cada `DomainVocabulary` agrupa los términos que las heurísticas usan.
  - `EMPTY` es el default: no asume nada. Las métricas que dependen de
    vocabulario se omiten en lugar de devolver falsos negativos.
  - El registro `_VOCABULARIES` mapea ids ("supermercado", "inmuebles", ...)
    a instancias. Se invoca con `get_vocabulary(domain_id)`.
  - Para agregar un agente de otro dominio: registrar un vocab nuevo o pasarlo
    explícito vía `EvaluationSpec.domain_vocabulary_id`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet


@dataclass(frozen=True)
class DomainVocabulary:
    """Bolsa de términos por dominio para heurísticas del engine.

    Todos los campos son frozensets para que la instancia sea hashable y se
    pueda compartir entre threads sin riesgo.
    """
    id: str
    # Entidades concretas del dominio. Si el input del usuario las menciona,
    # se considera que NO es una solicitud ambigua (no necesita aclaración).
    specific_entities: FrozenSet[str] = field(default_factory=frozenset)
    # Patrones que marcan un input como ambiguo (sin entidad concreta).
    ambiguous_patterns: FrozenSet[str] = field(default_factory=frozenset)
    # Palabras que indican que el output del agente está aclarando dudas
    # (además del signo "?" que es universal).
    clarifying_terms: FrozenSet[str] = field(default_factory=frozenset)
    # Categorías del dominio. Si un claim del agente menciona una categoría
    # y el contexto también, el claim se considera soportado.
    categories: FrozenSet[str] = field(default_factory=frozenset)

    def is_empty(self) -> bool:
        """True si este vocab no tiene términos — las heurísticas dependientes
        deben omitirse en lugar de aplicarse sin datos."""
        return not (
            self.specific_entities
            or self.ambiguous_patterns
            or self.clarifying_terms
            or self.categories
        )


# Default: sin asunciones de dominio. Métricas que dependen de vocabulario
# se omitirán explícitamente cuando este vocab esté activo.
EMPTY = DomainVocabulary(id="empty")


# Vocabulario heredado de Lía Euro / Pickers (e-commerce / supermercado).
# Antes estaba como constantes del engine; ahora es opt-in vía spec.
SUPERMERCADO = DomainVocabulary(
    id="supermercado",
    specific_entities=frozenset({
        "detergente", "jabon", "leche", "yogurt", "arroz", "azucar",
        "aceite", "cafe", "pan", "queso", "pollo", "carne",
        "pasta", "agua", "jugo", "refresco", "huevos",
    }),
    ambiguous_patterns=frozenset({
        "recomienda", "recomendar", "recomendacion", "sugerir", "sugiere",
        "que me recomiendas", "que recomiendas",
        "algo", "alguna", "alguno",
        "cual es mejor", "que me conviene", "quiero comprar",
    }),
    clarifying_terms=frozenset({
        "cual", "que marca", "que tamano", "cuantas", "cuantos", "referencia",
    }),
    categories=frozenset({
        "frutas y verduras", "frutas", "verduras", "carnes", "lacteos",
        "panaderia", "bebidas", "limpieza", "higiene",
        "congelados", "snacks", "panes",
    }),
)


# Vocabulario para agentes de inventario de inmuebles (Abad y similares).
# Los términos vienen del flujo mvp_abad_telegram: ambientes, items de
# inventario, comandos típicos del inventarista.
INMUEBLES = DomainVocabulary(
    id="inmuebles",
    specific_entities=frozenset({
        "sala", "cocina", "baño", "bano", "habitacion", "habitación",
        "comedor", "hall", "entrada", "balcon", "balcón", "patio",
        "armario", "cama", "mesa", "silla", "sillon", "sillón",
        "nevera", "refrigerador", "estufa", "lavadora", "televisor",
        "ambiente", "inmueble", "contrato", "inventario", "propietario",
        "item",
    }),
    ambiguous_patterns=frozenset({
        "algo", "alguna", "alguno", "que hago", "como sigo",
    }),
    clarifying_terms=frozenset({
        "que ambiente", "cual ambiente", "que estado", "en que estado",
        "cuantos items", "que items",
    }),
    categories=frozenset({
        "ambientes", "items", "muebles", "electrodomesticos",
        "documentos", "fotos", "audio", "video",
    }),
)


_VOCABULARIES: Dict[str, DomainVocabulary] = {
    "empty":        EMPTY,
    "supermercado": SUPERMERCADO,
    "inmuebles":    INMUEBLES,
}


def get_vocabulary(domain_id: str | None) -> DomainVocabulary:
    """Resuelve un id de dominio a su DomainVocabulary. Si no existe o es
    None, retorna EMPTY (sin asumir vocabulario de ningún dominio)."""
    if not domain_id:
        return EMPTY
    return _VOCABULARIES.get(domain_id.lower(), EMPTY)


def register_vocabulary(vocab: DomainVocabulary) -> None:
    """Registra un vocabulario nuevo. Pensado para que un agente pueda
    declarar el suyo en su módulo de adaptador sin tocar este archivo."""
    _VOCABULARIES[vocab.id.lower()] = vocab
