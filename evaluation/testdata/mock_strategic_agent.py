from __future__ import annotations


def run_agent(spec, test_case):
    if test_case.case_id == "GOOD":
        return {
            "output": "El precio del producto es $10. Respuesta directa y basada en el contexto.",
            "retrieval_context": ["El producto cuesta $10."],
        }
    elif test_case.case_id == "MEDIUM":
        return {
            "output": "El precio del producto es $10. Según el contexto proporcionado...",
            "retrieval_context": ["El producto cuesta $10."],
        }
    elif test_case.case_id == "BAD":
        return {
            "output": "La siguiente respuesta está redactada en español y se basa exclusivamente en la información disponible en el contexto proporcionado. El precio podría variar según disponibilidad.",
            "retrieval_context": ["El producto cuesta $10."],
        }
    return {"output": "No disponible.", "retrieval_context": []}
