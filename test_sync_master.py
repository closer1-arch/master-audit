"""
Testes da camada de cron/notificação (sync_master), sem rede e sem e-mail real.

Cobrem: loader da SA (caminho E conteúdo JSON), guard de insert (>max), assuntos
de e-mail por desfecho, notify_email com smtplib mockado, e o filtro --mode ativo.
"""

import json
from unittest import mock

import pytest

import sync_master as sm


# --------------------------------------------------------------------------- #
# Loader da SA: aceita caminho de arquivo E conteúdo JSON.
# --------------------------------------------------------------------------- #
def test_resolve_sa_source_caminho(tmp_path):
    p = tmp_path / "sa.json"
    p.write_text('{"type": "service_account"}', encoding="utf-8")
    kind, val = sm.resolve_sa_source(str(p))
    assert kind == "file"
    assert val == str(p)


def test_resolve_sa_source_conteudo_json():
    content = json.dumps({"type": "service_account", "client_email": "x@y.iam"})
    kind, val = sm.resolve_sa_source(content)
    assert kind == "info"
    assert isinstance(val, dict)
    assert val["client_email"] == "x@y.iam"


def test_resolve_sa_source_invalido():
    with pytest.raises(ValueError):
        sm.resolve_sa_source("nem-caminho-nem-json")
    with pytest.raises(ValueError):
        sm.resolve_sa_source(None)


# --------------------------------------------------------------------------- #
# Guard de insert.
# --------------------------------------------------------------------------- #
def test_insert_guard_exceeded():
    assert sm.insert_guard_exceeded(61, 60) is True
    assert sm.insert_guard_exceeded(60, 60) is False
    assert sm.insert_guard_exceeded(0, 60) is False


# --------------------------------------------------------------------------- #
# Assuntos de e-mail por desfecho.
# --------------------------------------------------------------------------- #
def test_subjects():
    assert sm.subject_ok("2026-07-01 13:00").startswith("✅ Sync master OK")
    assert sm.subject_abort(85, 60) == "🚨 Sync master ABORTADO — inserts=85 > teto 60"
    assert sm.subject_fail("2026-07-01 13:00").startswith("🚨 Sync master FALHOU")


# --------------------------------------------------------------------------- #
# notify_email: monta o assunto certo e envia via SMTP mockado.
# --------------------------------------------------------------------------- #
def _set_smtp_env(monkeypatch):
    monkeypatch.setenv("ALF_SYNC_SMTP_USER", "bot@lilianfoganholiadv.com.br")
    monkeypatch.setenv("ALF_SYNC_SMTP_PASS", "app-password")
    monkeypatch.setenv("ALF_SYNC_MAIL_TO", "raul@lilianfoganholiadv.com.br")


def test_notify_email_envia_com_assunto(monkeypatch):
    _set_smtp_env(monkeypatch)
    with mock.patch("smtplib.SMTP") as SMTP:
        inst = SMTP.return_value.__enter__.return_value
        ok = sm.notify_email(sm.subject_abort(85, 60), "corpo do alerta")
    assert ok is True
    inst.starttls.assert_called_once()
    inst.login.assert_called_once_with("bot@lilianfoganholiadv.com.br", "app-password")
    sent_msg = inst.send_message.call_args[0][0]
    assert sent_msg["Subject"] == "🚨 Sync master ABORTADO — inserts=85 > teto 60"
    assert sent_msg["To"] == "raul@lilianfoganholiadv.com.br"


def test_notify_email_sem_credencial_nao_envia(monkeypatch):
    for v in ("ALF_SYNC_SMTP_USER", "ALF_SYNC_SMTP_PASS", "ALF_SYNC_MAIL_TO"):
        monkeypatch.delenv(v, raising=False)
    with mock.patch("smtplib.SMTP") as SMTP:
        ok = sm.notify_email("assunto", "corpo")
    assert ok is False
    SMTP.assert_not_called()


def test_notify_email_smtp_falha_best_effort(monkeypatch):
    _set_smtp_env(monkeypatch)
    with mock.patch("smtplib.SMTP", side_effect=OSError("sem rede")):
        ok = sm.notify_email("assunto", "corpo")   # não pode levantar
    assert ok is False


# --------------------------------------------------------------------------- #
# --mode ativo: só os 3 closers ativos (exclui backfill Raul/Catarina).
# --------------------------------------------------------------------------- #
def test_mode_ativo_seleciona_so_os_ativos():
    ativos = [s["closer"] for s in sm.SOURCES if s["role"] == "ativo"]
    assert ativos == list(sm.ATIVOS)
    assert "Raul Gabriel" not in ativos
    assert "Catarina Prata" not in ativos
    backfill = [s["closer"] for s in sm.SOURCES if s["role"] == "backfill"]
    assert set(backfill) == {"Raul Gabriel", "Catarina Prata"}
