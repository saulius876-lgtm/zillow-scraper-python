"""Zestimate, rent Zestimate, tax value and last sale for a list of street addresses, Zillow links or zpids.

    python zestimates.py addresses.txt --out zestimates.csv

The input file has one address, Zillow link or zpid per line.
"""
import argparse
import csv
import json
import os
import sys

from apify_client import ApifyClient

ACTOR = "sauliusautomatesit/zillow-scraper-api"
COLUMNS = [
    "input", "matchedAddress", "zpid", "zestimate", "zestimateLow", "zestimateHigh", "rentZestimate",
    "taxAssessedValue", "lastSoldPrice", "price", "status", "bedrooms", "bathrooms", "livingArea", "lotSize",
    "yearBuilt", "homeType", "county", "url",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", help="text file with one address, Zillow link or zpid per line")
    parser.add_argument("--history", action="store_true", help="add price history, tax history and schools (JSON columns)")
    parser.add_argument("--out", default="zestimates.csv")
    parser.add_argument("--max-spend", type=float, default=1.0, help="hard spending cap for the run in USD (default 1)")
    args = parser.parse_args()

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("Set APIFY_TOKEN first (Apify Console > Settings > API & Integrations).")

    with open(args.file, encoding="utf-8") as f:
        properties = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    if not properties:
        sys.exit(f"{args.file} has no addresses.")

    client = ApifyClient(token)
    run = client.actor(ACTOR).call(
        run_input={"properties": properties, "includeHistory": args.history, "maxPhotos": 0},
        max_total_charge_usd=args.max_spend,
        logger=None,  # set to "default" to stream the run log
    )
    if run is None or run.status != "SUCCEEDED":
        sys.exit(f"Run did not succeed: {run.status if run else 'not found'}")

    columns = COLUMNS + (["priceHistory", "taxHistory"] if args.history else [])
    found, missing = [], []
    for item in client.dataset(run.default_dataset_id).iterate_items():
        if item.get("type") == "error":
            missing.append(item)
            continue
        for key in ("priceHistory", "taxHistory"):
            if item.get(key) is not None:
                item[key] = json.dumps(item[key])
        found.append(item)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(found)

    print(f"{len(found)} of {len(properties)} properties saved to {args.out}")
    for m in missing:
        print(f"not found: {m.get('task')}: {m.get('error')}")


if __name__ == "__main__":
    main()
