# VendaCertaAI — readiness notes

## Included in this release

- Atomic, fsync-backed JSON writes with rotating local recovery copies under `generated/backups/`; concurrent writes are serialized.
- Tenant-scoped order reads/writes, server-side catalog/price resolution, bounded input validation, order idempotency, and no client-supplied price authority.
- Real customer profile persistence, server-side catalog used by the AI seller, and HTML escaping in the rendered catalog.
- Mercado Pago plan checkout records, payment history, amount/currency validation, idempotent activation, audit events, and `init_point` preference over `sandbox_init_point`.
- Webhook HMAC verification when `MERCADO_PAGO_WEBHOOK_SECRET` is configured, timestamp replay protection, and safe no-secret compatibility for environments where the MP secret is not configured.
- Secure session cookie attributes, expiring sessions, origin checks for browser state changes, request size limits, and removal of the client-side ADM password.
- Server-backed one-time access-code generation/redemption behind an `ADMIN_PASSWORD` environment secret.

## Explicit beta/blocker status

- Render free filesystem is not durable storage and is not a production backup target. A managed PostgreSQL/object-storage migration and an off-site encrypted backup schedule are still required before production guarantees can be made.
- `MERCADO_PAGO_ACCESS_TOKEN` must be a production token for real sales. No real payment was initiated during this readiness pass. Configure `MERCADO_PAGO_WEBHOOK_SECRET` in Render to enable mandatory signature validation.
- Real marketplace split/OAuth is not active. It requires the final seller onboarding/legal/commercial decisions and Mercado Pago OAuth application credentials/redirect configuration; the UI continues to label the 5% amount as an estimate.
- Account recovery, email verification, LGPD export/deletion workflows, fiscal issuance, WhatsApp/Instagram channels, and advanced autonomous agents remain unimplemented modules.
- The seller AI is real only when `GEMINI_API_KEY` is configured; it must remain clearly unavailable rather than being represented by a fake fallback.
