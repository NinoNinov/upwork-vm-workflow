# CLAUDE.md -- Upwork-VM-workflow

> Project bootstrapped 2026-05-19. Last comprehensive update: **2026-05-21**.
> Started as a containerized Contabo deployment; pivoted to **Windows-laptop
> scraper + Contabo VM hosting n8n + Google Sheets sink + MySQL archive** after
> Cloudflare/Datadome blocked headless Chrome on datacenter IPs.

## End-to-end pipeline (current)

```
Windows laptop                           Contabo VM                              Inbox
--------------                           ----------                              -----
python main.py
  -> 5 boolean searches on Upwork
     (uc-Chrome, max_workers=3)
  -> enrich (continent, encoder, regex)
  -> append new rows to Sheet  ----->    n8n polls upwork_master (~1 min)
                                         -> aggregate -> read CV doc
                                         -> OpenAI gpt-4o-mini scores all jobs
                                         -> filter score >= 7
                                         -> grouped HTML email   -----> nino.ninov@hotmail.com
                                              (5 niche sections,
                                               post-time, 2 buttons per match)

Click [📝 Generate proposal] -> webhook -> OpenAI proposal -> Google Doc -> redirect
Click [🗂️ Save for later]    -> webhook -> append to saved_jobs tab -> confirm page

Every Sunday 03:00 BG: n8n archives upwork_master rows older than 30 days
  -> INSERT IGNORE into stockprojectdb.upwork_history (MySQL on Contabo)
  -> DELETE archived rows from Sheet
```

## Key identifiers

| Thing | Identifier |
|---|---|
| Output Google Sheet | `1wsLPktPzfIdf0dSKX0Ghxa21mnI8kdJ2FaZLAmX27QQ` |
| `upwork_master` tab gid | `856015411` |
| `saved_jobs` tab gid | `1662370258` (created 2026-05-21) |
| `Job Titles` tab | same spreadsheet — holds the 5 boolean queries |
| Candidate CV doc | `1k6iXZLxle4Ad9JRIFPpDges7bw_BuvNWm0X_arohZfQ` |
| Upwork Proposals folder (Drive) | `1Z1oBFGoYU0ubFnYoSEzPRLjjmq-qawu-` |
| Contabo VM | `ubuntu@84.247.133.131` (passwordless SSH from laptop) |
| GCP service account key | `secrets/sa.json` (gitignored) |
| GitHub project repo | `https://github.com/NinoNinov/upwork-vm-workflow` |
| GitHub fork of upwork_analysis | `https://github.com/NinoNinov/upwork_analysis` (pinned by SHA) |

## 5-string search strategy (current Job Titles)

Replaced single-keyword "Business Analyst" with five overlapping boolean queries targeting different facets of the user's CV. Each row in the `Job Titles` tab has `Value = 1` (one page = 50 jobs).

1. **Quant / Financial Data Scientist** (highest value)
   ```
   ("Financial Data Scientist" OR "Quant" OR "Quantitative" OR (("Machine Learning" OR "ML Engineer" OR "AI Engineer") AND ("Finance" OR "Trading" OR "Stock" OR "Options" OR "Portfolio")))
   ```
2. **Fintech & Advanced Analytics**
   ```
   ("Financial Modeling" OR "Valuation") AND ("Tableau" OR "Power BI" OR "Python" OR "Predictive")
   ```
3. **Financial GenAI & Agentic Workflows**
   ```
   ("RAG" OR "LangChain" OR "LangGraph" OR "n8n" OR "LlamaIndex") AND ("Finance" OR "Automation" OR "Report" OR "Analysis")
   ```
4. **Predictive Analytics (Real Estate / Retail)**
   ```
   ("Predictive Analytics" OR "Time Series" OR "XGBoost" OR "Forecasting") AND ("Real Estate" OR "Retail" OR "Demand Planning")
   ```
5. **Enterprise BA / Data Analyst**
   ```
   "Business Analyst" AND ("Salesforce" OR "SQL" OR "Tableau" OR "Data Engineering")
   ```

