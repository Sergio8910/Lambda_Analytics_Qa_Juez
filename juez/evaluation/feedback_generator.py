from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

from .report_models import (
    CaseFeedback,
    ClaimAnalysis,
    MetricResult,
    OverallFeedback,
    PromptEdit,
    PromptImprovement,
    PromptPatch,
    QuestionFeedback,
    RagAudit,
    RagIssue,
    TaskContract,
)

_STOPWORDS_ES = {
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
    "a",
    "en",
    "que",
    "por",
    "para",
    "con",
    "sobre",
    "si",
    "es",
    "son",
    "al",
    "lo",
    "su",
    "sus",
}

_STOPWORDS_EN = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "on",
    "for",
    "with",
    "is",
    "are",
    "be",
    "this",
    "that",
    "it",
    "as",
}

_PREFIJOS_PREGUNTA = [
    "segun el contexto",
    "segun contexto",
    "por favor",
    "responde con",
    "responde",
    "dime",
    "indica",
    "por favor indica",
]

_PRICE_RE = re.compile(r"\$\s*\d+(?:\.\d+)?")

_ENTIDADES_DOMINIO = [
    "detergente",
    "jabon",
    "leche entera",
    "leche",
    "yogurt",
    "arroz",
    "azucar",
    "aceite",
    "cafe",
    "pan",
    "queso",
    "jamon",
    "pollo",
    "carne",
    "pasta",
    "salsa",
    "agua",
    "jugo",
    "refresco",
    "huevos",
    "frutas",
    "verduras",
    "papel higienico",
    "shampoo",
    "desinfectante",
    "limpiavidrios",
    "esponjas",
    "harina",
    "sal",
    "mantequilla",
]

_ENTIDADES_BASURA = {
    "necesito",
    "quiero",
    "busco",
    "precio",
    "precios",
    "producto",
    "productos",
    "promocion",
    "promociones",
    "contexto",
    "segun",
    "por favor",
    "responde",
    "dime",
    "indica",
}


def _normalizar(texto: str) -> str:
    texto = texto.lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


def _normalizar_lineal(texto: str) -> str:
    # Preserva longitud para alinear indices con el texto original.
    salida = []
    for ch in texto.lower():
        dec = unicodedata.normalize("NFKD", ch)
        base = "".join(c for c in dec if not unicodedata.combining(c))
        salida.append(base[0] if base else "")
    return "".join(salida)


def _tokenizar(texto: str) -> List[str]:
    texto = _normalizar(texto)
    return re.findall(r"[a-z0-9]+", texto)


def detect_language_es(texto: str) -> str:
    tokens = _tokenizar(texto)
    if not tokens:
        return "es"
    es_stop = sum(1 for t in tokens if t in _STOPWORDS_ES)
    en_stop = sum(1 for t in tokens if t in _STOPWORDS_EN)
    ratio_es = es_stop / max(len(tokens), 1)
    ratio_en = en_stop / max(len(tokens), 1)
    if ratio_es >= 0.08:
        return "es"
    if ratio_en >= 0.08:
        return "en"
    if ratio_es >= 0.03 or ratio_en >= 0.03:
        return "mixed"
    return "es"


def parse_subquestions(texto: str) -> List[str]:
    partes = re.split(r"\?", texto)
    preguntas = []
    for p in partes:
        p = p.strip()
        if not p:
            continue
        preguntas.append(p)
    return preguntas


def _limpiar_pregunta(texto: str) -> str:
    t = _normalizar(texto)
    t = t.replace("¿", "").replace("?", "").strip()
    for pref in _PREFIJOS_PREGUNTA:
        if t.startswith(pref):
            t = t[len(pref) :].strip()
    return t


def _extract_entity(q_norm: str) -> Optional[str]:
    mejor = None
    for ent in _ENTIDADES_DOMINIO:
        if ent in q_norm:
            if not mejor or len(ent) > len(mejor):
                mejor = ent
    if mejor:
        return mejor
    tokens = [
        t
        for t in _tokenizar(q_norm)
        if t not in _STOPWORDS_ES
        and len(t) > 3
        and t not in _ENTIDADES_BASURA
    ]
    if not tokens:
        return None
    # Fallback por keyword más informativa (longitud máxima)
    return sorted(tokens, key=len, reverse=True)[0]


