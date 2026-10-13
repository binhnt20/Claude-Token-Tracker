#!/usr/bin/env python3
"""API pricing tables and cost computation for Claude models (USD per 1M tokens)."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Source: platform.claude.com/docs/en/about-claude/pricing + /release-notes/overview, read 2026-10-13.
# Cache writes are 1.25x (5m) and 2x (1h) input on every model. Cache reads are 0.1x input except where the
# pricing page footnotes otherwise: 0.025x on Fable/Mythos 5.1, 0.05x on Opus 5.5 and on Sonnet 5.5 since
# 2026-10-07. test_pricing checks every row against these rules, so a misread price fails a test.
FABLE_5_1 = {"input": 10, "output": 50, "cache_write_5m": 12.50, "cache_write_1h": 20, "cache_read": 0.25}
FABLE_5 = {"input": 10, "output": 50, "cache_write_5m": 12.50, "cache_write_1h": 20, "cache_read": 1.00}
OPUS_5_5 = {"input": 4, "output": 20, "cache_write_5m": 5, "cache_write_1h": 8, "cache_read": 0.20}
OPUS = {"input": 5, "output": 25, "cache_write_5m": 6.25, "cache_write_1h": 10, "cache_read": 0.50}
OPUS_41 = {"input": 15, "output": 75, "cache_write_5m": 18.75, "cache_write_1h": 30, "cache_read": 1.50}
SONNET_5_5_LAUNCH = {"input": 2, "output": 10, "cache_write_5m": 2.50, "cache_write_1h": 4, "cache_read": 0.20}
SONNET_5_5 = {"input": 2, "output": 10, "cache_write_5m": 2.50, "cache_write_1h": 4, "cache_read": 0.10}
SONNET_5 = {"input": 2, "output": 10, "cache_write_5m": 2.50, "cache_write_1h": 4, "cache_read": 0.20}
SONNET = {"input": 3, "output": 15, "cache_write_5m": 3.75, "cache_write_1h": 6, "cache_read": 0.30}
HAIKU_5_5_SHORT = {"input": 0.10, "output": 0.50, "cache_write_5m": 0.125, "cache_write_1h": 0.20, "cache_read": 0.01}
HAIKU_5_5_LONG = {"input": 0.50, "output": 2.50, "cache_write_5m": 0.625, "cache_write_1h": 1, "cache_read": 0.05}
HAIKU_45 = {"input": 1, "output": 5, "cache_write_5m": 1.25, "cache_write_1h": 2, "cache_read": 0.10}
HAIKU_35 = {"input": 0.80, "output": 4, "cache_write_5m": 1.00, "cache_write_1h": 1.60, "cache_read": 0.08}

# Haiku 5.5 is priced by prompt length (input + cache writes + cache reads): (max prompt tokens, rates).
HAIKU_5_5 = ((100_000, HAIKU_5_5_SHORT), (None, HAIKU_5_5_LONG))

# model -> [(effective_from, effective_to, rates)], UTC dates inclusive, None = unbounded.
# rates is a rate dict, or a tuple of prompt-length tiers. Retired models keep their rows: old transcripts
# still name them. This is the only place in the project that contains price numbers.
PRICING = {
    "claude-fable-5-1": [(None, None, FABLE_5_1)],
    "claude-mythos-5-1": [(None, None, FABLE_5_1)],
    "claude-fable-5": [(None, None, FABLE_5)],
    "claude-mythos-5": [(None, None, FABLE_5)],
    "claude-opus-5-5": [(None, None, OPUS_5_5)],
    "claude-opus-5": [(None, None, OPUS)],
    "claude-opus-4-8": [(None, None, OPUS)],
    "claude-opus-4-7": [(None, None, OPUS)],
    "claude-opus-4-6": [(None, None, OPUS)],
    "claude-opus-4-5": [(None, None, OPUS)],
    "claude-opus-4-1": [(None, None, OPUS_41)],
    "claude-opus-4": [(None, None, OPUS_41)],
    "claude-sonnet-5-5": [(None, "2026-10-06", SONNET_5_5_LAUNCH), ("2026-10-07", None, SONNET_5_5)],
    "claude-sonnet-5": [(None, None, SONNET_5)],
    "claude-sonnet-4-6": [(None, None, SONNET)],
    "claude-sonnet-4-5": [(None, None, SONNET)],
    "claude-sonnet-4": [(None, None, SONNET)],
    "claude-haiku-5-5": [(None, None, HAIKU_5_5)],
    "claude-haiku-4-5": [(None, None, HAIKU_45)],
    "claude-haiku-3-5": [(None, None, HAIKU_35)],
}

# Fast mode multiplies every token price. Only these multipliers are published; fast mode on any other
# model (Opus 4.6 had one, never verified here) stays unpriced rather than billed at 1x.
FAST_MULTIPLIER = {"claude-opus-5-5": 2, "claude-opus-5": 2, "claude-opus-4-8": 2}

# inference_geo "us" costs 1.1x on Claude 4.6 and later models.
US_INFERENCE_MULTIPLIER = 1.1
US_INFERENCE_MODELS = frozenset({
    "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8", "claude-opus-5", "claude-opus-5-5",
    "claude-sonnet-4-6", "claude-sonnet-5", "claude-sonnet-5-5", "claude-haiku-5-5",
    "claude-fable-5", "claude-fable-5-1", "claude-mythos-5", "claude-mythos-5-1",
})

WEB_SEARCH_PER_REQUEST = 0.01  # $10 per 1,000 searches, a flat fee no token multiplier touches

TOKEN_RATES = (
    ("input", "input_tokens"),
    ("output", "output_tokens"),
    ("cache_write_5m", "cache_creation_5m_tokens"),
    ("cache_write_1h", "cache_creation_1h_tokens"),
    ("cache_read", "cache_read_input_tokens"),
)
COST_PARTS = tuple(part for part, _ in TOKEN_RATES) + ("web_search",)

# Optional user file, same shape and bounds as Claude Code's `modelPricing` setting:
# {"multiplier": 0.85, "overrides": {"<model id>": {"input", "output", "cacheRead"[, "cacheWrite5m", "cacheWrite1h"]}}}
OVERRIDES_PATH = Path(os.environ.get("CTT_PRICING") or Path.home() / ".config" / "claude-token-tracker" / "pricing.json")
OVERRIDE_FIELDS = (
    ("input", "input"),
    ("output", "output"),
    ("cache_read", "cacheRead"),
    ("cache_write_5m", "cacheWrite5m"),
    ("cache_write_1h", "cacheWrite1h"),
)
REQUIRED_OVERRIDE_FIELDS = ("input", "output", "cacheRead")
MAX_RATE = 10_000
MAX_MULTIPLIER = 10


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _warn(path: Path, message: str) -> None:
    print(f"claude-token-tracker: {path}: {message}", file=sys.stderr)


def _parse_override_row(path: Path, model: str, row) -> dict | None:
    """Turn one user row into a rate dict, or warn and return None."""
    if not isinstance(row, dict):
        _warn(path, f"override for {model!r} is not an object; skipped")
        return None
    missing = [key for key in REQUIRED_OVERRIDE_FIELDS if key not in row]
    if missing:
        _warn(path, f"override for {model!r} lacks {', '.join(missing)}; skipped")
        return None
    for key in (key for _, key in OVERRIDE_FIELDS if key in row):
        if not _is_number(row[key]) or not 0 <= row[key] <= MAX_RATE:
            _warn(path, f"override for {model!r}: {key} must be a number from 0 to {MAX_RATE}; skipped")
            return None
    # Cache writes follow the published 1.25x / 2x input rule unless the user gives them.
    derived = {"cacheWrite5m": row["input"] * 1.25, "cacheWrite1h": row["input"] * 2}
    return {part: row.get(key, derived.get(key)) for part, key in OVERRIDE_FIELDS}


def load_overrides(path: Path) -> tuple[float, dict[str, dict]]:
    """Read the user price file: (multiplier, model -> rates). A missing file gives (1.0, {})."""
    if not path.exists():
        return 1.0, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        _warn(path, f"ignored ({exc})")
        return 1.0, {}
    if not isinstance(data, dict):
        _warn(path, "ignored (top level is not an object)")
        return 1.0, {}

    multiplier = 1.0
    if "multiplier" in data:
        value = data["multiplier"]
        if _is_number(value) and 0 < value <= MAX_MULTIPLIER:
            multiplier = float(value)
        else:
            _warn(path, f"multiplier must be a number above 0 and at most {MAX_MULTIPLIER}; using 1")

    rows = data.get("overrides", {})
    if not isinstance(rows, dict):
        _warn(path, "overrides is not an object; ignored")
        rows = {}
    overrides = {}
    for model, row in rows.items():
        rates = _parse_override_row(path, model, row)
        if rates is not None:
            overrides[normalize_model(model)] = rates
    return multiplier, overrides


def normalize_model(model: str) -> str:
    """Strip context-window and date suffixes: 'claude-haiku-4-5-20251001' -> 'claude-haiku-4-5'."""
    stripped = re.sub(r"\[.*?\]$", "", model)
    return re.sub(r"-\d{8}$", "", stripped)


def rates_for(model: str, date: str, prompt_tokens: int = 0) -> dict | None:
    """Return the rate table for a model on a YYYY-MM-DD date, or None when the model is unpriced."""
    model = normalize_model(model)
    if model in _OVERRIDES:
        return _OVERRIDES[model]
    windows = PRICING.get(model)
    if not windows:
        return None
    for effective_from, effective_to, rates in windows:
        if (effective_from is None or date >= effective_from) and (effective_to is None or date <= effective_to):
            if isinstance(rates, dict):
                return rates
            for limit, tier_rates in rates:
                if limit is None or prompt_tokens <= limit:
                    return tier_rates
    return None


def price_multiplier(model: str, speed: str | None, inference_geo: str | None) -> float | None:
    """Combined fast-mode and data-residency multiplier, or None when fast mode has no known price."""
    model = normalize_model(model)
    multiplier = 1.0
    if speed == "fast":
        if model not in FAST_MULTIPLIER:
            return None
        multiplier *= FAST_MULTIPLIER[model]
    if inference_geo == "us" and model in US_INFERENCE_MODELS:
        multiplier *= US_INFERENCE_MULTIPLIER
    return multiplier


def cost_usd(record: dict, model: str, date: str) -> dict | None:
    """Return per-part cost in USD for one call or a bucket of calls, or None when it cannot be priced."""
    multiplier = price_multiplier(model, record.get("speed"), record.get("inference_geo"))
    prompt_tokens = sum(record.get(k, 0) for k in (
        "input_tokens", "cache_creation_5m_tokens", "cache_creation_1h_tokens", "cache_read_input_tokens"))
    rates = rates_for(model, date, prompt_tokens)
    if rates is None or multiplier is None:
        return None

    multiplier *= _MULTIPLIER
    cost = {part: record.get(field, 0) * rates[part] * multiplier / 1e6 for part, field in TOKEN_RATES}
    cost["web_search"] = record.get("web_search_requests", 0) * WEB_SEARCH_PER_REQUEST
    cost["total"] = sum(cost.values())
    return cost


_MULTIPLIER, _OVERRIDES = load_overrides(OVERRIDES_PATH)
