import re
from dataclasses import dataclass


@dataclass
class ParsedEvent:
    event_type: str
    account: str | None = None
    proxy: str | None = None
    prompt_idx: int | None = None
    extra: dict | None = None


_RULES: list[tuple[str, re.Pattern, callable]] = []


def _rule(event_type: str, pattern: str):
    compiled = re.compile(pattern)

    def decorator(fn):
        _RULES.append((event_type, compiled, fn))
        return fn

    return decorator


@_rule("403", r"\[(\d+)\] 403 on (\S+) / (\S+) \(total consecutive: (\d+)/(\d+)\)")
def _parse_403(m: re.Match) -> ParsedEvent:
    return ParsedEvent(
        event_type="403",
        prompt_idx=int(m.group(1)),
        account=m.group(2),
        proxy=m.group(3),
        extra={"consecutive": int(m.group(4)), "limit": int(m.group(5))},
    )


@_rule("403_limit", r"\[(\d+)\] ConsecutiveFailuresError")
def _parse_403_limit(m: re.Match) -> ParsedEvent:
    return ParsedEvent(event_type="403_limit", prompt_idx=int(m.group(1)))


@_rule("429", r"\[(\d+)\] HTTP 429.*consecutive: (\d+)/(\d+)")
def _parse_429(m: re.Match) -> ParsedEvent:
    return ParsedEvent(
        event_type="429",
        prompt_idx=int(m.group(1)),
        extra={"consecutive": int(m.group(2)), "limit": int(m.group(3))},
    )


@_rule("quota", r"Daily quota exhausted — (\S+) on cooldown (\d+)m")
def _parse_quota(m: re.Match) -> ParsedEvent:
    return ParsedEvent(
        event_type="quota",
        account=m.group(1),
        extra={"cooldown_min": int(m.group(2))},
    )


@_rule("cooldown", r"(\S+) on cooldown for (\d+)m \(hit #(\d+)\)")
def _parse_cooldown(m: re.Match) -> ParsedEvent:
    return ParsedEvent(
        event_type="cooldown",
        account=m.group(1),
        extra={"cooldown_min": int(m.group(2)), "hit": int(m.group(3))},
    )


@_rule("all_cooldown", r"All accounts on cooldown — sleeping (\S+)m")
def _parse_all_cooldown(m: re.Match) -> ParsedEvent:
    return ParsedEvent(
        event_type="all_cooldown",
        extra={"sleep_min": m.group(1)},
    )


@_rule("rotation", r"Rotating account: (\S+) \((\S+)\) → (\S+) \((\S+)\)")
def _parse_rotation(m: re.Match) -> ParsedEvent:
    return ParsedEvent(
        event_type="rotation",
        account=m.group(1),
        proxy=m.group(2),
        extra={"to_account": m.group(3), "to_proxy": m.group(4)},
    )


@_rule("rotation_complete", r"Rotation complete — now using: (\S+)")
def _parse_rotation_complete(m: re.Match) -> ParsedEvent:
    return ParsedEvent(event_type="rotation_complete", account=m.group(1))


@_rule("proxy_dead", r"Proxy (\S+) marked dead for (\S+)")
def _parse_proxy_dead(m: re.Match) -> ParsedEvent:
    return ParsedEvent(event_type="proxy_dead", proxy=m.group(1), account=m.group(2))


@_rule("proxy_patched", r"Profile (\S+) proxy patched → (\S+)")
def _parse_proxy_patched(m: re.Match) -> ParsedEvent:
    return ParsedEvent(event_type="proxy_patched", account=m.group(1), proxy=m.group(2))


@_rule("success", r"\[(\d+)\] Done: ")
def _parse_success(m: re.Match) -> ParsedEvent:
    return ParsedEvent(event_type="success", prompt_idx=int(m.group(1)))


@_rule("filtered", r"\[(\d+)\] Content filtered")
def _parse_filtered(m: re.Match) -> ParsedEvent:
    return ParsedEvent(event_type="filtered", prompt_idx=int(m.group(1)))


@_rule("ip_prohibited", r"\[(\d+)\] IP_PROHIBITED")
def _parse_ip_prohibited(m: re.Match) -> ParsedEvent:
    return ParsedEvent(event_type="ip_prohibited", prompt_idx=int(m.group(1)))


@_rule("401", r"\[(\d+)\] HTTP 401")
def _parse_401(m: re.Match) -> ParsedEvent:
    return ParsedEvent(event_type="401", prompt_idx=int(m.group(1)))


@_rule("worker_started", r"\[parallel.*\] Worker (\S+) started")
def _parse_worker_started(m: re.Match) -> ParsedEvent:
    return ParsedEvent(event_type="worker_started", account=m.group(1))


def parse_message(message: str) -> ParsedEvent | None:
    for event_type, pattern, handler in _RULES:
        m = pattern.search(message)
        if m:
            return handler(m)
    return None
