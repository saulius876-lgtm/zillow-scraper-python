"""Export new listings and observed price/status changes from repeated Zillow searches.

The Actor remembers prior results in your Apify account. A local journal resumes
an unfinished export before starting another run. Python 3.10+, apify-client 3.x.
"""
import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ACTOR = "sauliusautomatesit/zillow-scraper-api"
ACTOR_ID = "Q9ElvXWKQ1SYQeJuv"
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
COLUMNS = ["changeKind", "changeType", "zpid", "address", "price", "previousPrice",
           "priceChange", "status", "previousStatus", "firstSeenAt", "previousSeenAt", "url"]


def positive_int(value):
    value = int(value)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def monitor_key(run_input):
    # This is a local lock/journal identity, not a recreation of the Actor's store hash.
    # A named store always shares a journal, even if mode, cap or output is changed.
    own = re.sub(r"[^a-z0-9-]+", "-", str(run_input.get("storeName") or "").strip().lower()).strip("-")[:63]
    if own:
        identity = {"storeName": own}
    else:
        identity = {key: run_input[key] for key in ("status", "sortBy", "maxPrice")
                    if key in run_input}
        places = run_input.get("locations") or []
        if isinstance(places, str):
            places = places.splitlines()
        identity["locations"] = sorted({str(place).strip() for place in places if str(place).strip()})
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def state_path_for_input(run_input, explicit_path=None):
    return Path(explicit_path) if explicit_path else Path(".zillow-monitor-state") / (monitor_key(run_input) + ".json")


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_state(path, state):
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def StateLock(path):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise RuntimeError(f"State is locked: {lock}. Stop the other process first. After a crash, confirm it has stopped before removing this lock.") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(f"pid={os.getpid()}\n")
        yield path
    finally:
        lock.unlink()


def load_state(path, run_input):
    key = monitor_key(run_input)
    if not path.exists():
        return {"schemaVersion": 1, "actor": ACTOR, "monitorKey": key,
                "input": run_input, "pending": None, "lastExport": None}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("schemaVersion") != 1 or state.get("actor") != ACTOR:
        raise ValueError("Invalid state or different Actor/version; restore the journal before continuing.")
    if state.get("monitorKey") != key or not isinstance(state.get("input"), dict):
        raise ValueError("State belongs to a different monitor; use its original inputs or a separate state path.")
    if "pending" not in state:
        raise ValueError("Invalid state: missing pending field. Restore the journal before continuing.")
    pending = state["pending"]
    if pending is not None:
        if not isinstance(pending, dict) or pending.get("phase") not in {"starting", "accepted"}:
            raise ValueError("Invalid pending state; restore the journal before continuing.")
        run_id = pending.get("runId")
        if (pending["phase"] == "starting" and run_id is not None) or (pending["phase"] == "accepted" and not isinstance(run_id, str)) or run_id == "":
            raise ValueError("Invalid pending run ID; restore the journal before continuing.")
    return state


def make_client(token):
    from apify_client import ApifyClient
    # Retrying an uncertain POST can create a second run. Reconciliation is explicit.
    return ApifyClient(token, max_retries=0)


def status_of(run):
    status = run.status
    return str(getattr(status, "value", status))


def verified_run(client, run_id):
    run = client.run(run_id).get()
    if run is None or run.act_id != ACTOR_ID or run.id != run_id:
        raise ValueError("Recovery run was not found or does not belong to this Zillow Actor.")
    record = client.key_value_store(run.default_key_value_store_id).get_record("INPUT")
    actual = (record or {}).get("value")
    if not isinstance(actual, dict) or not (actual.get("monitoringMode") or actual.get("onlyNewSinceLastRun") or actual.get("monitor") == "priceDrops"):
        raise ValueError("Recovery run has no readable monitoring INPUT; inspect it in Console.")
    return run, actual


def input_matches(expected, actual):
    # Extra filters or mode switches can change which monitor state was advanced.
    # A recovery run must be the same request, not merely contain its input fields.
    return expected == actual


