import os
import json
from datetime import datetime, timezone
import asyncpg


_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=2, max_size=10)
    await _create_tables()


async def close_pool() -> None:
    if _pool:
        await _pool.close()


def get_pool() -> asyncpg.Pool:
    assert _pool is not None
    return _pool


async def _create_tables() -> None:
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          BIGSERIAL PRIMARY KEY,
                ts          TIMESTAMPTZ NOT NULL,
                received_at TIMESTAMPTZ DEFAULT now(),
                level       TEXT NOT NULL,
                module      TEXT,
                function    TEXT,
                message     TEXT NOT NULL,
                event_type  TEXT,
                account     TEXT,
                proxy       TEXT,
                prompt_idx  INTEGER,
                extra       JSONB
            );
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_events_account ON events(account);
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_account_type ON events(account, event_type);
        """)


async def insert_event(
    ts: str,
    level: str,
    module: str | None,
    function: str | None,
    message: str,
    event_type: str | None,
    account: str | None,
    proxy: str | None,
    prompt_idx: int | None,
    extra: dict | None,
) -> None:
    ts_dt = datetime.fromisoformat(ts).astimezone(timezone.utc)
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO events
                (ts, level, module, function, message, event_type, account, proxy, prompt_idx, extra)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            ts_dt, level, module, function, message,
            event_type, account, proxy, prompt_idx,
            json.dumps(extra) if extra else None,
        )


async def query_accounts(hours: int = 6) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                account,
                COUNT(*) FILTER (WHERE event_type = '403') AS errors_403,
                COUNT(*) FILTER (WHERE event_type = '429') AS errors_429,
                COUNT(*) FILTER (WHERE event_type IN ('rotation', 'rotation_complete')) AS rotations,
                COUNT(*) FILTER (WHERE event_type = 'cooldown') AS cooldowns,
                COUNT(*) FILTER (WHERE event_type = 'quota') AS quotas,
                COUNT(*) FILTER (WHERE event_type = 'success') AS successes,
                COUNT(*) FILTER (WHERE event_type = 'ip_prohibited') AS ip_prohibited,
                COUNT(*) FILTER (WHERE event_type IN ('dolphin_500', 'unresponsive')) AS dolphin_errors,
                COUNT(*) FILTER (WHERE event_type = 'producer_crashed') AS producer_crashes,
                MAX(ts) AS last_event,
                -- текущий прокси: последний proxy_patched для этого аккаунта
                (SELECT proxy FROM events e2
                 WHERE e2.account = events.account AND e2.event_type = 'proxy_patched'
                 ORDER BY e2.ts DESC LIMIT 1) AS current_proxy,
                bool_or(event_type = 'cooldown' AND ts > now() - INTERVAL '15 minutes') AS on_cooldown,
                bool_or(event_type IN ('dolphin_500', 'unresponsive') AND ts > now() - INTERVAL '5 minutes') AS dolphin_down
            FROM events
            WHERE account IS NOT NULL
              AND ts > now() - ($1 || ' hours')::INTERVAL
            GROUP BY account
            ORDER BY errors_403 DESC, account
        """, str(hours))
        return [dict(r) for r in rows]


async def query_proxies(hours: int = 6) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                proxy,
                COUNT(*) FILTER (WHERE event_type = '403') AS errors_403,
                COUNT(*) FILTER (WHERE event_type = 'ip_prohibited') AS ip_prohibited,
                COUNT(*) FILTER (WHERE event_type = 'proxy_dead') AS dead_count,
                -- аккаунты которые использовали этот прокси
                array_agg(DISTINCT account) FILTER (WHERE account IS NOT NULL) AS accounts,
                MAX(ts) AS last_seen
            FROM events
            WHERE proxy IS NOT NULL
              AND ts > now() - ($1 || ' hours')::INTERVAL
            GROUP BY proxy
            ORDER BY errors_403 DESC
        """, str(hours))
        return [dict(r) for r in rows]


async def query_timeline(hours: int = 2) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                date_trunc('minute', ts) AS bucket,
                COUNT(*) FILTER (WHERE event_type = '403') AS errors_403,
                COUNT(*) FILTER (WHERE event_type = 'success') AS successes,
                COUNT(*) FILTER (WHERE event_type IN ('rotation', 'rotation_complete')) AS rotations,
                COUNT(*) FILTER (WHERE event_type IN ('cooldown', 'quota')) AS cooldowns,
                COUNT(*) FILTER (WHERE event_type = 'ip_prohibited') AS ip_prohibited,
                COUNT(*) FILTER (WHERE event_type IN ('dolphin_500', 'unresponsive', 'producer_crashed')) AS system_errors
            FROM events
            WHERE ts > now() - ($1 || ' hours')::INTERVAL
            GROUP BY bucket
            ORDER BY bucket
        """, str(hours))
        return [dict(r) for r in rows]


async def query_recent_errors(limit: int = 50) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT ts, level, event_type, account, proxy, prompt_idx, message, extra
            FROM events
            WHERE event_type IN (
                '403', '429', 'quota', 'cooldown', '401',
                'ip_prohibited', 'proxy_dead',
                'dolphin_500', 'unresponsive', 'producer_crashed',
                'rotation_failed'
            )
            ORDER BY ts DESC
            LIMIT $1
        """, limit)
        return [dict(r) for r in rows]
