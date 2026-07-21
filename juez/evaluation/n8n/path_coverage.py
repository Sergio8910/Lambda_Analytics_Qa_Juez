"""Cobertura de CAMINOS de un flujo n8n.

Problema que resuelve: si todas las pruebas mandan un input parecido, todas
toman la MISMA rama de cada IF/Switch, y los nodos de las otras ramas nunca se
ejecutan -> nunca se evalúan. Para llegar a TODOS los nodos hay que:
  1. entender cada nodo de ramificación (qué condición evalúa, por qué salida),
  2. saber qué input hace que cada rama se dispare,
  3. distinguir lo que SÍ se controla desde el payload inicial de lo que
     depende de la ejecución (respuesta de un HTTP, salida de un Code node...).

Este módulo hace (1) y (2)+(3) a nivel de análisis estático (cero tokens):
enumera los caminos del grafo, extrae la condición de cada rama y la clasifica
como CONTROLABLE_POR_INPUT o DEPENDE_DE_EJECUCION. La síntesis del input por
camino y la cobertura de ejecución se construyen encima de esto.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .parser import parse_workflow

_MAX_CAMINOS = 64  # tope duro para evitar explosión combinatoria; se reporta si se trunca

# Categorías de nodo que TRANSFORMAN los datos: si uno de estos está entre el
# trigger y una rama, el campo que la rama evalúa probablemente viene de ESE
# nodo (su salida), no del payload inicial -> no es controlable desde el input.
_CATEGORIAS_TRANSFORMADORAS = {"http", "ai", "code"}

# Expresión n8n: ={{ $json.a.b }} / ={{ $('Nodo').item.json.x }} / ={{ $('N').last().json.y }}
# Captura la ruta dotted que sigue a `$json` o a `.json` (tras un $('Nodo')...).
_RE_JSON = re.compile(r"(?:\$json|\.json)((?:\.[A-Za-z_$][\w$]*)+)")
_RE_NODO_REF = re.compile(r"\$\(\s*['\"]([^'\"]+)['\"]\s*\)")


def _campo_de_expresion(left_value: Any) -> Tuple[str, str]:
    """Devuelve (fuente, campo) del leftValue de una condición.

    fuente: 'input' ($json), 'nodo:<Nombre>' ($('Nodo')...), o 'desconocido'.
    campo:  ruta del campo (ej. 'output.clasificacion') o el texto crudo.
    """
    texto = str(left_value or "")
    mjson = _RE_JSON.search(texto)
    campo = mjson.group(1).lstrip(".") if mjson else texto.strip()
    mnodo = _RE_NODO_REF.search(texto)
    if mnodo:
        return f"nodo:{mnodo.group(1)}", campo
    if mjson:
        return "input", campo
    return "desconocido", texto.strip()


def _condiciones_crudas(node_params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extrae la lista [{campo, fuente, operador, valor}] de un bloque de
    condiciones n8n (IF v2/v3, Switch rule, Filter)."""
    cond_block = node_params.get("conditions") or {}
    lista = cond_block.get("conditions") if isinstance(cond_block, dict) else None
    salida: List[Dict[str, Any]] = []
    for c in (lista or []):
        if not isinstance(c, dict):
            continue
        fuente, campo = _campo_de_expresion(c.get("leftValue"))
        op = c.get("operator") or {}
        salida.append({
            "campo": campo,
            "fuente": fuente,
            "operador": f"{op.get('type', '')}.{op.get('operation', '')}".strip("."),
            "valor": c.get("rightValue", ""),
        })
    return salida


def _ramas_de_nodo(node) -> Optional[List[Dict[str, Any]]]:
    """Para un nodo de ramificación (IF/Switch/Filter), devuelve una lista por
    output_index con {etiqueta, condiciones, es_fallback}. None si no ramifica."""
    tipo = node.node_type.lower()
    params = node.raw_parameters or {}

    if tipo.endswith(".if") or tipo.endswith(".filter"):
        conds = _condiciones_crudas(params)
        return [
            {"output_index": 0, "etiqueta": "true", "condiciones": conds, "es_fallback": False, "negar": False},
            {"output_index": 1, "etiqueta": "false", "condiciones": conds, "es_fallback": True, "negar": True},
        ]

    if tipo.endswith(".switch"):
        rules = ((params.get("rules") or {}).get("values")) or []
        ramas: List[Dict[str, Any]] = []
        for i, rule in enumerate(rules):
            if not isinstance(rule, dict):
                continue
            ramas.append({
                "output_index": i,
                "etiqueta": str(rule.get("outputKey") or f"salida_{i}"),
                "condiciones": _condiciones_crudas({"conditions": rule.get("conditions")}),
                "es_fallback": False,
                "negar": False,
            })
        opciones = params.get("options") or {}
        if opciones.get("fallbackOutput"):
            ramas.append({
                "output_index": len(ramas),
                "etiqueta": str(opciones.get("renameFallbackOutput") or "fallback"),
                "condiciones": [],
                "es_fallback": True,
                "negar": True,
            })
        return ramas or None
    return None