5 strings × 50 jobs × 3 parallel workers ≈ **~20 min wall clock** for a clean run. Daily skip-set cuts that to ~5 min on subsequent runs.

## n8n workflows (4 of them, all prefixed `upwork->`)

| ID | Name | Status |
|---|---|---|
| `KRmemQFoahALiKfh` | `upwork-> master sheet -> CV match -> email shortlist` | Active ✓ |
| `1XmONTJZmHPCRS6p` | `upwork-> Proposal generator (webhook: /proposal-gen)` | Active ✓ |
| `tW6gDZ4bwjpNsi7r` | `upwork-> Save job (webhook: /save-job)` | **NEEDS ACTIVATION** + Sheets credentials |
| `DvxtaYExd32PvOVC` | `upwork-> Weekly archive (>30d -> MySQL)` | **NEEDS ACTIVATION** + Sheets & MySQL credentials |

Note: n8n's folder feature requires the paid tier; we use the `upwork->` name prefix instead. The MCP `addTag` operation is buggy (errors with `Cannot read properties of undefined`), so don't bother with tags.

### Workflow 1: scoring email (`KRmemQFoahALiKfh`)

`Sheets Trigger -> Aggregate -> Read CV doc -> Build LLM prompt -> OpenAI gpt-4o-mini (jsonOutput=true) -> Filter matches + build email body (Code) -> Gmail send`

Key facts:
- Threshold: score ≥ 7/10
- LLM returns `{"jobs": [{"index", "score", "reason"}, ...]}` — the wrapper key is required because `jsonOutput=true` forces an object root.
- Filter node JS has heavy logic: HTML escaping, category derivation via keyword fingerprint on the `position` field, render order, two button URLs.
- Email is HTML (`emailType: html`). Matches grouped by 5 categories + "Other", each line shows `[score/10] · <link>title</link> · Posted X ago / why / budget / client / [📝 Generate proposal] [🗂️ Save for later]`.

### Workflow 2: proposal generator (`1XmONTJZmHPCRS6p`)

Triggered by `GET /webhook/proposal-gen?job_id=~02...` from the email's blue button.

`Webhook -> Lookup job in upwork_master by job_id -> Read CV doc -> Build prompt (Code) -> OpenAI gpt-4o-mini -> Build doc title + body (Code) -> Google Docs Create (in Upwork Proposals folder, title "[YYYY-MM-DD] Job Title") -> Google Docs Update (insert proposal text) -> Respond with HTML redirect to the new Doc`

Click → ~7-10s spinner page → redirect to a Google Doc with the AI-generated tailored proposal.

### Workflow 3: save for later (`tW6gDZ4bwjpNsi7r`)

Triggered by `GET /webhook/save-job?job_id=...&score=...&reason=...` from the email's amber button.

`Webhook -> Lookup job in upwork_master -> Build saved_jobs row (Code) -> Append to saved_jobs tab -> Respond with confirmation page`

Appends a row to `saved_jobs` with: `date_saved, job_id, title, url, score, reason, position, budget, client_location, description (truncated 3000 chars), status='to review', notes=''`. User manually reviews the tab later and updates `status` / `notes` columns.

### Workflow 4: weekly archive (`DvxtaYExd32PvOVC`)

`Schedule Trigger (Sun 03:00 Europe/Sofia) -> Read upwork_master (all rows) -> Code: filter extraction_date < now-30d + sort row_number DESC -> MySQL Execute Query (INSERT IGNORE into stockprojectdb.upwork_history with parameterized values) -> Sheets Delete (loops over items bottom-up) -> Code: summary`

Idempotent: `INSERT IGNORE` skips duplicates on the unique `job_id` index. If MySQL fails, the downstream Sheets Delete doesn't run (n8n error propagation), so the sheet stays consistent. Re-run picks up where it left off.

## Sheets schema

### `upwork_master` (32 columns, the live tab)
`position, title, url, job_id, description, time, time_raw, skills, type, experience_level, time_estimate, budget, proposals, client_location, client_jobs_posted, client_hire_rate, client_hourly_rate, client_total_spent, continent, extraction_date, StartUp, Valuation, word_count, description_label, position_en, type_en, time_estimate_en, experience_level_en, client_location_en, continent_en, description_label_en, proposals_en`

