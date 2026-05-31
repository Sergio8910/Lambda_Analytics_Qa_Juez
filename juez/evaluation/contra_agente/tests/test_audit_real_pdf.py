"""Tests para audit_real_pdf — auditar PDFs reales sin disparar el flow.

Mockea BD + requests para no tocar Postgres ni red.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests as _requests_real

from juez.evaluation.contra_agente import audit_real_pdf as audit_mod
from juez.evaluation.contra_agente import verificador_client as vc
from juez.evaluation.contra_agente.audit_real_pdf import (
    _extract_drive_file_id,
    audit_real_inventario,
)
from juez.evaluation.contra_agente.verificador_client import verify_drive_pdf


# ── helpers ──────────────────────────────────────────────────────────────────
def _make_resp(status, body=None):
    r = MagicMock()
    r.status_code = status
    r.text = json.dumps(body or {})
    r.json.return_value = body if body is not None else {}
    r.raise_for_status = MagicMock()
    return r


def _fake_snapshot():
    return (
        {
            "artifact_id": "42",
            "counts": {"fotos": 22, "ambientes": 2},
            "structure": {
                "ambientes": ["Cocina", "Baño social"],
                "fotos_por_ambiente": {"Cocina": 12, "Baño social": 10},
                "tipo_inventario": "INICIAL",
            },
            "required_strings": ["1234", "INICIAL"],
        },
        {"source": "real_db", "inventario_id": 42, "contrato_id": "1234"},
    )


# ── _extract_drive_file_id ──────────────────────────────────────────────────
class TestExtractDriveFileId:
    def test_url_file_d_format(self):
        url = "https://drive.google.com/file/d/ABC123xyz_-/view"
        assert _extract_drive_file_id(url) == "ABC123xyz_-"

    def test_url_with_query_params(self):
        url = "https://drive.google.com/file/d/ABC123xyz/view?usp=sharing"
        assert _extract_drive_file_id(url) == "ABC123xyz"

    def test_url_open_id_format(self):
        url = "https://drive.google.com/open?id=XYZ789abc"
        assert _extract_drive_file_id(url) == "XYZ789abc"

    def test_url_uc_id_format(self):
        url = "https://drive.google.com/uc?id=PDF_ID_HERE&export=download"
        assert _extract_drive_file_id(url) == "PDF_ID_HERE"

    def test_bare_file_id(self):
        assert _extract_drive_file_id("ABCdef1234567890") == "ABCdef1234567890"

    def test_none_returns_none(self):
        assert _extract_drive_file_id(None) is None

    def test_empty_string_returns_none(self):
        assert _extract_drive_file_id("") is None

    def test_whitespace_only_returns_none(self):
        assert _extract_drive_file_id("   ") is None

    def test_invalid_short_string_returns_none(self):
        # menos de 10 chars y sin patrón de URL — no es file_id válido
        assert _extract_drive_file_id("abc") is None

    def test_url_without_file_id_returns_none(self):
        assert _extract_drive_file_id("https://example.com/something") is None


# ── verify_drive_pdf — happy path ────────────────────────────────────────────
class TestVerifyDrivePdf:
    """Validamos que el body manda source.type=drive con file_id correcto."""

    def test_post_body_uses_drive_source(self):
        dispatch = _make_resp(
            202, {"verification_id": "v1", "status": "queued"}
        )
        poll_done = _make_resp(
            200,
            {
                "status": "completed",
                "verdict": "OK",
                "score": 0.98,
                "checks": [],
                "issues": [],
            },
        )

        with patch.object(vc, "requests") as mock_req:
            mock_req.RequestException = _requests_real.RequestException
            mock_req.post.return_value = dispatch
            mock_req.get.return_value = poll_done

            result = verify_drive_pdf(
                cliente="abad_synthetic",
                artifact_id="42",
                drive_file_id="FAKE_DRIVE_ID_123",
                expected_snapshot={"counts": {"fotos": 5}},
                base_url="http://verificador.test",
                poll_timeout_s=2.0,
                poll_interval_s=0.0,
            )

            assert result["status"] == "completed"
            assert result["verdict"] == "OK"

            # Validar shape del body
            kwargs = mock_req.post.call_args.kwargs
            body = kwargs["json"]
            assert body["cliente"] == "abad_synthetic"
            assert body["artifact_type"] == "pdf"
            assert body["artifact_id"] == "42"
            assert body["source"]["type"] == "drive"
            assert body["source"]["file_id"] == "FAKE_DRIVE_ID_123"
            assert "blob_base64" not in body["source"]
            assert body["metadata"]["synthetic"] is True
            assert body["metadata"]["expected_snapshot"] == {"counts": {"fotos": 5}}

    def test_dispatch_failure_raises_unavailable(self):
        bad = _make_resp(503, {"error": "down"})
        with patch.object(vc, "requests") as mock_req:
            mock_req.RequestException = _requests_real.RequestException
            mock_req.post.return_value = bad
            with pytest.raises(vc.VerificadorUnavailable):
                verify_drive_pdf(
                    cliente="abad_synthetic",
                    artifact_id="42",
                    drive_file_id="FAKE_ID",
                    expected_snapshot={},
                    base_url="http://verificador.test",
                    poll_timeout_s=0.5,
                    poll_interval_s=0.0,
                )


# ── audit_real_inventario — orquestación ─────────────────────────────────────
class TestAuditRealInventario:
    def test_happy_path(self):
        """Snapshot + file_id + Verificador OK → verdict OK en el dict."""
        dispatch = _make_resp(202, {"verification_id": "v_real", "status": "queued"})
        poll_done = _make_resp(
            200,
            {
                "status": "completed",
                "verdict": "OK",
                "score": 0.93,
                "checks": [{"name": "integridad", "verdict": "OK", "score": 1.0}],
                "issues": [],
            },
        )

        with patch.object(audit_mod, "make_real_db_data", return_value=_fake_snapshot()), \
             patch.object(audit_mod, "_get_pdf_drive_file_id", return_value="DRIVE_ID_OK"), \
             patch.object(vc, "requests") as mock_req:
            mock_req.RequestException = _requests_real.RequestException
            mock_req.post.return_value = dispatch
            mock_req.get.return_value = poll_done

            out = audit_real_inventario(42, base_url="http://verificador.test")

            assert "error" not in out
            assert out["inventario_id"] == 42
            assert out["artifact_id"] == "42"
            assert out["pdf_drive_file_id"] == "DRIVE_ID_OK"
            assert out["verdict"] == "OK"
            assert out["score"] == 0.93
            assert len(out["checks"]) == 1

            # Body fue con drive + el file_id retornado por _get_pdf_drive_file_id
            body = mock_req.post.call_args.kwargs["json"]
            assert body["source"] == {"type": "drive", "file_id": "DRIVE_ID_OK"}
            assert body["cliente"] == "abad_synthetic"

    def test_no_drive_file_id_returns_error(self):
        """Si _get_pdf_drive_file_id retorna None → dict con error, no crashea."""
        with patch.object(audit_mod, "make_real_db_data", return_value=_fake_snapshot()), \
             patch.object(audit_mod, "_get_pdf_drive_file_id", return_value=None):
            out = audit_real_inventario(99)
            assert "error" in out
            assert "no tiene un PDF aprobado" in out["error"].lower() or \
                   "drive_url" in out["error"].lower()
            assert out["verdict"] == "UNVERIFIABLE"
            assert out["pdf_drive_file_id"] is None
            assert out["inventario_id"] == 99

    def test_snapshot_db_error_returns_error(self):
        """Si la BD productiva falla → dict con error, sin crash."""
        from juez.evaluation.contra_agente.synthetic.real_db_source import RealDbError

        with patch.object(audit_mod, "make_real_db_data",
                          side_effect=RealDbError("inventario no existe")):
            out = audit_real_inventario(404)
            assert "error" in out
            assert "snapshot" in out["error"].lower() or "inventario" in out["error"].lower()
            assert out["verdict"] == "UNVERIFIABLE"

    def test_verificador_unavailable_returns_error(self):
        """Si Verificador cae → dict con error, sin crash."""
        with patch.object(audit_mod, "make_real_db_data", return_value=_fake_snapshot()), \
             patch.object(audit_mod, "_get_pdf_drive_file_id", return_value="DRIVE_ID"), \
             patch.object(audit_mod, "verify_drive_pdf",
                          side_effect=vc.VerificadorUnavailable("conn refused")):
            out = audit_real_inventario(42)
            assert "error" in out
            assert "verificador" in out["error"].lower()
            assert out["verdict"] == "UNVERIFIABLE"
            assert out["pdf_drive_file_id"] == "DRIVE_ID"


# ── _get_pdf_drive_file_id — mockeando psycopg2 ──────────────────────────────
class TestGetPdfDriveFileId:
    def test_arrendador_url_takes_precedence(self):
        """Cuando hay arrendador y propietario URLs, se elige arrendador."""
        fake_conn = MagicMock()
        fake_cur = MagicMock()
        fake_conn.cursor.return_value = fake_cur
        fake_cur.fetchone.return_value = (
            "https://drive.google.com/file/d/ARRENDADOR_ID_999/view",
            "https://drive.google.com/file/d/PROPIETARIO_ID_888/view",
        )

        with patch.object(audit_mod, "_connect_readonly", return_value=fake_conn):
            file_id = audit_mod._get_pdf_drive_file_id(42)
            assert file_id == "ARRENDADOR_ID_999"
        fake_conn.close.assert_called_once()

    def test_falls_back_to_propietario_if_arrendador_missing(self):
        fake_conn = MagicMock()
        fake_cur = MagicMock()
        fake_conn.cursor.return_value = fake_cur
        fake_cur.fetchone.return_value = (
            None,
            "https://drive.google.com/file/d/PROPIETARIO_ID_777/view",
        )

        with patch.object(audit_mod, "_connect_readonly", return_value=fake_conn):
            file_id = audit_mod._get_pdf_drive_file_id(42)
            assert file_id == "PROPIETARIO_ID_777"

    def test_returns_none_when_no_rows(self):
        fake_conn = MagicMock()
        fake_cur = MagicMock()
        fake_conn.cursor.return_value = fake_cur
        fake_cur.fetchone.return_value = None

        with patch.object(audit_mod, "_connect_readonly", return_value=fake_conn):
            assert audit_mod._get_pdf_drive_file_id(42) is None

    def test_returns_none_when_both_urls_empty(self):
        fake_conn = MagicMock()
        fake_cur = MagicMock()
        fake_conn.cursor.return_value = fake_cur
        fake_cur.fetchone.return_value = (None, None)

        with patch.object(audit_mod, "_connect_readonly", return_value=fake_conn):
            assert audit_mod._get_pdf_drive_file_id(42) is None

    def test_returns_none_when_db_unreachable(self):
        """Si _connect_readonly tira RealDbError → retorna None sin crashear."""
        from juez.evaluation.contra_agente.synthetic.real_db_source import RealDbError

        with patch.object(audit_mod, "_connect_readonly",
                          side_effect=RealDbError("ABAT_DB_URL no configurado")):
            assert audit_mod._get_pdf_drive_file_id(42) is None
