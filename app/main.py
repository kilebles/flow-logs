from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.db import init_pool, close_pool, insert_event, query_accounts, query_proxies, query_timeline, query_recent_errors
from app.parser import parse_message


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(title="flow-stat", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


class LogEvent(BaseModel):
    ts: str
    level: str
    module: str | None = None
    function: str | None = None
    message: str
    # structured fields sent explicitly by flow-api logger sink
    event_type: str | None = None
    account: str | None = None
    proxy: str | None = None


@app.post("/ingest", status_code=204)
async def ingest(event: LogEvent) -> None:
    # If flow-api already classified the event, use that; otherwise fall back to regex parsing
    if event.event_type:
        event_type = event.event_type
        account = event.account
        proxy = event.proxy
        prompt_idx = None
        extra = None
    else:
        parsed = parse_message(event.module, event.function, event.message)
        event_type = parsed.event_type if parsed else None
        account = parsed.account if parsed else None
        proxy = parsed.proxy if parsed else None
        prompt_idx = parsed.prompt_idx if parsed else None
        extra = parsed.extra if parsed else None

    await insert_event(
        ts=event.ts,
        level=event.level,
        module=event.module,
        function=event.function,
        message=event.message,
        event_type=event_type,
        account=account,
        proxy=proxy,
        prompt_idx=prompt_idx,
        extra=extra,
    )


@app.get("/api/accounts")
async def api_accounts(hours: int = 6) -> list[dict]:
    rows = await query_accounts(hours)
    result = []
    for r in rows:
        r["last_event"] = r["last_event"].isoformat() if r["last_event"] else None
        result.append(r)
    return result


@app.get("/api/proxies")
async def api_proxies(hours: int = 6) -> list[dict]:
    rows = await query_proxies(hours)
    result = []
    for r in rows:
        r["last_seen"] = r["last_seen"].isoformat() if r["last_seen"] else None
        result.append(r)
    return result


@app.get("/api/timeline")
async def api_timeline(hours: int = 2) -> list[dict]:
    rows = await query_timeline(hours)
    result = []
    for r in rows:
        r["bucket"] = r["bucket"].isoformat() if r["bucket"] else None
        result.append(r)
    return result


@app.get("/api/errors")
async def api_errors(limit: int = 50) -> list[dict]:
    rows = await query_recent_errors(limit)
    result = []
    for r in rows:
        r["ts"] = r["ts"].isoformat() if r["ts"] else None
        if r["extra"] and not isinstance(r["extra"], dict):
            import json
            r["extra"] = json.loads(r["extra"])
        result.append(r)
    return result


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))
