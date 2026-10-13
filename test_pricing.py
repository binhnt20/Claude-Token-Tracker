#!/usr/bin/env python3
"""Tests for pricing tables, cost math and transcript parsing. Run: python3 -m unittest test_pricing -v"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from track_tokens import parse_transcript

from pricing import (
    COST_PARTS,
    HAIKU_35,
    HAIKU_45,
    OPUS_41,
    PRICING,
    cost_usd,
    normalize_model,
    price_multiplier,
    rates_for,
)

TODAY = "2026-07-17"
NOW = "2026-10-13"

# Every model id the pricing page listed on 2026-10-13, retired ones included: old transcripts still name them.
PUBLISHED_MODELS = (
    "claude-fable-5-1", "claude-mythos-5-1", "claude-fable-5", "claude-mythos-5",
    "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-opus-4-5", "claude-opus-4-1", "claude-opus-4",
    "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6", "claude-sonnet-4-5", "claude-sonnet-4",
    "claude-haiku-5-5", "claude-haiku-4-5", "claude-haiku-3-5",
)


def all_rate_cards():
    """Yield (label, effective_from, rates) for every rate dict in PRICING, prompt tiers included."""
    for model, windows in PRICING.items():
        for effective_from, _to, rates in windows:
            if isinstance(rates, dict):
                yield model, effective_from, rates
            else:
                for limit, tier_rates in rates:
                    yield f"{model} (prompt <= {limit})", effective_from, tier_rates


class TestNormalizeModel(unittest.TestCase):
    def test_normalize_strips_date_suffix(self):
        self.assertEqual(normalize_model("claude-haiku-4-5-20251001"), "claude-haiku-4-5")

    def test_normalize_strips_bracket_suffix(self):
        self.assertEqual(normalize_model("claude-opus-4-8[1m]"), "claude-opus-4-8")

    def test_normalize_leaves_plain(self):
        self.assertEqual(normalize_model("claude-opus-4-8"), "claude-opus-4-8")

    def test_normalize_keeps_dashed_date(self):
        """'-YYYY-MM-DD' is not the 8-digit suffix form and must survive."""
        self.assertEqual(normalize_model("gpt-5.4-mini-2026-03-17"), "gpt-5.4-mini-2026-03-17")


class TestRatesFor(unittest.TestCase):
    def test_unknown_model_returns_none(self):
        self.assertIsNone(rates_for("gpt-5.4", TODAY))

    def test_synthetic_returns_none(self):
        self.assertIsNone(rates_for("<synthetic>", TODAY))

    def test_no_silent_fallback(self):
        """Regression: an unseen model must be unpriced, never priced as Sonnet."""
        self.assertIsNone(rates_for("claude-totally-new-9", TODAY))

    def test_opus46_is_not_opus41_price(self):
        """Opus 4.6 is $5/$25; the old dashboard wrongly charged it deprecated Opus 4.1 rates."""
        self.assertEqual(rates_for("claude-opus-4-6", TODAY)["input"], 5)

    def test_haiku_date_suffix_resolves(self):
        self.assertEqual(rates_for("claude-haiku-4-5-20251001", TODAY), HAIKU_45)

    def test_retired_models_keep_their_rows(self):
        self.assertEqual(rates_for("claude-opus-4-1", NOW), OPUS_41)
        self.assertEqual(rates_for("claude-haiku-3-5", NOW), HAIKU_35)


class TestCurrentModels(unittest.TestCase):
    def test_every_published_model_is_priced(self):
        for model in PUBLISHED_MODELS:
            self.assertIsNotNone(rates_for(model, NOW), model)

    def test_opus55_rates(self):
        r = rates_for("claude-opus-5-5", NOW)
        self.assertEqual((r["input"], r["output"], r["cache_read"]), (4, 20, 0.20))

    def test_fable51_cache_read(self):
        self.assertEqual(rates_for("claude-fable-5-1", NOW)["cache_read"], 0.25)


class TestPriceWindows(unittest.TestCase):
    def test_sonnet5_stays_at_intro_price(self):
        """The $3/$15 step planned for 2026-09-01 was cancelled on 2026-08-10."""
        for date in ("2026-07-17", "2026-09-01", "2099-01-01"):
            self.assertEqual(rates_for("claude-sonnet-5", date)["input"], 2, date)

    def test_sonnet55_cache_read_cut_on_2026_10_07(self):
        self.assertEqual(rates_for("claude-sonnet-5-5", "2026-10-06")["cache_read"], 0.20)
        self.assertEqual(rates_for("claude-sonnet-5-5", "2026-10-07")["cache_read"], 0.10)

    def test_windows_cover_every_date(self):
        for model in PUBLISHED_MODELS:
            for date in ("2024-01-01", "2026-10-06", "2026-10-07", "2099-12-31"):
                self.assertIsNotNone(rates_for(model, date), f"{model} {date}")


class TestPublishedRules(unittest.TestCase):
    """The pricing page derives writes from input everywhere, and reads too except in its footnotes."""

    READ_EXCEPTIONS = {
        "claude-fable-5-1": 0.025,
        "claude-mythos-5-1": 0.025,
        "claude-opus-5-5": 0.05,
    }

    def _read_ratio(self, label, effective_from):
        model = label.split(" ")[0]
        if model == "claude-sonnet-5-5" and effective_from == "2026-10-07":
            return 0.05
        return self.READ_EXCEPTIONS.get(model, 0.1)

    def test_cache_5m_is_1_25x_input(self):
        for label, _from, r in all_rate_cards():
            self.assertAlmostEqual(r["cache_write_5m"], r["input"] * 1.25, msg=label)

    def test_cache_1h_is_2x_input(self):
        for label, _from, r in all_rate_cards():
            self.assertAlmostEqual(r["cache_write_1h"], r["input"] * 2, msg=label)

    def test_cache_read_ratio(self):
        for label, effective_from, r in all_rate_cards():
            ratio = self._read_ratio(label, effective_from)
            self.assertAlmostEqual(r["cache_read"], r["input"] * ratio, msg=label)

    def test_cache_1h_costs_more_than_5m(self):
        """The bug this project fixes: 1h writes billed at 5m rates understates cost."""
        for label, _from, r in all_rate_cards():
            self.assertGreater(r["cache_write_1h"], r["cache_write_5m"], msg=label)


class TestPromptTiers(unittest.TestCase):
    def test_haiku55_low_card_up_to_100k(self):
        self.assertEqual(rates_for("claude-haiku-5-5", NOW, 100_000)["input"], 0.10)

    def test_haiku55_high_card_over_100k(self):
        self.assertEqual(rates_for("claude-haiku-5-5", NOW, 100_001)["input"], 0.50)

    def test_haiku55_default_prompt_is_low_card(self):
        self.assertEqual(rates_for("claude-haiku-5-5", NOW)["output"], 0.50)


class TestModifiers(unittest.TestCase):
    def test_fast_doubles_opus55(self):
        self.assertEqual(price_multiplier("claude-opus-5-5", "fast", None), 2)

    def test_fast_without_verified_multiplier_is_unpriced(self):
        self.assertIsNone(price_multiplier("claude-opus-4-6", "fast", None))
        self.assertIsNone(price_multiplier("claude-sonnet-5-5", "fast", None))

    def test_us_inference_is_1_1x(self):
        self.assertEqual(price_multiplier("claude-opus-5-5", "standard", "us"), 1.1)

    def test_modifiers_stack(self):
        self.assertAlmostEqual(price_multiplier("claude-opus-5-5", "fast", "us"), 2.2)

    def test_us_inference_only_from_claude_46(self):
        self.assertEqual(price_multiplier("claude-haiku-4-5", None, "us"), 1)

    def test_other_geos_are_standard(self):
        self.assertEqual(price_multiplier("claude-opus-5-5", None, "global"), 1)
        self.assertEqual(price_multiplier("claude-opus-5-5", None, "not_available"), 1)


class TestCostUsd(unittest.TestCase):
    def _one_m_each(self):
        return {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_creation_5m_tokens": 1_000_000,
            "cache_creation_1h_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
        }

    def test_cost_usd_math(self):
        # opus-5-5 @ 1M each: 4 + 20 + 5 + 8 + 0.20 = 37.20
        cost = cost_usd(self._one_m_each(), "claude-opus-5-5", NOW)
        self.assertAlmostEqual(cost["input"], 4)
        self.assertAlmostEqual(cost["output"], 20)
        self.assertAlmostEqual(cost["cache_write_5m"], 5)
        self.assertAlmostEqual(cost["cache_write_1h"], 8)
        self.assertAlmostEqual(cost["cache_read"], 0.20)
        self.assertAlmostEqual(cost["total"], 37.20)

    def test_cost_usd_total_is_sum_of_parts(self):
        cost = cost_usd(self._one_m_each(), "claude-fable-5", TODAY)
        parts = sum(cost[k] for k in COST_PARTS)
        self.assertAlmostEqual(cost["total"], parts)

    def test_cost_usd_unknown_returns_none(self):
        self.assertIsNone(cost_usd(self._one_m_each(), "gpt-5.6-terra", TODAY))

    def test_cost_usd_zero_stats(self):
        cost = cost_usd({}, "claude-opus-4-8", TODAY)
        self.assertEqual(cost["total"], 0.0)

    def test_cost_usd_uses_split_cache_fields(self):
        """Legacy 'cache_creation_input_tokens' must not be read; only the 5m/1h split."""
        cost = cost_usd(
            {"cache_creation_input_tokens": 1_000_000},
            "claude-opus-4-8",
            TODAY,
        )
        self.assertEqual(cost["total"], 0.0)

    def test_haiku55_tier_uses_whole_prompt(self):
        """Cache reads count toward the 100k threshold, so 60k fresh + 50k cached is a long prompt."""
        cost = cost_usd({"input_tokens": 60_000, "cache_read_input_tokens": 50_000}, "claude-haiku-5-5", NOW)
        self.assertAlmostEqual(cost["input"], 60_000 * 0.50 / 1e6)

    def test_fast_doubles_every_part(self):
        cost = cost_usd({"output_tokens": 1_000_000, "speed": "fast"}, "claude-opus-5-5", NOW)
        self.assertAlmostEqual(cost["output"], 40)

    def test_fast_on_unverified_model_is_unpriced(self):
        self.assertIsNone(cost_usd({"output_tokens": 1, "speed": "fast"}, "claude-opus-4-6", NOW))

    def test_web_search_billed_per_request(self):
        cost = cost_usd({"web_search_requests": 50}, "claude-opus-5-5", NOW)
        self.assertAlmostEqual(cost["web_search"], 0.50)
        self.assertAlmostEqual(cost["total"], 0.50)


class TestParseTranscript(unittest.TestCase):
    """Covers transcript shapes that real data does not currently produce."""

    def _write(self, usage, model="claude-opus-4-8"):
        line = json.dumps({
            "type": "assistant", "timestamp": "2026-07-17T10:00:00Z", "sessionId": "s",
            "message": {"model": model, "usage": usage},
        })
        path = Path(self.tmp.name) / "t.jsonl"
        path.write_text(line + "\n", encoding="utf-8")
        return parse_transcript(path)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_total_is_derived_from_split(self):
        """The top-level total under-reports on some entries; the split wins."""
        e = self._write({"cache_creation_input_tokens": 0,
                         "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                            "ephemeral_1h_input_tokens": 14_037}})[0]
        self.assertEqual(e["cache_creation_input_tokens"], 14_037)
        self.assertEqual(e["cache_creation_1h_tokens"], 14_037)
        self.assertAlmostEqual(e["cost"]["cache_write_1h"], 14_037 * 10 / 1e6)

    def test_counters_and_cost_never_disagree(self):
        e = self._write({"cache_creation_input_tokens": 2_283,
                         "cache_creation": {"ephemeral_5m_input_tokens": 3_880,
                                            "ephemeral_1h_input_tokens": 0}})[0]
        self.assertEqual(e["cache_creation_input_tokens"],
                         e["cache_creation_5m_tokens"] + e["cache_creation_1h_tokens"])

    def test_unmodelled_cache_tier_is_unpriced(self):
        """A tier we don't know must not bill as zero — that is the original bug's shape."""
        e = self._write({"cache_creation": {"ephemeral_5m_input_tokens": 0,
                                            "ephemeral_1h_input_tokens": 0,
                                            "ephemeral_15m_input_tokens": 1_000_000}})[0]
        self.assertIsNone(e["cost"])

    def test_legacy_transcript_without_split(self):
        e = self._write({"cache_creation_input_tokens": 5_000})[0]
        self.assertEqual(e["cache_creation_5m_tokens"], 5_000)
        self.assertEqual(e["cache_creation_1h_tokens"], 0)
        self.assertIsNotNone(e["cost"])

    def test_null_model_does_not_crash(self):
        e = self._write({"input_tokens": 10}, model=None)[0]
        self.assertEqual(e["model"], "unknown")
        self.assertIsNone(e["cost"])

    def test_fast_mode_is_unpriced(self):
        e = self._write({"input_tokens": 1_000_000, "speed": "fast"})[0]
        self.assertIsNone(e["cost"])
        self.assertIn("fast", e["model"])

    def test_standard_speed_is_priced(self):
        e = self._write({"input_tokens": 1_000_000, "speed": "standard"})[0]
        self.assertAlmostEqual(e["cost"]["input"], 5)


if __name__ == "__main__":
    unittest.main()
