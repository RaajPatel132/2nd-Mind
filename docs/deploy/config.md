# Configuration matrix

Every variable the application reads, what it means, the value it has in each place it runs and
where the production value comes from. `Settings` is the source of truth for the application's
own variables; a test (`tests/unit/test_config_matrix.py`) fails when a field is added there
without a row here, the same way `.env.example` is checked. Values that are secrets are never
written in this file.

**The three columns.** *Development* is a laptop's `make up`, from `.env` or the compose
defaults. *Rehearsal* is `make up-prodlike`: the production compose file and images, real
models, with the values it generates or takes from `.env`. *Production* is the EC2 host
(ADR-0035): `compose.prodlike.yaml` with the environment file the deploy script writes.

**Source** (of the production value):

- `env`: fixed in `compose.prodlike.yaml`, the same in the rehearsal and production.
- `ssm`: an SSM Parameter Store `String` under `/secondmind/prod/`, written by
  `make prod-secrets` from the gitignored `.env.prod`.
- `secret`: an SSM `SecureString` under the same path (the passwords behind the three database
  and Redis URLs are stored, and the URLs themselves are built by compose).

The names `make prod-secrets` writes are exactly the ones in `.env.prod.example`, and a test
(`tests/unit/test_deploy_matrix.py`) keeps that file and this one in step.

