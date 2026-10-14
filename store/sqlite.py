"""SQLite persistence for the single-family hackathon deployment."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.audit import AuditEntry
from core.models import Case, Outcome, RiskSignals
from core.vault import ChannelIdentity, FamilySettings, TrustedContact


_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS family (
  id INTEGER PRIMARY KEY CHECK (id = 1), parent_name TEXT NOT NULL,
  language TEXT NOT NULL, guardian_contact_id TEXT NOT NULL,
  recorded_message_ref TEXT, consented_at TEXT, escalation_rule TEXT NOT NULL DEFAULT 'high_risk'
);
CREATE TABLE IF NOT EXISTS parent_identity (
  channel TEXT PRIMARY KEY, user_id TEXT NOT NULL, bound_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contact (
  contact_id TEXT PRIMARY KEY, name TEXT NOT NULL, relationship TEXT NOT NULL,
  phone TEXT, phone_self_shared INTEGER NOT NULL DEFAULT 0,
  approved_by_guardian INTEGER NOT NULL, is_guardian INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS identity (
  contact_id TEXT NOT NULL REFERENCES contact(contact_id) ON DELETE CASCADE,
  channel TEXT NOT NULL, user_id TEXT NOT NULL, bound_at TEXT NOT NULL,
  PRIMARY KEY(contact_id, channel), UNIQUE(channel, user_id)
);
CREATE TABLE IF NOT EXISTS invite (
  token_hash TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contact(contact_id) ON DELETE CASCADE,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT
);
CREATE TABLE IF NOT EXISTS case_record (
  case_id TEXT PRIMARY KEY, channel TEXT NOT NULL, reporter_id TEXT NOT NULL, redacted_text TEXT NOT NULL,
  signals_json TEXT NOT NULL, outcome TEXT NOT NULL, reasons_json TEXT NOT NULL,
  history_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  claimed_contact_id TEXT, pending_request_id TEXT, pending_nonce TEXT,
  authorized_contact_id TEXT, confirmation_deadline TEXT, circuit_breaker_opened INTEGER NOT NULL,
  high_risk INTEGER NOT NULL, rule_fired TEXT NOT NULL, latency_json TEXT NOT NULL,
  cost_rupees REAL NOT NULL, identity_route_verified INTEGER NOT NULL
  , evidence_json TEXT NOT NULL DEFAULT '[]', explanation TEXT NOT NULL DEFAULT '',
  traces_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS confirmation_request (
  request_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES case_record(case_id) ON DELETE CASCADE,
  claimed_contact_id TEXT, expected_verifier_id TEXT NOT NULL, nonce TEXT NOT NULL,
  expires_at TEXT NOT NULL, consumed_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, case_id TEXT NOT NULL,
  event TEXT NOT NULL, detail_json TEXT NOT NULL, content_digest TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS audit_case_id ON audit(case_id, id DESC);
CREATE INDEX IF NOT EXISTS case_created_at ON case_record(created_at);
"""


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def save_family(self, settings: FamilySettings) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO family(id,parent_name,language,guardian_contact_id,recorded_message_ref,escalation_rule)
                   VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET parent_name=excluded.parent_name,
                   language=excluded.language, guardian_contact_id=excluded.guardian_contact_id,
                   recorded_message_ref=excluded.recorded_message_ref,
                   escalation_rule=excluded.escalation_rule""",
                (settings.parent_name, settings.language, settings.guardian_contact_id,
                 settings.recorded_message_ref, settings.escalation_rule),
            )
        self.set_parent(settings.parent)

    def set_parent(self, identities: tuple[ChannelIdentity, ...]) -> None:
        # Parent identities are represented with a reserved contact id to preserve uniqueness.
        with self._connect() as db:
            db.execute("DELETE FROM parent_identity")
            db.executemany(
                "INSERT INTO parent_identity(channel,user_id,bound_at) VALUES(?,?,?)",
                [(i.channel, i.user_id, datetime.now(timezone.utc).isoformat()) for i in identities],
            )

    def load_family(self) -> FamilySettings | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM family WHERE id=1").fetchone()
            if row is None:
                return None
            parent_rows = db.execute("SELECT channel,user_id FROM parent_identity").fetchall()
            return FamilySettings(
                parent=tuple(ChannelIdentity(r["channel"], r["user_id"]) for r in parent_rows),
                parent_name=row["parent_name"], language=row["language"],
                guardian_contact_id=row["guardian_contact_id"],
                recorded_message_ref=row["recorded_message_ref"],
                escalation_rule=row["escalation_rule"],
            )

    def save_contact(self, contact: TrustedContact) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO contact VALUES(?,?,?,?,?,?,?) ON CONFLICT(contact_id) DO UPDATE SET
                   name=excluded.name, relationship=excluded.relationship, phone=excluded.phone,
                   phone_self_shared=excluded.phone_self_shared,
                   approved_by_guardian=excluded.approved_by_guardian, is_guardian=excluded.is_guardian""",
                (contact.contact_id, contact.name, contact.relationship, contact.phone or None,
                 int(bool(contact.phone)), int(contact.approved_by_guardian), int(contact.is_guardian)),
            )
            db.execute("DELETE FROM identity WHERE contact_id=?", (contact.contact_id,))
            db.executemany(
                "INSERT INTO identity(contact_id,channel,user_id,bound_at) VALUES(?,?,?,?)",
                [(contact.contact_id, i.channel, i.user_id, datetime.now(timezone.utc).isoformat())
                 for i in contact.identities],
            )

    def load_contacts(self) -> list[TrustedContact]:
        with self._connect() as db:
            contacts = db.execute("SELECT * FROM contact ORDER BY contact_id").fetchall()
            out = []
            for c in contacts:
                ids = db.execute("SELECT channel,user_id FROM identity WHERE contact_id=?", (c["contact_id"],)).fetchall()
                out.append(TrustedContact(
                    contact_id=c["contact_id"], name=c["name"], relationship=c["relationship"],
                    phone=c["phone"] or "", identities=tuple(ChannelIdentity(i["channel"], i["user_id"]) for i in ids),
                    approved_by_guardian=bool(c["approved_by_guardian"]), is_guardian=bool(c["is_guardian"]),
                ))
            return out

    def save_invite(self, token_hash: str, contact_id: str, expires_at: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO invite(token_hash,contact_id,created_at,expires_at) VALUES(?,?,?,?)",
                       (token_hash, contact_id, datetime.now(timezone.utc).isoformat(), expires_at))

    def consume_invite(self, token_hash: str, user_id: str) -> str | None:
        """Atomically consume one unexpired token and bind its Telegram ID."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT contact_id FROM invite WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
                             (token_hash, now)).fetchone()
            if not row:
                return None
            contact_id = row["contact_id"]
            db.execute("UPDATE invite SET used_at=? WHERE token_hash=?", (now, token_hash))
            db.execute("INSERT INTO identity(contact_id,channel,user_id,bound_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(contact_id,channel) DO UPDATE SET user_id=excluded.user_id,bound_at=excluded.bound_at",
                       (contact_id, "telegram", user_id, now))
            return contact_id

    def bind_parent(self, user_id: str) -> None:
        self.set_parent((ChannelIdentity("telegram", str(user_id)),))

    def remove_contact(self, contact_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM contact WHERE contact_id=?", (contact_id,))

    def family_consented(self) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT consented_at FROM family WHERE id=1").fetchone()
            return bool(row and row["consented_at"])

    @staticmethod
    def _case_payload(c: Case) -> tuple[Any, ...]:
        now = datetime.now(timezone.utc).isoformat()
        return (
            c.case_id, c.channel, c.reporter_id, c.redacted_text,
            json.dumps({k: getattr(c.signals, k) for k in c.signals.__dataclass_fields__}),
            c.outcome.value, json.dumps(c.reasons), json.dumps(c.history), c.created_at.isoformat(), now,
            c.claimed_contact_id, c.pending_request_id, c.pending_nonce, c.authorized_contact_id,
            c.confirmation_deadline.isoformat() if c.confirmation_deadline else None,
            int(c.circuit_breaker_opened), int(c.high_risk), c.rule_fired, json.dumps(c.latency_ms),
            c.cost_rupees, int(c.identity_route_verified), json.dumps(c.evidence), c.explanation,
            json.dumps(c.model_traces),
        )

    def save_case(self, case: Case) -> None:
        with self._connect() as db:
            db.execute("""INSERT INTO case_record VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(case_id) DO UPDATE SET channel=excluded.channel, reporter_id=excluded.reporter_id,
                redacted_text=excluded.redacted_text, signals_json=excluded.signals_json,
                outcome=excluded.outcome, reasons_json=excluded.reasons_json, history_json=excluded.history_json,
                updated_at=excluded.updated_at, claimed_contact_id=excluded.claimed_contact_id,
                pending_request_id=excluded.pending_request_id, pending_nonce=excluded.pending_nonce,
                authorized_contact_id=excluded.authorized_contact_id, confirmation_deadline=excluded.confirmation_deadline,
                circuit_breaker_opened=excluded.circuit_breaker_opened, high_risk=excluded.high_risk,
                rule_fired=excluded.rule_fired, latency_json=excluded.latency_json,
                cost_rupees=excluded.cost_rupees, identity_route_verified=excluded.identity_route_verified,
                evidence_json=excluded.evidence_json, explanation=excluded.explanation,
                traces_json=excluded.traces_json""",
                self._case_payload(case))
            if case.pending_request_id and case.authorized_contact_id and case.pending_nonce and case.confirmation_deadline:
                contact = db.execute("SELECT user_id FROM identity WHERE contact_id=? AND channel=?",
                                     (case.authorized_contact_id, case.channel)).fetchone()
                db.execute("""INSERT INTO confirmation_request(request_id,case_id,claimed_contact_id,
                    expected_verifier_id,nonce,expires_at,consumed_at) VALUES(?,?,?,?,?,?,NULL)
                    ON CONFLICT(request_id) DO NOTHING""",
                    (case.pending_request_id, case.case_id, case.claimed_contact_id,
                     contact["user_id"] if contact else "", case.pending_nonce, case.confirmation_deadline.isoformat()))
            elif case.pending_request_id is None:
                db.execute("UPDATE confirmation_request SET consumed_at=COALESCE(consumed_at,?) WHERE case_id=?",
                           (datetime.now(timezone.utc).isoformat(), case.case_id))

    def load_cases(self) -> list[Case]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM case_record ORDER BY created_at DESC").fetchall()
            out = []
            for r in rows:
                data = json.loads(r["signals_json"])
                out.append(Case(
                    case_id=r["case_id"], channel=r["channel"], reporter_id=r["reporter_id"],
                    redacted_text=r["redacted_text"], signals=RiskSignals(**data),
                    outcome=Outcome(r["outcome"]), reasons=json.loads(r["reasons_json"]),
                    created_at=datetime.fromisoformat(r["created_at"]),
                    claimed_contact_id=r["claimed_contact_id"], pending_request_id=r["pending_request_id"],
                    authorized_contact_id=r["authorized_contact_id"],
                    confirmation_deadline=_dt(r["confirmation_deadline"]),
                    circuit_breaker_opened=bool(r["circuit_breaker_opened"]), history=json.loads(r["history_json"]),
                    high_risk=bool(r["high_risk"]), rule_fired=r["rule_fired"],
                    latency_ms=json.loads(r["latency_json"]), cost_rupees=float(r["cost_rupees"]),
                    pending_nonce=r["pending_nonce"], identity_route_verified=bool(r["identity_route_verified"]),
                    evidence=json.loads(r["evidence_json"]), explanation=r["explanation"],
                    model_traces=json.loads(r["traces_json"]),
                ))
            return out

    def save_audit(self, entry: AuditEntry, content_digest: str = "") -> None:
        with self._connect() as db:
            db.execute("INSERT INTO audit(at,case_id,event,detail_json,content_digest) VALUES(?,?,?,?,?)",
                       (entry.at.isoformat(), entry.case_id, entry.event,
                        json.dumps(entry.detail, ensure_ascii=False), content_digest))

    def load_audit(self) -> list[AuditEntry]:
        with self._connect() as db:
            return [AuditEntry(datetime.fromisoformat(r["at"]), r["case_id"], r["event"], json.loads(r["detail_json"]))
                    for r in db.execute("SELECT at,case_id,event,detail_json FROM audit ORDER BY id")]

    def purge_expired(self, before: str) -> int:
        with self._connect() as db:
            rows = db.execute("SELECT case_id FROM case_record WHERE created_at < ?", (before,)).fetchall()
            ids = [r["case_id"] for r in rows]
            db.executemany("DELETE FROM case_record WHERE case_id=?", [(i,) for i in ids])
            db.execute("DELETE FROM audit WHERE at < ?", (before,))
            return len(ids)

    def save_consent(self) -> None:
        with self._connect() as db:
            db.execute("UPDATE family SET consented_at=? WHERE id=1", (datetime.now(timezone.utc).isoformat(),))

    def delete_family_data(self) -> None:
        with self._connect() as db:
            for table in ("confirmation_request", "invite", "case_record", "audit", "identity", "contact", "family", "parent_identity"):
                db.execute(f"DELETE FROM {table}")
