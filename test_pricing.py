#!/usr/bin/env python3
"""Tests for pricing tables, cost math and transcript parsing. Run: python3 -m unittest test_pricing -v"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from track_tokens import parse_transcript

from pricing import (
    FABLE,
    HAIKU_35,
    HAIKU_45,
    OPUS,
    OPUS_41,
    SONNET,
    SONNET_5_INTRO,
    cost_usd,
    normalize_model,
    rates_for,
)

TIERS = {
    "OPUS": OPUS,
    "FABLE": FABLE,
    "SONNET": SONNET,
    "SONNET_5_INTRO": SONNET_5_INTRO,
    "HAIKU_45": HAIKU_45,
    "HAIKU_35": HAIKU_35,
    "OPUS_41": OPUS_41,
}

TODAY = "2026-07-17"


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


class TestSonnet5PriceWindow(unittest.TestCase):
    def test_sonnet5_before_intro_end(self):
        self.assertEqual(rates_for("claude-sonnet-5", "2026-07-17"), SONNET_5_INTRO)

    def test_sonnet5_intro_boundary(self):
        self.assertEqual(rates_for("claude-sonnet-5", "2026-08-31"), SONNET_5_INTRO)
        self.assertEqual(rates_for("claude-sonnet-5", "2026-09-01"), SONNET)

    def test_sonnet5_windows_cover_every_date(self):
        for date in ("2024-01-01", "2026-08-31", "2026-09-01", "2099-12-31"):
            self.assertIsNotNone(rates_for("claude-sonnet-5", date), date)


class TestTierRatios(unittest.TestCase):
    def test_cache_5m_is_1_25x_input(self):
        for name, t in TIERS.items():
            self.assertAlmostEqual(t["cache_write_5m"], t["input"] * 1.25, msg=name)

    def test_cache_1h_double_base_input(self):
        for name, t in TIERS.items():
            self.assertAlmostEqual(t["cache_write_1h"], t["input"] * 2, msg=name)

    def test_cache_read_is_0_1x_input(self):
        for name, t in TIERS.items():
            self.assertAlmostEqual(t["cache_read"], t["input"] * 0.1, msg=name)

    def test_cache_1h_costs_more_than_5m(self):
        """The bug this project fixes: 1h writes billed at 5m rates understates cost."""
        for name, t in TIERS.items():
            self.assertGreater(t["cache_write_1h"], t["cache_write_5m"], msg=name)


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
        # opus-4-8 @ 1M each: 5 + 25 + 6.25 + 10 + 0.50 = 46.75
        cost = cost_usd(self._one_m_each(), "claude-opus-4-8", TODAY)
        self.assertAlmostEqual(cost["input"], 5)
        self.assertAlmostEqual(cost["output"], 25)
        self.assertAlmostEqual(cost["cache_write_5m"], 6.25)
        self.assertAlmostEqual(cost["cache_write_1h"], 10)
        self.assertAlmostEqual(cost["cache_read"], 0.50)
        self.assertAlmostEqual(cost["total"], 46.75)

    def test_cost_usd_total_is_sum_of_parts(self):
        cost = cost_usd(self._one_m_each(), "claude-fable-5", TODAY)
        parts = sum(v for k, v in cost.items() if k != "total")
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

    def test_cost_usd_honours_price_window(self):
        stats = {"input_tokens": 1_000_000}
        self.assertAlmostEqual(cost_usd(stats, "claude-sonnet-5", "2026-08-31")["input"], 2)
        self.assertAlmostEqual(cost_usd(stats, "claude-sonnet-5", "2026-09-01")["input"], 3)


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
