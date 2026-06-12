"""Ingesta de información previa (datos de referencia) para el Juzgado.

El cliente puede mandarnos información previa — la "verdad de base" — en
archivos (Excel, CSV, TXT, Word, JSON). Este paquete la recibe, la parsea a un
dataset estructurado y consultable, y expone una primitiva de verificación para
comprobar que lo que el agente maneja (ej. resultados de tools) es verídico
contra esa verdad.

Es ADITIVO: no toca el core del Juez ni el mock de tools de Abad. Produce un
`ReferenceDataset` neutral que otras capas (el Juzgado) pueden consumir.
"""
from .models import ReferenceDataset, ReferenceRecord
from .parser import ParseError, parse_reference_file
from .lookup import LookupResult, lookup_records, verify_value

__all__ = [
    "ReferenceDataset",
    "ReferenceRecord",
    "ParseError",
    "parse_reference_file",
    "LookupResult",
    "lookup_records",
    "verify_value",
]
