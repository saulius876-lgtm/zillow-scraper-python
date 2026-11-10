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
import zestimates


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



class AddressHistoryTests(unittest.TestCase):
    def export(self, rows, history=True):
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
            source = Path(directory) / "addresses.txt"
            source.write_text("123 Test St", encoding="utf-8")
            output = Path(directory) / "homes.csv"
            args = ["zestimates.py", str(source), "--out", str(output), "--max-spend", "0.01"]
            if history:
                args.append("--history")
            with patch.object(zestimates, "ApifyClient", return_value=client), \
                    patch.object(sys, "argv", args), \
                    patch.dict(os.environ, {"APIFY_TOKEN": "offline-fixture"}), \
                    contextlib.redirect_stdout(io.StringIO()):
                zestimates.main()
            with output.open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                return reader.fieldnames, list(reader), client

    def test_school_and_existing_histories_survive_json_csv_roundtrip(self):
        row = {"zpid": "123", "schools": [{"name": 'School, "École"\nNorth', "rating": 8,
                                           "grades": ["K", "5"], "distance": 0.5}],
               "priceHistory": [{"price": 100}], "taxHistory": [{"value": 90}]}
        columns, rows, client = self.export([row])
        self.assertEqual(columns, zestimates.COLUMNS + ["priceHistory", "taxHistory", "schools"])
        for key in ("priceHistory", "taxHistory", "schools"):
            self.assertEqual(json.loads(rows[0][key]), row[key])
        self.assertTrue(client.call_args["run_input"]["includeHistory"])
        self.assertEqual(client.call_args["max_total_charge_usd"], 0.01)

    def test_absent_null_and_empty_school_data_remain_distinct(self):
        _, rows, _ = self.export([{"zpid": "1"}, {"zpid": "2", "schools": None},
                                  {"zpid": "3", "schools": []}])
        self.assertEqual([row["schools"] for row in rows], ["", "", "[]"])

    def test_default_export_keeps_original_columns(self):
        columns, rows, client = self.export([{"zpid": "123", "schools": [{"name": "School"}]}], history=False)
        self.assertEqual(columns, zestimates.COLUMNS)
        self.assertNotIn("schools", rows[0])
        self.assertFalse(client.call_args["run_input"]["includeHistory"])

    def test_partial_lookup_retains_schools_for_successful_address(self):
        _, rows, _ = self.export([{"type": "error", "error": "NOT_FOUND", "task": "bad address"},
                                  {"type": "property", "zpid": "123", "schools": [{"name": "School"}]}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["schools"]), [{"name": "School"}])




class AddressStatusTests(unittest.TestCase):
    def run_cli(self, rows, status="SUCCEEDED", interrupted=False, entrypoint=False):
        class Client:
            def actor(self, name): return self
            def call(self, **kwargs):
                return types.SimpleNamespace(status=status, default_dataset_id="fixture")
            def dataset(self, dataset_id): return self
            def iterate_items(self):
                yield from [dict(row) for row in rows]
                if interrupted: raise ConnectionError("interrupted download")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "addresses.txt"
            source.write_text("123 Test St\ninvalid address\n", encoding="utf-8")
            output = Path(directory) / "homes.csv"
            output.write_text("previous complete export")
            argv = ["zestimates.py", str(source), "--out", str(output), "--history"]
            with patch.object(zestimates, "ApifyClient", return_value=Client()), \
                    patch.object(sys, "argv", argv), \
                    patch.dict(os.environ, {"APIFY_TOKEN": "offline-fixture"}), \
                    contextlib.redirect_stdout(io.StringIO()):
                try:
                    if entrypoint:
                        import runpy
                        sdk = types.ModuleType("apify_client")
                        sdk.ApifyClient = lambda token: Client()
                        with patch.dict(sys.modules, {"apify_client": sdk}):
                            runpy.run_path(zestimates.__file__, run_name="__main__")
                        code = None
                    else:
                        code = zestimates.main()
                except (SystemExit, ConnectionError) as exc:
                    code = exc.code if isinstance(exc, SystemExit) else exc
            return code, output.read_text()

    def test_error_only_returns_two_and_replaces_stale_export_with_header(self):
        code, text = self.run_cli([{"type": "error", "task": "invalid", "error": "NOT_FOUND"}])
        self.assertEqual(code, 2)
        self.assertEqual(len(list(csv.reader(io.StringIO(text)))), 1)
        self.assertNotIn("previous complete export", text)

    def test_partial_returns_two_preserving_property_and_histories(self):
        row = {"type": "property", "zpid": "123", "priceHistory": [], "taxHistory": [{"value": 90}], "schools": []}
        code, text = self.run_cli([row, {"type": "error", "task": "invalid", "error": "NOT_FOUND"}])
        self.assertEqual(code, 2)
        exported = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["zpid"], "123")
        for key in ("priceHistory", "taxHistory", "schools"):
            self.assertEqual(json.loads(exported[0][key]), row[key])

    def test_success_and_empty_dataset_return_zero(self):
        for rows in [[], [{"type": "property", "zpid": "123"}]]:
            with self.subTest(rows=rows):
                code, text = self.run_cli(rows)
                self.assertEqual(code, 0)
                self.assertEqual(len(list(csv.DictReader(io.StringIO(text)))), len(rows))

    def test_failed_run_preserves_previous_export(self):
        code, text = self.run_cli([], status="FAILED")
        self.assertIn("Run did not succeed", code)
        self.assertEqual(text, "previous complete export")

    def test_interrupted_download_preserves_previous_export(self):
        code, text = self.run_cli([{"zpid": "123"}], interrupted=True)
        self.assertIsInstance(code, ConnectionError)
        self.assertEqual(text, "previous complete export")

    def test_process_entrypoint_propagates_partial_exit_status(self):
        code, _ = self.run_cli([{"type": "error", "error": "NOT_FOUND"}], entrypoint=True)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
