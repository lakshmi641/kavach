# Kavach credentials and setup checklist

Use this guide to finish the local configuration before deploying Kavach. The `.env` file contains private credentials; keep it on your computer and do not paste its contents into chat or commit it to Git.

## What is already configured

Checked in the local `.env` on 2026-10-14. This is only a status list; secret values are not shown.

- Telegram bot token
- Telegram bot username
- Telegram webhook secret
- Tavily API key
- `ASR_MODEL`
- `SESSION_SECRET` is present, but still has the example demo value. Replace it before production.
- `KAVACH_ENV=demo`

Do not fetch or replace the configured Telegram and Tavily credentials unless you want to rotate them. Never replace the existing `.env` with `.env.example`; add only the missing settings below.

## Step 1: Configure Nebius models

The app needs a Nebius API key and three model IDs for live model checks.

1. Sign in to [Nebius Token Factory](https://tokenfactory.nebius.com/).
2. Create an API key in the Token Factory dashboard. See the [Nebius API key guide](https://dev.nebius.com/cookbook/opencode-nebius-token-factory).
3. Choose model IDs available to your account from the [Token Factory model catalog](https://tokenfactory.nebius.com/models). Copy the IDs exactly as shown.
4. Add these settings to your existing `.env`:

```dotenv
NEBIUS_API_KEY=
MODEL_NANO=
MODEL_SUPER=
MODEL_ULTRA=
```

Keep the existing `NEBIUS_BASE_URL` value unless your provider account requires a different endpoint. The default is `https://api.tokenfactory.nebius.com/v1`, which the app uses for chat completions. See [Nebius API examples](https://docs.tokenfactory.nebius.com/api-reference/examples/batches).

## Step 2: Configure speech transcription

This is needed for audio and video transcription. It is not needed for text-only or image-only use. The current production settings validator requires these credentials even if you do not plan to use audio or video.

1. Create an API key at [OpenAI API keys](https://platform.openai.com/api-keys).
2. The app uses the OpenAI-compatible `/audio/transcriptions` endpoint documented in the [OpenAI audio API reference](https://platform.openai.com/docs/api-reference/audio/json-object?lang=csharp).
3. Add the following to `.env`:

```dotenv
ASR_BASE_URL=https://api.openai.com/v1
ASR_API_KEY=
ASR_MODEL=whisper-1
```

`ASR_MODEL` is already set locally; keep its current value if it is `whisper-1`. Audio/video content is sent to this provider for transcription.

## Step 3: Get the parent Telegram account ID

This is a numeric Telegram account ID, not the bot username or phone number.

1. Open your bot in Telegram and send `/start`.
2. Before registering the webhook, use Telegram's `getUpdates` Bot API method to read the update and copy `message.from.id`. Follow the [Telegram Bot API documentation](https://core.telegram.org/bots/api). `getUpdates` is unavailable while a webhook is set.
3. Add the numeric ID to `.env`:

```dotenv
PARENT_TELEGRAM_ID=
```

Keep your bot token private while retrieving updates; do not put a token-bearing API URL in a document or chat.

## Step 4: Generate the production-only secrets

The existing webhook secret is already set. Keep it unless you are intentionally rotating it. Generate independent values for `AUDIT_HMAC_KEY` and `SESSION_SECRET` in a local terminal:

```bash
python3 - <<'PY'
import secrets
print("AUDIT_HMAC_KEY=" + secrets.token_urlsafe(32))
print("SESSION_SECRET=" + secrets.token_urlsafe(32))
PY
```

Copy each output into the matching `.env` entry. Do not reuse either generated value elsewhere or share the output. Production requires an audit key of at least 16 bytes and a session secret of at least 32 bytes.

## Step 5: Deploy and set the public URLs

Do this after you have deployed the app to a host with HTTPS and persistent storage for its SQLite database. The repository's [deployment guide](../deploy/README.md) has the deployment sequence.

1. Set `MINI_APP_URL` to the public HTTPS address where the Mini App is served.
2. Set `PUBLIC_BASE_URL` to the app's public HTTPS origin. The webhook registration script reads this from its process environment.
3. Configure the Mini App URL through [Telegram Mini Apps / BotFather](https://core.telegram.org/bots/webapps).
4. Set `KAVACH_ENV=production` only after the production settings below are filled in.
5. Register the webhook once by running `python deploy/register_webhook.py` with `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, and `PUBLIC_BASE_URL` available in that process environment.

Add these settings to `.env` when you have the HTTPS deployment address:

```dotenv
MINI_APP_URL=
PUBLIC_BASE_URL=
```

The Mini App and webhook should use the same deployed Kavach origin. Use a persistent disk/volume for the SQLite database; otherwise, app data can be lost when the host restarts or replaces the container.

## Production readiness checklist

Before changing `KAVACH_ENV` to `production`, ensure all of these are set:

- Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_BOT_USERNAME`, `PARENT_TELEGRAM_ID`
- Public app: HTTPS `MINI_APP_URL`, HTTPS `PUBLIC_BASE_URL`
- Nebius: `NEBIUS_API_KEY`, `MODEL_NANO`, `MODEL_SUPER`, `MODEL_ULTRA`
- Search: `TAVILY_API_KEY` (already configured locally)
- Transcription: HTTPS `ASR_BASE_URL`, `ASR_API_KEY`, `ASR_MODEL`
- App secrets: production `AUDIT_HMAC_KEY` and `SESSION_SECRET`
- Hosting: persistent database storage

The required provider values are defined in [`app/settings.py`](../app/settings.py); the webhook setup is in [`deploy/register_webhook.py`](../deploy/register_webhook.py).

## When you have filled it in

Keep all secret values private. Tell me which settings you added (for example, “Nebius key and the three model IDs are in `.env`”), without sending their values. I can then check the configuration safely, continue with the next item, and run the project checks.
