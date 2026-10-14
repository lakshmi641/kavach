# Deploy Kavach

The container serves the FastAPI webhook and the Telegram Mini App from one HTTPS origin. Mount a persistent volume at `/data`; SQLite cases, verifier bindings, and the redacted audit trail live there.

1. Copy `.env.example` to `.env` and set the production credentials and IDs.
2. Build and deploy the Docker image to a host with an HTTPS endpoint and persistent storage.
3. Set `PUBLIC_BASE_URL` to that HTTPS origin and run `python deploy/register_webhook.py` once.
4. Configure the same Mini App URL with BotFather and have the protected parent open it from the Telegram account named by `PARENT_TELEGRAM_ID`.
5. Open **Family** and save consent, language, guardian, and escalation settings. Add each verifier and send their one-time `/start` invite.
6. Verify `/healthz`, a text check, a verifier rejection, and the Circuit-Breaker on two synthetic Telegram accounts before using the demo.

The repository includes the image and webhook configuration, but it does not contain Nebius cloud project identifiers or a provider-specific Serverless Endpoint manifest. Those need to come from the hackathon Nebius account. A mounted SQLite volume is required; if the chosen endpoint cannot mount one, deploy this same image to a Nebius VM with a persistent disk.
