from pathlib import Path
import copy
import csv
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from experiment import (
    PRODUCTS,
    make_batch,
    mutate,
    structural,
    reserve,
    flag,
    write_csv,
)


class Contracts(unittest.TestCase):
    def setUp(self):
        self.counts = {p: 30 * (i + 1) for i, p in enumerate(PRODUCTS)}
        self.history = [dict(self.counts) for _ in range(15)]

    def test_mutations_do_not_change_reference_history(self):
        before = copy.deepcopy(self.history)
        for family in [
            "duplicate_largest",
            "drop_largest",
            "scale_double",
            "scale_half",
            "replay",
            "exchange_labels",
        ]:
            mutate(self.counts, self.history[-1], "fixture", family)
        self.assertEqual(self.history, before)

    def test_future_values_are_not_detector_inputs(self):
        config = {"kind": "numeric", "window": 6, "cutoff": 8, "relative": 0.1}
        batch = make_batch(self.counts, "fixture")
        history = copy.deepcopy(self.history)
        a = flag(config, batch, history[:12], "fixture")
        history[14][PRODUCTS[0]] = 999999
        self.assertEqual(a, flag(config, batch, history[:12], "fixture"))

    def test_total_conserving_exchange_passes_structure_but_changes_allocation(self):
        batch = mutate(self.counts, self.counts, "fixture", "exchange_labels")
        self.assertFalse(structural(batch, "fixture"))
        self.assertEqual(
            sum(r["count"] for r in batch["rows"]), sum(self.counts.values())
        )
        self.assertNotEqual(
            reserve(batch["rows"], self.history[-2:]),
            reserve(make_batch(self.counts, "fixture")["rows"], self.history[-2:]),
        )

    def test_scale_with_matching_header_is_structurally_valid(self):
        self.assertFalse(
            structural(
                mutate(self.counts, self.counts, "fixture", "scale_double"), "fixture"
            )
        )

    def test_duplicate_and_missing_grain_are_rejected(self):
        for family in ["duplicate_largest", "drop_largest"]:
            self.assertTrue(
                structural(
                    mutate(self.counts, self.counts, "fixture", family), "fixture"
                )
            )

    def test_deterministic_equal_count_ties(self):
        equal = {p: 30 for p in PRODUCTS}
        batch = mutate(equal, equal, "fixture", "duplicate_largest")
        self.assertEqual(batch["rows"][-1]["product"], min(PRODUCTS))

    def test_trial_export_preserves_optional_parameters(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("experiment.OUT", Path(folder)):
                write_csv(
                    "trials.csv",
                    [
                        {"name": "baseline"},
                        {"name": "numeric", "window": 6, "cutoff": 8},
                    ],
                )
            with open(Path(folder) / "trials.csv", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["window"], "")
            self.assertEqual(rows[1]["window"], "6")
            self.assertEqual(rows[1]["cutoff"], "8")


if __name__ == "__main__":
    unittest.main()
