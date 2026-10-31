"""Export new listings and observed price/status changes from repeated Zillow searches.

The Actor remembers prior results in your Apify account. Run this command again
with the same inputs to retrieve only changes. Python 3.10+, apify-client 3.x.
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

from apify_client import ApifyClient

ACTOR = "sauliusautomatesit/zillow-scraper-api"
COLUMNS = ["changeKind", "changeType", "zpid", "address", "price", "previousPrice",
           "priceChange", "status", "previousStatus", "firstSeenAt", "previousSeenAt", "url"]


def positive_int(value):
    value = int(value)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("places", nargs="+", help='ZIP codes or places, e.g. 78704 or "Austin, TX"')
    parser.add_argument("--mode", choices=["changes", "new", "price-drops"], default="changes")
    parser.add_argument("--status", choices=["forSale", "forRent", "sold"], default="forSale")
    parser.add_argument("--max", type=positive_int, default=200, help="listings checked per search, not the number of changes")
    parser.add_argument("--max-price", type=positive_int)
    parser.add_argument("--store-name", help="optional separate baseline; keep this and the search inputs stable")
    parser.add_argument("--out", default="zillow-changes.csv")
    parser.add_argument("--max-spend", type=float, default=1.0)
    args = parser.parse_args()
    if not 0 < args.max_spend <= 100:
        parser.error("--max-spend must be greater than zero and at most 100 USD")
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        parser.error("Set APIFY_TOKEN (Apify Console > Settings > API & Integrations).")

    run_input = {"locations": args.places, "status": args.status, "sortBy": "newest",
                 "maxResultsPerSearch": args.max, "includeDetails": False, "maxPhotos": 0}
    run_input.update({"changes": {"monitoringMode": True},
                      "new": {"onlyNewSinceLastRun": True},
                      "price-drops": {"monitor": "priceDrops"}}[args.mode])
    if args.max_price is not None:
        run_input["maxPrice"] = args.max_price
    if args.store_name:
        run_input["storeName"] = args.store_name
    client = ApifyClient(token)
    run = client.actor(ACTOR).call(run_input=run_input, max_total_charge_usd=args.max_spend, logger=None)
    if run is None or run.status != "SUCCEEDED":
        sys.exit(f"Run did not succeed: {run.status if run else 'not found'}")
    rows = list(client.dataset(run.default_dataset_id).iterate_items())
    errors = [row for row in rows if row.get("type") == "error"]
    changes = [row for row in rows if row.get("type") != "error"]
    if any(not row.get("changeType") for row in changes):
        sys.exit("Actor returned rows without monitoring fields; inspect the run before using this export.")
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(changes)
    evidence = {"actor": ACTOR, "runId": run.id, "datasetId": run.default_dataset_id,
                "input": run_input, "changeCount": len(changes), "errorCount": len(errors)}
    output.with_suffix(".run.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"{len(changes)} observed changes saved to {output}; run {run.id}")
    if not changes and not errors:
        print("No changes returned. Price-drops mode also returns zero when establishing its first baseline.")
    for row in errors:
        print(f"Input problem: {row.get('task') or row.get('input')}: {row.get('error')}", file=sys.stderr)
    if errors:
        sys.exit(2)


if __name__ == "__main__":
    main()