### `saved_jobs` (12 columns, manual review queue)
`date_saved, job_id, title, url, score, reason, position, budget, client_location, description, status, notes`

### Sheet header is enforced
`SheetsWriter.ensure_header()` raises if row 1 of `upwork_master` doesn't match `sheets_writer.COLUMNS`. Either edit the constant or wipe the tab content to rebuild.

## MySQL archive (Contabo VM)

- **Host:** `mysql` (Docker DNS name on `app-net` network — n8n was bridged onto this net 2026-05-21 by editing `/opt/n8n/docker-compose.yml`; backup at `.yml.bak`)
- **Port:** `3306`
- **Database:** `stockprojectdb` (shared with EquitiesRadar — user chose this over a separate DB)
- **Table:** `upwork_history` (32 data columns + auto-increment `id` PK + `UNIQUE KEY uk_job_id` + `archived_at`)
- **User:** `equitiesradar` (existing, already had full access — credential `MYSQL_PASSWORD` in mysql container env)
- **Connection from VM shell:**
  ```bash
  docker exec -it mysql mysql -uequitiesradar -p stockprojectdb
  ```
- **Connection from laptop:** SSH tunnel `ssh -L 3307:127.0.0.1:3306 ubuntu@84.247.133.131` then point a MySQL GUI at `localhost:3307`.

The redundant `upwork_pipeline` database + `upwork_n8n` user I initially created have been dropped — there is only the one DB.

## Scrape logic & timing

### Two-stage deduplication
1. **In-memory at scrape time:** `main.py` pre-loads existing `job_id` values from the sheet into a `set` → passes into `JobsScraper(known_job_ids=...)`. Inside `parse_one_job`, if a card's `job_id` is in the set, the expensive `driver.get(detail_url)` is skipped (~15s saved per known job). Card-level fields are still parsed (cheap).
2. **At write time:** `sheets_writer.append_new()` re-reads existing job_ids + descriptions, drops any duplicates. Belt-and-suspenders against concurrent edits.

### Three resilience layers (added 2026-05-21)
1. **`parse_time`** handles `quarter` (→ 90d) and `year` (→ 365d) units; returns `None` on unparseable input instead of raising.
2. **`_scrape_pages`** wraps `parse_one_job` in try/except:
   - Driver-death exceptions (`InvalidSessionIdException`, `NoSuchWindowException`, `SessionNotCreatedException`) → mark page failed + break out; `retry_failed` spins up a fresh driver.
   - Anything else → log + skip the article, continue.
3. **`main.py`** outer-loop retry: after the parallel scrape, any title that returned < 50% of expected rows is re-scraped sequentially. **This recovered String 1 (Quant) from 0 → 50 jobs in a real run on 2026-05-21.**

### Timing
- **Daily run, ~80% repeats:** ~3-5 min wall
- **First-ever scrape (empty skip set), 5 strings, 3 parallel workers:** ~20 min wall
- **Single 50-job string with empty skip set:** ~13 min sequential
- Detail-page fetch (~15s/job) is the dominant cost; skip-set eliminates that for known jobs.

## Fork history (`NinoNinov/upwork_analysis`)

Pinned by SHA in `requirements.txt`. Full details in [UPWORK_ANALYSIS_ROADMAP.md](UPWORK_ANALYSIS_ROADMAP.md).

| SHA | Change |
|---|---|
| `96f0f2bf` | Tier 1 first cut: card-level location attempt + url + job_id + time_raw |
| `dc955273` | Revert bad card-level location selectors |
| `4d901e19` | Null-safety on post_time + description |
| `e4417966` | Tier 3 Step A: replace click-panel with `driver.get(detail_url)`. Kills location race. |
| `fcbfe6ad` | Fix `post_time` (now `small[data-test="job-pubilshed-date"]`) and `client_total_spent` (drop ` > span`) after Upwork's May-2026 redesign. |
| `92dbb6c8` | Add `known_job_ids` parameter to `JobsScraper`; `parse_one_job` skips detail fetch for known jobs. |
| `c7021cd9` | Properly URL-encode the search query — unlocks boolean queries with `"` and `()`. |
| `95965d58` | **Current pin.** parse_time quarter/year support + per-job error containment in `_scrape_pages`. |

