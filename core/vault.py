"""Trusted-contact vault. Stores only guardian-approved trusted contacts.

No location, routines, bank details or family profiling are stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ChannelIdentity:
    channel: str
    user_id: str


@dataclass(frozen=True)
class TrustedContact:
    contact_id: str
    name: str
    relationship: str
    phone: str
    identities: tuple[ChannelIdentity, ...]
    approved_by_guardian: bool
    is_guardian: bool = False


@dataclass(frozen=True)
class FamilySettings:
    parent: tuple[ChannelIdentity, ...]
    parent_name: str
    language: str  # "en" | "te" | "hi"
    guardian_contact_id: str
    recorded_message_ref: Optional[str] = None
    escalation_rule: str = "high_risk"


class TrustedContactVault:
    def __init__(self, settings: FamilySettings) -> None:
        self.settings = settings
        self._contacts: dict[str, TrustedContact] = {}

    def add_contact(self, contact: TrustedContact) -> None:
        if not contact.approved_by_guardian:
            raise PermissionError("only guardian-approved contacts can be stored")
        self._contacts[contact.contact_id] = contact

    def get(self, contact_id: str) -> Optional[TrustedContact]:
        return self._contacts.get(contact_id)

    @property
    def guardian(self) -> Optional[TrustedContact]:
        return self._contacts.get(self.settings.guardian_contact_id)

    def find_by_name(self, name: Optional[str]) -> Optional[TrustedContact]:
        if not name:
            return None
        key = name.strip().casefold()
        for c in self._contacts.values():
            if c.name.casefold() == key:
                return c
        return None

    def find_by_identity(self, channel: str, user_id: Optional[str]) -> Optional[TrustedContact]:
        if not user_id:
            return None
        for contact in self._contacts.values():
            if self.identity_on(contact.contact_id, channel) == user_id:
                return contact
        return None

    def identity_on(self, contact_id: str, channel: str) -> Optional[str]:
        contact = self.get(contact_id)
        if contact is None:
            return None
        for ident in contact.identities:
            if ident.channel == channel:
                return ident.user_id
        return None

    def is_identity(self, contact_id: Optional[str], channel: str, user_id: str) -> bool:
        if contact_id is None:
            return False
        return self.identity_on(contact_id, channel) == user_id

    def is_parent(self, channel: str, user_id: str) -> bool:
        return ChannelIdentity(channel, user_id) in self.settings.parent

    def parent_id_on(self, channel: str) -> Optional[str]:
        for ident in self.settings.parent:
            if ident.channel == channel:
                return ident.user_id
        return None