def _controlabilidad(condiciones: List[Dict[str, Any]], hay_transformador_arriba: bool) -> str:
    """Clasifica si la rama se puede forzar desde el payload inicial."""
    if not condiciones:
        return "fallback"  # rama else/fallback: se toma cuando ninguna otra aplica
    if any(c["fuente"].startswith("nodo:") for c in condiciones):
        return "depende_de_ejecucion"
    if hay_transformador_arriba:
        return "depende_de_ejecucion"
    if all(c["fuente"] == "input" for c in condiciones):
        return "controlable_por_input"
    return "depende_de_ejecucion"


def analizar_caminos(workflow: Dict[str, Any], max_caminos: int = _MAX_CAMINOS) -> Dict[str, Any]:
    """Analiza la cobertura de caminos de un flujo n8n. 100% estático."""
    _inv, graph = parse_workflow(workflow)
    nodos = {n.name: n for n in graph.nodes}
    cat = {n.name: n.category for n in graph.nodes}

    # adyacencia por output_index: source -> {output_index: [targets]}
    ady: Dict[str, Dict[int, List[str]]] = {}
    for e in graph.edges:
        ady.setdefault(e.source, {}).setdefault(e.output_index, []).append(e.target)

    # ramas por nodo (solo los que ramifican)
    ramas_por_nodo: Dict[str, List[Dict[str, Any]]] = {}
    for n in graph.nodes:
        r = _ramas_de_nodo(n)
        if r:
            ramas_por_nodo[n.name] = r

    triggers = graph.trigger_nodes or [n.name for n in graph.nodes if n.incoming_edges == 0]

    caminos: List[Dict[str, Any]] = []
    truncado = False

    def dfs(nodo: str, visitados: set, secuencia: List[str], condiciones_camino: List[Dict[str, Any]], transformador_arriba: bool):
        nonlocal truncado
        if len(caminos) >= max_caminos:
            truncado = True
            return
        secuencia = secuencia + [nodo]
        if nodo in visitados:  # ciclo: cerrar el camino aquí
            caminos.append({"secuencia": secuencia, "condiciones": condiciones_camino, "cierra_en_ciclo": True})
            return
        visitados = visitados | {nodo}
        salidas = ady.get(nodo, {})
        if not salidas:  # hoja
            caminos.append({"secuencia": secuencia, "condiciones": condiciones_camino, "cierra_en_ciclo": False})
            return
        transf = transformador_arriba or (cat.get(nodo) in _CATEGORIAS_TRANSFORMADORAS)
        ramas = ramas_por_nodo.get(nodo)
        for output_index, targets in sorted(salidas.items()):
            rama_info = None
            if ramas:
                rama_info = next((r for r in ramas if r["output_index"] == output_index), None)
            for target in targets:
                cond_extra = []
                if rama_info:
                    cond_extra = [{
                        "nodo": nodo, "rama": rama_info["etiqueta"],
                        "condiciones": rama_info["condiciones"],
                        "negar": rama_info.get("negar", False),
                        "controlabilidad": _controlabilidad(rama_info["condiciones"], transf),
                    }]
                dfs(target, visitados, secuencia, condiciones_camino + cond_extra, transf)

    for t in triggers:
        dfs(t, set(), [], [], False)

    # nodos alcanzables por el DFS vs total
    nodos_en_caminos = {n for c in caminos for n in c["secuencia"]}
    nodos_no_cubiertos = sorted(set(nodos) - nodos_en_caminos)

    # resumen de ramas: cuántas son controlables por input vs dependen de ejecución
    ramas_resumen = {"controlable_por_input": 0, "depende_de_ejecucion": 0, "fallback": 0}
    detalle_ramas: List[Dict[str, Any]] = []
    for nombre, ramas in ramas_por_nodo.items():
        transf_arriba = _hay_transformador_upstream(nombre, graph, cat)
        for r in ramas:
            ctrl = _controlabilidad(r["condiciones"], transf_arriba)
            ramas_resumen[ctrl] = ramas_resumen.get(ctrl, 0) + 1
            detalle_ramas.append({
                "nodo": nombre, "rama": r["etiqueta"], "output_index": r["output_index"],
                "controlabilidad": ctrl,
                "condiciones": [
                    {"campo": c["campo"], "operador": c["operador"], "valor": c["valor"], "fuente": c["fuente"]}
                    for c in r["condiciones"]
                ],
            })

    return {
        "total_nodos": len(nodos),
        "total_caminos": len(caminos),
        "caminos_truncados": truncado,
        "nodos_de_ramificacion": sorted(ramas_por_nodo.keys()),
        "ramas": detalle_ramas,
        "ramas_resumen": ramas_resumen,
        "nodos_no_cubiertos_por_ningun_camino": nodos_no_cubiertos,
        "caminos": caminos,
        "nota": (
            "Análisis 100% estático de caminos. 'controlable_por_input' = la rama se puede "
            "forzar armando el payload inicial; 'depende_de_ejecucion' = depende de la salida "
            "de un nodo previo (HTTP/Code/AI) y solo se ejerce con esos datos reales o un stub."
        ),
    }