| Variable | Meaning | Development | Rehearsal | Production | Source |
|---|---|---|---|---|---|
| `ENV` | Which environment this is; turns the start-up guards on for production (there is no staging: ADR-0035). | development | production | production | env |
| `APP_VERSION` | Build label shown in /v1/meta and logs; the image tag (git SHA) in CI. | dev | git SHA | git SHA | env |
| `LOG_LEVEL` | Minimum log level. | INFO | INFO | INFO | env |
| `LOG_FORMAT` | json for machines, console for a terminal. | json | json | json | env |
| `LOG_INCLUDE_CONTENT` | Log message content (dev flag only; refused in production). | false | false | false | env |
| `RESOURCES_DIR` | Where config/ and prompts/ live (the image's /app). | backend/ | /app | /app | env |
| `DATABASE_URL` | Postgres URL for the app's non-owner role (carries the password). | compose default | built by compose from the generated passwords | built by compose from `APP_DB_PASSWORD` | secret |
| `DATABASE_POOL_SIZE` | Connections in the app pool. | 10 | 10 | 10 | env |
| `REDIS_URL` | Redis URL: queue, spend counters, kill switch, rate limits (may carry a password). | compose default | built by compose | built by compose from `REDIS_PASSWORD` | secret |
| `MODEL_PROVIDER_MODE` | auto falls back to the fake provider without keys; live refuses to start without them; fake never calls out. | auto | live | live | env |
| `ANTHROPIC_API_KEY` | Anthropic key. | in .env, optional | from .env | set | secret |
| `OPENAI_API_KEY` | OpenAI key (chat and embeddings). | in .env, optional | from .env | set | secret |
| `PROVIDER_MAX_RETRIES` | Retries per model call before the fallback. | 2 | 2 | 2 | env |
| `PROVIDER_RETRY_BASE_MS` | First retry delay. | 250 | 250 | 250 | env |
| `PROVIDER_RETRY_MAX_MS` | Longest retry delay. | 4000 | 4000 | 4000 | env |
| `BREAKER_FAILURE_THRESHOLD` | Failures in the window that open a provider's circuit. | 5 | 5 | 5 | env |
| `BREAKER_WINDOW_S` | Seconds the failure count covers. | 60 | 60 | 60 | env |
| `BREAKER_COOLDOWN_S` | Seconds an open circuit waits before a probe. | 30 | 30 | 30 | env |
| `FAKE_PROVIDER_TOKEN_DELAY_MS` | Pause between streamed tokens of the fake provider. | 15 | n/a | n/a | env |
| `TRACING_ENABLED` | Send traces to Langfuse. | true | false unless keys are in .env | true | ssm |
| `LANGFUSE_HOST` | Langfuse URL (self-hosted locally; Langfuse Cloud in production). | compose | Langfuse Cloud if keys are in .env | Langfuse Cloud URL | ssm |
| `LANGFUSE_PUBLIC_KEY` | Langfuse project public key. | compose | from .env | set | ssm |
| `LANGFUSE_SECRET_KEY` | Langfuse project secret key. | compose | from .env | set | secret |
| `LANGFUSE_UI_URL` | Where trace links point. | localhost | Langfuse Cloud if keys are in .env | Langfuse Cloud URL | ssm |
| `LANGFUSE_PROJECT_ID` | Langfuse project id, for trace links. | compose | from .env | set | ssm |
| `TRACE_INCLUDE_CONTENT` | Put message text in traces (metadata only by default). | false | false | false | env |
| `DEV_AUTH` | Dev sign-in and the /v1/dev helpers. In production it is code sign-in (needs `ACCESS_CODE`) and the helpers don't exist. | true | true (needs the access code) | true (needs the access code) | env |
| `DEV_USER_EMAIL` | The dev user when none is named. | dev@example.com | n/a | n/a | env |
| `SESSION_SECRET` | Signs the session cookie (32+ chars; the example value is refused off-laptop). | example | generated | random | secret |
| `SESSION_COOKIE_SECURE` | Mark the cookie Secure and send HSTS (required in production). | false | true | true | env |
| `MAX_MESSAGE_CHARS` | Longest message a turn accepts. | 8000 | 8000 | 8000 | env |
| `DEFAULT_TIMEZONE` | IANA timezone of a new workspace. | UTC | UTC | UTC | env |
| `POLICY_BULK_THRESHOLD` | Writes in one turn that need the person's OK. | 5 | 5 | 5 | env |
| `CORE_TOKEN_BUDGET` | Size of the always-in-context core memory. | 1500 | 1500 | 1500 | env |
| `QUICK_HORIZON_DAYS` | How far ahead the quick layer looks. | 30 | 30 | 30 | env |
| `QUICK_RECENT_DAYS` | How far back recent items stay in quick. | 7 | 7 | 7 | env |
| `RECONCILE_SIMILARITY_THRESHOLD` | Similarity above which a new memory is checked against an old one. | 0.85 | 0.85 | 0.85 | env |
| `CUE_KEYS_MAX` | Cue keys written per memory. | 3 | 3 | 3 | env |
| `EMBED_DIMENSIONS` | Embedding width; must match the database column. | 1536 | 1536 | 1536 | env |
| `ENRICH_ENABLED` | Write alternative wordings and cues when saving. | true | true | true | env |
| `VERBAL_KEYS_ENABLED` | Render memory keys as sentences. | true | true | true | env |
| `SOFT_CHANNEL_ENABLED` | Search by meaning as well as by filters. | true | true | true | env |
| `SOFT_CHANNEL_K` | Candidates the search by meaning returns. | 20 | 20 | 20 | env |
| `RRF_K` | Reciprocal rank fusion constant. | 60 | 60 | 60 | env |
| `HISTORY_DEMOTION` | Score multiplier for memories that no longer hold. | 0.5 | 0.5 | 0.5 | env |
| `RERANK_ENABLED` | Score candidates with a model before answering. | true | true | true | env |
| `RERANK_TOP_N` | Candidates sent to the reranker. | 20 | 20 | 20 | env |
| `RERANK_MIN_SCORE` | Score a candidate needs to reach the answer. | 0.5 | 0.5 | 0.5 | env |
| `ANSWER_TOP_K` | Memories the answer may draw on. | 8 | 8 | 8 | env |
| `LIST_MAX_ITEMS` | Longest list an answer shows. | 20 | 20 | 20 | env |
| `TOOL_TIMEOUT_MS` | Time each recall tool gets. | 3000 | 3000 | 3000 | env |
| `COUNT_CHECK_MIN_SCORE` | Score for a look-alike to be offered after a count. | 0.6 | 0.6 | 0.6 | env |
| `TRIGGER_SIMILARITY_THRESHOLD` | Similarity for a topic reminder to fire. | 0.6 | 0.6 | 0.6 | env |
| `UPCOMING_DAYS` | Days ahead the Upcoming page shows. | 30 | 30 | 30 | env |
| `QUICK_FREQUENT_MIN` | Retrievals that make a memory frequent. | 3 | 3 | 3 | env |
| `QUOTA_USD_GUEST` | Lifetime spend a guest may use, in dollars. | 0.75 | 0.75 | 0.75 | env |
| `QUOTA_USD_STANDARD` | Lifetime spend a signed-in person may use. | 2.50 | 2.50 | 2.50 | env |
| `QUOTA_USD_PREMIUM` | Lifetime spend of a person the operator upgraded. | 4.00 | 4.00 | 4.00 | env |
| `SPEND_CAP_DAILY_USD` | App-wide spend per UTC day before turns stop. | 0.50 | 0.50 | 0.50 | ssm |
| `SPEND_CAP_MONTHLY_USD` | App-wide spend per UTC month before turns stop. | 5 | 5 | 5 | ssm |
| `SPEND_CAP_WARN_RATIO` | Share of a cap at which a warning is logged. | 0.8 | 0.8 | 0.8 | env |
| `PROVIDER_CREDIT_USD_ANTHROPIC` | What the app may spend on Anthropic since PROVIDER_CREDIT_SINCE (below the real balance). | unset | unset | set | ssm |
| `PROVIDER_CREDIT_USD_OPENAI` | What the app may spend on OpenAI since PROVIDER_CREDIT_SINCE. | unset | unset | set | ssm |
| `PROVIDER_CREDIT_SINCE` | The date the prepaid credit was topped up. | unset | unset | set | ssm |
| `KILL_SWITCH` | Start with model work stopped; the runtime flag (make kill-switch) overrides it. | false | false | false | env |
| `RATE_TURNS_PER_MINUTE` | Turns a person may start per minute. | 10 | 10 | 10 | env |
| `MAX_REQUEST_BYTES` | Largest request body the API accepts. | 262144 | 262144 | 262144 | env |
| `ALLOWED_ORIGINS` | Extra origins allowed to send state-changing requests (comma separated). | empty | https://localhost:8443 | https://2nd-mind.<domain> | ssm |
| `ACCESS_CODE` | The code sign-in asks for in production until real accounts arrive (S6). | unset | generated | set | secret |
| `GUESTS_OPEN` | Off keeps every way in (code sign-in, guest, persona) behind the access code; S5 turns it on to open the site. | false | false | false | ssm |
| `LOGIN_ATTEMPTS_PER_MINUTE` | Sign-in attempts per address per minute in production. | 5 | 5 | 5 | env |
| `SSE_HEARTBEAT_S` | Seconds between heartbeat comments on a quiet turn stream. | 15 | 15 | 15 | env |
| `SHUTDOWN_GRACE_S` | Seconds running turns and jobs get after SIGTERM (under the 45 s container stop timeout). | 30 | 30 | 30 | env |
| `DATABASE_MIGRATION_URL` | Postgres URL for the schema owner: migrations and the admin CLI (never the running app). | compose default | built by compose | built by compose from `POSTGRES_PASSWORD` | secret |
| `OPENAI_BASE_URL` | Points the OpenAI provider at another endpoint (a stand-in for a provider outage, or vLLM). | unset | unset | unset | env |
| `MODEL_<STEP>` | provider:model for one step, over models.yaml (also MODEL_<STEP>_FALLBACK). | unset | unset | unset | env |
| `EFFORT_<STEP>` | Reasoning depth (none, low, medium, high) for one step, over models.yaml. | unset | unset | unset | env |
| `API_UPSTREAM` | Where the web container proxies /v1 and /readyz (the api service). | http://api:8000 | http://api:8000 | http://api:8000 | env |
| `NGINX_HSTS` | The web tier's Strict-Transport-Security value; empty sends none (plain HTTP). | empty | set | set | env |
| `NGINX_MAX_BODY` | Largest request body the web tier passes on. | 1m | 1m | 1m | env |
| `NGINX_READ_TIMEOUT` | How long the web tier waits on a quiet stream from the API. | 60s | 120s | 120s | env |
| `NGINX_DESIGN` | Serve the /design gallery (on/off); off in production. | on | off | off | env |
| `LINK_FETCH_TIMEOUT_S` | Whole-fetch deadline for reading a link, in seconds. | 10 | 10 | 10 | env |
| `LINK_MAX_BYTES` | Largest page body the fetcher reads, counted after decompression. | 2000000 | 2000000 | 2000000 | env |
| `LINK_MAX_REDIRECTS` | Redirects the fetcher follows, each checked as a new URL. | 5 | 5 | 5 | env |
| `LINK_CHUNK_TOKENS` | Size of a page chunk, in tokens (about). | 300 | 300 | 300 | env |
| `LINK_MAX_CHUNKS` | Most chunks kept for one link. | 40 | 40 | 40 | env |
| `MAX_LINKS_PER_MESSAGE` | Links read from one message; more are saved as refused. | 3 | 3 | 3 | env |
| `MAX_LINKS_PER_MESSAGE_GUEST` | The same for a guest. | 1 | 1 | 1 | env |
| `RATE_FETCHES_PER_MINUTE` | Link fetches a person may start per minute. | 10 | 10 | 10 | env |
| `RATE_FETCHES_PER_MINUTE_GUEST` | The same for a guest. | 3 | 3 | 3 | env |
| `LINK_ALLOW_PRIVATE_HOSTS` | Test only: hosts the fetcher may reach although private (refused in production). | unset | unset | refused | env |
| `POSTGRES_PASSWORD` | The database owner's password (the compose file builds `DATABASE_MIGRATION_URL` from it). | compose default | generated | set | secret |
| `APP_DB_PASSWORD` | The app database role's password (`DATABASE_URL`, and `bootstrap_role` sets it). | compose default | generated | set | secret |
| `REDIS_PASSWORD` | The Redis password (`REDIS_URL`). | compose default | generated | set | secret |
| `APP_HOST` | The public host name Caddy serves and gets a certificate for. | n/a | localhost | 2nd-mind.<domain> | ssm |
| `IMAGE_REGISTRY` | Where the images are pulled from; empty means local image names. | n/a | empty | ghcr.io/<owner> | ssm |
| `IMAGE_TAG` | The release: the git SHA (first 12 characters) the images are tagged with. | local | git SHA | git SHA | env |
| `EDGE_HTTP_PORT` | The host port Caddy's port 80 is published on. | n/a | 8081 | 80 | env |
| `EDGE_HTTPS_PORT` | The host port Caddy's port 443 is published on. | n/a | 8443 | 443 | env |

## Where a value comes from, by name

- **Application variables** are `Settings` fields (the rows above that a test ties to the code).
- **Deploy-only variables** (`POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `REDIS_PASSWORD`, `APP_HOST`,
  `IMAGE_REGISTRY`, `IMAGE_TAG` and the `EDGE_*` ports) are read by `compose.prodlike.yaml`, not
  by the application; the compose file builds the database and Redis URLs from the passwords.
- **Tooling only** (the application doesn't read them, and they are never set on the host):
  `LIVE_RUN_BUDGET_USD`, `LIVE_TOTAL_BUDGET_USD`, `LIVE_BATCH`, `ALLOW_EXPENSIVE` and
  `ALLOW_DIRTY` for live evaluation runs; `AWS_PROFILE`, `PROD_URL` and `SHA` for the make
  targets that talk to AWS and GitHub.
