# Upwork-VM-workflow

Containerized Upwork scraper that writes results to a Google Sheet and pings an
n8n webhook on completion. Designed to run as a daily cron job on a Contabo VM.

```
host cron -> docker run --rm -> scrape -> enrich -> Google Sheets -> n8n webhook
```

## Architecture at a glance

- **Scraper**: [upwork_analysis](https://github.com/Yazan-Sharaya/upwork_analysis) v1.0.0
  (Selenium / undetected-chromedriver under the hood).
- **Enrichment**: continent mapping, StartUp/Valuation regex flags, word-count
  buckets, and `StableEncoder` integer codes that persist across runs via
  `state/label_mappings.json`.
- **Sink**: one master tab in Google Sheets, dedup-on-append against the
  `description` column.
- **Notifier**: POST to an n8n webhook with a run summary; n8n picks up from
  there.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate   # or .\.venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env                                # then fill values

# Provide a GCP service-account JSON at the path in GOOGLE_APPLICATION_CREDENTIALS
mkdir -p secrets && cp /path/to/sa.json secrets/sa.json

python main.py
```

To shrink the local smoke-test, edit `job_titles.csv` down to a single title /
page and re-run.

## Tests

```bash
pytest tests/ -v
```

`test_sheets_writer.py` mocks gspread, so no Sheets credentials are needed for
CI.

## Docker

```bash
docker build -t upwork-vm:latest .

docker run --rm \
  --env-file .env \
  -v "$PWD/secrets/sa.json:/secrets/sa.json:ro" \
  -v "$PWD/state:/app/state" \
  -v "$PWD/logs:/app/logs" \
  upwork-vm:latest
```

Final image is ~700 MB (chromium dominates). First build downloads several
hundred MB; subsequent builds reuse the apt + pip layers.

## Production deployment

See [deploy/README.md](deploy/README.md) for the Contabo VM setup checklist:
GCP service account, sheet sharing, cron line, log rotation.

## Configuration reference

All settings come from environment variables (`.env` file or `--env-file`).
See [.env.example](.env.example) for the full list and defaults.

Critical variables:

| Variable | Required | Purpose |
|---|---|---|
| `GOOGLE_SHEET_ID` | yes | Spreadsheet ID from the sheet URL |
| `GOOGLE_APPLICATION_CREDENTIALS` | yes | Path to service-account JSON inside container |
| `N8N_WEBHOOK_URL` | no | If blank, n8n notify is skipped |
| `LABEL_MAPPINGS_PATH` | no | Must be on a volume-mounted dir to persist encoder codes |

## Project structure

```
main.py              Phased orchestration
config.py            Env-driven dataclasses
job_scraper.py       Parallel scrape wrapper around upwork_analysis
data_processor.py    Enrichment + StableEncoder
sheets_writer.py     Google Sheets sink (dedup-on-append)
n8n_notifier.py      Webhook POST with run summary
utils.py             JSON logging + retry helpers

job_titles.csv       Search terms + page counts
countries_continents.csv  Continent lookup
state/               Volume-mounted; holds label_mappings.json
tests/               pytest suite (mocks Sheets API)
deploy/              Cron line + VM deployment guide
```