def export_run(client, run, run_input, output):
    rows = list(client.dataset(run.default_dataset_id).iterate_items())
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Actor returned invalid dataset rows; pending run preserved for inspection.")
    errors = [row for row in rows if row.get("type") == "error"]
    changes = [row for row in rows if row.get("type") != "error"]
    if any(not row.get("changeType") for row in changes):
        raise ValueError("Actor returned rows without monitoring fields; pending run preserved for inspection.")
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(changes)
    evidence = {"actor": ACTOR, "runId": run.id, "datasetId": run.default_dataset_id,
                "input": run_input, "status": status_of(run), "changeCount": len(changes),
                "errorCount": len(errors)}
    atomic_write_text(output, csv_buffer.getvalue())
    atomic_write_text(output.with_suffix(".run.json"), json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    return evidence, errors


def main(argv=None, *, client_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("places", nargs="*", help='ZIP codes or places, e.g. 78704 or "Austin, TX"')
    parser.add_argument("--mode", choices=["changes", "new", "price-drops"], default="changes")
    parser.add_argument("--status", choices=["forSale", "forRent", "sold"], default="forSale")
    parser.add_argument("--max", type=positive_int, default=200, help="listings checked per search, not the number of changes")
    parser.add_argument("--max-price", type=positive_int)
    parser.add_argument("--store-name", help="optional separate baseline; keep this and the search inputs stable")
    parser.add_argument("--out", default="zillow-changes.csv")
    parser.add_argument("--max-spend", type=float, default=1.0)
    parser.add_argument("--state", help="optional journal path; use the same path for the same monitor")
    parser.add_argument("--resume-run", help="export an existing run without starting another Actor run")
    args = parser.parse_args(argv)
    if not args.places and not args.resume_run:
        parser.error("Give at least one place, or use --resume-run RUN_ID to recover an existing run.")
    if not 0 < args.max_spend <= 100:
        parser.error("--max-spend must be greater than zero and at most 100 USD")
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        parser.error("Set APIFY_TOKEN (Apify Console > Settings > API & Integrations).")
    run_input = None
    if args.places:
        run_input = {"locations": args.places, "status": args.status, "sortBy": "newest",
                     "maxResultsPerSearch": args.max, "includeDetails": False, "maxPhotos": 0}
        run_input.update({"changes": {"monitoringMode": True},
                          "new": {"onlyNewSinceLastRun": True},
                          "price-drops": {"monitor": "priceDrops"}}[args.mode])
        if args.max_price is not None:
            run_input["maxPrice"] = args.max_price
        if args.store_name:
            run_input["storeName"] = args.store_name
    try:
        client = (client_factory or make_client)(token)
        recovery_run = None
        actual_input = None
        if args.resume_run:
            recovery_run, actual_input = verified_run(client, args.resume_run)
            if run_input is not None and not input_matches(run_input, actual_input):
                raise ValueError("Recovery run INPUT does not match the requested monitor inputs.")
            if run_input is None:
                run_input = actual_input
        state_path = state_path_for_input(run_input, args.state)
        output = Path(args.out).resolve()
        protected = {state_path.resolve(), state_path.resolve().with_name(state_path.name + ".lock")}
        if output in protected or output.with_suffix(".run.json") in protected:
            raise ValueError("Output or receipt would overwrite the state/lock; choose a different output path.")
        with StateLock(state_path) as path:
            state = load_state(path, run_input)
            pending = state["pending"]
            if pending:
                matches = input_matches(state["input"], actual_input) if args.resume_run else state["input"] == run_input
                if not matches:
                    raise ValueError("Pending run input does not match. Resume it with the original inputs before changing this monitor.")
                if args.resume_run and pending.get("runId") not in {None, args.resume_run}:
                    raise ValueError("A different run is pending; export that run before importing another.")
                run_input = state["input"]
            if args.resume_run:
                pending = {"phase": "accepted", "runId": recovery_run.id,
                           "datasetId": recovery_run.default_dataset_id, "status": status_of(recovery_run)}
                print(f"Recovering existing run {recovery_run.id}; state {path}", flush=True)
                state.update(input=run_input, pending=pending)
                save_state(path, state)
            elif pending and not pending.get("runId"):
                raise RuntimeError(f"An earlier Actor start is ambiguous. Find its run in Console and use --resume-run RUN_ID --state {path}. No new run was started. If no run was accepted, remove this journal only after checking Console.")
            elif not pending:
                output.parent.mkdir(parents=True, exist_ok=True)
                pending = {"phase": "starting", "runId": None, "datasetId": None, "status": "STARTING",
                           "submittedAt": datetime.now(timezone.utc).isoformat()}
                state.update(input=run_input, pending=pending)
                save_state(path, state)
                run = client.actor(ACTOR).start(run_input=run_input, max_total_charge_usd=Decimal(str(args.max_spend)))
                if run is None or not run.id:
                    raise RuntimeError("Actor start returned no run ID; the journal remains ambiguous. Inspect Console before retrying.")
                # Print before saving, so even a disk error leaves a manual recovery route.
                print(f"Accepted run {run.id}; state {path}", flush=True)
                pending.update(phase="accepted", runId=run.id, datasetId=run.default_dataset_id, status=status_of(run))
                save_state(path, state)
            run = recovery_run
            if run is None or status_of(run) not in TERMINAL:
                run = client.run(pending["runId"]).wait_for_finish(wait_duration=timedelta(minutes=5))
            if run is None:
                raise RuntimeError("Run could not be read. Rerun the same command to resume; no additional Actor will be started.")
            if run.act_id != ACTOR_ID or run.id != pending["runId"]:
                raise ValueError("Pending run does not belong to this Actor; journal preserved for inspection.")
            pending.update(datasetId=run.default_dataset_id, status=status_of(run))
            save_state(path, state)
            if status_of(run) not in TERMINAL:
                raise RuntimeError("Run is not terminal yet. Rerun the same command to resume it without starting another run.")
            if status_of(run) != "SUCCEEDED" and not args.resume_run:
                raise RuntimeError(f"Run {run.id} ended {status_of(run)}. It may contain partial changes. Use --resume-run {run.id} --state {path} to export those rows; no new run was started.")
            evidence, errors = export_run(client, run, run_input, output)
            # Keep pending until both output files are durably replaced.
            state.update(pending=None, lastExport=evidence)
            save_state(path, state)
            print(f"{evidence['changeCount']} observed changes saved to {output}; run {run.id} ({evidence['status']})")
            if not evidence["changeCount"] and not errors:
                print("No changes returned. Price-drops mode also returns zero when establishing its first baseline.")
            for row in errors:
                print(f"Input problem: {row.get('task') or row.get('input')}: {row.get('error')}", file=sys.stderr)
            if status_of(run) != "SUCCEEDED":
                print(f"Warning: recovered partial results from {status_of(run)} run {run.id}; this is not a complete successful check.", file=sys.stderr)
            return 2 if errors or status_of(run) != "SUCCEEDED" else 0
    except Exception as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
