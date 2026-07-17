#!/usr/bin/env python3
"""API pricing tables and cost computation for Claude models (USD per 1M tokens)."""

from __future__ import annotations

import re

# Anthropic derives every cache rate from the base input rate:
#   cache_write_5m = 1.25x input, cache_write_1h = 2x input, cache_read = 0.1x input
# A new tier that breaks this ratio means the pricing source was misread.
OPUS = {"input": 5, "output": 25, "cache_write_5m": 6.25, "cache_write_1h": 10, "cache_read": 0.50}
FABLE = {"input": 10, "output": 50, "cache_write_5m": 12.50, "cache_write_1h": 20, "cache_read": 1.00}
SONNET = {"input": 3, "output": 15, "cache_write_5m": 3.75, "cache_write_1h": 6, "cache_read": 0.30}
SONNET_5_INTRO = {"input": 2, "output": 10, "cache_write_5m": 2.50, "cache_write_1h": 4, "cache_read": 0.20}
HAIKU_45 = {"input": 1, "output": 5, "cache_write_5m": 1.25, "cache_write_1h": 2, "cache_read": 0.10}
HAIKU_35 = {"input": 0.80, "output": 4, "cache_write_5m": 1.00, "cache_write_1h": 1.60, "cache_read": 0.08}
OPUS_41 = {"input": 15, "output": 75, "cache_write_5m": 18.75, "cache_write_1h": 30, "cache_read": 1.50}

# model -> [(effective_from, effective_to, rates)], dates inclusive, None = unbounded.
# This is the only place in the project that contains price numbers.
PRICING = {
    "claude-opus-4-8": [(None, None, OPUS)],
    "claude-opus-4-7": [(None, None, OPUS)],
    "claude-opus-4-6": [(None, None, OPUS)],
    "claude-opus-4-5": [(None, None, OPUS)],
    "claude-opus-4-1": [(None, None, OPUS_41)],
    "claude-fable-5": [(None, None, FABLE)],
    "claude-sonnet-5": [(None, "2026-08-31", SONNET_5_INTRO), ("2026-09-01", None, SONNET)],
    "claude-sonnet-4-6": [(None, None, SONNET)],
    "claude-sonnet-4-5": [(None, None, SONNET)],
    "claude-haiku-4-5": [(None, None, HAIKU_45)],
    "claude-haiku-3-5": [(None, None, HAIKU_35)],
}


def normalize_model(model: str) -> str:
    """Strip context-window and date suffixes: 'claude-haiku-4-5-20251001' -> 'claude-haiku-4-5'."""
    stripped = re.sub(r"\[.*?\]$", "", model)
    return re.sub(r"-\d{8}$", "", stripped)


def rates_for(model: str, date: str) -> dict | None:
    """Return the rate table for a model on a YYYY-MM-DD date, or None when the model is unpriced."""
    windows = PRICING.get(normalize_model(model))
    if not windows:
        return None
    for effective_from, effective_to, rates in windows:
        if (effective_from is None or date >= effective_from) and (effective_to is None or date <= effective_to):
            return rates
    return None


def cost_usd(stats: dict, model: str, date: str) -> dict | None:
    """Return per-token-type cost in USD, or None when the model has no price table."""
    rates = rates_for(model, date)
    if rates is None:
        return None

    cost = {
        "input": stats.get("input_tokens", 0) * rates["input"] / 1e6,
        "output": stats.get("output_tokens", 0) * rates["output"] / 1e6,
        "cache_write_5m": stats.get("cache_creation_5m_tokens", 0) * rates["cache_write_5m"] / 1e6,
        "cache_write_1h": stats.get("cache_creation_1h_tokens", 0) * rates["cache_write_1h"] / 1e6,
        "cache_read": stats.get("cache_read_input_tokens", 0) * rates["cache_read"] / 1e6,
    }
    cost["total"] = sum(cost.values())
    return cost
