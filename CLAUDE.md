# CLAUDE.md -- Upwork-VM-workflow

> Project bootstrapped 2026-05-19. Updated 2026-05-20.
> Started as a containerized Contabo deployment; pivoted to **Windows-laptop
> scraper + Contabo VM hosting n8n + Google Sheets sink** after Cloudflare/Datadome
> blocked headless Chrome on datacenter IPs (see "VM path abandoned" below).

## Current pipeline (end-to-end)

```
Windows laptop                                Contabo VM                Inbox
--------------                                ----------                -----
python main.py
  -> scrape Upwork (seleniumbase + Google Chrome)
  -> enrich (continent, encoder, regex flags)
  -> append new rows to Google Sheet  ----->  n8n polls sheet (every ~1 min)
                                              -> aggregate rows
                                              -> read CV from Google Doc
                                              -> OpenAI gpt-4o-mini scores each
                                                 job vs CV (single batched call)
                                              -> filter score >= 7
                                              -> Gmail send 1 summary email ---> nino.ninov@hotmail.com
```

### What runs where

- **Scraper:** Windows laptop only. **Do NOT** try to run on the Contabo VM — see "VM path abandoned" section. Real Google Chrome on Windows passes Cloudflare; Linux Chromium does not.
- **n8n workflow:** Hosted at `https://n8n.equitiesradar.com/` on the Contabo VM. Workflow ID: `KRmemQFoahALiKfh`.
- **Google Sheet (sink):** `1wsLPktPzfIdf0dSKX0Ghxa21mnI8kdJ2FaZLAmX27QQ`, tab `upwork_master`.
- **Google Sheet (job titles source):** Same spreadsheet, tab `Job Titles` (columns: `Job Title`, `Value`).
- **Candidate CV:** Google Doc `1k6iXZLxle4Ad9JRIFPpDges7bw_BuvNWm0X_arohZfQ`. Edit the doc to tune what counts as a fit -- no code change needed.

## Key identifiers

| Thing | Identifier |
|---|---|
| GCP project | `upwork-workflow-496808` (proj number `418198035410`), org `nino-ninov22-org` |
| Service account (Sheets writer) | `upwork-scraper@upwork-workflow-496808.iam.gserviceaccount.com` |
| Service-account key (local) | `secrets/sa.json` |
| Output sheet | `https://docs.google.com/spreadsheets/d/1wsLPktPzfIdf0dSKX0Ghxa21mnI8kdJ2FaZLAmX27QQ/` |
| CV doc | `https://docs.google.com/document/d/1k6iXZLxle4Ad9JRIFPpDges7bw_BuvNWm0X_arohZfQ/` |
| n8n workflow URL | `https://n8n.equitiesradar.com/workflow/KRmemQFoahALiKfh` |
| Contabo VM (n8n host) | `ubuntu@84.247.133.131` (passwordless SSH from this laptop) |
| GitHub project repo | `https://github.com/NinoNinov/upwork-vm-workflow` |
| GitHub fork of upwork_analysis | `https://github.com/NinoNinov/upwork_analysis` (pinned by SHA in `requirements.txt`) |

## Architecture (code)

```
main.py
  +-- config.py               ScrapingConfig, SheetsConfig, N8nConfig, load_job_titles
  +-- job_scraper.py          ThreadPoolExecutor over upwork_analysis.JobsScraper
  |                           (monkey-patches create_driver to inject proxy if set)
  |     +-- data_processor.py  JobDataProcessor (raw dict -> DataFrame + continent)
  +-- data_processor.py       process_upwork_data (regex flags, StableEncoder, encoded cols)
  +-- sheets_writer.py        SheetsWriter (gspread; dedup-on-append; header bootstrap)
                              + read_job_titles_from_sheet (sheet-driven title list)
  +-- n8n_notifier.py         notify_n8n (POST summary; never raises) -- currently unused
  +-- utils.py                JSONFormatter, configure_logging, exponential_backoff,
                              retry_with_backoff
```

State persisted across runs (NOT in repo, gitignored):
- `state/label_mappings.json` -- StableEncoder integer codes
- `secrets/sa.json` -- GCP service account key

## n8n workflow nodes (current)

`Sheets Trigger -> Aggregate to one item -> Read CV from Google Doc -> Build LLM prompt (Code) -> OpenAI gpt-4o-mini -> Filter matches + build email body (Code) -> Gmail send`

