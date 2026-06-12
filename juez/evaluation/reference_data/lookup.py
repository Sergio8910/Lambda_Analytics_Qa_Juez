"""Primitivas de verificación contra la información previa (verdad de base).

Permiten preguntarle al dataset: "¿este valor/registro que el agente manejó es
verídico?" — la base del Juzgado sobre ejecución de tools.

Sin red, sin estado: funciones puras sobre un `ReferenceDataset` ya parseado.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .models import ReferenceDataset, ReferenceRecord


class LookupResult(BaseModel):
    """Resultado de buscar registros que cumplan un filtro."""

    encontrado: bool
    n_coincidencias: int
    coincidencias: List[ReferenceRecord] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


def _norm(value: Any) -> str:
    """Normaliza para comparar de forma tolerante (str, trim, lower)."""
    if value is None:
        return ""
    return str(value).strip().lower()


def lookup_records(
    dataset: ReferenceDataset,
    filtro: Dict[str, Any],
    *,
    limit: int = 50,
) -> LookupResult:
    """Devuelve los records cuyo valor coincide con TODOS los pares de `filtro`.

    Comparación tolerante (str/trim/lower). El nombre de columna también se
    matchea de forma tolerante, así que 'Contrato_ID' matchea 'contrato_id'.
    """
    if not filtro:
        return LookupResult(encontrado=False, n_coincidencias=0)

    filtro_norm = {_norm(k): v for k, v in filtro.items()}
    coincidencias: List[ReferenceRecord] = []
    for rec in dataset.records:
        rec_norm = {_norm(k): v for k, v in rec.items()}
        ok = True
        for k, v in filtro_norm.items():
            if k not in rec_norm or _norm(rec_norm[k]) != _norm(v):
                ok = False
                break
        if ok:
            coincidencias.append(rec)
            if len(coincidencias) >= limit:
                break
    return LookupResult(
        encontrado=bool(coincidencias),
        n_coincidencias=len(coincidencias),
        coincidencias=coincidencias,
    )


def verify_value(
    dataset: ReferenceDataset,
    columna: str,
    valor: Any,
    *,
    where: Optional[Dict[str, Any]] = None,
) -> bool:
    """¿Existe en la verdad un record donde `columna == valor` (y opcionalmente
    cumpliendo `where`)?

    Úsalo para verificar que un dato que el agente devolvió/usó (ej. el precio
    de un producto, el estado de un ticket) coincide con la verdad provista.
    """
    filtro: Dict[str, Any] = dict(where or {})
    filtro[columna] = valor
    return lookup_records(dataset, filtro, limit=1).encontrado
