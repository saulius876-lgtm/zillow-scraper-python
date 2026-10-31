# Monitoring example validation

Checked 31 October 2026 with `apify-client` 3.4.0 against the live Zillow Scraper API build 0.1.36. The exact `zillow_monitor.py` CLI was executed, using ZIP 78704, `forSale`, newest first, a three-listing limit and a $0.05 maximum charge per run.

| Check | Result |
|---|---|
| First `changes` run, fresh named baseline | Succeeded; 3 `newListing` rows with monitoring fields |
| Same command and baseline repeated immediately | Succeeded; 0 rows, unchanged listings skipped |
| First `price-drops` run, separate fresh baseline | Succeeded; 0 rows, as expected while establishing a price baseline |

The CSVs and companion run records were checked. No real price change occurred between these short tests, so this check does not establish later price-cut delivery, complete market coverage or long-term reliability. No schedule or notification integration was created.

Live pricing checked through the Actor API on the same date: search listing $0.0015, full property $0.002 on the free plan; paid-plan discounts 5-15%; Actor start $0.00005 per GB of run memory, minimum one event. These are customer event prices, not owner test-run compute charges. The monitoring example requests search listings without full details.

Source: [Actor details and pricing](https://apify.com/sauliusautomatesit/zillow-scraper-api/pricing).
