# Track Zillow listing changes from Python

Run the same search every day and save only the listings that are new to your monitor or whose price/status changed. This uses the existing [Zillow Scraper API](https://apify.com/sauliusautomatesit/zillow-scraper-api), which remembers results in your Apify account. I build and maintain that paid Actor.

## Try it twice

After following the repository's Python setup and setting `APIFY_TOKEN`:

```bash
python zillow_monitor.py 78704 --max 20 --out first-run.csv
python zillow_monitor.py 78704 --max 20 --out second-run.csv
```

The first run establishes the baseline and reports the observed homes as `newListing`. That means **first seen by this monitor**, not necessarily listed today. The second run skips homes that have not changed. Each export has a companion `.run.json` file with the run ID and input, so you can inspect the original run.

For a daily routine, run the same command again tomorrow, using a new output filename. Keep the locations, filters and limit stable. To have Apify run it on a schedule, save this JSON as an Actor Task in Console and create a daily schedule for the task:

```json
{
  "locations": ["78704"],
  "status": "forSale",
  "sortBy": "newest",
  "maxResultsPerSearch": 200,
  "includeDetails": false,
  "monitoringMode": true
}
```

Set the task's maximum charge per run in Console. The Python script sets its own cap with `--max-spend` (default $1). Don't run the same monitor concurrently.

## Choose the feed

```bash
# Only listings this monitor has not observed before
python zillow_monitor.py 78704 --mode new --max 200 --out new-homes.csv

# Only observed price cuts; the first run saves a baseline and returns no rows
python zillow_monitor.py 78704 --mode price-drops --max 200 --out price-cuts.csv

# Rentals under $2,500; remember to repeat the same search settings
python zillow_monitor.py "Denver, CO" --status forRent --max-price 2500 --out rental-changes.csv
```

Use `--store-name my-separate-monitor` only when you intentionally want a separate baseline. Never reuse the same named store for unrelated searches. Leaving it out lets the Actor derive the store from the search.

## What the export means

The CSV includes `changeKind`, current and previous prices, the price difference, current and previous statuses, first/previous seen times, address and source URL.

The limit is the number of listings **checked**, not a guarantee of complete market coverage. A cap of 200 may miss changes on older homes outside the newest 200. An absent row does not prove a home sold or was removed; sale observations need a separate `--status sold` monitor. Empty results in price-drops mode are expected on the first run. The script reports input errors separately and exits with code 2 when they occur.

## Cost

The script requests search listings without full details. At the live free-plan rate checked 31 October 2026, emitted listings cost **$0.0015 each ($1.50 per 1,000)**. Unchanged homes are skipped without a listing charge. Actor start (`apify-actor-start`) is **$0.00005 per GB of run memory**; paid-plan discounts apply. The live checks and pricing are recorded in [VALIDATION.md](VALIDATION.md). Check the Actor's Pricing tab before scaling up.

This example exports files. It does not send notifications or set up a schedule for you.
