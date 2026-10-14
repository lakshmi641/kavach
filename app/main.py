"""FastAPI entry point for the Telegram webhook and Mini App APIs."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from adapters.http import TelegramHttpClient
from app.security import session_cookie, validate_init_data, validate_session
from app.settings import Settings
from channels.base import OutboundReply
from channels.fake import FakeChannel
from channels.telegram import TelegramChannel
from core import templates
from core.audit import AuditLog
from core.explain import Evidence, Explanation
from core.models import Case, Outcome, RiskSignals
from core.pipeline import KavachPipeline, UnknownReporter, UnknownRequest
from core.redaction import redact
from core.vault import ChannelIdentity, FamilySettings, TrustedContact, TrustedContactVault
from evidence.tavily import TavilyEvidence, build_sanitized_claim
from evidence.cache import cached_for
from models.nemotron import DemoExtractor, NebiusModels, safe_reply
from models.media import MediaProcessor, MediaUnavailable
from store.sqlite import SQLiteStore


settings = Settings()
settings.validate_live()
store = SQLiteStore(settings.database_path)
if store.load_family() is None:
    parent_id = "demo-parent" if settings.environment == "demo" else settings.parent_telegram_id
    store.save_family(FamilySettings(
        parent=(ChannelIdentity("telegram", parent_id),) if parent_id else (),
        parent_name="Amma" if settings.environment == "demo" else "Parent",
        language="en", guardian_contact_id="ravi" if settings.environment == "demo" else "",
        recorded_message_ref=None,
    ))
if settings.environment == "production" and not store.load_family().parent:
    store.set_parent((ChannelIdentity("telegram", settings.parent_telegram_id),))
if settings.environment == "demo" and store.load_family() and not store.load_family().parent:
    store.set_parent((ChannelIdentity("telegram", "demo-parent"),))
if settings.environment == "demo" and not store.load_contacts():
    store.save_contact(TrustedContact(
        "ravi", "Ravi", "son", "+91 90000 11111",
        (ChannelIdentity("telegram", "demo-ravi"),), True, True,
    ))
    store.save_contact(TrustedContact(
        "sita", "Sita", "daughter", "+91 90000 22222",
        (ChannelIdentity("telegram", "demo-sita"),), True, False,
    ))
    store.set_parent((ChannelIdentity("telegram", "demo-parent"),))


def build_vault() -> TrustedContactVault:
    family = store.load_family() or FamilySettings((), "Parent", "en", "")
    vault = TrustedContactVault(family)
    for contact in store.load_contacts():
        vault.add_contact(contact)
    return vault


vault = build_vault()
if settings.telegram_enabled:
    channel = TelegramChannel(TelegramHttpClient(), settings.telegram_bot_token,
                              mini_app_url=settings.mini_app_url or "https://localhost")
else:
    channel = FakeChannel("telegram")
extractor = NebiusModels(settings) if settings.live_models_enabled else DemoExtractor()
tavily = TavilyEvidence(settings.tavily_api_key) if settings.live_tavily_enabled else None
audit = AuditLog(
    lambda: datetime.now(timezone.utc),
    hmac_key=settings.audit_hmac_key.encode() if settings.audit_hmac_key else None,
    on_record=lambda entry, digest: store.save_audit(entry, digest),
    initial=[entry for entry in store.load_audit()
             if entry.at >= datetime.now(timezone.utc) - timedelta(days=30)],
)
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(expire_sweeper())
    app.state.tasks.add(task)
    task.add_done_callback(app.state.tasks.discard)
    try:
        yield
    finally:
        for active in tuple(app.state.tasks):
            active.cancel()
        await asyncio.gather(*app.state.tasks, return_exceptions=True)


app = FastAPI(title="Kavach", docs_url=None, redoc_url=None, lifespan=lifespan)
app.state.store = store
app.state.vault = vault
app.state.channel = channel
app.state.extractor = extractor
app.state.models = extractor if isinstance(extractor, NebiusModels) else None
app.state.tavily = tavily
app.state.media_processor = MediaProcessor(settings)
app.state.audit = audit
app.state.tasks = set()
app.state.check_lock = asyncio.Lock()
app.state.check_details = {}
pipeline = KavachPipeline(
    channel=channel, extractor=extractor, vault=vault, audit=audit, store=store,
    reply_builder=lambda case, text, language: render_reply(case, text, language),
)
app.state.pipeline = pipeline


async def render_reply(case: Case, fixed_template: str, language: str) -> str:
    """Enrich a fixed safety line; providers never choose the case outcome."""
    evidence: list[Evidence] = []
    evidence_latency = 0.0
    explanation = Explanation("Identity verification is still required.")
    model = app.state.models
    evidence_source = app.state.tavily
    if evidence_source and any(case.signals.active()):
        started = time.perf_counter()
        try:
            evidence = await evidence_source.search(build_sanitized_claim(case.signals))
        except Exception:
            evidence = []
        evidence_latency = (time.perf_counter() - started) * 1000
    if not evidence:
        evidence = cached_for(case.signals)
    if model and case.outcome is Outcome.PAUSE:
        try:
            explanation = await model.explain(case.redacted_text, case.signals, evidence)
        except Exception:
            explanation = Explanation("Identity verification is still required.")
    case.evidence = [e.__dict__ for e in evidence]
    case.explanation = explanation.summary
    result = f"{fixed_template} {' '.join(case.reasons)}"
    if model and model.settings.model_super:
        try:
            result = await model.reply(fixed_template, explanation, language)
        except Exception:
            pass
    details = app.state.check_details.setdefault(case.case_id, {})
    prior_traces = list(details.get("traces", case.model_traces))
    details["explanation"] = explanation.summary
    details["evidence"] = case.evidence
    current_traces = [t.__dict__ for t in getattr(extractor, "traces", [])]
    if model:
        current_traces = [t.__dict__ for t in model.traces]
    details["traces"] = prior_traces + current_traces
    if evidence_source:
        details["traces"].append({"model": "Tavily Search + Extract", "step": "official_evidence",
                                  "latency_ms": evidence_latency, "cost_rupees": None})
    case.model_traces = list(details["traces"])
    case.latency_ms = {t["step"]: float(t["latency_ms"]) for t in details["traces"]}
    case.cost_rupees = sum(float(t.get("cost_rupees") or 0) for t in details["traces"])
    if evidence:
        first = evidence[0]
        result += f" Official advisory dated {first.published_date}: {first.title} {first.url}"
    return safe_reply(result)


class SetupInput(BaseModel):
    parent_name: str = Field(min_length=1, max_length=80)
    language: Literal["en", "te", "hi"]
    guardian_contact_id: str = Field(min_length=1, max_length=80)
    escalation_rule: Literal["high_risk", "all_pause", "all_checks"] = "high_risk"
    consent: bool


class ContactInput(BaseModel):
    contact_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=80)
    relationship: str = Field(min_length=1, max_length=80)
    phone: str = Field(default="", max_length=32)
    phone_self_shared: bool = False
    is_guardian: bool = False


class CheckInput(BaseModel):
    text: str = Field(min_length=1, max_length=12_000)
    kind: Literal["text", "image", "audio", "video"] = "text"


class DemoConfirmation(BaseModel):
    confirmed: bool


def actor_for(request: Request) -> str:
    token = request.cookies.get("kavach_session", "")
    actor = validate_session(token, settings.session_secret)
    if actor:
        return actor
    if settings.environment == "demo" and request.headers.get("x-demo-mode") == "local":
        return "demo-parent"
    raise HTTPException(401, "Open this page from the Kavach Telegram Mini App to continue.")


def require_parent(request: Request) -> str:
    actor = actor_for(request)
    current = app.state.vault.parent_id_on("telegram")
    if not current or current != actor:
        raise HTTPException(403, "This Telegram account is not the protected parent for this family.")
    return actor


def reload_vault() -> None:
    app.state.vault = build_vault()
    app.state.pipeline.vault = app.state.vault


def case_json(case: Case) -> dict:
    details = app.state.check_details.get(case.case_id, {})
    return {
        "case_id": case.case_id, "outcome": case.outcome.value,
        "language": app.state.vault.settings.language,
        "circuit_breaker_text": templates.parent_text(app.state.vault.settings.language, "circuit_breaker"),
        "identity_route_verified": case.identity_route_verified,
        "redacted_text": case.redacted_text, "signals": {k: getattr(case.signals, k) for k in case.signals.__dataclass_fields__},
        "reasons": case.reasons, "rule_fired": case.rule_fired,
        "created_at": case.created_at.isoformat(), "pending": bool(case.pending_request_id),
        "history": case.history, "latency_ms": case.latency_ms, "cost_rupees": case.cost_rupees,
        "cost_reporting_configured": bool(settings.model_input_cost_per_million_inr or settings.model_output_cost_per_million_inr),
        "explanation": case.explanation, "evidence": case.evidence,
        "traces": case.model_traces or details.get("traces", []), "circuit_breaker_opened": case.circuit_breaker_opened,
    }


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "mode": settings.environment, "telegram": settings.telegram_enabled,
            "models": settings.live_models_enabled, "evidence": settings.live_tavily_enabled}


@app.post("/api/session")
async def create_session(request: Request):
    if not settings.telegram_bot_token:
        raise HTTPException(503, "Telegram Mini App authentication is not configured.")
    body = await request.json()
    try:
        init = validate_init_data(str(body.get("initData", "")), settings.telegram_bot_token)
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    actor_id = init["user_id"]
    parent_id = app.state.vault.parent_id_on("telegram")
    if not parent_id:
        if settings.environment != "demo":
            raise HTTPException(403, "Set PARENT_TELEGRAM_ID before enabling the production Mini App.")
        store.set_parent((ChannelIdentity("telegram", actor_id),))
        reload_vault()
    elif parent_id != actor_id:
        raise HTTPException(403, "This Telegram account is not the protected parent.")
    response = JSONResponse({"ok": True, "actor_id": actor_id})
    response.set_cookie("kavach_session", session_cookie(actor_id, settings.session_secret),
                        httponly=True, secure=bool(settings.mini_app_url.startswith("https://")),
                        samesite="strict", max_age=3600, path="/")
    return response


@app.post("/api/demo/session")
async def demo_session():
    if settings.environment != "demo":
        raise HTTPException(404, "Not found")
    if not app.state.vault.parent_id_on("telegram"):
        store.set_parent((ChannelIdentity("telegram", "demo-parent"),))
        reload_vault()
    response = JSONResponse({"ok": True, "demo": True})
    response.set_cookie("kavach_session", session_cookie("demo-parent", settings.session_secret),
                        httponly=True, samesite="strict", max_age=3600, path="/")
    return response


@app.get("/api/config")
async def get_config(request: Request):
    actor = actor_for(request)
    if actor != app.state.vault.parent_id_on("telegram"):
        raise HTTPException(403, "Not this family's parent account.")
    family = store.load_family()
    contacts = store.load_contacts()
    return {
        "mode": settings.environment, "demo": settings.environment == "demo",
        "providers": {"telegram": settings.telegram_enabled, "models": settings.live_models_enabled,
                      "tavily": settings.live_tavily_enabled},
        "family": {"parent_name": family.parent_name, "language": family.language,
                   "guardian_contact_id": family.guardian_contact_id,
                   "escalation_rule": family.escalation_rule, "consented": store.family_consented(),
                   "recording_available": bool(family.recorded_message_ref)},
        "contacts": [{"contact_id": c.contact_id, "name": c.name, "relationship": c.relationship,
                      "is_guardian": c.is_guardian, "bound": bool(c.identities),
                      "phone_available": bool(c.phone)} for c in contacts],
        "case_count": len(pipeline.cases),
        "notice": "Demo-only synthetic data" if settings.environment == "demo" else "Telegram servers can see forwarded messages.",
    }


@app.put("/api/setup")
async def save_setup(payload: SetupInput, request: Request):
    require_parent(request)
    if payload.consent is not True:
        raise HTTPException(400, "Parent consent is required before using the family vault.")
    old = store.load_family() or FamilySettings((), payload.parent_name, payload.language,
                                               payload.guardian_contact_id)
    store.save_family(FamilySettings(old.parent, payload.parent_name, payload.language,
                                     payload.guardian_contact_id, old.recorded_message_ref,
                                     payload.escalation_rule))
    store.save_consent()
    reload_vault()
    return {"ok": True}


@app.post("/api/contacts")
async def add_contact(payload: ContactInput, request: Request):
    require_parent(request)
    if payload.phone and not payload.phone_self_shared:
        raise HTTPException(400, "Store a callback number only when the contact shared it themselves.")
    contact = TrustedContact(payload.contact_id, payload.name.strip(), payload.relationship.strip(),
                             payload.phone.strip() if payload.phone_self_shared else "", (), True,
                             payload.is_guardian)
    store.save_contact(contact)
    reload_vault()
    return {"ok": True, "contact_id": contact.contact_id}


@app.delete("/api/contacts/{contact_id}")
async def delete_contact(contact_id: str, request: Request):
    require_parent(request)
    if contact_id == (store.load_family().guardian_contact_id if store.load_family() else ""):
        raise HTTPException(400, "Choose a new guardian before removing this contact.")
    store.remove_contact(contact_id)
    reload_vault()
    return {"ok": True}


@app.post("/api/contacts/{contact_id}/invite")
async def create_invite(contact_id: str, request: Request):
    require_parent(request)
    contact = app.state.vault.get(contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    token = secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    store.save_invite(token_hash, contact_id, expires.isoformat())
    instruction = f"Ask {contact.name} to open the Kavach bot and send /start {token}."
    invite_url = f"https://t.me/{settings.telegram_bot_username}?start={token}" if settings.telegram_bot_username else None
    return {"start_payload": token, "invite_url": invite_url, "expires_at": expires.isoformat(),
            "instruction": instruction}


@app.post("/api/consent")
async def consent(request: Request):
    require_parent(request)
    store.save_consent()
    return {"ok": True}


@app.post("/api/check")
async def check_text(payload: CheckInput, request: Request):
    actor_id = require_parent(request)
    if not store.family_consented():
        raise HTTPException(409, "Complete family setup and consent before checking a message.")
    text = payload.text
    # The demo file input can carry user-entered text only; media must be inspected locally first.
    return await run_check(local_inbound(actor_id, text, payload.kind))


async def run_check(raw: dict, kind_override: str | None = None):
    if kind_override:
        msg = raw.get("message", {})
        if kind_override == "image": msg["photo"] = [{"file_id": "not-forwarded"}]
        elif kind_override == "audio": msg["voice"] = {"file_id": "not-forwarded"}
        elif kind_override == "video": msg["video"] = {"file_id": "not-forwarded"}
    try:
        case = await handle_inbound_locked(raw)
    except UnknownReporter as exc:
        raise HTTPException(403, str(exc)) from exc
    store.save_case(case)
    return case_json(case)


def local_inbound(actor_id: str, text: str, kind: str = "text") -> dict:
    if isinstance(channel, FakeChannel):
        return {"sender_id": actor_id, "kind": kind, "text": text}
    message = {"from": {"id": actor_id}, "text": text}
    if kind == "image": message["photo"] = [{"file_id": "not-forwarded"}]
    elif kind == "audio": message["voice"] = {"file_id": "not-forwarded"}
    elif kind == "video": message["video"] = {"file_id": "not-forwarded"}
    return {"message": message}


async def handle_inbound_locked(raw: dict) -> Case:
    async with app.state.check_lock:
        traces = getattr(extractor, "traces", None)
        if isinstance(traces, list):
            traces.clear()
        return await pipeline.handle_inbound(raw)


async def handle_confirmation_locked(raw: dict) -> Case:
    async with app.state.check_lock:
        traces = getattr(extractor, "traces", None)
        if isinstance(traces, list):
            traces.clear()
        return await pipeline.handle_confirmation(raw)


@app.post("/api/check/upload")
async def check_upload(request: Request):
    actor_id = require_parent(request)
    if not store.family_consented():
        raise HTTPException(409, "Complete family setup and consent before checking a message.")
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith(("image/", "audio/", "video/")):
        raise HTTPException(415, "Upload an image, audio recording or video.")
    size = 0
    chunks = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > 20 * 1024 * 1024:
            raise HTTPException(413, "This prototype accepts media files up to 20 MB.")
        chunks.append(chunk)
    content = b"".join(chunks)
    kind = "image" if content_type.startswith("image/") else "video" if content_type.startswith("video/") else "audio"
    analyzed = False
    try:
        text = await app.state.media_processor.analyze(content, kind, content_type)
        note = "Media text was extracted, sensitive values were masked, and only the redacted text was analyzed."
        if not text.strip():
            raise MediaUnavailable("No readable message content was found in this upload.")
        analyzed = True
    except MediaUnavailable as exc:
        note = f"Media was not analyzed: {exc} Add a text summary before relying on this check."
        text = "[MEDIA CONTENT NOT AVAILABLE FOR ANALYSIS]"
    result = await run_check(local_inbound(actor_id, text, kind))
    result["media_note"] = note
    result["redacted_text"] = "[MEDIA NOT TRANSCRIBED]"
    if not analyzed:
        result["redacted_text"] = "[MEDIA NOT TRANSCRIBED]"
    # The raw bytes existed only in memory (video preprocessing also uses a cleaned-up temp directory).
    del content, chunks
    return result


@app.post("/api/demo/cases/{case_id}/confirmation")
async def demo_confirmation(case_id: str, payload: DemoConfirmation, request: Request):
    if settings.environment != "demo":
        raise HTTPException(404, "Not found")
    require_parent(request)
    case = pipeline.cases.get(case_id)
    if not case or not case.pending_request_id:
        raise HTTPException(404, "No open verifier request for this case.")
    responder_id = app.state.vault.identity_on(case.authorized_contact_id, case.channel)
    if not responder_id:
        raise HTTPException(409, "No verifier is bound in the demo family.")
    app.state.audit.record(case.case_id, "demo_simulated_verifier_response", confirmed=payload.confirmed)
    response = {"request_id": case.pending_request_id, "responder_id": responder_id,
                "confirmed": payload.confirmed, "nonce": case.pending_nonce}
    resolved = await handle_confirmation_locked(response)
    store.save_case(resolved)
    return case_json(resolved)


@app.get("/api/history")
async def history(request: Request):
    require_parent(request)
    cases = sorted(pipeline.cases.values(), key=lambda c: c.created_at, reverse=True)
    return {"cases": [case_json(c) for c in cases[:100]]}


@app.get("/api/cases/{case_id}")
async def case_detail(case_id: str, request: Request):
    require_parent(request)
    case = pipeline.cases.get(case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    return case_json(case)


@app.get("/api/cases/{case_id}/callback")
async def callback_link(case_id: str, request: Request):
    require_parent(request)
    case = pipeline.cases.get(case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    contact = app.state.vault.get(case.claimed_contact_id) if case.claimed_contact_id else None
    contact = contact or app.state.vault.guardian
    if not contact or not contact.phone:
        raise HTTPException(404, "No contact-shared callback number is available for this case.")
    digits = "".join(ch for ch in contact.phone if ch.isdigit())
    if len(digits) < 10 or len(digits) > 15:
        raise HTTPException(409, "The saved callback number is invalid.")
    phone = ("+" if contact.phone.strip().startswith("+") else "") + digits
    return {"tel": f"tel:{phone}"}


@app.post("/api/cases/{case_id}/refresh-evidence")
async def refresh_evidence(case_id: str, request: Request):
    require_parent(request)
    case = pipeline.cases.get(case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    if not app.state.tavily:
        evidence = cached_for(case.signals)
        case.evidence = [e.__dict__ for e in evidence]
        store.save_case(case)
        return {"evidence": case.evidence, "message": "Showing the cached official advisory." if evidence else "No applicable dated official advisory found."}
    try:
        evidence = await app.state.tavily.search(build_sanitized_claim(case.signals))
    except Exception as exc:
        raise HTTPException(502, "Official-source search is temporarily unavailable.") from exc
    case.evidence = [e.__dict__ for e in evidence]
    store.save_case(case)
    app.state.audit.record(case.case_id, "evidence_refreshed", result_count=len(evidence))
    return {"evidence": case.evidence, "message": "No applicable dated official advisory found." if not evidence else "Official advisory found."}


@app.get("/api/recording")
async def get_recording(request: Request):
    require_parent(request)
    file_id = app.state.vault.settings.recorded_message_ref
    if not file_id or not settings.telegram_enabled:
        raise HTTPException(404, "No approved recording is available.")
    try:
        data, content_type = await app.state.channel._http.download_file(settings.telegram_bot_token, file_id)
    except Exception as exc:
        raise HTTPException(502, "The approved recording could not be loaded from Telegram.") from exc
    return Response(data, media_type=content_type, headers={"Cache-Control": "private, no-store"})


@app.get("/api/audit")
async def get_audit(request: Request):
    require_parent(request)
    return {"entries": [{"at": e.at.isoformat(), "case_id": e.case_id, "event": e.event,
                         "detail": e.detail} for e in reversed(audit.entries[-200:])]}


@app.post("/api/recovery")
async def recovery(request: Request):
    require_parent(request)
    return {"steps": templates.recovery_checklist(app.state.vault.settings.language)}


@app.delete("/api/family-data")
async def delete_family_data(request: Request):
    require_parent(request)
    store.delete_family_data()
    pipeline.cases.clear()
    pipeline._by_request.clear()
    audit._entries.clear()
    return {"ok": True}


@app.post("/tg/webhook")
async def telegram_webhook(request: Request):
    if not settings.telegram_enabled:
        raise HTTPException(503, "Telegram bot credentials are not configured.")
    provided = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(provided, settings.telegram_webhook_secret):
        raise HTTPException(401, "Invalid Telegram webhook secret.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 2 * 1024 * 1024:
            raise HTTPException(413, "Telegram update is too large.")
    try:
        update = json.loads(body)
        if not isinstance(update, dict):
            raise ValueError("update must be an object")
    except Exception as exc:
        raise HTTPException(400, "Invalid JSON update.") from exc
    task = asyncio.create_task(process_update(update))
    app.state.tasks.add(task)
    task.add_done_callback(app.state.tasks.discard)
    return {"ok": True}


async def process_update(update: dict) -> None:
    if "callback_query" in update:
        try:
            case = await handle_confirmation_locked(update)
            store.save_case(case)
        except UnknownRequest:
            pass
        return
    message = update.get("message")
    sender = message.get("from") if isinstance(message, dict) else None
    if not isinstance(sender, dict) or "id" not in sender:
        return
    text = str(message.get("text") or message.get("caption") or "")
    file_info = message.get("voice") or message.get("audio")
    if text.strip() == "/approve_recording" and isinstance(file_info, dict) and file_info.get("file_id"):
        if app.state.vault.is_parent("telegram", str(sender["id"])):
            family = store.load_family()
            if family:
                store.save_family(replace(family, recorded_message_ref=str(file_info["file_id"])))
                reload_vault()
                await channel.send_reply(OutboundReply(str(sender["id"]), "recording", "Your voice message is now the approved family recording. Send /approve_recording again with another voice note to replace it.", family.language))
        return
    parts = text.split(maxsplit=1)
    if parts and parts[0] == "/start" and len(parts) == 2:
        digest = hashlib.sha256(parts[1].encode()).hexdigest()
        contact_id = store.consume_invite(digest, str(sender["id"]))
        if contact_id:
            reload_vault()
            await channel.send_reply(OutboundReply(str(sender["id"]), "binding", "This Telegram account is now bound as a trusted verifier.", "en"))
        return
    media = message.get("photo") or message.get("voice") or message.get("audio") or message.get("video") or message.get("video_note")
    if media and settings.telegram_enabled:
        kind = "image" if message.get("photo") else "audio" if message.get("voice") or message.get("audio") else "video"
        file_id = media[-1].get("file_id") if isinstance(media, list) and media else media.get("file_id") if isinstance(media, dict) else None
        if isinstance(file_id, str):
            try:
                content, content_type = await channel._http.download_file(settings.telegram_bot_token, file_id)
                transcript = await app.state.media_processor.analyze(content, kind, content_type)
                message["text"] = (text + "\n" + transcript).strip()
            except MediaUnavailable as exc:
                message["text"] = (text + "\n[MEDIA CONTENT NOT AVAILABLE FOR ANALYSIS] " + str(exc)).strip()
            except Exception:
                message["text"] = (text + "\n[MEDIA DOWNLOAD OR ANALYSIS FAILED; MANUAL VERIFICATION REQUIRED]").strip()
    try:
        case = await handle_inbound_locked(update)
        store.save_case(case)
    except UnknownReporter:
        return


async def expire_sweeper() -> None:
    while True:
        await asyncio.sleep(30)
        try:
            async with app.state.check_lock:
                expired = await pipeline.expire_pending()
            for case in expired:
                store.save_case(case)
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            store.purge_expired(cutoff.isoformat())
        except Exception:
            # A failed maintenance pass is observable through health/logging but doesn't stop the service.
            continue


@app.get("/")
async def index():
    return FileResponse(Path(__file__).parents[1] / "miniapp" / "index.html")


@app.get("/pause")
async def pause_page():
    return FileResponse(Path(__file__).parents[1] / "miniapp" / "index.html")


app.mount("/static", StaticFiles(directory=Path(__file__).parents[1] / "miniapp"), name="static")
