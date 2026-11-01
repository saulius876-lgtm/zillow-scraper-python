"""Offline regression tests: no network calls and no real Actor starts.

Run from this directory: python -m unittest -v test_monitor_recovery.py
"""
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import zillow_monitor as app


def change(zpid="100"):
    return {"type": "listing", "changeType": "NEW", "changeKind": "newListing",
            "zpid": zpid, "address": "An observed home", "price": 250000,
            "status": "FOR_SALE", "url": "https://www.zillow.com/homedetails/" + zpid + "_zpid/"}


def run_input():
    return {"locations": ["78704"], "status": "forSale", "sortBy": "newest",
            "maxResultsPerSearch": 3, "includeDetails": False, "maxPhotos": 0,
            "monitoringMode": True, "storeName": "recovery-test"}


class FakeClient:
    def __init__(self):
        self.starts = 0
        self.start_error = False
        self.download_error = False
        self.wait_error = False
        self.rows = [change()]
        self.input = run_input()
        self.run_value = SimpleNamespace(id="run-1", status="SUCCEEDED",
                                         act_id="Q9ElvXWKQ1SYQeJuv",
                                         default_dataset_id="dataset-1",
                                         default_key_value_store_id="store-1")
        self.wait_result = self.run_value
        self.requested_runs = []

    def actor(self, name):
        assert name == app.ACTOR
        return self

    def start(self, **kwargs):
        self.starts += 1
        self.input = copy.deepcopy(kwargs["run_input"])
        self.run_value.id = "run-" + str(self.starts)
        if self.start_error:
            raise TimeoutError("simulated ambiguous start response")
        return self.run_value

    def run(self, run_id):
        self.requested_runs.append(run_id)
        return self

    def get(self):
        return self.run_value

    def wait_for_finish(self, **kwargs):
        if self.wait_error:
            raise ConnectionError("simulated wait failure")
        return self.wait_result

    def dataset(self, dataset_id):
        assert dataset_id == "dataset-1"
        return self

    def iterate_items(self):
        for i, row in enumerate(self.rows):
            yield row
            if i == 0 and self.download_error:
                raise ConnectionError("simulated partial dataset download")

    def key_value_store(self, store_id):
        assert store_id == "store-1"
        return self

    def get_record(self, key):
        assert key == "INPUT"
        return {"key": "INPUT", "value": copy.deepcopy(self.input)}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.state = self.folder / "state.json"
        self.out = self.folder / "changes.csv"
        self.client = FakeClient()

    def execute(self, *, out=None, extra=(), resume=None, places=True):
        args = ["78704", "--max", "3", "--store-name", "recovery-test"] if places else []
        args += ["--state", str(self.state), "--out", str(out or self.out)]
        if resume:
            args += ["--resume-run", resume]
        args += list(extra)
        capture = io.StringIO()
        with patch.dict(os.environ, {"APIFY_TOKEN": "offline-test-token"}), redirect_stdout(capture), redirect_stderr(capture):
            code = app.main(args, client_factory=lambda token: self.client)
        self.assertFalse(list(self.folder.glob("*.lock")), "lock must release after the command exits")
        return code, capture.getvalue()

    def state_value(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def test_normal_success_then_intentional_next_poll(self):
        self.assertEqual(self.execute()[0], 0)
        self.assertIsNone(self.state_value()["pending"])
        first = self.out.read_text(encoding="utf-8")
        self.assertIn("newListing", first)
        self.client.rows = []
        second = self.folder / "tomorrow.csv"
        self.assertEqual(self.execute(out=second)[0], 0)
        self.assertEqual(self.client.starts, 2)
        self.assertEqual(self.out.read_text(encoding="utf-8"), first)
        self.assertNotIn("newListing", second.read_text(encoding="utf-8"))

    def test_partial_download_resumes_same_run_even_with_new_output_name(self):
        self.client.rows = [change("100"), change("200")]
        self.client.download_error = True
        self.assertEqual(self.execute()[0], 1)
        self.assertEqual(self.state_value()["pending"]["runId"], "run-1")
        self.assertFalse(self.out.exists())
        self.client.download_error = False
        new_out = self.folder / "recovered.csv"
        self.assertEqual(self.execute(out=new_out)[0], 0)
        self.assertEqual(self.client.starts, 1)
        self.assertIn("100", new_out.read_text(encoding="utf-8"))
        self.assertIn("200", new_out.read_text(encoding="utf-8"))

    def test_csv_failure_preserves_old_file_and_pending_run(self):
        self.out.write_text("previous successful export\n", encoding="utf-8")
        original = app.atomic_write_text
        def broken(path, text):
            if Path(path) == self.out:
                raise OSError("simulated CSV disk failure")
            return original(path, text)
        with patch.object(app, "atomic_write_text", side_effect=broken):
            self.assertEqual(self.execute()[0], 1)
        self.assertEqual(self.out.read_text(encoding="utf-8"), "previous successful export\n")
        self.assertEqual(self.state_value()["pending"]["runId"], "run-1")
        self.assertEqual(self.execute()[0], 0)
        self.assertEqual(self.client.starts, 1)

    def test_receipt_failure_after_csv_still_resumes_same_run(self):
        original = app.atomic_write_text
        def broken(path, text):
            if str(path).endswith(".run.json"):
                raise OSError("simulated receipt disk failure")
            return original(path, text)
        with patch.object(app, "atomic_write_text", side_effect=broken):
            self.assertEqual(self.execute()[0], 1)
        self.assertTrue(self.out.exists())
        self.assertEqual(self.state_value()["pending"]["runId"], "run-1")
        self.assertEqual(self.execute()[0], 0)
        self.assertEqual(self.client.starts, 1)

    def test_ambiguous_start_blocks_automatic_retry_then_manual_recovery(self):
        self.client.start_error = True
        self.assertEqual(self.execute()[0], 1)
        self.client.start_error = False
        code, message = self.execute()
        self.assertEqual(code, 1)
        self.assertIn("ambiguous", message.lower())
        self.assertEqual(self.client.starts, 1)
        self.assertEqual(self.execute(resume="run-1", places=False)[0], 0)
        self.assertEqual(self.client.starts, 1)

    def test_ambiguous_recovery_rejects_run_with_extra_search_filter(self):
        self.client.start_error = True
        self.assertEqual(self.execute()[0], 1)
        self.client.start_error = False
        self.client.input["minPrice"] = 500000
        code, message = self.execute(resume="run-1", places=False)
        self.assertEqual(code, 1)
        self.assertIn("input", message.lower())
        self.assertIsNone(self.state_value()["pending"]["runId"])
        self.assertEqual(self.client.starts, 1)

    def test_failed_journal_write_after_acceptance_prints_id_and_blocks_retry(self):
        original = app.atomic_write_text
        failed = False
        def broken(path, text):
            nonlocal failed
            if Path(path) == self.state and not failed:
                pending = json.loads(text).get("pending") or {}
                if pending.get("runId"):
                    failed = True
                    raise OSError("simulated accepted-run journal write failure")
            return original(path, text)
        with patch.object(app, "atomic_write_text", side_effect=broken):
            code, message = self.execute()
        self.assertEqual(code, 1)
        self.assertIn("run-1", message)
        self.assertEqual(self.execute()[0], 1)
        self.assertEqual(self.client.starts, 1)
        self.assertEqual(self.execute(resume="run-1", places=False)[0], 0)
        self.assertEqual(self.client.starts, 1)

    def test_pending_wait_resumes_without_another_start(self):
        self.client.run_value.status = "RUNNING"
        self.assertEqual(self.execute()[0], 1)
        self.assertEqual(self.state_value()["pending"]["runId"], "run-1")
        self.client.run_value.status = "SUCCEEDED"
        self.assertEqual(self.execute()[0], 0)
        self.assertEqual(self.client.starts, 1)

    def test_wait_network_failure_keeps_accepted_run(self):
        self.client.wait_error = True
        self.assertEqual(self.execute()[0], 1)
        self.client.wait_error = False
        self.assertEqual(self.execute()[0], 0)
        self.assertEqual(self.client.starts, 1)

    def test_malformed_journal_never_starts_or_overwrites_state(self):
        for text in ["{not json", '{"schemaVersion": 999}', '{"schemaVersion": 1, "pending": "broken"}']:
            with self.subTest(text=text):
                self.state.write_text(text, encoding="utf-8")
                self.assertEqual(self.execute()[0], 1)
                self.assertEqual(self.state.read_text(encoding="utf-8"), text)
        self.assertEqual(self.client.starts, 0)

    def test_exclusive_lock_refuses_second_writer(self):
        # Do not use execute() here: its postcondition expects the outer lock to be gone.
        with app.StateLock(self.state):
            with patch.dict(os.environ, {"APIFY_TOKEN": "offline-test-token"}), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = app.main(["78704", "--max", "3", "--store-name", "recovery-test",
                                 "--state", str(self.state), "--out", str(self.out)],
                                client_factory=lambda token: self.client)
            self.assertEqual(code, 1)
            self.assertEqual(self.client.starts, 0)
        self.assertEqual(self.execute()[0], 0)

    def test_changed_input_cannot_bypass_pending_export(self):
        self.client.download_error = True
        self.assertEqual(self.execute()[0], 1)
        self.client.download_error = False
        self.assertEqual(self.execute(extra=["--max", "4"])[0], 1)
        self.assertEqual(self.client.starts, 1)
        self.assertEqual(self.execute()[0], 0)

    def test_existing_run_export_never_starts_actor(self):
        self.assertEqual(self.execute(resume="run-1", places=False)[0], 0)
        self.assertEqual(self.client.starts, 0)
        receipt = json.loads(self.out.with_suffix(".run.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["runId"], "run-1")
        self.assertEqual(receipt["status"], "SUCCEEDED")

    def test_wrong_actor_recovery_is_rejected_without_start(self):
        self.client.run_value.act_id = "some-other-actor"
        self.assertEqual(self.execute(resume="run-1", places=False)[0], 1)
        self.assertEqual(self.client.starts, 0)
        self.assertFalse(self.out.exists())

    def test_failed_run_requires_manual_export_and_preserves_status(self):
        self.client.run_value.status = "FAILED"
        self.assertEqual(self.execute()[0], 1)
        self.assertEqual(self.execute()[0], 1)
        self.assertEqual(self.client.starts, 1)
        self.assertEqual(self.execute(resume="run-1", places=False)[0], 2)
        receipt = json.loads(self.out.with_suffix(".run.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "FAILED")
        self.assertEqual(self.client.starts, 1)

    def test_error_rows_are_reported_without_becoming_listing_changes(self):
        self.client.rows = [change(), {"type": "error", "error": "Unknown place", "input": "unknown"}]
        self.assertEqual(self.execute()[0], 2)
        receipt = json.loads(self.out.with_suffix(".run.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["changeCount"], 1)
        self.assertEqual(receipt["errorCount"], 1)
        self.assertIsNone(self.state_value()["pending"])

    def test_missing_monitor_fields_keep_pending_for_inspection(self):
        self.client.rows = [{"type": "listing", "zpid": "100"}]
        self.assertEqual(self.execute()[0], 1)
        self.assertEqual(self.state_value()["pending"]["runId"], "run-1")
        self.assertFalse(self.out.exists())

    def test_default_state_identity_does_not_change_with_mode_or_scan_cap(self):
        first = run_input()
        second = run_input()
        second["maxResultsPerSearch"] = 20
        second.pop("monitoringMode")
        second["monitor"] = "priceDrops"
        self.assertEqual(app.state_path_for_input(first), app.state_path_for_input(second))
        first.pop("storeName")
        second.pop("storeName")
        self.assertEqual(app.state_path_for_input(first), app.state_path_for_input(second))

    def test_native_equivalent_named_stores_share_default_journal(self):
        first, second = run_input(), run_input()
        first["storeName"] = "  My Feed!  "
        second["storeName"] = "my-feed"
        self.assertEqual(app.state_path_for_input(first), app.state_path_for_input(second))
        first["storeName"] = "x" * 63 + "-one"
        second["storeName"] = "x" * 63 + "-two"
        self.assertEqual(app.state_path_for_input(first), app.state_path_for_input(second))

    def test_unnamed_monitor_identity_ignores_location_order_and_surrounding_spaces(self):
        first, second = run_input(), run_input()
        first.pop("storeName")
        second.pop("storeName")
        first["locations"] = ["78704", "Denver, CO"]
        second["locations"] = [" Denver, CO ", "78704"]
        self.assertEqual(app.state_path_for_input(first), app.state_path_for_input(second))

    def test_store_name_that_normalizes_empty_uses_unnamed_identity(self):
        first, second = run_input(), run_input()
        first["storeName"] = " !!! "
        second.pop("storeName")
        self.assertEqual(app.state_path_for_input(first), app.state_path_for_input(second))

    def test_atomic_replace_failure_keeps_original_and_removes_temp_file(self):
        self.out.write_text("old", encoding="utf-8")
        with patch.object(app.os, "replace", side_effect=OSError("simulated replacement failure")):
            with self.assertRaises(OSError):
                app.atomic_write_text(self.out, "new")
        self.assertEqual(self.out.read_text(encoding="utf-8"), "old")
        self.assertEqual(list(self.folder.iterdir()), [self.out])


if __name__ == "__main__":
    unittest.main()
