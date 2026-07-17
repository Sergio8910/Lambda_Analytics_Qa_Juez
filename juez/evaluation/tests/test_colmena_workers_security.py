"""Tests del SecurityWorker: cobertura mas amplia de la deteccion de secretos
hardcodeados. Antes, el regex solo reconocia el patron literal `api_key=...`/
`secret=...` etc con el operador de asignacion inmediatamente despues del
nombre -- variables como OPENAI_KEY, AUTH, CLAVE, anotaciones de tipo
(`x_api_key: str = "..."`) y secretos usados como default de
`os.environ.get()`/`getenv()` pasaban desapercibidos.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from juez.colmena.scanner import scan_project
from juez.colmena.workers import SecurityWorker


def _findings_para(contenido: str, nombre_archivo: str = "config.py") -> list:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / nombre_archivo).write_text(contenido, encoding="utf-8")
        inventory = scan_project(root)
        return SecurityWorker(root, inventory, _builder()).run()


def _builder():
    from juez.colmena.workers import FindingBuilder
    return FindingBuilder()


def _hay_secreto_hardcodeado(findings) -> bool:
    return any(f.category == "security" and "secreto" in f.title.lower() for f in findings)


def test_detecta_variable_con_prefijo_no_api():
    findings = _findings_para('OPENAI_KEY = "sk-proj-abc123def456ghi789"\n')
    assert _hay_secreto_hardcodeado(findings)


def test_detecta_variable_generica_key():
    findings = _findings_para('DEFAULT_KEY = "sk-proj-abc123def456ghi789"\n')
    assert _hay_secreto_hardcodeado(findings)


def test_detecta_auth_y_clave_en_espanol():
    assert _hay_secreto_hardcodeado(_findings_para('AUTH = "sk-proj-abc123def456ghi789"\n'))
    assert _hay_secreto_hardcodeado(_findings_para('CLAVE = "sk-proj-abc123def456ghi789"\n'))
    assert _hay_secreto_hardcodeado(_findings_para('CONTRASENA = "SuperClaveReal123456"\n'))


def test_detecta_con_anotacion_de_tipo():
    findings = _findings_para('x_api_key: str = "sk-proj-abc123def456ghi789"\n')
    assert _hay_secreto_hardcodeado(findings)


def test_detecta_secreto_como_default_de_environ_get():
    findings = _findings_para(
        'api_key = os.environ.get("KEY_FALLBACK", "sk-proj-abc123def456ghi789")\n'
    )
    assert any("valor por defecto" in f.title.lower() for f in findings)


def test_detecta_secreto_como_default_de_getenv():
    findings = _findings_para('token = os.getenv("TOKEN", "ghp_realtoken1234567890abcdef")\n')
    assert any("valor por defecto" in f.title.lower() for f in findings)


def test_no_falso_positivo_con_default_vacio_o_placeholder():
    findings = _findings_para('api_key = os.environ.get("OPENAI_API_KEY", "")\n')
    assert not any("valor por defecto" in f.title.lower() for f in findings)


def test_no_falso_positivo_variable_key_generica_sin_digitos():
    """sort_key/primary_key con un valor corto o sin apariencia de secreto NO
    deben dispararse -- el nombre 'key' es demasiado comun en codigo normal."""
    assert not _hay_secreto_hardcodeado(_findings_para('sort_key = "name"\n'))
    assert not _hay_secreto_hardcodeado(_findings_para('primary_key = "id"\n'))
    assert not _hay_secreto_hardcodeado(_findings_para('cache_key = "short"\n'))


def test_no_falso_positivo_llamada_a_funcion():
    """El valor asignado es una llamada a funcion, no un literal -- el regex
    generico podria capturar el nombre de la funcion como si fuera el
    'secreto', pero no debe generar un finding (no hay digitos ni es un
    valor real)."""
    assert not _hay_secreto_hardcodeado(_findings_para('api_key = load_from_vault()\n'))
    assert not _hay_secreto_hardcodeado(_findings_para('api_key = os.environ["OPENAI_API_KEY"]\n'))


def test_detecta_formato_env_file_sin_comillas():
    """Regresion: el formato clasico de .env (API_KEY=valor, sin comillas)
    debe seguir detectandose igual que antes de ampliar el regex."""
    findings = _findings_para("API_KEY=sk_live_123456789abcdef\n", nombre_archivo=".env")
    assert _hay_secreto_hardcodeado(findings)


def test_detecta_secreto_embebido_en_un_prompt_txt():
    """El asset mas evaluado por el Juez es el prompt del agente (.txt) -- un
    secreto pegado ahi (ej. 'usa esta clave: OPENAI_KEY = "sk-..."') antes era
    invisible para SecurityWorker porque .txt no estaba en su lista de
    extensiones escaneadas."""
    findings = _findings_para(
        'Eres un agente. Usa esta clave: OPENAI_KEY = "sk-proj-abc123def456ghi789"\n',
        nombre_archivo="agente_prompt.txt",
    )
    assert _hay_secreto_hardcodeado(findings)
