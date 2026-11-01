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

## Recovery checks — 1 November 2026

The recovery update was checked with Python and `apify-client` 3.4.0 against the unchanged live Actor build **0.1.38**.

| Check | Result |
|---|---|
| Export an existing successful monitoring run | Recovered all 3 rows; no new Actor start |
| New bounded live run with an injected download interruption | Accepted 1 Actor run; stopped after the first downloaded row and retained the pending run |
| Retry that interrupted export with a different `--out` filename | Recovered all 3 rows from the same run; total Actor starts remained 1; journal cleared and lock released |
| Offline failure and recovery suite | 22 tests passed |
| `--help` without importing the SDK | Passed |

The instrumented fresh test used ZIP 78704, a new named baseline, 3 listings, a $0.02 maximum customer charge, a 120-second Actor timeout and 1,024 MB memory. The injected error interrupted the client download after the Actor had succeeded. It did not alter the Actor or cause a second monitoring run.

The offline suite checks ambiguous starts, a failed journal write after acceptance, partial downloads, CSV and receipt write failures, interrupted waits, malformed journals, exclusive locking, recovery input/Actor validation, failed-run partial exports, unchanged normal next-poll behavior, and equivalent monitor names sharing a journal. Run it with:

```bash
python -m unittest -v test_monitor_recovery.py
```

CSV and receipt files are replaced separately. An incomplete export retains the pending run so the command can recover it before starting another check. Recovery still depends on keeping the local journal and the original remote run input and dataset. This is a local client lock, not coordination across different journal paths, computers or separate Console schedules.

The fresh check validates start, wait and interrupted-download recovery. It does not establish later real price-cut delivery, complete market coverage, resilience to every possible filesystem failure, or long-term monitoring reliability.

Live pricing rechecked 1 November: listing $0.0015, property $0.002 on the free plan; Actor start $0.00005 per GB, minimum one event. Prices and the deployed Actor were unchanged by this wrapper repair.

