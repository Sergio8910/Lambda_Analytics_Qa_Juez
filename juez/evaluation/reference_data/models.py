"""Modelos del dataset de referencia (información previa del cliente)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

ReferenceRecord = Dict[str, Any]


class ReferenceDataset(BaseModel):
    """Información previa parseada a una forma estructurada y neutral.

    - `records`: filas tabulares (Excel/CSV) o ítems estructurados. Cada record
      es un dict columna->valor.
    - `columns`: nombres de columna detectados (en orden), si aplica.
    - `text`: texto plano extraído (Word/TXT, o representación del tabular).
    - `format`: formato de origen (xlsx, csv, txt, docx, json).
    - `source_name`: nombre del archivo original.
    - `notas`: observaciones del parseo (hojas múltiples, columnas vacías, etc.).
    """

    source_name: str = Field(..., description="Nombre del archivo original")
    format: str = Field(..., description="Formato detectado: xlsx, csv, txt, docx, json")
    columns: List[str] = Field(default_factory=list, description="Columnas detectadas (tabular)")
    records: List[ReferenceRecord] = Field(default_factory=list, description="Filas/ítems estructurados")
    text: str = Field("", description="Texto plano extraído (para formatos no tabulares)")
    notas: List[str] = Field(default_factory=list, description="Observaciones del parseo")
    payload_template: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Ejemplo REAL del sobre que espera un webhook (ej. JSON de WhatsApp Business API), "
            "con el marcador {{JUEZ_MENSAJE}} donde debe ir el texto del turno. Se detecta "
            "automaticamente en JSON que contenga ese marcador; permite que las conversaciones "
            "de prueba disparen el webhook con la forma real que el flujo necesita para recorrer "
            "todos sus nodos, en vez de un payload generico que el flujo no reconoce."
        ),
    )

    model_config = {"extra": "forbid"}

    @property
    def n_records(self) -> int:
        return len(self.records)

    def resumen(self) -> Dict[str, Any]:
        """Resumen compacto para reportes/respuestas de API."""
        return {
            "source_name": self.source_name,
            "format": self.format,
            "n_records": self.n_records,
            "columns": self.columns,
            "tiene_texto": bool(self.text.strip()),
            "tiene_payload_template": self.payload_template is not None,
            "n_notas": len(self.notas),
        }