Threshold: jobs with score >= 7/10 land in the email.
Active: yes. Latest tested: 2026-05-20.
Email format: subject `Upwork: N matching job(s) of M new`, body lists matched jobs with `[score/10]` + reason + budget + client country.

## Current scraper status (2026-05-20)

### What works
- Scrape via `python main.py` from Windows -> Google Sheet append (dedup on description) -> n8n auto-fires within ~1 min.
- AI matching email with accurate scoring & reasoning.
- Job titles editable via `Job Titles` tab in the spreadsheet (no code change to rescope).

### Sheet schema (32 columns; defined in `sheets_writer.COLUMNS`)
`position, title, url, job_id, description, time, time_raw, skills, type, experience_level, time_estimate, budget, proposals, client_location, client_jobs_posted, client_hire_rate, client_hourly_rate, client_total_spent, continent, extraction_date, StartUp, Valuation, word_count, description_label, position_en, type_en, time_estimate_en, experience_level_en, client_location_en, continent_en, description_label_en, proposals_en`

### Known broken / partial fields (after fork SHA `fcbfe6a`, 2026-05-20)
- `time_raw` / `time` -- **FIXED**. New selector `small[data-test="job-pubilshed-date"]`; smoke test 10/10.
- `client_total_spent` -- **FIXED selector** (`strong[data-qa="client-spend"]`, dropped the ` > span` constraint). Hit rate now varies by client: new clients legitimately have no spend yet, so blank cells here are real, not bugs.
- `client_jobs_posted`, `client_hire_rate` -- **PERMANENTLY EMPTY**. Upwork removed these metrics from the DOM in May 2026; replaced by a new `client-hires` element we don't capture. Columns kept nullable to avoid a sheet rebuild.
- `client_hourly_rate` -- present only on hourly listings with confirmed rates; expect blanks on fixed-price jobs.
- All other fields populate correctly.

### Scrape timing (after the known_job_ids skip, fork SHA `92dbb6c8`)
Each job's `driver.get(detail_url)` costs ~15s. Daily runs save the bulk of
that because `main.py` pre-loads `existing_job_ids()` from the sheet and
hands them to `JobsScraper` — `parse_one_job` then short-circuits for any
repeat, parsing only the cheap card-level fields and skipping the detail
navigation. Typical daily run with 80%+ repeats: ~3-5 min (vs. ~12 min
pre-skip). First-ever scrape of a new title is still slow because the
skip-set is empty.

Tier 3 Step B (parallel detail fetching) would cut the remainder further but
requires +400MB RAM per Chrome worker — kept on the backlog until needed.

## VM path abandoned (read before considering Docker/Contabo for scraping)

We invested ~4 hours trying to run the scraper inside Docker on the Contabo VM.
Every reasonable mitigation hit a wall:

