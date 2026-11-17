# PHASE 4 REQUIRED ENV

Configure values in the repository's `.env` file or the deployment environment. Never commit `.env` or paste secret values into logs or support reports. Goaloo's observed endpoints do not require an API key; its provider must still be selected and a controlled league universe configured.

| NAME | PURPOSE | REQUIRED / OPTIONAL | USED BY |
|---|---|---|---|
| `PROVIDER_NAME` or `MATCH_PROVIDER` | Select `goaloo` or `sportmonks` | Required for provider ingestion | Provider registry |
| `GOALOO_BASE_URL` | Goaloo schedule source | Optional; defaults to public endpoint | Goaloo adapter |
| `GOALOO_ODDS_BASE_URL` | Goaloo odds source | Optional; defaults to public endpoint | Goaloo adapter |
| `ENABLED_LEAGUES` | Comma-separated provider league IDs; required to bound Goaloo discovery | Required for Goaloo | Ingestion and scanner |
| `ENABLED_SEASONS` | Optional comma-separated season IDs | Optional | Goaloo discovery and historical ingestion |
| `SPORTMONKS_API_TOKEN` | Sportmonks API credential | Required when Sportmonks selected | Sportmonks adapter |
| `SPORTMONKS_BASE_URL` | Sportmonks API base URL | Optional; defaults to v3 football API | Sportmonks adapter |
| `PROVIDER_API_KEY` | Generic provider credential for adapters that use one | Optional; provider-specific | Provider registry |
| `PROVIDER_BASE_URL` | Generic provider base URL override | Optional | Provider registry |
| `PROVIDER_REQUEST_TIMEOUT_SECONDS` | Generic provider request timeout | Optional | Provider caller |
| `PROVIDER_REQUESTS_PER_MINUTE` | Provider rate limit | Optional | Provider caller |
| `PROVIDER_MAX_RETRIES` | Bounded transient retries | Optional | Provider caller |
| `SPORTMONKS_TIMEOUT_SECONDS` | Sportmonks timeout | Optional | Sportmonks adapter |
| `SPORTMONKS_MAX_RETRIES` | Sportmonks retry count | Optional | Sportmonks adapter |
| `DATABASE_URL` | PostgreSQL connection URL | Required for operational persistence | SQLAlchemy/API/jobs |
| `R2_ENDPOINT` | Cloudflare account S3 endpoint | Required for R2 | R2 object store |
| `R2_ACCESS_KEY_ID` | R2 access key ID | Required for R2 | R2 object store |
| `R2_SECRET_ACCESS_KEY` | R2 secret access key | Required for R2 | R2 object store |
| `R2_BUCKET` | Historical object bucket | Required for R2; defaults to `sports-analytics` | R2 object store |
| `R2_REGION` | S3-compatible region; Cloudflare uses `auto` | Optional | R2 object store |
| `AI_PROVIDER` | Select `groq` to enable Groq interpretation | Optional | AI provider factory |
| `GROQ_API_KEY` | Groq API credential | Required when Groq selected | Groq adapter |
| `GROQ_MODEL` | Explicit Groq model ID | Required when Groq selected | Groq adapter |
| `GROQ_BASE_URL` | Groq OpenAI-compatible API base URL | Optional; defaults to `https://api.groq.com/openai/v1` | Groq adapter |
| `AI_API_KEY` | Generic AI credential fallback | Optional; only used with a supported configured AI provider | AI provider factory |
| `TELEGRAM_BOT_TOKEN` | Telegram bot credential | Required to send Telegram notifications | Telegram notifier |
| `TELEGRAM_CHAT_ID` | Telegram destination | Required to send Telegram notifications | Telegram notifier |
| `HISTORICAL_START_DATE` | Earliest requested historical ingest date | Optional | Historical ingestion |
| `WORKER_INTERVAL_SECONDS` | Background worker interval | Optional | Worker |

`POSTGRES_*` names in this project configure storage thresholds (`POSTGRES_NORMAL_MB`, `POSTGRES_WARNING_MB`, `POSTGRES_HIGH_MB`, `POSTGRES_CRITICAL_MB`); database connectivity itself uses `DATABASE_URL`. No separate `CLOUDFLARE_*` variables are currently consumed: R2 uses the existing `R2_*` names above.

## Current connection audit

The application settings were inspected without printing `.env` contents. In the audited environment, provider selection/credentials, `DATABASE_URL`, the complete R2 credential set, Groq key/model, and Telegram credentials were unset. Configure them before attempting infrastructure integration tests or a persistent paper loop.
