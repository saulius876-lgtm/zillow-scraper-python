# Zillow scraper in Python: listings, sold homes and Zestimates to CSV

Two small Python scripts that pull Zillow data without a Zillow API key, cookies or a browser:

- **`zillow_search.py`**: every home for sale, for rent or recently sold in a city, ZIP code,
  neighborhood or county, with price, Zestimate, rent Zestimate, beds, baths, size and days on Zillow.
- **`zestimates.py`**: the Zestimate, its low and high range, rent Zestimate, tax assessed value
  and last sale price for a list of street addresses, Zillow links or zpids.

Zillow shut down its public API, and its website caps every search at about 820 homes and pushes back
on repeated requests. Both scripts call the
[Zillow Scraper API](https://apify.com/sauliusautomatesit/zillow-scraper-api) on Apify, which handles
the place lookup, paging, map splitting past the 820 cap and retries, and returns plain JSON.
Disclosure: I build and maintain that Actor. It is pay per result (prices below), and you can cap
what each run spends.

## Setup

```bash
git clone https://github.com/saulius876-lgtm/zillow-scraper-python
cd zillow-scraper-python
pip install -r requirements.txt
export APIFY_TOKEN=your_token   # Windows PowerShell: $env:APIFY_TOKEN="your_token"
```

The token is in [Apify Console](https://console.apify.com/settings/integrations) under
Settings > API & Integrations. A free Apify account works.

## Homes for sale in a ZIP code

```bash
python zillow_search.py 78704 --status forSale --max 20 --max-price 700000 --out austin.csv
```

```text
20 homes saved to austin.csv
```

Zillow had 153 homes for that search; raise `--max` to get them all.

Output from a real run on 27 Oct 2026 (first rows):

| zpid | status | price | zestimate | rentZestimate | address | beds | baths | livingArea | daysOnZillow |
|---|---|---:|---:|---:|---|---:|---:|---:|---:|
| 58312113 | FOR_SALE | 549999 | | | 1011 Brodie St APT 15, Austin, TX 78704 | 2 | 2 | 1362 | 137 |
| 29462091 | FOR_SALE | 525000 | | | 2403 Little John Ln, Austin, TX 78704 | 3 | 1 | 1269 | 111 |
| 29476276 | FOR_SALE | 699000 | 680000 | 3021 | 2506 E Side Dr, Austin, TX 78704 | 3 | 2 | 1094 | 12 |

The CSV also has city, state, zipcode, home type, sold date, latitude, longitude and the Zillow link.
Some homes have no Zestimate (Zillow leaves it out on some listings and in some states); the column
is then empty.

More examples:

```bash
# Rentals under $2,500 a month with at least 2 bedrooms
python zillow_search.py "Denver, CO" --status forRent --max-price 2500 --min-beds 2

# Homes sold recently in two neighborhoods
python zillow_search.py "Capitol Hill, Seattle, WA" "Ballard, Seattle, WA" --status sold --max 500

# Full property rows (Zestimate range, description, facts, agent) for every result
python zillow_search.py 98109 --details --max 50
```

A place Zillow does not know comes back as a free error row instead of another city's homes:

```text
no result for search "Nowhereville, ZZ": NOT_FOUND: no such place, property or address on Zillow
```

## Zestimates for a list of addresses

Put one address, Zillow link or zpid per line in a text file (see `addresses.txt`):

```text
2114 Bigelow Ave N, Seattle, WA 98109
https://www.zillow.com/homedetails/48841077_zpid/
```

```bash
python zestimates.py addresses.txt --out zestimates.csv
```

```text
2 of 2 properties saved to zestimates.csv
```

| input | zestimate | zestimateLow | zestimateHigh | rentZestimate | taxAssessedValue | lastSoldPrice | yearBuilt |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2114 Bigelow Ave N, Seattle, WA 98109 | 2137400 | 1923660 | 2372514 | 6182 | 2044000 | 995000 | 1924 |
| https://www.zillow.com/homedetails/48841077_zpid/ | 1674700 | 1557471 | 1808676 | 6590 | 1218000 | 1675027 | 1914 |

Add `--history` for each home's price history and tax history (stored as JSON in two extra columns).

## Calling it from your own code

The scripts are thin wrappers around one call:

```python
from apify_client import ApifyClient

client = ApifyClient("YOUR_APIFY_TOKEN")
run = client.actor("sauliusautomatesit/zillow-scraper-api").call(
    run_input={
        "locations": ["78704"],          # cities, ZIP codes, neighborhoods, counties
        "status": "forSale",             # forSale, forRent or sold
        "maxResultsPerSearch": 200,
        "properties": ["2114 Bigelow Ave N, Seattle, WA 98109"],  # optional: addresses, links, zpids
    },
    max_total_charge_usd=1,              # the run stops charging at $1
)
for home in client.dataset(run.default_dataset_id).iterate_items():
    print(home.get("price"), home.get("zestimate"), home.get("address") or home.get("streetAddress"))
```

This is `apify-client` 3.x, where `call()` returns a `Run` object. On 1.x and 2.x use
`run["defaultDatasetId"]`.

Without Python, the same input works over plain HTTP and returns the rows in one response:

```bash
curl -X POST "https://api.apify.com/v2/acts/sauliusautomatesit~zillow-scraper-api/run-sync-get-dataset-items" \
  -H "Authorization: Bearer $APIFY_TOKEN" -H "Content-Type: application/json" \
  -d '{"locations": ["78704"], "status": "forSale", "maxResultsPerSearch": 20}'
```

The Actor also takes Zillow search links with your own filters (`searchUrls`), price, bed, bath,
size, year built and home type filters, listing types such as FSBO, foreclosures and auctions, and
apartment building units. The full input is on the
[Actor page](https://apify.com/sauliusautomatesit/zillow-scraper-api).

## What it costs

Pay per result, no subscription. Prices on 27 Oct 2026, Apify free plan:

| Row | Price |
|---|---|
| Listing from a search | $0.0015 ($1.50 per 1,000) |
| Property with full details (also each `--details` or `zestimates.py` row) | $0.002 ($2 per 1,000) |
| Run start | $0.00005 |

Apify's paid plans pay 5% to 15% less. Places and addresses that give nothing come back as free
error rows. Both scripts pass `--max-spend` (default $1) as a hard cap on each run.

## Notes

The data is what Zillow shows to any logged-out visitor. Use it in line with Zillow's terms and the
privacy and fair housing laws that apply to you. Questions or a home the Actor gets wrong: open an
issue here or on the Actor's Issues tab.

MIT licensed.
