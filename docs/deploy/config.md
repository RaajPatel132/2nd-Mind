# Configuration matrix

Every variable the application reads, what it means, the value it has in each environment and where
the value comes from. `Settings` is the source of truth for the application's own variables; a test
(`tests/unit/test_config_matrix.py`) fails when a field is added there without a row here, the same
way `.env.example` is checked. Values that are secrets are never written in this file.

**Source:** `env` is a plain environment variable in the task definition; `ssm` is an SSM Parameter
Store parameter (config that isn't secret but is set per environment); `secret` is a Secrets Manager
secret injected by ECS. On a laptop everything comes from `.env` or the compose defaults.

| Variable | Meaning | Development | Staging | Production | Source |
|---|---|---|---|---|---|
| `ENV` | Which environment this is; turns the start-up guards on for staging and production. | development | staging | production | env |
| `APP_VERSION` | Build label shown in /v1/meta and logs; the image tag (git SHA) in CI. | dev | git SHA | git SHA | env |
| `LOG_LEVEL` | Minimum log level. | INFO | INFO | INFO | env |
| `LOG_FORMAT` | json for machines, console for a terminal. | json | json | json | env |
| `LOG_INCLUDE_CONTENT` | Log message content (dev flag only; refused in staging and production). | false | false | false | env |
| `RESOURCES_DIR` | Where config/ and prompts/ live (the image's /app). | backend/ | /app | /app | env |
| `DATABASE_URL` | Postgres URL for the app's non-owner role (carries the password). | compose default | RDS, app role | RDS, app role | secret |
| `DATABASE_POOL_SIZE` | Connections in the app pool. | 10 | 10 | 10 | env |
| `REDIS_URL` | Redis URL: queue, spend counters, kill switch, rate limits (may carry a password). | compose default | ElastiCache | ElastiCache | secret |
| `MODEL_PROVIDER_MODE` | auto falls back to the fake provider without keys; live refuses to start without them; fake never calls out. | auto | live | live | env |
| `ANTHROPIC_API_KEY` | Anthropic key. | in .env, optional | set | set | secret |
| `OPENAI_API_KEY` | OpenAI key (chat and embeddings). | in .env, optional | set | set | secret |
| `PROVIDER_MAX_RETRIES` | Retries per model call before the fallback. | 2 | 2 | 2 | env |
| `PROVIDER_RETRY_BASE_MS` | First retry delay. | 250 | 250 | 250 | env |
| `PROVIDER_RETRY_MAX_MS` | Longest retry delay. | 4000 | 4000 | 4000 | env |
| `BREAKER_FAILURE_THRESHOLD` | Failures in the window that open a provider's circuit. | 5 | 5 | 5 | env |
| `BREAKER_WINDOW_S` | Seconds the failure count covers. | 60 | 60 | 60 | env |
| `BREAKER_COOLDOWN_S` | Seconds an open circuit waits before a probe. | 30 | 30 | 30 | env |
| `FAKE_PROVIDER_TOKEN_DELAY_MS` | Pause between streamed tokens of the fake provider. | 15 | n/a | n/a | env |
| `TRACING_ENABLED` | Send traces to Langfuse. | true | true | true | env |
| `LANGFUSE_HOST` | Langfuse URL (self-hosted locally; Langfuse Cloud on AWS). | compose | cloud URL | cloud URL | env |
| `LANGFUSE_PUBLIC_KEY` | Langfuse project public key. | compose | set | set | ssm |
| `LANGFUSE_SECRET_KEY` | Langfuse project secret key. | compose | set | set | secret |
| `LANGFUSE_UI_URL` | Where trace links point. | localhost | cloud URL | cloud URL | env |
| `LANGFUSE_PROJECT_ID` | Langfuse project id, for trace links. | compose | set | set | ssm |
| `TRACE_INCLUDE_CONTENT` | Put message text in traces (metadata only by default). | false | false | false | env |
| `DEV_AUTH` | Dev sign-in and the /v1/dev helpers; refused in production. | true | true (needs the access code) | false | env |
| `DEV_USER_EMAIL` | The dev user when none is named. | dev@example.com | n/a | n/a | env |
| `SESSION_SECRET` | Signs the session cookie (32+ chars; the example value is refused off-laptop). | example | random | random | secret |
| `SESSION_COOKIE_SECURE` | Mark the cookie Secure and send HSTS (required in staging and production). | false | true | true | env |
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
| `SPEND_CAP_DAILY_USD` | App-wide spend per UTC day before turns stop. | 0.50 | 0.50 | 0.50 | env |
| `SPEND_CAP_MONTHLY_USD` | App-wide spend per UTC month before turns stop. | 5 | 5 | 5 | env |
| `SPEND_CAP_WARN_RATIO` | Share of a cap at which a warning is logged. | 0.8 | 0.8 | 0.8 | env |
| `PROVIDER_CREDIT_USD_ANTHROPIC` | What the app may spend on Anthropic since PROVIDER_CREDIT_SINCE (below the real balance). | unset | set | set | ssm |
| `PROVIDER_CREDIT_USD_OPENAI` | What the app may spend on OpenAI since PROVIDER_CREDIT_SINCE. | unset | set | set | ssm |
| `PROVIDER_CREDIT_SINCE` | The date the prepaid credit was topped up. | unset | set | set | ssm |
| `KILL_SWITCH` | Start with model work stopped; the runtime flag (make kill-switch) overrides it. | false | false | false | env |
| `RATE_TURNS_PER_MINUTE` | Turns a person may start per minute. | 10 | 10 | 10 | env |
| `MAX_REQUEST_BYTES` | Largest request body the API accepts. | 262144 | 262144 | 262144 | env |
| `ALLOWED_ORIGINS` | Extra origins allowed to send state-changing requests (comma separated). | empty | empty | empty | env |
| `STAGING_ACCESS_CODE` | The code dev sign-in asks for on staging. | unset | set | n/a | secret |
| `LOGIN_ATTEMPTS_PER_MINUTE` | Sign-in attempts per address per minute on staging. | 5 | 5 | 5 | env |
| `SSE_HEARTBEAT_S` | Seconds between heartbeat comments on a quiet turn stream. | 15 | 15 | 15 | env |
| `SHUTDOWN_GRACE_S` | Seconds running turns and jobs get after SIGTERM (under the ECS stop timeout). | 30 | 30 | 30 | env |
| `DATABASE_MIGRATION_URL` | Postgres URL for the schema owner: migrations and the admin CLI (never the running app). | compose default | RDS, owner | RDS, owner | secret |
| `OPENAI_BASE_URL` | Points the OpenAI provider at another endpoint (a stand-in for a provider outage, or vLLM). | unset | unset | unset | env |
| `MODEL_<STEP>` | provider:model for one step, over models.yaml (also MODEL_<STEP>_FALLBACK). | unset | unset | unset | env |
| `EFFORT_<STEP>` | Reasoning depth (none, low, medium, high) for one step, over models.yaml. | unset | unset | unset | env |
| `API_UPSTREAM` | Where the web container proxies /v1 (the api service; the ALB in front of Fargate on AWS). | http://api:8000 | internal ALB | internal ALB | env |
| `NGINX_HSTS` | The web tier's Strict-Transport-Security value; empty sends none (plain HTTP). | empty | set | set | env |
| `NGINX_MAX_BODY` | Largest request body the web tier passes on. | 1m | 1m | 1m | env |
| `NGINX_READ_TIMEOUT` | How long the web tier waits on a quiet stream from the API. | 60s | 120s | 120s | env |
| `NGINX_DESIGN` | Serve the /design gallery (on/off); off in production. | on | on | off | env |

## Application variables added this sprint

`QUOTA_USD_*`, `SPEND_CAP_*`, `PROVIDER_CREDIT_*`, `KILL_SWITCH`, `RATE_TURNS_PER_MINUTE`,
`MAX_REQUEST_BYTES`, `ALLOWED_ORIGINS`, `STAGING_ACCESS_CODE`, `LOGIN_ATTEMPTS_PER_MINUTE`,
`SSE_HEARTBEAT_S` and `SHUTDOWN_GRACE_S` (Sprint 3.9). `LIVE_RUN_BUDGET_USD`, `LIVE_TOTAL_BUDGET_USD`,
`LIVE_BATCH` and `ALLOW_EXPENSIVE` are tooling for live evaluation runs only: the application
does not read them and they are never set on AWS.
