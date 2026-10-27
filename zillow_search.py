"""Zillow homes for sale, for rent or recently sold in any city, ZIP code or neighborhood, saved to CSV.

    python zillow_search.py "Austin, TX" 78704 --status forSale --max 200 --out austin.csv
"""
import argparse
import csv
import os
import sys

from apify_client import ApifyClient

ACTOR = "sauliusautomatesit/zillow-scraper-api"
COLUMNS = [
    "zpid", "status", "price", "zestimate", "rentZestimate", "address", "city", "state", "zipcode",
    "bedrooms", "bathrooms", "livingArea", "homeType", "daysOnZillow", "dateSold", "latitude", "longitude",
    "url", "searchInput",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("places", nargs="+", help='cities, ZIP codes, neighborhoods or counties, e.g. "Austin, TX" 78704')
    parser.add_argument("--status", choices=["forSale", "forRent", "sold"], default="forSale")
    parser.add_argument("--max", type=int, default=100, help="most homes per place (default 100)")
    parser.add_argument("--min-price", type=int)
    parser.add_argument("--max-price", type=int, help="for rentals this is the monthly rent")
    parser.add_argument("--min-beds", type=int)
    parser.add_argument("--details", action="store_true",
                        help="open every home for the full property row (Zestimate range, description, facts)")
    parser.add_argument("--out", default="zillow.csv")
    parser.add_argument("--max-spend", type=float, default=1.0, help="hard spending cap for the run in USD (default 1)")
    args = parser.parse_args()

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("Set APIFY_TOKEN first (Apify Console > Settings > API & Integrations).")

    run_input = {
        "locations": args.places,
        "status": args.status,
        "maxResultsPerSearch": args.max,
        "includeDetails": args.details,
        "maxPhotos": 0,
    }
    for key, value in (("minPrice", args.min_price), ("maxPrice", args.max_price), ("minBeds", args.min_beds)):
        if value is not None:
            run_input[key] = value

    client = ApifyClient(token)
    run = client.actor(ACTOR).call(run_input=run_input, max_total_charge_usd=args.max_spend, logger=None)
    if run is None or run.status != "SUCCEEDED":
        sys.exit(f"Run did not succeed: {run.status if run else 'not found'}")

    homes, problems = [], []
    for item in client.dataset(run.default_dataset_id).iterate_items():
        if item.get("type") == "error":
            problems.append(item)
            continue
        # Search rows carry "address"; full property rows split it into streetAddress, city, state, zipcode.
        item.setdefault("address", item.get("streetAddress"))
        item.setdefault("searchInput", item.get("input"))
        homes.append(item)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(homes)

    print(f"{len(homes)} homes saved to {args.out}")
    for p in problems:
        print(f"no result for {p.get('task')}: {p.get('error')}")


if __name__ == "__main__":
    main()
