"""
Testes da camada de cron/notificação (sync_master), sem rede e sem envio real.

Cobrem: loader da SA (caminho E conteúdo JSON), guard de insert (>max), assuntos
por desfecho, notify_email via Telegram com urllib mockado (payload/env/rede/
truncagem), e o filtro --mode ativo.
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
# notify_email (Telegram): monta chat_id+text e faz POST via urllib mockado.
# --------------------------------------------------------------------------- #
def _set_tg_env(monkeypatch):
    monkeypatch.setenv("ALF_SYNC_TG_TOKEN", "123456:ABC-token")
    monkeypatch.setenv("ALF_SYNC_TG_CHAT", "-1009876543210")


def _posted(urlopen_mock):
    """Extrai (url, payload_dict) do Request passado ao urlopen mockado."""
    req = urlopen_mock.call_args[0][0]
    return req.full_url, json.loads(req.data.decode("utf-8"))


def test_notify_envia_payload_correto(monkeypatch):
    _set_tg_env(monkeypatch)
    with mock.patch("urllib.request.urlopen") as urlopen:
        ok = sm.notify_email(sm.subject_abort(85, 60), "corpo do alerta")
    assert ok is True
    url, payload = _posted(urlopen)
    assert url == "https://api.telegram.org/bot123456:ABC-token/sendMessage"
    assert payload["chat_id"] == "-1009876543210"
    assert payload["text"] == "🚨 Sync master ABORTADO — inserts=85 > teto 60\n\ncorpo do alerta"


def test_notify_sem_credencial_nao_envia(monkeypatch):
    for v in ("ALF_SYNC_TG_TOKEN", "ALF_SYNC_TG_CHAT"):
        monkeypatch.delenv(v, raising=False)
    with mock.patch("urllib.request.urlopen") as urlopen:
        ok = sm.notify_email("assunto", "corpo")
    assert ok is False
    urlopen.assert_not_called()


def test_notify_rede_falha_best_effort(monkeypatch):
    _set_tg_env(monkeypatch)
    with mock.patch("urllib.request.urlopen", side_effect=OSError("sem rede")):
        ok = sm.notify_email("assunto", "corpo")   # não pode levantar
    assert ok is False


def test_notify_trunca_corpo_longo(monkeypatch):
    _set_tg_env(monkeypatch)
    big = "x" * 9000
    with mock.patch("urllib.request.urlopen") as urlopen:
        ok = sm.notify_email("ASSUNTO", big)
    assert ok is True
    _, payload = _posted(urlopen)
    text = payload["text"]
    assert len(text) <= sm.TG_MAX_TEXT
    assert text.startswith("ASSUNTO")
    assert text.endswith("…(truncado)")


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
