
# Kavach

Kavach gives families a verified pause before panic becomes payment.

Kavach is a working prototype with a Telegram webhook, a Telegram Mini App,
SQLite persistence, media preprocessing, provider adapters, and a
channel-independent verification core. The core makes every safety decision;
models can extract signals and explain evidence, but cannot choose outcomes.
Use synthetic data for the prototype. Telegram receives forwarded originals,
even though Kavach masks sensitive values before provider calls.

Provider calls are enabled by environment configuration. Without Nebius model
credentials, Kavach uses deterministic demo rules. Live voice and video
transcription requires an ASR endpoint. See `deploy/README.md` before deploying.

## Layout

```
channels/
  base.py       MessagingChannel protocol + typed dataclasses (the only thing core imports)
  telegram.py   Telegram parsing and formatting; network via an injected HttpClient
  fake.py       In-memory channel that records replies, alerts, confirmations, Circuit-Breakers
core/
  models.py     Outcome (VERIFIED / PAUSE / LIKELY_SCAM), RiskSignals, Case
  redaction.py  Local masking of OTPs, account/card numbers, UPI IDs, phones, passwords
  policy.py     Deterministic rules: the only place outcomes and actions are decided
  vault.py      Guardian-approved trusted contacts only
  audit.py      Append-only, redacted audit trail
  templates.py  Fixed wording for safety-critical messages (te/hi drafts need native review)
  pipeline.py   KavachPipeline: accepts any MessagingChannel and any SignalExtractor
tests/
  test_channels.py           adapter contract, Telegram fixtures, core-isolation check
  test_verification_flow.py  the safety-critical flow on FakeChannel
app/main.py                  authenticated API, webhook, media path, expiry sweeper
miniapp/                      check, family setup, history and Circuit-Breaker UI
store/sqlite.py              persisted contacts, cases, requests and audit trail
models/                       demo extractor and optional Nebius model clients
evidence/                     official-source lookup and dated demo cache
bench/                        synthetic datasets, runner and Markdown report
```

## Run locally

```bash
python3 -m pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000` for the browser demo. It uses synthetic contacts
and local session auth. The demo page displays which providers are connected.
Images require Pillow and Tesseract; audio and video require ASR settings, and
video additionally requires ffmpeg.

## Run tests

```bash
python3 -m pytest -v
```

The tests use fake channels and provider-free fixtures. They should not make
Telegram, Nebius, or Tavily calls, even when a developer `.env` has credentials.

## Run the synthetic benchmark

```bash
python3 -m bench.run --split both --output /tmp/kavach-benchmark.json
python3 -m bench.report /tmp/kavach-benchmark.json --output /tmp/kavach-benchmark.md
```

The default run uses generated synthetic signals and simulated verifier
answers. It does not measure live evidence coverage, provider latency, cost, or
real-world scam detection. Add `--live-nano` to the runner only when `NEBIUS_API_KEY`
and `MODEL_NANO` are configured; this uses provider credits.

## Safety properties the tests enforce

| Property | Test |
| --- | --- |
| Same pipeline runs on FakeChannel and on the Telegram adapter | `test_pipeline_runs_on_fake_channel`, `test_pipeline_runs_unchanged_on_telegram_adapter` |
| A stranger, the parent, or a different trusted contact cannot confirm an identity | `test_untrusted_responder_cannot_confirm_identity` |
| No answer (or a late answer) leaves the case in PAUSE and escalates | `test_no_response_keeps_case_paused_and_escalates`, `test_confirmation_arriving_after_deadline_without_sweep_is_ignored` |
| Only the asked trusted contact's confirmation produces VERIFIED | `test_trusted_confirmation_verifies` |
| High-risk policy result opens the Circuit-Breaker with a human recording and a saved number | `test_high_risk_opens_circuit_breaker` |
| OTPs, account, card, UPI and password values never reach the extractor, alerts, replies or audit | `test_sensitive_values_never_leave_the_core` |
| Core modules never import a concrete adapter | `test_core_does_not_import_concrete_channels` |

## Adding a WhatsAppChannel later

The core depends only on `channels.base.MessagingChannel`. A WhatsApp adapter is a
new file, `channels/whatsapp.py`, and nothing in `core/` changes:

1. Implement the six members of `MessagingChannel`:
   `parse_inbound`, `parse_confirmation`, `send_reply`,
   `send_confirmation_request`, `send_alert`, `open_circuit_breaker`.
2. Keep every WhatsApp payload shape, button format and API URL inside
   `channels/whatsapp.py`. Inject its HTTP client and credentials, as
   `TelegramChannel` does.
3. Map confirmations to `ConfirmationResponse(channel="whatsapp", request_id, responder_id, confirmed)`.
   Authorization stays in the core: the vault checks that `responder_id` is the
   asked contact's saved WhatsApp identity.
4. Register WhatsApp identities for parent and trusted contacts in the vault:
   `ChannelIdentity("whatsapp", "<id>")`.
5. Construct the pipeline with the new adapter:
   `KavachPipeline(channel=WhatsAppChannel(...), extractor=..., vault=..., audit=...)`.
6. Reuse the tests: parametrize `tests/test_verification_flow.py` over the new
   adapter's fixture payloads. The isolation test fails if `core/` ever imports it.

Policy, vault, pipeline and audit code stay unchanged.

## Prototype boundaries

- Telegram is not end-to-end private; Telegram's servers see raw messages. The
  prototype uses synthetic data only.
- Redaction runs in the Kavach service before any model, Tavily or outbound call.
  Names are kept because verification needs the claimed name.
- Late confirmations are ignored by design: once a case times out, a new check is needed.
- The Mini App renders the one-tap call; Telegram buttons cannot dial numbers, so
  URLs carry only the case id.
- Telugu and Hindi template strings are drafts pending native-speaker review.