def _extract_expected_units(q_norm: str) -> Dict[str, bool]:
    return {
        "kg": bool(re.search(r"\b1\s*kg\b|\bkg\b", q_norm)),
        "l": bool(re.search(r"\b1\s*l\b|\b1l\b|\bl\b", q_norm)),
        "ml": bool(re.search(r"\bml\b", q_norm)),
        "x": bool(re.search(r"\bx\s*\d+\b", q_norm)),
    }


def _expected_unit_str(q_norm: str) -> Optional[str]:
    units = _extract_expected_units(q_norm)
    if units.get("kg"):
        return "kg"
    if units.get("l"):
        return "l"
    if units.get("ml"):
        return "ml"
    if units.get("x"):
        return "x"
    return None


def _find_positions(pattern: re.Pattern, text: str) -> List[Tuple[int, int]]:
    return [(m.start(), m.end()) for m in pattern.finditer(text)]


def _nearby_price(output_norm: str, entity_pos: List[Tuple[int, int]], unit_pos: List[Tuple[int, int]], window: int = 120) -> Optional[Tuple[int, int]]:
    price_pos = _find_positions(_PRICE_RE, output_norm)
    if not price_pos:
        return None
    anchors = entity_pos + unit_pos
    if not anchors:
        return price_pos[0]
    best = None
    best_dist = 10**9
    for ps, pe in price_pos:
        for as_, ae in anchors:
            dist = min(abs(ps - ae), abs(as_ - pe))
            if dist < best_dist:
                best_dist = dist
                best = (ps, pe)
    if best_dist <= window:
        return best
    return None


def _extract_snippet(output: str, pos: Tuple[int, int], radius: int = 80) -> str:
    start = max(pos[0] - radius, 0)
    end = min(pos[1] + radius, len(output))
    return output[start:end].strip()


def _expected_from_context(ctx: List[str], entity: Optional[str], expected_units: Dict[str, bool]) -> str:
    if not ctx or not entity:
        return ""
    q_unit = None
    if expected_units.get("kg"):
        q_unit = "kg"
    elif expected_units.get("l"):
        q_unit = "l"
    elif expected_units.get("ml"):
        q_unit = "ml"
    elif expected_units.get("x"):
        q_unit = "x"
    for chunk in ctx:
        c_norm = _normalizar(chunk)
        if entity in c_norm and _PRICE_RE.search(c_norm):
            if not q_unit or re.search(rf"\b{q_unit}\b|\b1{q_unit}\b", c_norm):
                return chunk.strip()[:200]
    return ""


def _formato_directo(output: str) -> bool:
    for line in output.splitlines():
        if re.search(r"^[^\n:]{2,}:\s*\$?\d", line.strip().lower()):
            return True
    if re.search(r"\b\w+:\s*\$?\d", output.lower()):
        return True
    return False


def _find_metric(metrics: List[MetricResult], name: str) -> Optional[MetricResult]:
    for m in metrics:
        if m.name == name:
            return m
    return None


def _add_edit(edits: List[PromptEdit], edit: PromptEdit) -> None:
    nuevo = _normalizar(edit.proposed_text)
    for existing in edits:
        if existing.edit_type == edit.edit_type and existing.proposed_text == edit.proposed_text:
            return
        if (
            edit.priority == "P0"
            and "no sustituyas" in nuevo
            and ("unidades" in nuevo or "presentaciones" in nuevo)
        ):
            existente = _normalizar(existing.proposed_text)
            if (
                existing.priority == "P0"
                and "no sustituyas" in existente
                and ("unidades" in existente or "presentaciones" in existente)
            ):
                return
    edits.append(edit)


