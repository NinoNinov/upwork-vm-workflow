# `upwork_analysis` Improvements Roadmap

> Background: we forked [Yazan-Sharaya/upwork_analysis](https://github.com/Yazan-Sharaya/upwork_analysis)
> to `NinoNinov/upwork_analysis` so this pipeline can ship targeted fixes without
> waiting on upstream. This file is the working list of improvements, grouped by
> value-vs-effort. Tier 1 is the initial cut; everything else is opt-in later.

Source library lives at: `https://github.com/NinoNinov/upwork_analysis`

**Current pin:** `fcbfe6ad71b637d892fe460534e64bab0437d099` (post-redesign selector fixes)

## Resolved via diagnostic-dump (2026-05-20)

`tools/dump_dom.py` + `tools/inspect_dump.py` captured one live search page + two detail pages and walked the DOM to find new `data-test` / `data-qa` hooks. Findings:

- **`time_raw` / `time`** — selector now `small[data-test="job-pubilshed-date"]`. Text changed from "5 hours ago" to "Posted 5 hours ago"; we strip the prefix before `parse_time` and keep the prefixed form in `time_raw`. Smoke-test hit rate: **10/10**.
- **`client_total_spent`** — selector changed from `strong[data-qa="client-spend"] > span` to just `strong[data-qa="client-spend"]`. The `> span` constraint missed clients whose template renders the value directly inside the `<strong>`.
- **`client_location`** — old selector still works on the detail page (`ul.ac-items.list-unstyled > li:nth-child(1) > strong` → `"United States"`). No change needed.
- **`client_jobs_posted` and `client_hire_rate`** — these metrics were **removed from Upwork's DOM**, replaced by a new `client-hires` element (e.g. "20 hires, 5 active"). Per product decision, columns kept nullable in the sheet (no schema change); these fields will be `None` going forward.
- **Card-level location: confirmed NOT in the new DOM.** No shortcut around per-job detail navigation — Tier 3 Step B remains the only path to speed up by skipping detail.

---

## Tier 1 — High value, ~45 min total (Initial fork scope)

| # | Improvement | What it gives you | Status |
|---|---|---|---|
| 1 | Fix `client_location` race condition (detail-panel reads stale data from previous click) | Accurate country per job (e.g., "United States" not "India") | **DONE via Tier 3 Step A** (fork SHA `e4417966`) — replaced click-panel pattern with `driver.get(detail_url)`. Side effect: scrape is ~3× slower until Step B parallelization. |
| 2 | Add `url` field — extract `href` from the title anchor | Click-through links in scoring emails + future per-match proposal docs | **DONE** (fork SHA `dc955273`) |
| 3 | Extract `job_id` from the URL (`~01abcd...` cipher) | Bullet-proof dedup key, survives Upwork re-wording the description | **DONE** (fork SHA `dc955273`) |
| 4 | Keep raw posted-time text alongside the parsed timestamp | Diagnosability when `parse_time` fails | **DONE** (fork SHA `fcbfe6a`) — new selector `small[data-test="job-pubilshed-date"]`, strips "Posted " prefix before `parse_time`. |

### Implementation notes

- **#1 race condition fix:** options are (a) `wait_for_selector` with a custom polling loop that compares text between clicks, or (b) close the previous panel first via `job_back_arrow_selector` before clicking the next tile, or (c) scrape location from the card itself if present in the listing HTML. Whichever is most stable.
- **#2 url:** the `<a>` tag at `.air3-line-clamp > h2 > a` has the URL in `href`. Currently only `.text` is read. One-line add.
- **#3 job_id:** parse the cipher from the URL pattern `/job/(~[a-zA-Z0-9]+)/`. Becomes the canonical dedup field in our `sheets_writer.py` (replacing description-based dedup).
- **#4 raw time:** add `time_raw` field with the unparsed string ("5 hours ago", "yesterday", etc.).

---

## Tier 2 — Medium value (1–2 hours)

| # | Improvement | What it gives you |
|---|---|---|
| 5 | Better error logging — currently bare `except` silently swallows failures | When a job fails: which selector failed, why |
| 6 | Capture "Payment verified" boolean | Strong risk signal; LLM can downweight or skip unverified |
| 7 | Capture client name / company name if visible | Lets the LLM match against company type or known good clients |

---

## Tier 3 — Bigger refactors (2+ hours each)

| # | Improvement | What it gives you |
|---|---|---|
| 8 | Native `proxy` parameter on `JobsScraper(...)` | Removes the monkey-patch in `job_scraper.py` |
| 9 | **Replace popup-click pattern with parallel detail-page fetching** | **2–3× faster scrapes** (50 jobs: ~4 min → ~30–60 sec). Also eliminates the panel race condition entirely. |
| 10 | Parse JSON-LD `<script>` blocks for canonical data | Replaces fragile DOM selectors for time/location/description |

---

## Tier 4 — Quality of life

| # | Improvement | What it gives you |
|---|---|---|
| 11 | Unit tests with saved HTML fixtures | Catch breakage when Upwork redesigns |
| 12 | Prefer `data-test=` / `data-qa=` attributes over class names | More stable selectors |
| 13 | Type hints + return-type annotations everywhere | Easier maintenance |
| 14 | CI workflow on the fork (lint + test) | Confidence on future patches |

---

## Selector reference (current upstream)

```
job_title_selector       = ".air3-line-clamp > h2 > a"
post_time_selector       = ".job-tile-header div small span:nth-child(2)"
job_skills_selector      = 'div[data-test="JobTileDetails"] div.air3-token-container span[data-test="token"] span'
description_selector     = "div.air3-line-clamp.is-clamped > p.mb-0"

# In the slide-in detail panel (the source of the race condition):
proposals_selector       = 'ul.client-activity-items > li.ca-item > span.value'
client_details_selector  = "ul.ac-items.list-unstyled"
client_location_selector = client_details_selector + ' > li:nth-child(1) > strong'
```

Note: any change to Upwork's HTML (which they do every few months) requires updating these.

---

## Maintenance plan

- **Sync upstream periodically:** `git remote add upstream https://github.com/Yazan-Sharaya/upwork_analysis.git && git fetch upstream && git merge upstream/main` — pull in any fixes the original maintainer publishes.
- **Pin our fork in `requirements.txt` by commit SHA**, not branch — gives reproducible builds and protects against accidental upstream-pull breakage.
- **Bump intentionally:** when you want a newer fork commit, change the SHA in `requirements.txt` and rebuild.
