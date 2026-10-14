"""Append-only audit trail. Every detail is redacted before it is stored."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
from typing import Callable, Iterable

from core.redaction import redact


@dataclass(frozen=True)
class AuditEntry:
    at: datetime
    case_id: str
    event: str
    detail: dict[str, str]


class AuditLog:
    def __init__(
        self,
        clock: Callable[[], datetime],
        *,
        hmac_key: bytes | None = None,
        on_record: Callable[[AuditEntry, str], None] | None = None,
        initial: Iterable[AuditEntry] = (),
    ) -> None:
        self._clock = clock
        self._hmac_key = hmac_key
        self._on_record = on_record
        self._entries: list[AuditEntry] = list(initial)

    def record(self, case_id: str, event: str, *, content: str | None = None, **detail: object) -> AuditEntry:
        safe = {k: redact(str(v)).text for k, v in detail.items()}
        entry = AuditEntry(self._clock(), case_id, event, safe)
        self._entries.append(entry)
        digest = ""
        if content is not None and self._hmac_key:
            digest = hmac.new(self._hmac_key, content.encode("utf-8"), hashlib.sha256).hexdigest()
        if self._on_record:
            self._on_record(entry, digest)
        return entry

    @property
    def entries(self) -> tuple[AuditEntry, ...]:
        return tuple(self._entries)

    def events(self, case_id: str | None = None) -> list[str]:
        return [e.event for e in self._entries if case_id is None or e.case_id == case_id]