## Known broken / partial fields

After fork SHA `95965d58`:
- `time_raw` / `time` — **FIXED**.
- `client_total_spent` — selector fixed; blank cells reflect real "new client, no spend yet", not bugs.
- `client_jobs_posted`, `client_hire_rate` — **permanently empty**. Upwork removed these metrics from the DOM. Columns kept nullable.
- `client_hourly_rate` — only present on hourly listings with confirmed rates.
- All other fields populate.

## Known operational issues (open)

1. **String 5 (Business Analyst) consistently dies at ~23/50 jobs.** uc-Chrome's `InvalidSessionIdException` fires on the same query position across runs. Resilience layer catches it (thread doesn't die), but we lose ~27 jobs. Suspect a specific job in the result set triggers a uc-Chrome bug, OR the long boolean URL is the trigger. Not blocking but recurring.
2. **MCP `n8n_update_partial_workflow` `addTag` is broken** — returns `success: true` but the tag never applies. Workaround: edit n8n's SQLite directly (see archived attempt) OR use the name prefix approach.
3. **n8n folders require paid tier** in this installation. Use name prefix `upwork->` instead.
4. **Headless scraping on Windows works but with long silent gaps.** Between log lines `Scraping page 1 of 1` and `Scraped X jobs`, ~12 minutes of silence is normal — the per-job loop has no log output. Don't kill prematurely.

## VM path abandoned (do not retry scraping on Contabo)

We invested ~4 hours running the scraper inside Docker on Contabo. Every reasonable mitigation hit a wall:
- IP-whitelisted Webshare residential proxies: Cloudflare still captcha'd 100%.
- Xvfb + non-headless Chrome: didn't help.
- Real Google Chrome (not Debian Chromium): same captcha.
- **Conclusion:** Linux container fingerprint (software WebGL, missing fonts, no audio device, no browsing history, no mouse) is too bot-like.

Do not retry without spending money (Bright Data Scraping Browser ~$15/mo, OR a Windows VPS). Dockerfile/entrypoint.sh/deploy/ still in repo for reference.

## Pending activations (in user's court)

1. **Activate `upwork-> Save job` workflow** in n8n UI:
   - Wire Google Sheets credential on "Look up job by id" and "Append to saved_jobs" nodes
   - Toggle Active
2. **Activate `upwork-> Weekly archive` workflow** in n8n UI:
   - Wire Google Sheets credential on "Read upwork_master" and "Delete archived rows"
   - Wire MySQL credential (`MySQL stockprojectdb`) on "Insert into upwork_history"
   - Toggle Active
