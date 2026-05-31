"""Modo e2e sintético del contra-agente: simula un caso completo sin tocar prod.

Componentes:
    snapshot_factory  — datos canónicos determinísticos por batch_id
    mock_tools        — respuestas fake por tool name (sin tocar webhook)
    pdf_builder       — construye PDF en memoria desde lo que el agente "hizo"
    mock_agent        — mini-LLM con function calling, actúa como el agente bajo test
    adapter           — MockAdapter compatible con la interfaz de N8nAdapter

Output del flujo: bytes de un PDF + un ExpectedSnapshot, ambos sintéticos pero
coherentes entre sí. Se mandan al Verificador vía cliente `abad_synthetic` +
source `inline`. Cero llamadas a webhook real, cero queries a BD productiva.
"""
