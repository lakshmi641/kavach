"""Persistence, auth, and API edge tests; no external provider calls."""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import asyncio
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app.security import validate_init_data, validate_session, session_cookie
from core.audit import AuditLog
from core.models import Case, Outcome, RiskSignals
from core.vault import ChannelIdentity, FamilySettings, TrustedContact
from store.sqlite import SQLiteStore


def signed_init_data(token: str, user_id: str, auth_date: int | None = None) -> str:
    fields = {"auth_date": str(auth_date or int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "Demo"}, separators=(",", ":"))}
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_mini_app_init_data_signature_and_age():
    token = "123456:test-token"
    init_data = signed_init_data(token, "42")
    assert validate_init_data(init_data, token)["user_id"] == "42"
    with pytest.raises(ValueError, match="signature"):
        validate_init_data(init_data.replace("Demo", "Mallory"), token)
    expired = signed_init_data(token, "42", int(time.time()) - 90_000)
    with pytest.raises(ValueError, match="expired"):
        validate_init_data(expired, token)


def test_session_cookie_is_signed_and_expires():
    now = int(time.time())
    token = session_cookie("123", "secret", issued_at=now)
    assert validate_session(token, "secret", max_age_seconds=10_000) == "123"
    assert validate_session(token, "wrong", max_age_seconds=10_000) is None
    expired = session_cookie("123", "secret", issued_at=now - 20)
    assert validate_session(expired, "secret", max_age_seconds=10) is None


def test_sqlite_persists_redacted_cases_contacts_and_keyed_audit(tmp_path):
    store = SQLiteStore(tmp_path / "kavach.sqlite3")
    family = FamilySettings((ChannelIdentity("telegram", "parent-1"),), "Amma", "te", "ravi")
    store.save_family(family)
    contact = TrustedContact("ravi", "Ravi", "son", "+91 90000 11111",
                             (ChannelIdentity("telegram", "ravi-1"),), True, True)
    store.save_contact(contact)
    now = datetime.now(timezone.utc)
    case = Case("case-1", "telegram", "parent-1", "OTP [CODE]", RiskSignals(urgency=True),
                Outcome.PAUSE, ["Still verify"], now, pending_request_id="request-1",
                authorized_contact_id="ravi", pending_nonce="0123456789abcdef0123456789abcdef",
                confirmation_deadline=now + timedelta(days=365))
    store.save_case(case)
    audit = AuditLog(lambda: now, hmac_key=b"test-key-material-32-bytes-long!!",
                     on_record=lambda entry, digest: store.save_audit(entry, digest))
    entry = audit.record(case.case_id, "case_opened", content=case.redacted_text,
                         detail="OTP 482913")
    restored = store.load_cases()[0]
    assert restored.pending_nonce == case.pending_nonce
    assert restored.outcome is Outcome.PAUSE
    assert store.load_contacts()[0].identities[0].user_id == "ravi-1"
    assert store.load_family().parent[0].user_id == "parent-1"
    with store._connect() as db:
        row = db.execute("SELECT content_digest, detail_json FROM audit").fetchone()
    assert len(row["content_digest"]) == 64
    assert "482913" not in row["detail_json"]
    assert store.load_audit()[0].event == entry.event


def test_invite_is_single_use_and_binds_telegram_id(tmp_path):
    store = SQLiteStore(tmp_path / "invite.sqlite3")
    store.save_contact(TrustedContact("ravi", "Ravi", "son", "", (), True))
    store.save_invite("token-digest", "ravi", "2999-01-01T00:00:00")
    assert store.consume_invite("token-digest", "telegram-user") == "ravi"
    assert store.consume_invite("token-digest", "attacker") is None
    assert store.load_contacts()[0].identities[0].user_id == "telegram-user"


