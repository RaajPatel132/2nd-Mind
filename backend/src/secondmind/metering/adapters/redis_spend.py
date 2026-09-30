"""Spend state in Redis (ADR-0032): the kill-switch flag, spend counters per UTC day and
month and per provider, "out of credit" marks, warn-once keys, and per-identity token buckets.

Every process reads and writes the same keys, so a flag flipped from the CLI reaches them all.
Amounts are stored as decimal strings (``INCRBYFLOAT``); the ledger reconciliation resets them
to exact values every few minutes.
"""

from datetime import UTC, datetime
from decimal import Decimal

from redis.asyncio import Redis

from secondmind.metering.gate import SpendTotals, utc_day, utc_month

PREFIX = "sm:"
KILL = f"{PREFIX}kill_switch"
PROVIDERS = f"{PREFIX}spend:providers"
DAY_TTL_S = 3 * 86_400
MONTH_TTL_S = 40 * 86_400

# A token bucket: capacity = per_minute, refilled continuously. Returns 0 when a token was
# taken, else the milliseconds until the next one.
_TAKE = """
local key, rate, now = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2])
local state = redis.call('HMGET', key, 'tokens', 'at')
local tokens, at = tonumber(state[1]), tonumber(state[2])
if tokens == nil then tokens, at = rate, now end
tokens = math.min(rate, tokens + (now - at) * rate / 60000)
local wait = 0
if tokens >= 1 then tokens = tokens - 1 else wait = math.ceil((1 - tokens) * 60000 / rate) end
redis.call('HSET', key, 'tokens', tostring(tokens), 'at', tostring(now))
redis.call('PEXPIRE', key, 120000)
return wait
"""


def _day(at: datetime) -> str:
    return f"{PREFIX}spend:day:{utc_day(at)}"


def _month(at: datetime) -> str:
    return f"{PREFIX}spend:month:{utc_month(at)}"


def _amount(raw: object) -> Decimal:
    if raw is None:
        return Decimal(0)
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    return Decimal(text)


class RedisSpendStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @classmethod
    def from_url(cls, url: str) -> "RedisSpendStore":
        return cls(Redis.from_url(url))

    async def aclose(self) -> None:
        await self._redis.aclose()

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def kill_switch(self) -> bool | None:
        raw = await self._redis.get(KILL)
        if raw is None:
            return None
        return (raw.decode() if isinstance(raw, bytes) else str(raw)) == "on"

    async def set_kill_switch(self, on: bool) -> None:
        await self._redis.set(KILL, "on" if on else "off")

    async def add(self, provider: str, cost: Decimal, at: datetime) -> SpendTotals:
        amount = float(cost)
        pipe = self._redis.pipeline(transaction=True)
        pipe.incrbyfloat(_day(at), amount)
        pipe.expire(_day(at), DAY_TTL_S)
        pipe.incrbyfloat(_month(at), amount)
        pipe.expire(_month(at), MONTH_TTL_S)
        pipe.hincrbyfloat(PROVIDERS, provider, amount)
        pipe.hgetall(PROVIDERS)
        day, _, month, _, _, providers = await pipe.execute()
        return SpendTotals(
            day=_amount(day),
            month=_amount(month),
            providers={_text(k): _amount(v) for k, v in providers.items()},
        )

    async def totals(self, at: datetime) -> SpendTotals:
        pipe = self._redis.pipeline(transaction=False)
        pipe.get(_day(at))
        pipe.get(_month(at))
        pipe.hgetall(PROVIDERS)
        day, month, providers = await pipe.execute()
        return SpendTotals(
            day=_amount(day),
            month=_amount(month),
            providers={_text(k): _amount(v) for k, v in providers.items()},
        )

    async def reset(self, totals: SpendTotals, at: datetime) -> None:
        pipe = self._redis.pipeline(transaction=True)
        pipe.set(_day(at), str(totals.day), ex=DAY_TTL_S)
        pipe.set(_month(at), str(totals.month), ex=MONTH_TTL_S)
        pipe.delete(PROVIDERS)
        if totals.providers:
            pipe.hset(PROVIDERS, mapping={k: str(v) for k, v in totals.providers.items()})
        await pipe.execute()

    async def credit_out(self, provider: str) -> bool:
        return bool(await self._redis.exists(f"{PREFIX}credit_out:{provider}"))

    async def mark_credit_out(self, provider: str, seconds: int) -> None:
        await self._redis.set(f"{PREFIX}credit_out:{provider}", "1", ex=seconds)

    async def clear_credit_out(self, provider: str) -> None:
        await self._redis.delete(f"{PREFIX}credit_out:{provider}")

    async def once(self, key: str, seconds: int) -> bool:
        return bool(await self._redis.set(f"{PREFIX}{key}", "1", ex=seconds, nx=True))

    async def incr_daily(self, key: str, at: datetime) -> int:
        slot = f"{PREFIX}daily:{_day_stamp(at)}:{key}"
        pipe = self._redis.pipeline(transaction=True)
        pipe.incr(slot)
        pipe.expire(slot, DAY_TTL_S)
        count, _ = await pipe.execute()
        return int(count)

    async def take(self, bucket: str, per_minute: int) -> float | None:
        now_ms = int(datetime.now().timestamp() * 1000)  # noqa: DTZ005 - an epoch, no zone
        wait_ms = await self._redis.eval(  # type: ignore[misc]
            _TAKE, 1, f"{PREFIX}rate:{bucket}", str(per_minute), str(now_ms)
        )
        wait = int(wait_ms)
        return None if wait <= 0 else wait / 1000


def _day_stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y%m%d")


def _text(raw: object) -> str:
    return raw.decode() if isinstance(raw, bytes) else str(raw)