def build_case_feedback(
    user_input: str,
    output: str,
    tags: List[str],
    metrics: List[MetricResult],
    claim_analysis: Optional[ClaimAnalysis],
    retrieval_context: Optional[List[str]] = None,
    contract: Optional[TaskContract] = None,
) -> CaseFeedback:
    tags_lower = [t.lower() for t in tags]
    output_norm = _normalizar_lineal(output)
    ctx = retrieval_context or []

    # Question-by-question
    q_feedback: List[QuestionFeedback] = []
    preguntas = parse_subquestions(user_input)
    for q in preguntas:
        q_norm = _limpiar_pregunta(q)
        entity = _extract_entity(q_norm)
        expected_units = _extract_expected_units(q_norm)

        entity_positions: List[Tuple[int, int]] = []
        if entity and entity in output_norm:
            for m in re.finditer(re.escape(entity), output_norm):
                entity_positions.append((m.start(), m.end()))

        unit_pos_kg = _find_positions(re.compile(r"\b1\s*kg\b|\bkg\b"), output_norm)
        unit_pos_l = _find_positions(re.compile(r"\b1\s*l\b|\b1l\b|\bl\b"), output_norm)
        unit_pos_ml = _find_positions(re.compile(r"\bml\b"), output_norm)
        unit_pos_x = _find_positions(re.compile(r"\bx\s*\d+\b"), output_norm)

        expected_unit_positions: List[Tuple[int, int]] = []
        if expected_units.get("kg"):
            expected_unit_positions += unit_pos_kg
        if expected_units.get("l"):
            expected_unit_positions += unit_pos_l
        if expected_units.get("ml"):
            expected_unit_positions += unit_pos_ml
        if expected_units.get("x"):
            expected_unit_positions += unit_pos_x

        entity_center = entity_positions[0][0] if entity_positions else None
        unit_near = None
        if entity_center is not None and expected_unit_positions:
            unit_near = min(expected_unit_positions, key=lambda p: abs(p[0] - entity_center))
            if abs(unit_near[0] - entity_center) > 200:
                unit_near = None

        center = unit_near[0] if unit_near else (entity_center if entity_center is not None else None)

        price_positions = _find_positions(_PRICE_RE, output_norm)
        price_pos = None
        if center is not None and price_positions:
            price_pos = min(price_positions, key=lambda p: abs(p[0] - center))
        price_near = bool(price_pos and center is not None and abs(price_pos[0] - center) <= 120)

        has_entity = bool(entity_positions)
        unit_required = any(expected_units.values())
        has_expected_unit = bool(unit_near) if unit_required else True

        answered = bool(has_entity and has_expected_unit and price_near)
        answer_snippet = _extract_snippet(output, price_pos) if price_pos else ""

        expected_ctx = _expected_from_context(ctx, entity, expected_units)

        verdict = "correcto"
        suggestion = ""
        if not answered:
            any_unit_near = False
            if entity_center is not None:
                all_units = unit_pos_kg + unit_pos_l + unit_pos_ml + unit_pos_x
                for pos in all_units:
                    if abs(pos[0] - entity_center) <= 200:
                        any_unit_near = True
                        break
            if has_entity and unit_required and not has_expected_unit and any_unit_near:
                verdict = "incorrecto_por_unidad"
                suggestion = "Respeta la unidad/presentacion solicitada."
            else:
                verdict = "no_respondido"
                suggestion = "Responde esta subpregunta de forma explicita."
        else:
            verdict = "correcto"

        q_feedback.append(
            QuestionFeedback(
                question=q.strip(),
                answered=answered,
                answer_snippet=answer_snippet,
                expected_from_context=expected_ctx,
                verdict=verdict,
                suggestion=suggestion,
            )
        )

    question_failures = [q for q in q_feedback if q.verdict in {"no_respondido", "incorrecto_por_unidad"}]
    unit_mismatch = any(q.verdict == "incorrecto_por_unidad" for q in q_feedback)
    language = detect_language_es(output)
    formato_ok = _formato_directo(output)
    unsupported_metric = _find_metric(metrics, "unsupported_claims")
    unsupported_fail = bool(unsupported_metric and not unsupported_metric.success)
    penalize_numbers = bool(unsupported_metric and unsupported_metric.raw.get("penalize_numbers"))
    penalize_numbers_triggered = False
    if penalize_numbers and claim_analysis:
        for c in claim_analysis.claims:
            if re.search(r"\d", c.text) and c.verdict != "supported":
                penalize_numbers_triggered = True
                break
    rag_fail = ("rag_grounding" in tags_lower) and any(
        m.name in {"format_compliance", "instruction_adherence", "task_success"} and not m.success
        for m in metrics
    )

    edits: List[PromptEdit] = []

    if question_failures:
        _add_edit(
            edits,
            PromptEdit(
                priority="P0",
                problem="Se detectaron subpreguntas sin respuesta explicita.",
                edit_type="add_rule",
                proposed_text="Responde cada subpregunta explicitamente y en el mismo orden en que se recibe.",
                evidence={
                    "metric_names": [],
                    "question_refs": [str(i + 1) for i, _ in enumerate(question_failures)],
                    "snippets": [q.question for q in question_failures][:3],
                },
            ),
        )

    if unit_mismatch:
        _add_edit(
            edits,
            PromptEdit(
                priority="P0",
                problem="Se detecto posible sustitucion de presentaciones/unidades.",
                edit_type="add_rule",
                proposed_text="No sustituyas presentaciones ni unidades; respeta exactamente lo solicitado.",
                evidence={
                    "metric_names": [],
                    "question_refs": [],
                    "snippets": [user_input, output[:200]],
                },
            ),
        )

    # Reglas por contrato
    if contract:
        if contract.require_clarifying_question_if_ambiguous and "?" not in output:
            _add_edit(
                edits,
                PromptEdit(
                    priority="P0",
                    problem="El contrato exige pregunta aclaratoria y no se detecto.",
                    edit_type="add_rule",
                    proposed_text="Si la solicitud es ambigua, haz al menos una pregunta aclaratoria.",
                    evidence={
                        "metric_names": [],
                        "question_refs": [],
                        "snippets": [output[:200]],
                    },
                ),
            )
        if contract.require_next_step and not re.search(r"puedes|siguiente|recomiendo", output.lower()):
            _add_edit(
                edits,
                PromptEdit(
                    priority="P1",
                    problem="El contrato exige un siguiente paso y no se detecto.",
                    edit_type="add_rule",
                    proposed_text="Incluye un siguiente paso recomendado de forma explicita.",
                    evidence={
                        "metric_names": [],
                        "question_refs": [],
                        "snippets": [output[:200]],
                    },
                ),
            )
        for must_not in contract.must_not_include:
            if must_not and must_not.lower() in output.lower():
                _add_edit(
                    edits,
                    PromptEdit(
                        priority="P0",
                        problem="Se incluyo texto prohibido por el contrato.",
                        edit_type="rewrite_rule",
                        proposed_text=f"No incluyas: {must_not}.",
                        evidence={
                            "metric_names": [],
                            "question_refs": [],
                            "snippets": [must_not],
                        },
                    ),
                )

    # Regla P0 mismatch especifica para 1kg/1L
    if re.search(r"\b1\s*kg\b", _normalizar(user_input)):
        if not re.search(r"\bkg\b", output_norm) and re.search(r"\bml\b|\bx\d+\b", output_norm):
            _add_edit(
                edits,
                PromptEdit(
                    priority="P0",
                    problem="Se sustituyeron unidades solicitadas (kg) por otras presentaciones.",
                    edit_type="add_rule",
                    proposed_text="No sustituyas presentaciones/unidades solicitadas (kg/L/xN).",
                    evidence={
                        "metric_names": [],
                        "question_refs": [],
                        "snippets": [user_input, output[:200]],
                    },
                ),
            )

    if re.search(r"\b1\s*l\b|\b1l\b", _normalizar(user_input)):
        if not re.search(r"\b1\s*l\b|\b1l\b|\bl\b", output_norm) and re.search(
            r"\bml\b|\bx\d+\b", output_norm
        ):
            _add_edit(
                edits,
                PromptEdit(
                    priority="P0",
                    problem="Se sustituyeron unidades solicitadas (L) por otras presentaciones.",
                    edit_type="add_rule",
                    proposed_text="No sustituyas presentaciones/unidades solicitadas (kg/L/xN).",
                    evidence={
                        "metric_names": [],
                        "question_refs": [],
                        "snippets": [user_input, output[:200]],
                    },
                ),
            )

    if language != "es":
        _add_edit(
            edits,
            PromptEdit(
                priority="P1",
                problem="La salida no esta completamente en espanol.",
                edit_type="add_rule",
                proposed_text="Responde 100% en espanol (sin mezcla/ingles).",
                evidence={
                    "metric_names": [],
                    "question_refs": [],
                    "snippets": [output[:200]],
                },
            ),
        )

    # Señales de idioma en reasons de métricas
    for m in metrics:
        reason = (m.reason or "").lower()
        if any(key in reason for key in ["not in spanish", "in english", "respond in spanish"]):
            _add_edit(
                edits,
                PromptEdit(
                    priority="P1",
                    problem="Una metrica detecto idioma incorrecto.",
                    edit_type="add_rule",
                    proposed_text="Responde 100% en espanol (sin ingles).",
                    evidence={
                        "metric_names": [m.name],
                        "question_refs": [],
                        "snippets": [m.reason or ""],
                    },
                ),
            )
            break

    if not formato_ok:
        _add_edit(
            edits,
            PromptEdit(
                priority="P2",
                problem="La salida no usa un formato directo tipo Producto: Precio.",
                edit_type="add_format_spec",
                proposed_text="Usa el formato 'Producto: Precio' y responde en lineas.",
                evidence={
                    "metric_names": [],
                    "question_refs": [],
                    "snippets": [output[:200]],
                },
            ),
        )

    if unsupported_fail or penalize_numbers_triggered:
        _add_edit(
            edits,
            PromptEdit(
                priority="P0",
                problem="Se detectaron afirmaciones sin soporte o numeros sin evidencia.",
                edit_type="add_refusal_policy",
                proposed_text="Si no hay evidencia en el contexto, pregunta; no inventes numeros.",
                evidence={
                    "metric_names": ["unsupported_claims"] if unsupported_metric else [],
                    "question_refs": [],
                    "snippets": [
                        (c.text if claim_analysis else "")
                        for c in (claim_analysis.claims if claim_analysis else [])
                        if c.verdict != "supported"
                    ][:3],
                },
            ),
        )

    if rag_fail:
        _add_edit(
            edits,
            PromptEdit(
                priority="P2",
                problem="Caso rag_grounding fallo por formato o claridad.",
                edit_type="add_example",
                proposed_text="Ejemplo de formato (sin inventar): Producto A: $precio. Producto B: $precio.",
                evidence={
                    "metric_names": [
                        m.name
                        for m in metrics
                        if m.name in {"format_compliance", "instruction_adherence", "task_success"} and not m.success
                    ],
                    "question_refs": [],
                    "snippets": [output[:200]],
                },
            ),
        )

    # Edits adicionales accionables
    if ctx:
        # Regla explicita para evitar rechazos cuando hay contexto
        _add_edit(
            edits,
            PromptEdit(
                priority="P0",
                problem="Hay contexto disponible; no se debe rechazar sin justificar.",
                edit_type="add_rule",
                proposed_text="Si hay contexto, NO digas 'no tengo acceso'.",
                evidence={
                    "metric_names": [],
                    "question_refs": [],
                    "snippets": [output[:200]],
                },
            ),
        )
        if "evidencia" not in output.lower() and "contexto" not in output.lower():
            _add_edit(
                edits,
                PromptEdit(
                    priority="P0",
                    problem="Falta evidenciar el soporte del contexto.",
                    edit_type="add_format_spec",
                    proposed_text="Responder como: Pregunta -> Respuesta (Precio exacto) -> Evidencia (texto del contexto).",
                    evidence={
                        "metric_names": [],
                        "question_refs": [],
                        "snippets": [output[:200]],
                    },
                ),
            )

    # No agregar items no solicitados (heuristica simple)
    if preguntas:
        tokens_preg = set(_tokenizar(" ".join(preguntas)))
        entidades = ["detergente", "jabon", "leche", "arroz", "azucar", "aceite", "yogurt"]
        extra = [e for e in entidades if e in output_norm and e not in tokens_preg]
        if extra:
            _add_edit(
                edits,
                PromptEdit(
                    priority="P1",
                    problem="Se detectaron items no solicitados.",
                    edit_type="add_rule",
                    proposed_text="No agregues items no solicitados.",
                    evidence={
                        "metric_names": [],
                        "question_refs": [],
                        "snippets": extra[:3],
                    },
                ),
            )

    prioridad = {"P0": 0, "P1": 1, "P2": 2}
    edits.sort(key=lambda e: (prioridad.get(e.priority, 9), e.edit_type))

    summary: List[str] = []
    if edits:
        summary.append(f"Se detectaron {len(edits)} mejoras de prompt sugeridas.")
        for e in edits:
            summary.append(f"{e.priority}: {e.problem}")
    else:
        summary.append("No se detectaron mejoras criticas de prompt.")

    patch_lines = [e.proposed_text for e in edits if e.priority == "P0"]
    # Solo incluir P1 en patch si hay fallos de idioma o de siguiente paso
    p1_incluir = any(
        "espanol" in _normalizar(e.proposed_text) or "siguiente paso" in _normalizar(e.proposed_text)
        for e in edits
        if e.priority == "P1"
    )
    if p1_incluir:
        patch_lines.extend([e.proposed_text for e in edits if e.priority == "P1"])
    patch_lines = patch_lines[:8]
    patch_text = "\n".join(patch_lines)
    prompt_patch = PromptPatch(mode="append", text=patch_text)
    prompt_improvement = PromptImprovement(
        summary=summary, suggested_edits=edits, prompt_patch=prompt_patch
    )

    primary_reasons = [
        f"{m.name}: {m.reason or m.error}"
        for m in metrics
        if not m.success and (m.reason or m.error)
    ][:3]
    if not primary_reasons:
        primary_reasons = ["Sin fallos criticos en este caso."]
    overall = OverallFeedback(primary_fail_reasons=primary_reasons, notes=[])

    rag_audit: Optional[RagAudit] = None
    if ctx:
        issues: List[RagIssue] = []
        contradictions: List[str] = []
        # Por subpregunta
        for idx, q in enumerate(q_feedback, start=1):
            q_norm = _limpiar_pregunta(q.question)
            entity = _extract_entity(q_norm)
            expected_unit = _expected_unit_str(q_norm)
            found_unit = None
            if q.answer_snippet:
                sn = _normalizar(q.answer_snippet)
                if "kg" in sn:
                    found_unit = "kg"
                elif re.search(r"\b1l\b|\bl\b", sn):
                    found_unit = "l"
                elif "ml" in sn:
                    found_unit = "ml"
                elif re.search(r"\bx\s*\d+\b", sn):
                    found_unit = "x"
            if q.verdict == "no_respondido":
                issues.append(
                    RagIssue(
                        type="missing_answer",
                        question_ref=f"Q{idx}",
                        entity=entity,
                        expected_unit=expected_unit,
                        found_unit=found_unit,
                        output_snippet=q.answer_snippet,
                        context_snippet=q.expected_from_context or None,
                    )
                )
            if q.verdict == "incorrecto_por_unidad":
                issues.append(
                    RagIssue(
                        type="unit_mismatch",
                        question_ref=f"Q{idx}",
                        entity=entity,
                        expected_unit=expected_unit,
                        found_unit=found_unit,
                        output_snippet=q.answer_snippet,
                        context_snippet=q.expected_from_context or None,
                    )
                )
                contradictions.append(f"Unidad incorrecta en {q.question}")

        # Extra items: entidad con precio no solicitada
        requested_entities = {(_extract_entity(_limpiar_pregunta(q.question)) or "") for q in q_feedback}
        for ent in _ENTIDADES_DOMINIO:
            if not ent or ent in requested_entities:
                continue
            if ent in output_norm and _PRICE_RE.search(output_norm):
                pos = output_norm.find(ent)
                price_pos = _nearby_price(output_norm, [(pos, pos + len(ent))], [], window=120)
                snippet = _extract_snippet(output, price_pos) if price_pos else ""
                issues.append(
                    RagIssue(
                        type="extra_item",
                        question_ref="extra",
                        entity=ent,
                        expected_unit=None,
                        found_unit=None,
                        output_snippet=snippet,
                        context_snippet=None,
                    )
                )

        # Números sin evidencia en contexto
        for m in _PRICE_RE.finditer(output_norm):
            price = m.group(0)
            if not any(price in _normalizar(c) for c in ctx):
                snippet = _extract_snippet(output, (m.start(), m.end()))
                issues.append(
                    RagIssue(
                        type="unsupported_number",
                        question_ref="global",
                        entity=None,
                        expected_unit=None,
                        found_unit=None,
                        output_snippet=snippet,
                        context_snippet=None,
                    )
                )

        summary: List[str] = []
        if issues:
            by_type: Dict[str, int] = {}
            for it in issues:
                by_type[it.type] = by_type.get(it.type, 0) + 1
            for k, v in sorted(by_type.items(), key=lambda x: x[0]):
                summary.append(f"{k}={v}")
        else:
            summary.append("Sin incidencias de RAG.")
        rag_audit = RagAudit(
            issues=issues,
            summary=summary,
            contradictions=contradictions,
        )

    return CaseFeedback(
        overall=overall,
        prompt_improvement=prompt_improvement,
        question_by_question=q_feedback,
        rag_audit=rag_audit,
    )
