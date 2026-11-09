"""Exercise the public CLI with offline Actor results; no network or paid runs."""
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import zillow_search


class DetailExportTests(unittest.TestCase):
    def export(self, rows, details):
        class Client:
            def actor(self, name):
                self.actor_name = name
                return self
            def call(self, **kwargs):
                self.call_args = kwargs
                return types.SimpleNamespace(status="SUCCEEDED", default_dataset_id="fixture")
            def dataset(self, dataset_id):
                return self
            def iterate_items(self):
                yield from [dict(row) for row in rows]
        client = Client()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "homes.csv"
            argv = ["zillow_search.py", "78704", "--max", "2", "--max-spend", "0.02", "--out", str(output)]
            if details:
                argv.append("--details")
            stdout = io.StringIO()
            with patch.object(zillow_search, "ApifyClient", return_value=client), \
                    patch.object(sys, "argv", argv), \
                    patch.dict(os.environ, {"APIFY_TOKEN": "offline-fixture"}), \
                    contextlib.redirect_stdout(stdout):
                zillow_search.main()
            with output.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                columns = reader.fieldnames
                exported = list(reader)
        return columns, exported, client, stdout.getvalue()

    def test_paid_fields_and_nested_values_survive_csv_roundtrip(self):
        facts = {"heating": ["Forced Air", "Heat pump"], "parking": {"spaces": 2}, "hasPool": False}
        history = [{"date": "2026-10-31", "price": 500000, "event": "Price change"}]
        description = 'Renovated, bright home.\nOwner calls it "café-ready".'
        row = {"type": "property", "zpid": "123", "price": 500000,
               "zestimateLow": 450000, "zestimateHigh": 550000, "description": description,
               "facts": facts, "priceHistory": history, "photos": ["https://example.test/photo.jpg"],
               "schools": [], "climateRisk": None, "agentPhone": "+1 555 0100"}
        columns, exported, client, _ = self.export([row], details=True)
        self.assertEqual(columns[:len(zillow_search.COLUMNS)], zillow_search.COLUMNS)
        self.assertTrue(set(row).issubset(columns))
        got = exported[0]
        self.assertEqual(got["zestimateLow"], "450000")
        self.assertEqual(got["zestimateHigh"], "550000")
        self.assertEqual(got["description"], description)
        self.assertEqual(json.loads(got["facts"]), facts)
        self.assertEqual(json.loads(got["priceHistory"]), history)
        self.assertEqual(json.loads(got["photos"]), row["photos"])
        self.assertEqual(json.loads(got["schools"]), [])
        self.assertEqual(got["climateRisk"], "")
        self.assertTrue(client.call_args["run_input"]["includeDetails"])
        self.assertEqual(client.call_args["max_total_charge_usd"], 0.02)

    def test_mixed_results_keep_optional_fields_without_misattributing_them(self):
        rows = [
            {"type": "property", "zpid": "123", "streetAddress": "123 Test St", "input": "78704",
             "facts": {"yearBuilt": 1924}, "addressMatch": "exact"},
            {"type": "listing", "zpid": "456", "address": "456 Test St", "price": 400000},
            {"type": "error", "task": "search missing", "error": "NOT_FOUND"},
        ]
        columns, exported, _, stdout = self.export(rows, details=True)
        self.assertEqual(len(exported), 2)
        self.assertEqual(exported[0]["address"], "123 Test St")
        self.assertEqual(exported[0]["searchInput"], "78704")
        self.assertEqual(exported[0]["addressMatch"], "exact")
        self.assertEqual(exported[1]["type"], "listing")
        self.assertEqual(exported[1]["facts"], "")
        self.assertEqual(exported[1]["addressMatch"], "")
        self.assertNotIn("error", columns)
        self.assertIn("NOT_FOUND", stdout)

    def test_normal_search_keeps_original_columns(self):
        row = {"zpid": "123", "price": 500000, "address": "123 Test St",
               "description": "Extra field", "facts": {"yearBuilt": 1924}, "type": "listing"}
        columns, exported, client, _ = self.export([row], details=False)
        self.assertEqual(columns, zillow_search.COLUMNS)
        self.assertEqual(exported[0]["address"], "123 Test St")
        self.assertEqual(exported[0]["price"], "500000")
        self.assertNotIn("facts", exported[0])
        self.assertFalse(client.call_args["run_input"]["includeDetails"])



class SearchStatusTests(unittest.TestCase):
    def run_cli(self, rows, status="SUCCEEDED", interrupted=False):
        class Client:
            def actor(self, name):
                return self
            def call(self, **kwargs):
                return types.SimpleNamespace(status=status, default_dataset_id="fixture")
            def dataset(self, dataset_id):
                return self
            def iterate_items(self):
                yield from [dict(row) for row in rows]
                if interrupted:
                    raise ConnectionError("download interrupted")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "homes.csv"
            output.write_text("previous complete export")
            argv = ["zillow_search.py", "78704", "invalid", "--out", str(output)]
            with patch.object(zillow_search, "ApifyClient", return_value=Client()), \
                    patch.object(sys, "argv", argv), \
                    patch.dict(os.environ, {"APIFY_TOKEN": "offline-fixture"}), \
                    contextlib.redirect_stdout(io.StringIO()):
                try:
                    code = zillow_search.main()
                except (SystemExit, ConnectionError) as exc:
                    code = exc
            return code, output.read_text()

    def test_error_only_export_returns_two_and_replaces_previous_with_header(self):
        code, text = self.run_cli([{"type": "error", "task": "invalid", "error": "NOT_FOUND"}])
        self.assertEqual(code, 2)
        self.assertEqual(len(list(csv.reader(io.StringIO(text)))), 1)
        self.assertNotIn("previous complete export", text)

    def test_partial_export_returns_two_and_keeps_valid_homes(self):
        code, text = self.run_cli([{"type": "listing", "zpid": "123", "price": 500000},
                                   {"type": "error", "task": "invalid", "error": "NOT_FOUND"}])
        self.assertEqual(code, 2)
        self.assertEqual(list(csv.DictReader(io.StringIO(text)))[0]["zpid"], "123")

    def test_successful_empty_and_nonempty_results_return_zero(self):
        for rows in [[], [{"type": "listing", "zpid": "123"}]]:
            with self.subTest(rows=rows):
                code, text = self.run_cli(rows)
                self.assertEqual(code, 0)
                self.assertEqual(len(list(csv.DictReader(io.StringIO(text)))), len(rows))

    def test_failed_run_preserves_previous_export(self):
        code, text = self.run_cli([], status="FAILED")
        self.assertIsInstance(code, SystemExit)
        self.assertEqual(text, "previous complete export")

    def test_interrupted_download_preserves_previous_export(self):
        code, text = self.run_cli([{"zpid": "123"}], interrupted=True)
        self.assertIsInstance(code, ConnectionError)
        self.assertEqual(text, "previous complete export")


if __name__ == "__main__":
    unittest.main()
