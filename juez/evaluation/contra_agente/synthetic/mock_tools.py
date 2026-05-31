"""MockToolRunner — responde fake a las tools que el agente "llamaría".

NO hace HTTP a webhook ni queries a BD. Solo genera respuestas plausibles
basadas en heurísticas por nombre de tool + los datos canónicos del
`snapshot_factory`.

Estructura: dict `{tool_name -> response_dict}` se popula al demanda. Cada
llamada se loggea en `self.calls` para que el `pdf_builder` reconstruya el
PDF según lo que el agente hizo.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

log = logging.getLogger("juez.synthetic.mock_tools")


class MockToolRunner:
    """Genera respuestas fake para tools — cero side effects."""

    def __init__(self, canonical: Dict[str, Any]) -> None:
        self.canonical = canonical
        self.calls: List[Dict[str, Any]] = []
        # Contadores para IDs sintéticos crecientes
        self._next_ambiente_id = 99000
        self._next_item_id = 80000

    def run(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Loggea la llamada y retorna una respuesta heurística por nombre."""
        self.calls.append({"tool": tool_name, "args": dict(args or {})})
        result = self._resolve(tool_name, args or {})
        log.info("mock_tool name=%s -> success=%s", tool_name, result.get("success"))
        return result

    def _resolve(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        name = tool_name.lower()

        # ── Generación del PDF (final del flujo) ──────────────────────────
        if "pdf" in name or "generar_pdf" in name:
            return {
                "success": True,
                "pdf_drive_file_id": f"synth_pdf_{len(self.calls):03d}",
                "pdf_url": f"https://drive.example.com/synth/{self.canonical['contrato_id']}.pdf",
                "aprobacion_id": 99001,
            }

        # ── Registrar inmueble / sesión ───────────────────────────────────
        if "registrar_inmueble" in name or "sesion" in name or "iniciar" in name:
            return {
                "success": True,
                "inmueble_id": 1001,
                "inventario_id": self.canonical["inventario_id"],
                "tipo_inventario": self.canonical.get("tipo_inventario", "INICIAL"),
            }

        # ── Crear/registrar ambiente ──────────────────────────────────────
        if "ambiente" in name and ("registrar" in name or "crear" in name or "guardar" in name):
            ambiente_nombre = args.get("ambiente") or args.get("nombre") or args.get("room_name")
            self._next_ambiente_id += 1
            return {
                "success": True,
                "ambiente_id": self._next_ambiente_id,
                "ambiente": ambiente_nombre,
                "esperadas_fotos": self.canonical["fotos_por_ambiente"].get(ambiente_nombre or "", 0),
            }

        # ── Cerrar inventario ─────────────────────────────────────────────
        if "cerrar" in name or "finalizar" in name:
            return {
                "success": True,
                "inventario_id": self.canonical["inventario_id"],
                "ambientes": len(self.canonical["ambientes"]),
                "total_items": sum(self.canonical["fotos_por_ambiente"].values()),
            }

        # ── Editar items / agregar foto a item ────────────────────────────
        if "editar" in name or "item" in name:
            self._next_item_id += 1
            return {
                "success": True,
                "item_id": self._next_item_id,
                "errors": [],
                "updates": 1,
            }

        # ── Consulta libre / queries de BD ────────────────────────────────
        if "consulta" in name or "obtener" in name or "verificar" in name:
            return {
                "success": True,
                "rows": [
                    {
                        "inventario_id": self.canonical["inventario_id"],
                        "contrato_id": self.canonical["contrato_id"],
                        "tipo_inventario": self.canonical["tipo_inventario"],
                    }
                ],
            }

        # ── Tools desconocidas ────────────────────────────────────────────
        # No rompemos el agente — retornamos success vacío.
        return {"success": True, "info": "synthetic_response", "tool_name": tool_name}