3. **Windows Task Scheduler** for daily `python main.py` runs at **08:00 + 17:00 BG** (user's chosen schedule — covers US East Coast morning peak at 17:00 BG = 10:00 ET).

## Code layout

```
main.py
  +-- config.py               ScrapingConfig, SheetsConfig, N8nConfig, load_job_titles
  +-- job_scraper.py          ThreadPoolExecutor over upwork_analysis.JobsScraper
  |                           (monkey-patches create_driver for proxy injection if set)
  |     +-- data_processor.py JobDataProcessor (raw dict -> DataFrame + continent)
  +-- data_processor.py       process_upwork_data (regex flags, StableEncoder, encoded cols)
  +-- sheets_writer.py        SheetsWriter (gspread; dedup-on-append; header bootstrap)
                              + read_job_titles_from_sheet (sheet-driven title list)
                              + existing_job_ids() / existing_descriptions()
  +-- n8n_notifier.py         notify_n8n -- currently unused (n8n polls sheet directly)
  +-- utils.py                JSONFormatter, configure_logging, exponential_backoff
  +-- tools/                  one-off scripts
      +-- dump_dom.py         capture live Upwork HTML for selector discovery
      +-- inspect_dump.py     walk the DOM in saved dumps to find data-test hooks
      +-- smoke_scrape.py     scrape 10 jobs without sheet write (test patch quickly)
      +-- smoke_skip.py       verify the known_job_ids skip path (pass1 vs pass2)
```

State persisted across runs (gitignored): `state/label_mappings.json`, `secrets/sa.json`, `dumps/`, `logs/`, `downloaded_files/`.

## How to run

```bash
# Local scraping (Windows)
cp .env.example .env
mkdir -p secrets && cp /path/to/sa.json secrets/sa.json
pip install -r requirements.txt
python main.py
```

```bash
# Re-pin the fork after a new commit:
# 1. push commit to https://github.com/NinoNinov/upwork_analysis
# 2. update SHA in requirements.txt
# 3. force-reinstall:
python -m pip install --force-reinstall --no-deps \
    "upwork_analysis @ git+https://github.com/NinoNinov/upwork_analysis.git@<NEW_SHA>"
```

```bash
# Inspect MySQL archive on Contabo
ssh ubuntu@84.247.133.131
docker exec -it mysql mysql -uequitiesradar -p stockprojectdb
# (paste password from mysql container env: docker exec mysql printenv MYSQL_PASSWORD)
```

## Risks / gotchas

- **Don't run the scraper in Docker/Linux** — see "VM path abandoned".
- **`upwork_analysis` is pinned to OUR fork by commit SHA** — upstream selectors break when Upwork redesigns; the fork is where we patch them.
- **Sheet header is enforced.** If you edit row 1 by hand, the next scrape raises.
- **Dedup happens on `job_id` first, `description` as fallback** for legacy rows that lack job_id.
- **n8n MCP `addTag` operation is buggy.** Don't waste time on it; use name prefix or direct SQLite write.
- **Long silent periods during headless scraping are normal** — don't kill prematurely.
- **OpenAI billing:** ~$0.001 per scrape + ~$0.001 per proposal click. ~$0.05/day at 2 scrapes/day + 5 proposals.
- **n8n container restart will lose its `app-net` connection** if `/opt/n8n/docker-compose.yml` ever gets reverted to the backup — that's the file with `app-net: external: true` in the networks section.
- **Cron timing:** `parse_time` writes timestamps using `datetime.now()` from the laptop (BG time, no TZ in the string). The archive workflow's date filter parses `YYYY-MM-DD HH:MM:SS` as UTC. There's a ~3h skew but doesn't matter for 30-day archival.

## Forward roadmap (in priority)

1. **Activate the two pending workflows** (save-job + weekly-archive). 1 min in n8n UI.
2. **Windows Task Scheduler** at 08:00 + 17:00 BG.
3. **Quality of "Generate Proposal" output** — once user has clicked it ~10 times, refine the prompt based on what feels generic.
4. **Investigate String 5 driver-crash root cause** — try shorter query, try `max_workers=1` for that one title, capture exact crashing job.
5. **Maybe Tier 3 Step B** (parallel detail fetching within a title) — only if first-day runs become a problem after Task Scheduler ramps up.
6. **Migration to Notion or Airtable** for `saved_jobs` — only if/when sheet's UX becomes painful for the review workflow. n8n nodes exist for both; the webhook URL stays the same.

## Useful commands

```bash
# Smoke-scrape 10 jobs of one title (no sheet write)
python tools/smoke_scrape.py "python developer"

# Test the known_job_ids skip path
python tools/smoke_skip.py "python developer"

# Capture live Upwork HTML for selector discovery (next time Upwork redesigns)
python tools/dump_dom.py --query "python developer" --jobs 2
python tools/inspect_dump.py dumps/search-*.html dumps/detail-*.html
```

```bash
# Connect from laptop to the Contabo MySQL (read upwork_history history)
ssh -L 3307:127.0.0.1:3306 ubuntu@84.247.133.131
# Then: MySQL GUI -> localhost:3307, user equitiesradar, db stockprojectdb
```

```bash
# Edit n8n's docker-compose if it ever needs reverting
ssh ubuntu@84.247.133.131
cat /opt/n8n/docker-compose.yml      # current (has app-net)
cat /opt/n8n/docker-compose.yml.bak  # original (no app-net) — pre-2026-05-21
```