def _valor_para_satisfacer(cond: Dict[str, Any]) -> Any:
    """Dado {campo, operador, valor}, devuelve un valor que HACE VERDADERA la
    condición (para forzar esa rama desde el input)."""
    op = (cond.get("operador") or "").lower()
    objetivo = cond.get("valor")
    if op.endswith(".exists") or op.endswith(".notempty"):
        return "valor_de_prueba_QA"
    if op.endswith(".notexists") or op.endswith(".empty"):
        return None
    if "true" in op:
        return True
    if "false" in op:
        return False
    if op.endswith(".contains"):
        return f"{objetivo}" if objetivo not in (None, "") else "contiene_QA"
    if op.startswith("number") or op.endswith(".gt") or op.endswith(".lt") or op.endswith(".gte") or op.endswith(".lte"):
        try:
            return float(objetivo)
        except (TypeError, ValueError):
            return 200
    # equals / por defecto: usar el valor objetivo tal cual.
    return objetivo if objetivo not in (None, "") else "valor_QA"


def _valor_para_negar(cond: Dict[str, Any]) -> Any:
    """Valor que HACE FALSA la condición (para tomar la rama false/else)."""
    op = (cond.get("operador") or "").lower()
    objetivo = cond.get("valor")
    if op.endswith(".exists") or op.endswith(".notempty"):
        return None  # no existe -> falla 'exists'
    if op.endswith(".notexists") or op.endswith(".empty"):
        return "valor_de_prueba_QA"  # existe -> falla 'notExists'
    if "true" in op:
        return False
    if "false" in op:
        return True
    if op.endswith(".contains"):
        return "QA_sin_coincidencia"
    if op.startswith("number") or op.endswith((".gt", ".lt", ".gte", ".lte")):
        try:
            return float(objetivo) + 1  # distinto del objetivo numérico
        except (TypeError, ValueError):
            return -999999
    # equals string / por defecto: un valor distinto al objetivo.
    return f"NO_{objetivo}" if objetivo not in (None, "") else "valor_distinto_QA"


def _set_path(dest: Dict[str, Any], dotted: str, value: Any) -> None:
    partes = [p for p in dotted.split(".") if p]
    cur = dest
    for p in partes[:-1]:
        cur = cur.setdefault(p, {})
        if not isinstance(cur, dict):  # colisión de tipos: no forzar
            return
    if partes:
        cur[partes[-1]] = value


