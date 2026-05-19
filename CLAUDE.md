# CLAUDE.md -- Upwork-VM-workflow

> Project bootstrapped 2026-05-19. Ports the scraper from
> `c:/Users/Nino/iCloudDrive/Projects/UpWork Container` (MySQL sink) to a
> containerized Contabo deployment with Google Sheets + n8n.

## Project summary

Daily containerized Upwork scraper. Flow:

```
host cron (Contabo VM) -> docker run --rm
  -> scrape job titles (parallel, upwork_analysis / seleniumbase)
  -> enrich (continent, regex flags, StableEncoder)
  -> append new rows to Google Sheet (dedup on `description`)
  -> POST run summary to n8n webhook
```

The n8n workflow takes it from there; results land in the user's inbox / wherever
n8n is configured to deliver.

## Architecture

```
main.py
  +-- config.py               ScrapingConfig, SheetsConfig, N8nConfig, load_job_titles
  +-- job_scraper.py          ThreadPoolExecutor over upwork_analysis.JobsScraper
  |     +-- data_processor.py  JobDataProcessor (raw dict -> DataFrame + continent)
  +-- data_processor.py       process_upwork_data (regex flags, StableEncoder, encoded cols)
  +-- sheets_writer.py        SheetsWriter (gspread; dedup-on-append; header bootstrap)
  +-- n8n_notifier.py         notify_n8n (POST summary; never raises)
  +-- utils.py                JSONFormatter, configure_logging, exponential_backoff,
                              retry_with_backoff
```

State persisted across runs (volume-mounted in the container):
- `state/label_mappings.json` -- StableEncoder integer codes for categorical columns

## Key design decisions

| Decision | Choice | Notes |
|---|---|---|
| Sink | Google Sheets, one master tab, dedup-on-append | n8n consumes the sheet. |
| Scheduling | Host cron -> `docker run --rm` | Container is stateless. |
| Scraper engine | `upwork_analysis` v1.0.0 (GitHub) | MIT-licensed; uses seleniumbase. |
| Auth (Sheets) | Service-account JSON, mounted read-only | Path via `GOOGLE_APPLICATION_CREDENTIALS`. |
| Auth (n8n) | Optional `X-Auth-Token` header | Set `N8N_WEBHOOK_TOKEN` to enable. |
| Dedup key | `description` column | Same as the source MySQL design. |
| Logging | `logging` + optional JSON output | `JSON_LOGS=true` to stream JSON. |
| Encoder persistence | `state/label_mappings.json` | Volume-mounted; never delete or codes reset. |

## Running it

```bash
# Local
cp .env.example .env
mkdir -p secrets && cp /path/to/sa.json secrets/sa.json
pip install -r requirements.txt
python main.py

# Docker (local)
docker build -t upwork-vm:local .
docker run --rm --env-file .env \
  -v $PWD/secrets/sa.json:/secrets/sa.json:ro \
  -v $PWD/state:/app/state \
  upwork-vm:local
```

VM deployment: see [deploy/README.md](deploy/README.md).

## File reference

| File | Purpose |
|---|---|
| `main.py` | Phased orchestration: load -> scrape -> enrich -> sheets -> notify |
| `config.py` | Env-driven dataclasses + `load_job_titles()` |
| `job_scraper.py` | `JobScraper.scrape_jobs_per_title_parallel()` |
| `data_processor.py` | `JobDataProcessor`, `process_upwork_data`, `StableEncoder`, `validate_dataframe` |
| `sheets_writer.py` | `SheetsWriter` -- dedup, header bootstrap, batch append |
| `n8n_notifier.py` | `notify_n8n` -- swallow-all webhook POST |
| `utils.py` | Logging + retry helpers (shared) |
| `job_titles.csv` | Search terms + page counts (legacy / fallback when `JOB_TITLES_SOURCE=csv` or Sheets read fails) |
| Google Sheet "Job Titles" tab | **Primary source** for job titles when `JOB_TITLES_SOURCE=sheet` (default). Same columns as the CSV. Edit it directly in your browser to change what gets scraped — no code or container rebuild needed. |
| `countries_continents.csv` | Continent lookup (`Country`, `Continent` columns) |
| `state/label_mappings.json` | StableEncoder codes; created at runtime, volume-mounted |
| `Dockerfile` | python:3.11-slim + chromium + chromium-driver + seleniumbase |
| `docker-compose.yml` | Local dev convenience |
| `deploy/upwork-vm.cron` | Crontab line for the Contabo VM |
| `deploy/README.md` | End-to-end VM deployment checklist |

## Schema written to the sheet

29 columns, defined in `sheets_writer.COLUMNS`. Order matters because the
writer reindexes against it. The schema mirrors the source project's
"extended" MySQL schema with two differences:

- `extraction_date` (no space). The original used `Extraction Date`.
- No basic/backup tabs; just the one master tab.

Columns: `position`, `title`, `description`, `time`, `skills`, `type`,
`experience_level`, `time_estimate`, `budget`, `proposals`, `client_location`,
`client_jobs_posted`, `client_hire_rate`, `client_hourly_rate`,
`client_total_spent`, `continent`, `extraction_date`, `StartUp`, `Valuation`,
`word_count`, `description_label`, plus eight `_en` columns produced by
`StableEncoder` (`position_en`, `type_en`, `time_estimate_en`,
`experience_level_en`, `client_location_en`, `continent_en`,
`description_label_en`, `proposals_en`).

## Tests

```bash
pytest tests/ -v
```

- `tests/test_data_processor.py` -- ported from source (12 tests).
- `tests/test_sheets_writer.py` -- gspread fully mocked; verifies header bootstrap,
  dedup logic, batch shape, column reindexing.

`tests/conftest.py` redirects `LABEL_MAPPINGS_PATH` to a tmp dir so the
StableEncoder used inside `process_upwork_data()` never touches the real
`state/label_mappings.json`.

## Risks / gotchas

- **`upwork_analysis` is installed from GitHub at v1.0.0**. If upstream
  changes its API the scraper will break -- the pin in `requirements.txt`
  protects against that. Bump intentionally.
- **Chromium / chromedriver drift**: Debian's `chromium` package can move
  ahead of `chromium-driver` between releases. The Dockerfile reinstalls
  via `seleniumbase install chromedriver` to catch up. Rebuild monthly.
- **Sheet header is enforced**: hand-editing row 1 will make
  `SheetsWriter.ensure_header()` raise. Either reset row 1 or update
  `sheets_writer.COLUMNS` and rebuild.
- **`description` as dedup key**: empty/None descriptions are dropped before
  write (can't be deduped). Same constraint as the source design.
- **Label mappings volume**: `state/label_mappings.json` MUST persist across
  runs (volume-mount the host dir). Losing it resets all integer codes for
  new rows, breaking analytical consistency.
- **n8n notify is best-effort**: a failed webhook does NOT fail the run.
  Sheets has the data; user can recover manually.
