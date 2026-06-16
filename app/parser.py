import re
from dataclasses import dataclass, field


@dataclass
class ParsedEvent:
    event_type: str
    account: str | None = None
    proxy: str | None = None
    prompt_idx: int | None = None
    extra: dict = field(default_factory=dict)


def _host_only(proxy_url: str) -> str:
    """Extract host:port from http://user:pass@host:port or host:port."""
    m = re.search(r'@([\d.]+:\d+)', proxy_url)
    return m.group(1) if m else proxy_url


def parse_message(module: str | None, function: str | None, message: str) -> ParsedEvent | None:
    mod = module or ""
    fn = function or ""

    # 403 с аккаунтом и прокси
    # "[0] 403 on account_14 / http://user:pass@66.248.139.236:12323 (total consecutive: 1/2)"
    m = re.search(r'\[(\d+)\] 403 on (\S+) / (\S+) \(total consecutive: (\d+)/(\d+)\)', message)
    if m:
        return ParsedEvent(
            event_type="403",
            prompt_idx=int(m.group(1)),
            account=m.group(2),
            proxy=_host_only(m.group(3)),
            extra={"consecutive": int(m.group(4)), "limit": int(m.group(5))},
        )

    # 429
    m = re.search(r'\[(\d+)\] HTTP 429.*consecutive: (\d+)/(\d+)', message)
    if m:
        return ParsedEvent(
            event_type="429",
            prompt_idx=int(m.group(1)),
            extra={"consecutive": int(m.group(2)), "limit": int(m.group(3))},
        )

    # Daily quota exhausted
    m = re.search(r'Daily quota exhausted — (\S+) on cooldown (\d+)m', message)
    if m:
        return ParsedEvent(
            event_type="quota",
            account=m.group(1),
            extra={"cooldown_min": int(m.group(2))},
        )

    # Cooldown: "account_14 on cooldown for 5m (hit #1)"
    m = re.search(r'(account_\S+) on cooldown for (\d+)m \(hit #(\d+)\)', message)
    if m:
        return ParsedEvent(
            event_type="cooldown",
            account=m.group(1),
            extra={"cooldown_min": int(m.group(2)), "hit": int(m.group(3))},
        )

    # Account rotation: "Rotating account: account_15 (46.34.37.13:12323) → account_15 (142.248.144.1:12323)"
    m = re.search(r'Rotating account: (\S+) \(([^)]+)\) → (\S+) \(([^)]+)\)', message)
    if m:
        return ParsedEvent(
            event_type="rotation",
            account=m.group(1),
            proxy=_host_only(m.group(2)),
            extra={"to_account": m.group(3), "to_proxy": _host_only(m.group(4))},
        )

    # Rotation complete
    m = re.search(r'Rotation complete — now using: (\S+)', message)
    if m:
        return ParsedEvent(event_type="rotation_complete", account=m.group(1))

    # Proxy dead
    m = re.search(r'Proxy (\S+) marked dead for (\S+)', message)
    if m:
        return ParsedEvent(event_type="proxy_dead", proxy=_host_only(m.group(1)), account=m.group(2))

    # Proxy patched: "Profile account_13 proxy patched → 195.178.142.29:12323"
    m = re.search(r'Profile (account_\S+) proxy patched → (\S+)', message)
    if m:
        return ParsedEvent(event_type="proxy_patched", account=m.group(1), proxy=m.group(2))

    # Success parallel_pipeline с суффиксом: "[account_17/img] Saved:" / "[account_17/i2v] Saved:"
    m = re.search(r'\[(account_\S+)/\w+\] Saved:', message)
    if m:
        return ParsedEvent(event_type="success", account=m.group(1))

    # Success parallel_pipeline без суффикса: "[account_17] Saved:"
    m = re.search(r'\[(account_\S+)\] Saved:', message)
    if m:
        return ParsedEvent(event_type="success", account=m.group(1))

    # Success pipeline/image_pipeline: "[6] Done:" / "[6] Saved:" / "[6] Downloaded:"
    m = re.search(r'\[(\d+)\] (?:Done|Saved|Downloaded):', message)
    if m:
        return ParsedEvent(event_type="success", prompt_idx=int(m.group(1)))

    # Filtered
    m = re.search(r'\[(\d+)\] Content filtered', message)
    if m:
        return ParsedEvent(event_type="filtered", prompt_idx=int(m.group(1)))

    # IP_PROHIBITED: "[0] IP_PROHIBITED — triggering account rotation"
    m = re.search(r'\[(\d+)\] IP_PROHIBITED', message)
    if m:
        return ParsedEvent(event_type="ip_prohibited", prompt_idx=int(m.group(1)))

    # 401
    m = re.search(r'\[(\d+)\] HTTP 401', message)
    if m:
        return ParsedEvent(event_type="401", prompt_idx=int(m.group(1)))

    # Dolphin 500: "Failed to start profile 782332520 (account account_17), attempt 1/2: Server error '500..."
    m = re.search(r"Failed to start profile \d+ \(account (account_\S+)\), attempt (\d+)/(\d+).*500", message)
    if m:
        return ParsedEvent(
            event_type="dolphin_500",
            account=m.group(1),
            extra={"attempt": int(m.group(2)), "max_attempts": int(m.group(3))},
        )

    # Profile unresponsive: "Profile 782332520 (account_17) unresponsive — skipping"
    m = re.search(r'Profile \d+ \((account_\S+)\) unresponsive', message)
    if m:
        return ParsedEvent(event_type="unresponsive", account=m.group(1))

    # Producer crashed: "Producer crashed (Error: BrowserType.connect_over_cdp: WebSocket error..."
    # module = src.services.recaptcha, function = run
    if "Producer crashed" in message and "recaptcha" in mod:
        m = re.search(r'ECONNREFUSED 127\.0\.0\.1:(\d+)', message)
        return ParsedEvent(
            event_type="producer_crashed",
            extra={"port": m.group(1) if m else None},
        )

    # Worker started — связывает аккаунт с запуском
    m = re.search(r'\[parallel.*\] Worker (account_\S+) started', message)
    if m:
        return ParsedEvent(event_type="worker_started", account=m.group(1))

    # All accounts on cooldown
    m = re.search(r'All accounts on cooldown — sleeping (\S+)m', message)
    if m:
        return ParsedEvent(event_type="all_cooldown", extra={"sleep_min": m.group(1)})

    # Account rotation failed (CRITICAL)
    if "Account rotation failed" in message:
        return ParsedEvent(event_type="rotation_failed", extra={"message": message})

    return None