def sintetizar_inputs_por_camino(workflow: Dict[str, Any], max_caminos: int = _MAX_CAMINOS) -> Dict[str, Any]:
    """Para cada camino, arma un payload de input que FUERZA las ramas
    CONTROLABLES de ese camino (poniendo el campo del $json en el valor que
    satisface la condición). Declara honestamente qué ramas del camino NO se
    pueden forzar desde el input (dependen de la ejecución) -> ese nodo solo se
    cubre con datos reales de un nodo previo o un stub.

    Este es el material que alimenta al contra-agente para recorrer caminos
    distintos en vez de golpear siempre la misma rama.
    """
    analisis = analizar_caminos(workflow, max_caminos=max_caminos)
    inputs: List[Dict[str, Any]] = []
    for idx, camino in enumerate(analisis["caminos"], start=1):
        payload: Dict[str, Any] = {}
        forzadas: List[str] = []
        no_forzables: List[str] = []
        for salto in camino["condiciones"]:
            ctrl = salto.get("controlabilidad")
            negar = salto.get("negar", False)
            etiqueta = f"{salto['nodo']}->{salto['rama']}"
            if ctrl == "controlable_por_input":
                for c in salto["condiciones"]:
                    if c["fuente"] == "input" and c["campo"]:
                        valor = _valor_para_negar(c) if negar else _valor_para_satisfacer(c)
                        _set_path(payload, c["campo"], valor)
                forzadas.append(etiqueta)
            elif ctrl == "depende_de_ejecucion":
                no_forzables.append(etiqueta)
        inputs.append({
            "camino_id": idx,
            "secuencia": camino["secuencia"],
            "payload_sugerido": payload,
            "ramas_forzadas_por_input": forzadas,
            "ramas_no_forzables_desde_input": no_forzables,
            "totalmente_cubrible_por_input": not no_forzables,
        })
    cubribles = sum(1 for i in inputs if i["totalmente_cubrible_por_input"])
    return {
        "total_caminos": len(inputs),
        "caminos_cubribles_solo_con_input": cubribles,
        "caminos_que_requieren_datos_de_ejecucion": len(inputs) - cubribles,
        "inputs_por_camino": inputs,
        "nota": (
            "payload_sugerido fuerza las ramas controlables del camino. Las ramas en "
            "'ramas_no_forzables_desde_input' dependen de la salida de un nodo previo "
            "(HTTP/Code/AI): para cubrir esos nodos hace falta que ese nodo devuelva el "
            "valor esperado (ejecución real con esos datos, o un stub del nodo)."
        ),
    }


def _frase_objetivo(cond: Dict[str, Any]) -> str:
    """Describe, en lenguaje natural, qué debe pasar con un campo para disparar
    la rama -- para guiar la generación de un input que lo provoque."""
    campo = cond.get("campo") or "el campo"
    op = (cond.get("operador") or "").lower()
    val = cond.get("valor")
    if op.endswith(".exists") or op.endswith(".notempty"):
        return f"'{campo}' venga con un valor (exista y no esté vacío)"
    if op.endswith(".notexists") or op.endswith(".empty"):
        return f"'{campo}' NO venga / quede vacío"
    if "true" in op:
        return f"'{campo}' resulte verdadero"
    if "false" in op:
        return f"'{campo}' resulte falso"
    if op.endswith(".contains"):
        return f"'{campo}' contenga '{val}'"
    return f"'{campo}' sea '{val}'"


def generar_escenarios_por_rama(workflow: Dict[str, Any], max_caminos: int = _MAX_CAMINOS) -> List[Dict[str, Any]]:
    """Genera escenarios DIRIGIDOS a cada rama, para que las pruebas recorran
    caminos distintos en vez de golpear siempre el mismo.

    Para ramas controlables por input, el escenario incluye el payload exacto.
    Para ramas gated por un nodo previo (AI/HTTP), describe el objetivo
    semántico ('haz que el usuario hable de X para que el agente clasifique Y')
    -- steering: el input real hace que el nodo previo produzca el valor que
    dispara la rama. Cada escenario es texto que alimenta al contra-agente.
    """
    analisis = analizar_caminos(workflow, max_caminos=max_caminos)
    escenarios: List[Dict[str, Any]] = []
    vistos: set = set()
    for rama in analisis["ramas"]:
        clave = (rama["nodo"], rama["rama"])
        if clave in vistos:
            continue
        vistos.add(clave)
        ctrl = rama["controlabilidad"]
        objetivos = "; ".join(_frase_objetivo(c) for c in rama["condiciones"]) or "la condición por defecto (else)"
        if ctrl == "controlable_por_input":
            texto = (
                f"Para ejercer la rama '{rama['rama']}' del nodo '{rama['nodo']}', "
                f"el input debe hacer que {objetivos}. Manda un mensaje/petición coherente con eso."
            )
        elif ctrl == "depende_de_ejecucion":
            texto = (
                f"Objetivo de cobertura: recorrer la rama '{rama['rama']}' del nodo '{rama['nodo']}', "
                f"que se dispara cuando {objetivos}. Como ese valor lo produce un nodo previo "
                f"(clasificador/HTTP), redacta un mensaje del usuario cuyo SIGNIFICADO lleve a ese "
                f"resultado (ej. hablar claramente del caso que produce ese valor)."
            )
        else:  # fallback / else
            texto = (
                f"Para ejercer la rama por defecto '{rama['rama']}' del nodo '{rama['nodo']}', "
                f"manda un caso que NO cumpla ninguna de las otras condiciones del nodo."
            )
        escenarios.append({
            "nodo": rama["nodo"], "rama": rama["rama"], "controlabilidad": ctrl,
            "escenario": texto,
        })
    return escenarios


