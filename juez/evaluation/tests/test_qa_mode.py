"""Test del modo de QA (técnico / funcional / ambos)."""
from __future__ import annotations

from juez.evaluation.qa_mode import agrupar_por_seccion, clasificar, clasificar_seccion, filtrar_problemas

_PROBLEMAS = [
    {"tipo": "Seguridad / SSRF", "descripcion": "x"},
    {"tipo": "Seguridad / Código", "descripcion": "x"},
    {"tipo": "Objetivo no cumplido", "descripcion": "x"},
    {"tipo": "Artefacto / PDF", "descripcion": "x"},
    {"tipo": "Conversacion", "descripcion": "x"},
]


def test_clasificar():
    assert clasificar("Seguridad / SSRF") == "tecnico"
    assert clasificar("Objetivo no cumplido") == "funcional"
    assert clasificar("Artefacto / PDF") == "funcional"


def test_filtrar_tecnico():
    out = filtrar_problemas(_PROBLEMAS, "tecnico")
    assert len(out) == 2
    assert all(clasificar(p["tipo"]) == "tecnico" for p in out)


def test_filtrar_funcional():
    out = filtrar_problemas(_PROBLEMAS, "funcional")
    assert len(out) == 3


def test_ambos_no_filtra():
    assert len(filtrar_problemas(_PROBLEMAS, "ambos")) == 5
    assert len(filtrar_problemas(_PROBLEMAS, "")) == 5


# ── clasificar_seccion / agrupar_por_seccion (3 vias, para el informe unico) ──

def test_clasificar_seccion_detecta_seguridad_por_tipo():
    assert clasificar_seccion("Seguridad") == "seguridad"
    assert clasificar_seccion("Seguridad / SSRF") == "seguridad"


def test_clasificar_seccion_detecta_seguridad_por_descripcion():
    """Aunque el 'tipo' sea generico, si la descripcion menciona algo de
    seguridad (credencial, token, api key...), debe caer en seguridad."""
    assert clasificar_seccion("Configuracion", "API key hardcodeada en el nodo") == "seguridad"
    assert clasificar_seccion("Configuracion", "usa credencial de prueba insegura") == "seguridad"


def test_clasificar_seccion_respeta_funcional_y_tecnico_cuando_no_es_seguridad():
    assert clasificar_seccion("Objetivo no cumplido") == "funcional"
    assert clasificar_seccion("Resiliencia") == "tecnico"


def test_agrupar_por_seccion_no_pierde_ningun_problema():
    secciones = agrupar_por_seccion(_PROBLEMAS)
    total = sum(len(v) for v in secciones.values())
    assert total == len(_PROBLEMAS)
    assert set(secciones.keys()) == {"seguridad", "funcional", "tecnico"}


def test_agrupar_por_seccion_clasifica_correctamente():
    problemas = [
        {"tipo": "Seguridad", "descripcion": "API key expuesta"},
        {"tipo": "Objetivo no cumplido", "descripcion": "no crea el ticket"},
        {"tipo": "Resiliencia", "descripcion": "sin retryOnFail"},
    ]
    secciones = agrupar_por_seccion(problemas)
    assert len(secciones["seguridad"]) == 1
    assert len(secciones["funcional"]) == 1
    assert len(secciones["tecnico"]) == 1