def test_callback_number_requires_a_bound_contact_and_is_marked_self_shared(tmp_path):
    store = SQLiteStore(tmp_path / "phone.sqlite3")
    store.save_contact(TrustedContact(
        "ravi", "Ravi", "son", "", (ChannelIdentity("telegram", "ravi-1"),), True
    ))
    assert store.contact_for_identity("telegram", "stranger") is None
    assert store.contact_for_identity("telegram", "ravi-1") == "ravi"
    assert store.save_self_shared_phone("ravi", "+91 90000 11111")
    contact = store.load_contacts()[0]
    assert contact.phone == "+91 90000 11111"


def test_expiring_a_case_also_removes_its_confirmation_request(tmp_path):
    store = SQLiteStore(tmp_path / "retention.sqlite3")
    store.save_contact(TrustedContact(
        "ravi", "Ravi", "son", "", (ChannelIdentity("telegram", "ravi-1"),), True
    ))
    created = datetime(2020, 1, 1, tzinfo=timezone.utc)
    case = Case(
        "expired-case", "telegram", "parent-1", "redacted", RiskSignals(), Outcome.PAUSE,
        ["Still verify"], created, pending_request_id="pending-request", pending_nonce="a" * 32,
        authorized_contact_id="ravi", confirmation_deadline=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    store.save_case(case)
    assert store.purge_expired("2021-01-01T00:00:00+00:00") == 1
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM confirmation_request").fetchone()[0] == 0


def test_telegram_contact_is_saved_only_when_bound_user_shares_their_own_number(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "share-phone.sqlite3"))
    monkeypatch.setenv("KAVACH_ENV", "demo")
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "NEBIUS_API_KEY",
                "MODEL_NANO", "MODEL_SUPER", "MODEL_ULTRA", "TAVILY_API_KEY",
                "ASR_BASE_URL", "ASR_API_KEY"):
        monkeypatch.setenv(key, "")
    module = importlib.import_module("app.main")
    module = importlib.reload(module)
    module.store.save_contact(TrustedContact(
        "ravi", "Ravi", "son", "", (ChannelIdentity("telegram", "2002"),), True
    ))
    module.reload_vault()
    asyncio.run(module.process_update({"message": {
        "from": {"id": 2002}, "contact": {"user_id": 9999, "phone_number": "+91 90000 11111"}
    }}))
    assert module.store.load_contacts()[0].phone == ""
    asyncio.run(module.process_update({"message": {
        "from": {"id": 2002}, "contact": {"user_id": 2002, "phone_number": "+91 90000 11111"}
    }}))
    saved = module.store.load_contacts()[0]
    assert saved.phone == "+91 90000 11111"


def test_demo_api_check_and_live_webhook_secret_rejection(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api.sqlite3"))
    monkeypatch.setenv("KAVACH_ENV", "demo")
    # .env may contain real provider credentials; this test must never call them.
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "NEBIUS_API_KEY",
                "MODEL_NANO", "MODEL_SUPER", "MODEL_ULTRA", "TAVILY_API_KEY",
                "ASR_BASE_URL", "ASR_API_KEY"):
        monkeypatch.setenv(key, "")
    module = importlib.import_module("app.main")
    module = importlib.reload(module)
    with TestClient(module.app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.post("/api/demo/session", json={}).status_code == 200
        config = client.get("/api/config")
        assert config.status_code == 200
        invalid_guardian = client.put("/api/setup", json={"parent_name": "Amma", "language": "en",
                                                            "guardian_contact_id": "missing", "consent": True})
        assert invalid_guardian.status_code == 400
        setup = client.put("/api/setup", json={"parent_name": "Amma", "language": "en",
                                                "guardian_contact_id": "ravi", "consent": True})
        assert setup.status_code == 200
        checked = client.post("/api/check", json={"text": "Reached home safely."})
        assert checked.status_code == 200
        assert checked.json()["outcome"] == "PAUSE"
        assert not checked.json()["identity_route_verified"]
        module.settings = replace(module.settings, telegram_bot_token="123456:test",
                                   telegram_webhook_secret="correct-secret")
        response = client.post("/tg/webhook", json={}, headers={
            "x-telegram-bot-api-secret-token": "wrong"})
        assert response.status_code == 401