def cobertura_de_nodos(workflow: Dict[str, Any], max_caminos: int = _MAX_CAMINOS) -> Dict[str, Any]:
    """Mapa de cobertura de NODOS: por cada nodo, si se puede cubrir armando el
    payload inicial, si requiere datos de ejecución (rama que depende de un
    HTTP/Code/AI previo), o si es inalcanzable en el grafo.

    Responde directo la pregunta '¿qué nodos podemos recorrer y cuáles no, y por
    qué?'. Es la base honesta de la cobertura: no promete cubrir lo que
    estáticamente no se puede forzar desde el input.
    """
    _inv, graph = parse_workflow(workflow)
    todos = {n.name for n in graph.nodes}
    inalcanzables = set(graph.unreachable_nodes or [])

    sintesis = sintetizar_inputs_por_camino(workflow, max_caminos=max_caminos)
    cubribles: set = set()
    requieren_ejecucion: set = set()
    for camino in sintesis["inputs_por_camino"]:
        nodos_camino = set(camino["secuencia"])
        if camino["totalmente_cubrible_por_input"]:
            cubribles |= nodos_camino
        else:
            requieren_ejecucion |= nodos_camino

    # Un nodo que aparece en ALGÚN camino cubrible, se considera cubrible
    # (aunque también aparezca en caminos que requieren ejecución).
    requieren_ejecucion -= cubribles
    requieren_ejecucion -= inalcanzables
    # Nodos que no salieron en ningún camino main y no son inalcanzables:
    # típicamente sub-nodos de AI (languageModel/tool) conectados por canales
    # no-main; se listan aparte para no inflar el denominador.
    en_algun_camino = cubribles | requieren_ejecucion | inalcanzables
    fuera_de_camino_main = sorted(todos - en_algun_camino)

    alcanzables = todos - inalcanzables
    denom = len(alcanzables - set(fuera_de_camino_main)) or 1
    pct = round(100.0 * len(cubribles) / denom, 1)

    return {
        "total_nodos": len(todos),
        "nodos_cubribles_por_input": sorted(cubribles),
        "nodos_que_requieren_datos_de_ejecucion": sorted(requieren_ejecucion),
        "nodos_inalcanzables": sorted(inalcanzables),
        "nodos_fuera_del_camino_main": fuera_de_camino_main,
        "porcentaje_cubrible_por_input": pct,
        "nota": (
            "cubrible_por_input = un payload inicial lo alcanza. requiere_datos_de_ejecucion = "
            "solo se alcanza si un nodo previo (HTTP/Code/AI) devuelve cierto valor -> ejecución "
            "real con esos datos o un stub. inalcanzable = no conectado desde el trigger. "
            "'fuera_del_camino_main' suele ser sub-nodos de AI (modelo/tools) por canales no-main."
        ),
    }


