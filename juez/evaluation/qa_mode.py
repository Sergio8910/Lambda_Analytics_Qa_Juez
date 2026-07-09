"""Modo de QA: técnico, funcional o ambos.

Clasifica cada hallazgo (por su 'tipo') en técnico o funcional y filtra la lista
según el modo elegido. 'tecnico' = cómo está construido (estructura, código,
redundancia); 'funcional' = si cumple su propósito (objetivos,
conversación, salida/artefacto, negocio).

clasificar()/filtrar_problemas() (2 vías) se mantienen intactas -- ya las usa
run_n8n_single vía modo_qa. Para el informe unico con 3 secciones (tecnico/
funcional/seguridad) se usan las funciones nuevas de abajo, que NO filtran
(no descartan hallazgos): solo los agrupan para presentarlos organizados.
"""
from __future__ import annotations

from typing import Any, Dict, List

# Si el 'tipo' contiene alguno de estos, es funcional; si no, técnico.
_FUNCIONAL = (
    "objetivo", "conversa", "intencion", "intención", "task", "negocio",
    "artefacto", "pdf", "respuesta", "grounding", "alucina", "formato",
    "escenario", "rag",
)

# Si el 'tipo' o la 'descripcion' contienen alguno de estos, es seguridad --
# se evalua ANTES que funcional/tecnico (una API key hardcodeada es seguridad,
# no "configuracion" generica).
_SEGURIDAD = (
    "seguridad", "secreto", "credencial", "contraseña", "password", "token",
    "ssrf", "inyeccion", "inyección", "jailbreak", "vulnerab", "exfiltra",
    "acceso no autorizado", "autenticacion", "autenticación", "hardcode",
    "api key", "apikey",
)

MODOS = ("tecnico", "funcional", "ambos")
SECCIONES = ("seguridad", "funcional", "tecnico")


def clasificar(tipo: str) -> str:
    t = (tipo or "").lower()
    return "funcional" if any(k in t for k in _FUNCIONAL) else "tecnico"


def clasificar_seccion(tipo: str, descripcion: str = "") -> str:
    """Clasificacion de 3 vias (seguridad/funcional/tecnico) para el informe
    unico. Reusa clasificar() para la distincion tecnico/funcional existente;
    solo agrega la deteccion de seguridad por encima."""
    texto = f"{tipo or ''} {descripcion or ''}".lower()
    if any(k in texto for k in _SEGURIDAD):
        return "seguridad"
    return clasificar(tipo)


def agrupar_por_seccion(problemas: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Agrupa TODOS los problemas en 3 secciones sin descartar ninguno --
    a diferencia de filtrar_problemas(), que si descarta segun el modo."""
    secciones: Dict[str, List[Dict[str, Any]]] = {s: [] for s in SECCIONES}
    for p in problemas:
        clave = clasificar_seccion(p.get("tipo", ""), p.get("descripcion", ""))
        secciones[clave].append(p)
    return secciones


def filtrar_problemas(problemas: List[Dict[str, Any]], modo: str = "ambos") -> List[Dict[str, Any]]:
    modo = (modo or "ambos").lower()
    if modo not in MODOS or modo == "ambos":
        return list(problemas)
    return [p for p in problemas if clasificar(p.get("tipo", "")) == modo]


if __name__ == "__main__":  # ponytail: self-check sin red
    p = [{"tipo": "Seguridad / SSRF"}, {"tipo": "Objetivo no cumplido"}, {"tipo": "Artefacto / PDF"}]
    assert len(filtrar_problemas(p, "tecnico")) == 1
    assert len(filtrar_problemas(p, "funcional")) == 2
    assert len(filtrar_problemas(p, "ambos")) == 3
    secciones = agrupar_por_seccion(p)
    assert len(secciones["seguridad"]) == 1
    assert len(secciones["funcional"]) == 2
    assert len(secciones["tecnico"]) == 0
    print("ok")