- IP-whitelisted Webshare residential proxies via `UPWORK_PROXIES` env var: works for the IP layer, but Cloudflare/Datadome still captcha 100% of pages.
- Xvfb + non-headless Chrome (xvfb wrapper hung in container; switched to explicit Xvfb in entrypoint -- worked, but didn't help).
- Real Google Chrome (not Debian Chromium): same Cloudflare captcha.
- Conclusion: Linux container fingerprint (software WebGL, missing fonts, no audio device, no browsing history, no mouse interaction) is too bot-like regardless of which browser binary or IP we use.

**Do not retry this without spending money** (Bright Data Scraping Browser ~$15/mo, OR a Windows VPS) OR writing a real residential-proxy + browser-fingerprint-randomization solution.

The Docker tooling we built (Dockerfile, entrypoint.sh, deploy/) is still in the repo for reference. Container builds fine and the GCP/n8n bits inside the container WORK -- it's only the scraper that gets captcha'd.

## upwork_analysis fork (`NinoNinov/upwork_analysis`)

Pinned by SHA in `requirements.txt`. Roadmap and per-fix status in [UPWORK_ANALYSIS_ROADMAP.md](UPWORK_ANALYSIS_ROADMAP.md).

| SHA | Change |
|---|---|
| `96f0f2bf` | Tier 1 first cut: card-level location attempt + url + job_id + time_raw |
| `dc955273` | Revert bad card-level location selectors (matched a sidebar element on every tile -> returned "United Kingdom" for all 50) |
| `4d901e19` | Null-safety on post_time + description (placeholder articles were crashing) |
| `e4417966` | Tier 3 Step A: replace click-panel with `driver.get(detail_url)`. Kills location race. |
| `fcbfe6ad` | Fix `post_time` (now `small[data-test="job-pubilshed-date"]`) and `client_total_spent` (drop ` > span`) after Upwork's May-2026 redesign. |
| `92dbb6c8` | **Current pin.** Add `known_job_ids` to `JobsScraper`; `parse_one_job` skips the per-job `driver.get(detail_url)` when job_id is already known. ~3× faster on typical daily runs (80%+ repeats). |

## Next steps (in priority order)

1. **Diagnostic-dump approach to find new card-level selectors** for `time_raw` and (optionally) `client_location`. ~30 min. If it works, we get accurate location at original scrape speed, and `time_raw` populates again. Recommended next.
2. **Fix selectors for `client_jobs_posted` / `client_total_spent`** on the detail page DOM.
3. **Tier 3 Step B** (parallelize Step A across N workers) -- if scrape speed becomes a real problem, this brings ~12 min back to ~1 min.
4. **Q1: expand LLM prompt to use more columns** (skills, type, experience_level, budget, client_location). Quick prompt rewrite in the n8n Code node.
5. **Q2: per-match Google Doc proposals.** For each matched job, call OpenAI again to generate a tailored proposal, create a Doc, include the link in the email.
6. **Q4: phone trigger.** Tailscale + a tiny HTTP listener on the laptop OR an iOS Shortcut hitting an n8n webhook that triggers a Telegram bot that runs `python main.py`. Open architecture choice.
7. **Windows Task Scheduler** at 06:00 daily for the cron. Not yet set up.

## How to run

```bash
# Local development / scraping
cp .env.example .env
mkdir -p secrets && cp /path/to/sa.json secrets/sa.json
pip install -r requirements.txt
python main.py
```

```bash
# To re-pin the fork to a newer commit:
# 1. push new commit to https://github.com/NinoNinov/upwork_analysis
# 2. update SHA in requirements.txt
# 3. reinstall:
python -m pip install --force-reinstall --no-deps "upwork_analysis @ git+https://github.com/NinoNinov/upwork_analysis.git@<NEW_SHA>"
```

## File reference

| File | Purpose |
|---|---|
| `main.py` | Phased orchestration: load -> scrape -> enrich -> sheets -> notify |
| `config.py` | Env-driven dataclasses + `load_job_titles()` (reads from sheet by default) |
| `job_scraper.py` | `JobScraper.scrape_jobs_per_title_parallel()` + monkey-patched proxy injection |
| `data_processor.py` | `JobDataProcessor`, `process_upwork_data`, `StableEncoder`, `validate_dataframe` |
| `sheets_writer.py` | `SheetsWriter` (dedup, header bootstrap, batch append) + `read_job_titles_from_sheet` |
| `n8n_notifier.py` | `notify_n8n` -- not currently used (n8n polls the sheet directly) |
| `utils.py` | Logging + retry helpers |
| `job_titles.csv` | Fallback list if `JOB_TITLES_SOURCE=csv` or Sheets read fails |
| `countries_continents.csv` | Continent lookup |
| `state/label_mappings.json` | StableEncoder codes; created at runtime |
| `Dockerfile`, `entrypoint.sh`, `deploy/` | VM container artifacts. **Not in use** -- kept for reference. |
| `UPWORK_ANALYSIS_ROADMAP.md` | Per-fix tracker for the upwork_analysis fork |

## Risks / gotchas

- **Don't run the scraper in Docker/Linux** -- see "VM path abandoned" above. It will fail with Cloudflare captchas.
- **`upwork_analysis` is pinned to OUR fork by commit SHA.** Upstream selectors break when Upwork redesigns; the fork is where we patch those.
- **Sheet header is enforced** in `SheetsWriter.ensure_header()`. If you edit row 1 of `upwork_master` by hand the next scrape raises. Either match the 32-column schema exactly or delete the entire tab content to let the scraper rebuild.
- **Dedup happens on `job_id` first, `description` as fallback** (rows pre-Tier-1 may lack `job_id`). Known `job_id`s are also fed into `JobsScraper` so its `parse_one_job` skips the per-job detail-page navigation for repeats — the daily speedup driver.
- **n8n workflow draft vs active**: edits via the n8n MCP tool save as drafts; validation errors prevent promotion to active. After every workflow edit, verify in n8n UI that all nodes have credentials assigned and the workflow is toggled `Active`.
- **OpenAI billing**: ~$0.001 per run at current volume (one batched gpt-4o-mini call per scrape). Top up the key periodically to avoid silent fails.