def _stubs_de_camino(camino: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Los STUBS necesarios para recorrer un camino: por cada salto gated por
    ejecución (AI/HTTP), qué campo forzar y a qué valor para tomar esa rama."""
    stubs: List[Dict[str, Any]] = []
    for salto in camino.get("condiciones", []):
        if salto.get("controlabilidad") != "depende_de_ejecucion":
            continue
        negar = salto.get("negar", False)
        for c in salto.get("condiciones", []):
            if not c.get("campo"):
                continue
            forzar_a = _valor_para_negar(c) if negar else _valor_para_satisfacer(c)
            stubs.append({
                "nodo_gating": salto["nodo"],
                "rama": salto["rama"],
                "campo": c["campo"],
                "fuente": c.get("fuente", ""),
                "forzar_a": forzar_a,
            })
    return stubs


def cobertura_combinada(workflow: Dict[str, Any], max_caminos: int = _MAX_CAMINOS) -> Dict[str, Any]:
    """Cobertura DETERMINISTA de todos los nodos combinando ejecución real +
    stubs. Cada nodo queda como:
      - cubrible_real: un payload inicial lo alcanza (no necesita stub),
      - cubrible_por_stub: solo se alcanza forzando el valor de un nodo previo
        (AI/HTTP) -> se entrega la RECETA (qué campo forzar y a qué valor),
      - inalcanzable: no conectado desde el trigger.

    Responde 'recorrer absolutamente todos los nodos' de forma honesta: con
    stubs, el 100% de los nodos alcanzables tiene una receta de cobertura;
    el reporte distingue qué se cubre real vs qué necesitó stub.
    """
    _inv, graph = parse_workflow(workflow)
    todos = {n.name for n in graph.nodes}
    inalcanzables = set(graph.unreachable_nodes or [])
    analisis = analizar_caminos(workflow, max_caminos=max_caminos)

    # Para cada nodo, la mejor forma de cubrirlo: real gana a stub; entre stubs,
    # el que necesite menos valores forzados.
    mejor: Dict[str, Dict[str, Any]] = {}
    for camino in analisis["caminos"]:
        stubs = _stubs_de_camino(camino)
        es_real = not stubs
        for nodo in camino["secuencia"]:
            actual = mejor.get(nodo)
            if actual is None:
                mejor[nodo] = {"real": es_real, "stubs": stubs}
            elif es_real and not actual["real"]:
                mejor[nodo] = {"real": True, "stubs": []}
            elif (not es_real) and (not actual["real"]) and len(stubs) < len(actual["stubs"]):
                mejor[nodo] = {"real": False, "stubs": stubs}

    cubrible_real = sorted(n for n, v in mejor.items() if v["real"])
    cubrible_por_stub = [
        {"nodo": n, "stubs_necesarios": v["stubs"]}
        for n, v in sorted(mejor.items()) if not v["real"]
    ]
    en_camino = set(mejor.keys())
    fuera_main = sorted(todos - en_camino - inalcanzables)

    alcanzables = todos - inalcanzables - set(fuera_main)
    denom = len(alcanzables) or 1
    pct_real = round(100.0 * len(cubrible_real) / denom, 1)
    cubiertos_total = len(cubrible_real) + len(cubrible_por_stub)
    pct_con_stubs = round(100.0 * cubiertos_total / denom, 1)

    return {
        "total_nodos": len(todos),
        "porcentaje_cubrible_real": pct_real,
        "porcentaje_cubrible_con_stubs": pct_con_stubs,
        "nodos_cubribles_real": cubrible_real,
        "nodos_cubribles_por_stub": cubrible_por_stub,
        "nodos_inalcanzables": sorted(inalcanzables),
        "nodos_fuera_del_camino_main": fuera_main,
        "nota": (
            "Cobertura combinada real + stubs. 'cubrible_real' no necesita forzar nada; "
            "'cubrible_por_stub' trae la receta (qué campo forzar y a qué valor) para "
            "recorrer ese nodo sin depender de que el AI/HTTP se comporte. Con stubs, el "
            "100% de los nodos alcanzables queda cubierto de forma determinista."
        ),
    }


def _hay_transformador_upstream(nodo: str, graph, cat: Dict[str, str]) -> bool:
    """True si hay un nodo transformador (http/ai/code) entre algún trigger y `nodo`."""
    padres: Dict[str, List[str]] = {}
    for e in graph.edges:
        padres.setdefault(e.target, []).append(e.source)
    visto: set = set()
    pila = list(padres.get(nodo, []))
    while pila:
        p = pila.pop()
        if p in visto:
            continue
        visto.add(p)
        if cat.get(p) in _CATEGORIAS_TRANSFORMADORAS:
            return True
        pila.extend(padres.get(p, []))
    return False
