# Fetch (retrieval-framework)

A SaaS-style document retrieval platform. Users sign in with company credentials, run natural-language queries against the PDF reports they are authorised to see, and get a structured results table back in real time.

---

## IBM Bob — AI Pair Programmer

[IBM Bob](https://www.ibm.com/bob) is the AI coding assistant used throughout the development of this project. Bob operates directly inside the workspace and understands the full codebase — files, symbols, and their relationships — without needing manual context-pasting.

### How Bob helped build this application

| Area | What Bob did |
|------|-------------|
| **PDFSource** | Wrote `pdf.py` — lazy directory indexing, filename parsing, `pypdf` text extraction, and per-ticker ACL filtering |
| **Auth layer** | Designed `auth.py` — random 32-byte session tokens, TTL expiry, brute-force lockout counter in `server.py` |
| **Server wiring** | Built `server.py` — Flask routes, per-user scoped `Retriever` construction, cookie + JSON token dual-auth, structured `rows`/`note` response shape |
| **User store** | Seeded `users.py` with bcrypt-hashed passwords and ticker-based document ACLs |
| **Retrieval pipeline** | Assembled `Retriever` — parallel source fan-out, deduplication, `KeywordRanker`, and `TTLCache` integration |
| **README** | Wrote and maintained this document |

### How to use Bob on this project

**Step 1 — Open your workspace in Bob**
Bob automatically reads the project structure. No manual file pasting is needed.

**Step 2 — Describe what you want**
Be specific about the feature or problem. Examples:
- *"Add a PostgreSQL source that queries a `documents` table"*
- *"The `/api/retrieve` endpoint is returning 401 — here's the traceback"*
- *"Rerank results using BM25 instead of keyword matching"*

**Step 3 — Review Bob's plan**
Bob will read every relevant file before touching anything, then show exactly what it intends to change.

**Step 4 — Accept or refine**
Approve the diff to apply it, or give feedback and Bob will revise before writing anything.

**Step 5 — Validate**
Bob runs `pytest` (or whichever checks apply) after changes and reports the result. If a test fails, it diagnoses and fixes it in the same session.

Common tasks Bob handles on this project:

| Task | How to ask |
|------|-----------|
| Add a new data source | *"Add a `<backend>` source that does `<behaviour>`"* |
| Extend ranking | *"Implement BM25 / embedding reranking in `ranking.py`"* |
| Debug a test failure | *"This test is failing: `<paste traceback>`"* |
| Refactor a module | *"Rename `document_ids` to `allowed_tickers` everywhere"* |
| Update docs | *"Update the README to reflect the new `<feature>`"* |

Bob follows a minimal-change discipline: every edit traces directly to the request, with no unrequested refactors or speculative additions.

---

## User guides

1. [Install and start the app](#1-install-and-start-the-app)
2. [Sign in and run a query](#2-sign-in-and-run-a-query)
3. [Walkthrough: supplier contracts expiring in Q4 2026](#3-walkthrough-supplier-contracts-expiring-in-q4-2026)
4. [Analyze supplier contracts](#4-analyze-supplier-contracts)
5. [Analyze financial trends](#5-analyze-financial-trends)
6. [Analyze customer behavior](#6-analyze-customer-behavior)
7. [Company overview](#7-company-overview)
8. [Saved views and reports](#8-saved-views-and-reports)
9. [Manage users and permissions](#9-manage-users-and-permissions)
10. [Add or regenerate data](#10-add-or-regenerate-data)
11. [Run the tests](#11-run-the-tests)
12. [Troubleshooting](#12-troubleshooting)

### 1. Install and start the app

**Quickest way.** With Python 3.10+ installed, one step does everything below (creates `.venv`, installs the dependencies the first time, starts the server and opens your browser):

| System | Do this |
|--------|---------|
| Windows | Double-click `Fetch.bat` |
| macOS / Linux | `./run.sh` |
| Any | `python run.py` (add `--no-browser` to skip opening the browser) |

Press `Ctrl+C` in the window to stop the server. If Fetch is already running, the launcher just opens it. The manual steps are below if you prefer them.

**Prerequisites:** Python 3.10 or newer and [pip](https://pip.pypa.io/).

```bash
git clone <repo-url>
cd Fetch
python -m venv .venv
```

Activate the virtual environment:

```bash
# macOS / Linux
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Install the dependencies:

```bash
pip install flask pypdf bcrypt
```

Start the server from the project root (the folder that contains `server.py`):

```bash
python server.py
```

On first run the server creates an `instance/` folder holding `users.db` (seeded with the demo accounts) and `audit.log`. Both are git-ignored.

Open [http://localhost:5000](http://localhost:5000). Always use this address: opening `ui/index.html` directly, or serving it from another dev server, will not reach the API.

### 2. Sign in and run a query

The sign-in form starts empty. Use one of these demo accounts:

| ID | Password | Can search |
|----|----------|------------|
| `BLFD-01` | `blfd2024` | BrightLeaf Foods only |
| `HDYN-01` | `hdyn2024` | Halcyon Dynamics only |
| `MCLD-01` | `mcld2024` | Meridian Cloudworks only |
| `BLFD-LOG` | `blfdlog2024` | BrightLeaf, **Logistics contracts only** |
| `HDYN-ENG` | `hdyneng2024` | Halcyon, **Engineering contracts only** |
| `MCLD-INF` | `mcldinf2024` | Meridian, **Infrastructure contracts only** |
| `NL-2048` | `password1234` | All three companies |
| `ADM-01` | `admin2024` | **Administrator**: no company data, but can manage accounts and read the audit log (see [guide 9](#9-manage-users-and-permissions)) |

The three department accounts search the same companies as their `-01` counterparts, but see only their own department's supplier contracts (see [guide 4](#4-analyze-supplier-contracts)).

`alice` and `bob` also exist as unit-test accounts. They are tied to test document IDs rather than company tickers, so they can sign in but searches return no results.

Type a search into the box and click **Pull**. Try `revenue`, `Q1 2025`, or `operating expenses`. The results table lists the matching reports from your authorised companies:

| Column | Meaning |
|--------|---------|
| Report | Company, period and report type |
| Category | The data folder the PDF came from |
| Matched text | Excerpt around the first hit, matched terms in bold |
| Status | Availability of the file |
| View | Opens the full PDF |

**Key figures under a hit.** When a result is a quarterly report or a customer-behavior report, a line under it shows that report's headline numbers (revenue and growth, margins, net income, EPS, free cash flow, or customer and revenue growth) and its improved / worse / flat count, with a button that opens the matching analytics screen for that company. They come from the same extracted data as the analytics screens and only appear for companies your account can search.

A period in your query (`Q3 2025`, `2024`, `Q4-2026`) narrows results to reports for that period. Sign-in errors are specific: wrong ID or password shows "incorrect", a locked account shows "Account locked", and a connection problem shows "Can't reach the server" with the HTTP status.

### 3. Walkthrough: supplier contracts expiring in Q4 2026

This is the demo workflow. It shows one query returning different results for two users with different permissions.

The sample data is nine synthetic PDFs in `data/Supplier_Contracts/`, one per company for each expiry quarter (Q3 2026, Q4 2026, Q1 2027). Each lists every supplier contract expiring that quarter, with status as of November 4, 2026.

**Step 1 — Sign in as `BLFD-01` / `blfd2024`.**

**Step 2 — Run the query:**

```
Show active supplier contracts expiring in Q4 2026
```

You get one row: `BrightLeaf Foods Co.: Q4-2026 Supplier Contracts`. Click **View** to open the register. It lists 7 contracts expiring in Q4 2026, of which 5 are Active (combined annual value $10,770K). The others are Expired or Terminated, and are shown with their status.

**Step 3 — Sign out, then sign in as `HDYN-01` / `hdyn2024`** and run the same query.

You now get only the Halcyon Dynamics register (5 active contracts, $12,220K). BrightLeaf's data does not appear. `MCLD-01` sees only Meridian's (5 active, $28,230K).

**Step 4 — Try to reach the other company's file.** While signed in as `HDYN-01`, paste the BrightLeaf file's address into the browser:

```
http://localhost:5000/api/pdf/BLFD/Supplier_Contracts/BLFD_Q4-2026_Supplier_Contracts.pdf
```

The server answers `403 FORBIDDEN`, and the attempt is written to the audit log as `pdf_denied`. Signed out, the same address returns `401`.

**Step 5 — Change the quarter.** Ask for `supplier contracts expiring in Q1 2027` or `Q3 2026` to get the matching register for the same user.

**Step 6 — Check the audit trail.** Every sign-in, query and PDF view or refusal is in `instance/audit.log` (see [Security features](#security-features)).

> **How "active" works today.** The search selects the right company and quarter, then returns that quarter's register. The Active / Expired / Renewed / Terminated status is read from the PDF itself; the results table does not yet filter rows by status.

### 4. Analyze supplier contracts

Search finds documents. The analytics view turns the contract data into totals, charts and a filterable table.

**Open it.** Sign in, then on the "What data would you like pulled?" screen click **Open analytics** (on a results page, use **Contract analytics**). It opens on *Active* contracts expiring in the current quarter.

**Filters** (every change re-runs the analysis):

| Filter | Options |
|--------|---------|
| Company | Shown only if your account covers more than one company |
| Status | Active (default), All statuses, Expired, Renewed, Terminated |
| Expiry period | All expiry dates, or any quarter from Q1 2025 to Q4 2028 |
| Category | Ingredients, Logistics, Software… (the categories in your scope) |
| Department | The contract owner. Department-limited accounts only see their own |
| Renewal | Any renewal stage: Not started, In negotiation, Renewal agreed, Notice to exit |
| Supplier options | All contracts, or Sole-source only (no alternative supplier) |

**What you get:**

| Item | Meaning |
|------|---------|
| Contracts | How many contracts match the filters |
| Annual value | Sum of their annual contract value. For Active status it also shows the share of the *active spend* (all Active contracts in the same company, category and department selection, whatever their expiry date) |
| Expiring in the next 30 days | Contracts whose expiry falls within 30 days of the status date |
| Largest supplier | Highest-value supplier in the selection, with its share and the top-3 share |
| Needs attention | Active contracts that expire within 60 days and whose renewal has **not started**, with their annual value. The rule is shown on the card |
| Sole-source contracts | Contracts flagged as having no alternative supplier, with their value |
| Renewal pipeline | Annual value by renewal stage |
| Next four quarters | Value expiring in the current and next three quarters, with how many of those contracts have no renewal started |
| Supplier concentration | Per category: supplier count, value, **HHI** (sum of squared supplier shares of value: under 1,500 low, 1,500–2,499 moderate, 2,500 or more high), the largest supplier and its share (flagged above 40%), and sole-source value |
| Charts | Expiries by month (or by quarter for windows over 12 months), by category, time until expiry, top suppliers, and by department or company when more than one applies |
| Contracts table | Every matching contract with its renewal stage and a *sole* tag, paged, with **Export CSV** |

Days to expiry are counted from the data's status date, **November 4, 2026**, not from today's date.

#### Walkthrough: two users, same company, different records

`BLFD-01` can see every BrightLeaf department. `BLFD-LOG` can see only Logistics contracts. Open analytics as each and leave the defaults (Active, Q4 2026):

| | `BLFD-01` (all departments) | `BLFD-LOG` (Logistics only) |
|---|---|---|
| Contracts | 5 | 2 |
| Annual value | $10.8M (10,770 $K) | $6.1M (6,110 $K) |
| Share of active spend | 7.7% of $139.6M | 15.8% of $38.7M |
| Needs attention | 1 contract, $3.8M (BLFD-SC-2411, renewal not started, 41 days left) | 0 |
| Departments in the filters and charts | Logistics, Procurement, Operations, Quality | Logistics only |

Both totals use the same data. The limited account's numbers, including the share-of-spend denominator, are computed from its own department's contracts only. `BLFD-LOG` is also refused everything that could show the other rows:

- The contract register PDFs list every department, so they are hidden from its search results and return `403` if opened directly.
- Asking the API for another department (`"owner": "Quality"`) or another company returns `403`.

The department limit applies to *contract* data. Company-wide documents such as quarterly reports stay available to those accounts.

### 5. Analyze financial trends

From the request screen or the results screen, choose **Financial trends**. It turns the quarterly-report PDFs (Q3 2023 to Q3 2026, 13 quarters per company) into numbers you can compare, for the companies your account is cleared to see.

| Section | What it shows |
|---------|---------------|
| Latest quarter | Revenue, growth vs. last year, gross and operating margin with the change in basis points (bp) vs. last year, net income, EPS, free cash flow, trailing-12-month revenue |
| Chart | Any of revenue, gross / operating / net margin, net income, EPS, free cash flow, FCF margin, by quarter, one line per company |
| Improved / Worse / Flat flags | For each company, the latest quarter's growth, margin, cost, cash, balance-sheet and segment measures (about 20 per company), each tagged *Improved*, *Worse* or *Flat* with a one-sentence reason, plus a quarter-by-quarter tally of how many measures fell into each group. **Export flags** downloads them |
| Next-quarter estimate | For each company, an estimate of next quarter's revenue, operating income, net income, EPS and free cash flow, with a likely range and the change against the same quarter a year earlier |
| Revenue growth index | Each company's revenue with its first quarter set to 100, so a small and a large company can be compared on growth |
| Who leads | League table for the latest quarter on growth, margins and EPS growth (only when you can see two or more companies) |
| Revenue by segment | Each company's segment mix and each segment's growth vs. last year |
| Trend statistics | Two-year revenue CAGR, average and volatility of quarter-on-quarter growth, least-squares trend and its R², and a seasonality index |
| Quarter by quarter | The full table behind the charts. **Export CSV** downloads it |

Filters: company, first and last quarter, the chart measure, what the flags compare against (*same quarter last year* or *previous quarter*) and which flags to show (*All / Improved only / Worse only / Flat only*). Growth rates are always calculated on the full history, so the first quarter in a window still shows its YoY.

**Reading the flags.** They use the same rule as Customer behavior: a ratio (a margin, or SG&A as a share of revenue) counts as moved when it changes by 1 percentage point or by 5% of its own level, and an amount (revenue, profit, cash, debt, a segment's revenue) when it changes by 2%. Anything smaller is *Flat*, so noise is not reported as progress or decline. Each measure knows which direction is good: lower debt and a lower SG&A share count as better, higher everything else does. Quarter-on-quarter flags are noisier than year-on-year ones because of seasonality. The company overview also raises a watch item when any financial measure got worse.

**Reading the estimate.** It is the same quarter a year earlier plus the average of the last three year-on-year changes, so it keeps each business's seasonal shape. The range comes from running that same method on every earlier quarter it could have been applied to: it is about 80% of the typical misses (the *±%* shown is the typical miss). It is a statistical estimate from past reports, not company guidance, and it cannot see events that have not shown up in a report yet. It needs at least ten quarters of history and follows the *To* filter.

**Reading the statistics**

- *2-yr revenue CAGR* compares trailing-12-month revenue now with two years earlier.
- *Trend* is the straight-line change in revenue per quarter; *R²* close to 1 means growth has been steady (MCLD, 0.98), close to 0 means it is mostly noise or seasonality (BLFD, 0.28).
- *QoQ volatility* is the standard deviation of quarter-on-quarter growth. Seasonal businesses read high because Q4 is always strong.
- *Seasonality* is each calendar quarter's revenue divided by its year's average (1.00 = average). It uses complete calendar years only, so with this data that means 2024 and 2025.

**Permissions.** Access follows the quarterly-report PDFs: a user sees the companies in their `document_ids`, and asking for another company returns `403` and an `analyze_denied` audit entry. Other companies are not used in any ranking or comparison either. The department limit (`owners`) covers the contract register only, so `BLFD-LOG` sees BrightLeaf's financials just as it can open BrightLeaf's quarterly reports.

**Accuracy.** The numbers come from CSVs extracted from the PDFs by `tools/extract_financials.py` (see [guide 10](#10-add-or-regenerate-data)), not from text parsed at request time. Growth rates are calculated from figures printed to $0.1M, so they can differ from the percentage printed in a report by about a tenth of a point (BLFD Q2 2026 revenue: 2.15% here, "+2.2%" in the PDF).

### 6. Analyze customer behavior

Choose **Customer behavior** from the request, results, contract or financial screens. It turns the annual customer-behavior reports (2024 and 2025) into a year-over-year scorecard for each company you are cleared to see.

| Section | What it shows |
|---------|---------------|
| Header | Revenue and customers for the year, with growth vs. the year before |
| Where growth came from | Revenue growth split into *more customers* and *more revenue per customer*, so you can tell volume from pricing or mix |
| Scorecard | How many of the reported measures improved, worsened or stayed flat, plus the biggest improvements and the concerns |
| Customer roll-forward | Customers at the start of the year, plus those won, minus those lost, equals the end (Halcyon and Meridian), with the churn rate |
| Customer mix | The mix tables (for example by segment or region) and the biggest shifts in points |
| Every measure | All reported measures by section, with last year's value, the change, and whether it is better or worse. **Export CSV** downloads them |
| Compared with peers | Measures that mean the same thing for every company, with the best one marked (only when you can see two or more companies) |

Filters: company, year and *Improved / Worsened / Flat*.

**Reading the assessment.** Each measure knows which direction is good (more customers is good, more churn is not). A change counts when a rate moves by 1 percentage point or by 5% of its own level (so churn falling from 8.5% to 7.7% counts, while retention rising from 73.8% to 74.3% does not), or any other measure by 2%; smaller moves are shown as *flat* so noise is not reported as progress. Every improved, worse and flat measure says why in one sentence (hover the tag, or read the line under the measure name), and each concern also shows how the rest of its group moved and how revenue did. Mix shares, such as the share of revenue from one segment, are shown but not judged.

**Reading the comparison.** The three companies measure different things: BrightLeaf counts households, Halcyon and Meridian count accounts or logos. For that reason only like-for-like measures are compared: revenue growth, customer base growth, change in revenue per customer, Net Promoter Score (level and change in points) and logo churn (BrightLeaf does not report it, so it shows a dash).

**Permissions.** Access follows the ticker list in `document_ids`, as for financials: asking for another company returns `403` and an `analyze_denied` audit entry, and other companies are left out of the comparison. The department limit (`owners`) covers the contract register only, so `BLFD-LOG` sees BrightLeaf's behavior data.

**Accuracy.** The numbers come from `data/behavior/behavior.csv`, extracted from the PDFs by `tools/extract_behavior.py` (see [guide 10](#10-add-or-regenerate-data)). Changes are the ones printed in the 2025 report, which the extractor has already checked against both years. BrightLeaf's 2024 text says 31.4% household penetration but its table says 31.5%; the tables are used.

### 7. Company overview

Choose **Company overview** from the request screen. It reads across the contract register, the financials and the customer-behavior data for each company you can see, and lists the signals worth a look. It does not load any new data: every figure is one the three analytics screens already show.

| Part | What it shows |
|------|---------------|
| Headline figures | Revenue, growth, customers, and supplier spend as a share of trailing-twelve-month revenue |
| Signals | A short list marked **Watch**, **Good** or **Info**, each with the sources it draws on and a button that opens the matching analytics screen with the company already selected |

Signals only appear when the data shows the condition: contracts lapsing within the next two quarters, sole-source spend above 20% of the register, the top three suppliers above 40% of spend, revenue growth slowing or speeding up by 2 points or more, and customer measures that got worse. Seeing a company with no Watch signals is normal (Halcyon today).

**Permissions.** Access follows `document_ids`. For a department-limited account the contract signals that compare their slice with company-wide figures are left out, and the screen says the contract view is partial. **Export CSV** downloads the signals.

#### Needs attention

**Needs attention** (request screen, or the button on the company overview) is one ranked list across every company you can see. It contains each company's signals from the overview plus *results outside the expected range*: a measure whose latest quarter landed beyond the range that the estimate method (see [guide 5](#5-analyze-financial-trends)) would have given from the quarters before it, and at least 3% away from that estimate. A miss on revenue, profit or cash is a **Watch** item; a beat is **Good news**. Filter by company, show only what needs attention or everything, jump to the matching screen, or **Export CSV**. It is built from the same permission-scoped profiles as the overview, so a department-limited account sees only its own departments' contract items.

#### A five-minute demo

1. Sign in as `NL-2048`, search `Q3 2026 revenue`: each quarterly report carries its key figures and flags; BrightLeaf shows 11 worse.
2. Open **Needs attention**: BrightLeaf and Halcyon report below the expected range, Meridian beats it.
3. Open BrightLeaf's financials: the Worse flags, then the Q4 2026 estimate and range.
4. Sign in as `BLFD-LOG` and repeat step 2: same company, only Logistics contracts, and the supplier-concentration item is gone.

### 8. Saved views and reports

On the contract, financial, behavior and company screens, **Save this view** stores the screen and its current filters under a name you choose. Saved views are listed on the request screen; opening one runs the normal analysis again with those filters, so the numbers are always current and always limited to what you can see today.

- Only the screen and its filters are stored, never the results. A view cannot show more than your account allows, even if your access changes later.
- Each account keeps up to 25 views; saving the same name on the same screen replaces it. Names are up to 60 characters.
- Views live in `instance/views.db`, separate from the user database, so resetting `users.db` does not delete them. Deleting an account removes its views.
- **Print / PDF report** opens the browser's print dialog with a clean layout (title, who ran it, when, filters, then the tables). Choose *Save as PDF* as the destination to keep a copy.

### 9. Manage users and permissions

Accounts live in the `users` table of `instance/users.db`:

| Column | Meaning |
|--------|---------|
| `username` | The sign-in ID (case-sensitive) |
| `password_hash` | bcrypt hash |
| `document_ids` | JSON array of tickers the user may search, e.g. `["BLFD","HDYN"]` |
| `owners` | Record-level limit for contract data: a JSON array of departments the user may see, e.g. `["Logistics"]`. `NULL` (the default) means every department in their companies. `[]` means none |
| `is_admin` | `1` for administrators. This is separate from data access: an admin sees company data only for the tickers in `document_ids` |

**Administration screen.** Sign in as an administrator (demo: `ADM-01` / `admin2024`) and choose **Administration** on the request screen.

- *Accounts* lists every account with its companies, department limit, role and lock state. **New account** and **Edit** set the companies, the department limit (all departments, or a chosen few from the selected companies) and the administrator flag; a new password is optional when editing. **Unlock** clears a lockout without restarting the server. **Delete** removes the account, ends its sessions and removes its saved views.
- *Audit log* shows the latest entries (newest first), filtered by account or event, with a **Flagged accounts** alert for accounts with three or more refusals, failed sign-ins or rate-limit hits. **Export CSV** downloads what is on screen.

Safety rules: passwords must be at least 10 characters and are never written to the audit log; changing a password, deleting an account or removing its admin flag ends that account's sessions; you cannot delete or demote yourself, and the last administrator cannot be deleted or demoted. Non-admins calling the admin endpoints get `403` and an `admin_denied` audit entry. Every change is recorded as `admin_user_created`, `admin_user_updated`, `admin_user_deleted` or `admin_user_unlocked`.

**Change the default accounts.** The defaults are defined once, in `DEFAULT_ACCOUNTS` in `retrieval/users.py`. They are inserted only when the table is empty, so to apply a change delete `instance/users.db` and restart the server.

**Add a user.** Save this as a script in the project root (next to `server.py`) and run it:

```python
from retrieval import db_users

db_users.init_db("instance/users.db")
db_users.register_user("ACME-01", "a-strong-password", ["BLFD", "HDYN"])   # at least 10 characters

# A department analyst: BrightLeaf contracts owned by Quality only
db_users.register_user("ACME-QA", "another-strong-password", ["BLFD"], owners=["Quality"])

# An administrator (pass is_admin=True; document_ids can be empty)
db_users.register_user("ACME-ADM", "an-admin-password", [], is_admin=True)
```

Valid department names are the contract owners in the data: BrightLeaf has Procurement, Operations, Logistics and Quality; Halcyon has Manufacturing, Engineering, Procurement, Supply Chain and Quality; Meridian has Infrastructure, Engineering, Security, Customer Success and Finance.

**Upgrading an existing `instance/users.db`.** The `owners` column is added automatically on start-up (existing users stay unrestricted). The `is_admin` column is added the same way. The new department demo accounts and the `ADM-01` administrator are only created on a fresh database, so delete `instance/users.db` and restart if you want them, or promote an existing account with `db_users.update_user("NL-2048", is_admin=True)`.

**Remove a user.** Use the Administration screen, or from the command line:

```bash
python -c "import sqlite3; c=sqlite3.connect('instance/users.db'); c.execute('DELETE FROM users WHERE username=?',('ACME-01',)); c.commit()"
```

If the account is one of the defaults, also remove it from `DEFAULT_ACCOUNTS`, otherwise it comes back when the database is recreated.

### 10. Add or regenerate data

PDFs are read from the `data/` folder, recursively. The sub-folder name becomes the report **category**, and the filename decides the ticker and period:

| Filename pattern | Parsed as |
|------------------|-----------|
| `BLFD_Q1-2024_Quarterly_Report.pdf` | ticker `BLFD`, period `Q1-2024`, type "Quarterly Report" |
| `BLFD_Q4-2026_Supplier_Contracts.pdf` | ticker `BLFD`, period `Q4-2026`, type "Supplier Contracts" |
| `BLFD_BrightLeaf_Customer_Behavior_2024.pdf` | ticker `BLFD`, period `2024`, type "BrightLeaf Customer Behavior" |

The first part of the filename must be the ticker. A file with no period in its name is skipped whenever the query contains a period. Restart the server after adding files; PDFs are indexed on first use.

**Regenerate the supplier-contract sample data** (needs `pip install reportlab`):

```bash
python tools/generate_supplier_contracts.py
```

This writes two things, identically every time:

- `data/Supplier_Contracts/contracts.csv` — the analytics dataset: 388 contracts for the three companies, expiring Q1 2025 to Q4 2028, with status as of November 4, 2026.
- The nine register PDFs for Q3 2026, Q4 2026 and Q1 2027. These three quarters are hand-written in the script's `CONTRACTS` table and are also the first rows of the CSV, so the PDFs and the analytics always agree.

The other quarters are generated from a fixed random seed (`SEED`, with the supplier names and value ranges in `PROFILE`). Edit those to change the sample data.

The CSV also carries `renewal_status` (set for Active contracts only, roughly three in four of them *Not started*) and `sole_source` (about 16% of contracts). Both are derived from a hash of the contract ID, so they are stable between runs and the PDFs and the original columns do not change. Older CSVs without these columns still load; the new filters then see blanks.

The server loads the CSV into `instance/contracts.db` at start-up and reloads it automatically whenever the CSV changes, so you never edit the database by hand.

**Extract the quarterly financials** from the report PDFs:

```bash
python tools/extract_financials.py            # rebuild the CSVs
python tools/extract_financials.py --check    # only check that the PDFs reconcile
```

The Q3 2026 reports were written by `python tools/generate_q3_2026_reports.py`, which builds each PDF from the Q2 2026 and Q3 2025 rows so the comparison columns agree with the earlier reports (the extractor then reconciles them). BrightLeaf's quarter is deliberately weak and Halcyon's cash flow slips, so the Worse flags have something to show; Meridian beats its guidance.

This reads every `data/individual/<TICKER>/*_Quarterly_Report.pdf` and writes `data/individual/financials.csv` (one row per company per quarter) and `data/individual/segments.csv`. It refuses to write anything unless the numbers agree: each statement adds up, segments sum to total revenue, the "prior quarter" and "prior-year" columns match the reports they refer to, and the three-year summary PDFs match the individual reports. Run it after adding or replacing a quarterly report, and restart the server (the CSVs are re-read when they change).

**Extract the customer behavior** from the report PDFs:

```bash
python tools/extract_behavior.py            # rebuild data/behavior/behavior.csv
python tools/extract_behavior.py --check    # only check that the PDFs reconcile
```

This reads every `data/behavior/*_Customer_Behavior_<year>.pdf` and writes `data/behavior/behavior.csv`. It refuses to write anything unless the reports agree: both years report the same measures, the changes printed in 2025 match the two years' values, each mix table adds up to 100%, revenue matches the quarterly financials, and the customer roll-forward, churn, seat activation and revenue per customer calculations hold. Run it after adding or replacing a behavior report.

**Data on disk**

| Ticker | Company | Industry |
|--------|---------|----------|
| BLFD | BrightLeaf Foods Co. | Consumer Packaged Goods |
| HDYN | Halcyon Dynamics Inc. | Industrial Automation |
| MCLD | Meridian Cloudworks Corp. | Enterprise Software |

All three are fictional. `data/` holds quarterly reports (Q3-2023 to Q3-2026) plus summaries, customer-behaviour reports, topic reports, the supplier-contract register PDFs, and `Supplier_Contracts/contracts.csv` and `behavior/behavior.csv`, which feed the analytics.

### 11. Run the tests

```bash
pip install pytest
pytest
```

Run it from the project root. `pyproject.toml` already sets the test folder and import path. The suite writes its user database and audit log to a throw-away temp folder (via `FETCH_INSTANCE_DIR`), so it never touches your real `instance/` data.

### 12. Troubleshooting

**Sign-in says "ID or password is incorrect".**
If only a newer account such as `BLFD-LOG` fails, your `instance/users.db` was created before that account existed. Stop the server, delete `instance/users.db` and start again; it is re-created with all demo accounts.

Run the backend check from the project root:

```bash
python tools/check_login.py
```

It prints the database path, the users in it, whether `BLFD-01` exists, whether the password matches, and the result of a full `auth.login()`. Pass your own account to test it: `python tools/check_login.py MY-ID my-password`. If the check works, the problem is between the browser and the server; look for the `[signin] ...` line in the server terminal after clicking Sign in.

**"Missing credentials" (HTTP 400).**
The server received an empty ID or password. Check the **Network** tab in browser dev tools and look at the `signin` request payload.

**"Account locked".**
Three failed attempts lock the account. An administrator can unlock it from the Administration screen (guide 9); otherwise it stays locked until the server restarts.

**Analytics shows no contracts.**
Check that `data/Supplier_Contracts/contracts.csv` exists (run `python tools/generate_supplier_contracts.py` if not), and that your account's `document_ids` contain a company ticker. If you are on a department-limited account, only contracts owned by your department appear. A status of *Active* with an expiry period in the past also correctly returns nothing.

**Searches return no rows.**
Check that `data/` exists and contains your PDFs, that the user's `document_ids` include the right ticker, and that the filename starts with the ticker. A period in the query (e.g. `Q4 2026`) hides files whose names have no period.

**`ImportError: attempted relative import with no known parent package`.**
You ran a file inside `retrieval/` directly. Modules in that folder use relative imports and must be imported, not run. Run `server.py` from the project root.

**VS Code shows `Import "retrieval.users" could not be resolved`.**
Open the `Fetch` folder (not `retrieval`) as the workspace, and make sure `retrieval/__init__.py` exists.

**The server won't start on port 5000, or changes don't take effect.**
An old server process may still be holding the port. On Windows:

```powershell
netstat -ano | findstr :5000
taskkill /PID <pid> /F
```

Repeat until `netstat` prints nothing, then start the server again. On macOS / Linux use `lsof -i :5000`. If it persists, change the port in the last line of `server.py`.

---

## Reference

### Project layout

```
server.py                 Flask HTTP server (endpoints below)
pyproject.toml            Dependencies and pytest settings

retrieval/                The library
  types.py                Query, Document
  sources/
    base.py               Source ABC
    memory.py             InMemorySource (tests and demos)
    pdf.py                PDFSource — indexes *.pdf directories lazily
  ranking.py              Ranker ABC + KeywordRanker
  cache.py                TTLCache (in-memory, configurable TTL)
  retriever.py            Pipeline: fan-out → dedupe → rank → cache
  auth.py                 Session tokens (login / issue_token / validate / logout)
  db_users.py             SQLite user store (bcrypt hashes, ticker ACLs, department limits, admin flag)
  users.py                UserRecord, DEFAULT_ACCOUNTS, in-memory fallback store
  contracts.py            Supplier-contract store (SQLite) and expiry analytics, permission-scoped
  financials.py           Quarterly growth, margin, trend and peer statistics (from the extracted CSVs)
  behavior.py             Customer-behavior scorecard, growth split, roll-forward and peer comparison
  company.py              Cross-source company overview: signals built from the three analyses above
  alerts.py               The ranked "needs attention" list built from the company profiles
  views.py                Saved views (screen + filters per user) in instance/views.db
  audit.py                Append-only access log
  pull_request.py         PullRequestAgent — ownership-aware retrieval helper

run.py, run.sh, Fetch.bat   One-step launcher (creates .venv, installs, starts, opens browser)
ui/index.html             Single-page front-end
data/                     PDF reports, one sub-folder per category
tools/                    generate_supplier_contracts.py (CSV + PDFs), generate_q3_2026_reports.py (Q3 2026 quarterly PDFs), extract_financials.py and extract_behavior.py (PDFs → CSVs), check_login.py
examples/demo.py          Auth + retrieval demo (PYTHONPATH=. python examples/demo.py)
tests/                    pytest suite
instance/                 Runtime files: users.db, views.db, contracts.db, audit.log (git-ignored, created on first run)
```

### Request flow

```
Browser ──POST /api/signin──► server.py ──► auth.login() ──► db_users.get_user() ──► session token
Browser ──POST /api/retrieve─► server.py ──► validate_token() + rate limit
                                           ──► db_users.get_user() (ticker ACL)
                                           ──► per-ticker PDFSource(s)
                                           ──► Retriever (fan-out + rank + cache)
                                           ──► JSON rows table
```

`server.py` calls `db_users.init_db(...)` once at startup. If `init_db()` has not been called, `db_users` falls back to the in-memory store in `users.py`, which is intended only for unit tests and scripts.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Serves `ui/index.html` |
| `POST` | `/api/signin` | `{ company, id, password }` → `{ name, id, company, admin, token }`. Also sets an HttpOnly `session_token` cookie. |
| `POST` | `/api/retrieve` | `{ query, token }` → `{ title, subtitle, columns, rows, note }`; each row also has `figures` (key numbers for quarterly and customer-behavior reports, otherwise `null`). Rate limited. |
| `POST` | `/api/refresh` | `{ token }` → `{ token }`. Issues a new token and invalidates the old one. |
| `POST` | `/api/analyze/contracts` | Optional `{ ticker, status, quarter \| expiry_from + expiry_to, category, owner, renewal, sole_source, token }` → statistics (`kpis` incl. `at_risk` and `sole_source`, `risk_rule`, `renewal_pipeline`, `concentration`, `outlook`, `timeline`, `by_category`, `by_owner`, `by_ticker`, `top_suppliers`, `buckets`, `rows`, `options`, `scope`). Scoped to the user's companies and, for department-limited accounts, their departments. `403` if a requested company or department is outside that scope. Rate limited. |
| `POST` | `/api/analyze/financials` | Optional `{ ticker, from, to, metric, assessment, basis, token }` (periods like `Q2-2026`; `assessment`: `all`, `improved`, `worsened`, `flat`; `basis`: `yoy` or `qoq`) → `companies` (latest quarter, margin change in bp, `stats`, `series`, `indexed`, `segments`, `scorecard` with the improved/worsened/flat tally, `measures`, `concerns` and per-quarter `history`, `forecast` with the next-quarter estimates and ranges, `surprises`), `ranking`, `periods`, `metrics`, `scope`. `403` for a company outside the user's scope. |
| `POST` | `/api/analyze/behavior` | Optional `{ ticker, year, assessment, token }` (`assessment`: `all`, `improved`, `worsened`, `flat`) → `companies` (revenue, customers, `growth`, `scorecard`, `improved`, `concerns`, `mix_moves`, `rollforward`, `sections`), `comparison`, `years`, `rule`, `scope`. `403` for a company outside the user's scope; `400` for an unknown year or assessment. |
| `POST` | `/api/analyze/alerts` | Optional `{ ticker, token }` → `alerts` (ranked; each with `level`, `kind` of `signal` or `surprise`, `ticker`, `title`, `detail`, `sources`, `evidence`, `screen`), `counts`, `companies`, `scope`. `403` for a company outside the user's scope. |
| `POST` | `/api/analyze/company` | Optional `{ ticker, token }` → `companies` (each with `financials`, `behavior`, `contracts`, `weight`, `signals`, `counts`, `partial_contracts`) and `scope`. `403` for a company outside the user's scope. |
| `GET` / `POST` | `/api/views` | List the signed-in user's saved views / save one `{ name, screen, filters }` (`screen`: `contracts`, `financials`, `behavior`, `company`). Filters are validated by the same rules as the screen; `400` for anything else. |
| `DELETE` | `/api/views/<id>` | Delete one of your own saved views. |
| `GET` / `POST` | `/api/admin/users` | **Admin only.** List accounts (with lock state) and the company/department options / create `{ username, password, document_ids, owners, is_admin }`. |
| `PATCH` / `DELETE` | `/api/admin/users/<username>` | **Admin only.** Change `document_ids`, `owners`, `password` or `is_admin` / delete the account. Cannot target yourself for delete or demotion, or the last admin. |
| `POST` | `/api/admin/users/<username>/unlock` | **Admin only.** Clear a sign-in lockout. |
| `GET` | `/api/admin/audit` | **Admin only.** Query `user`, `event`, `n` → `entries` (newest first), `events`, `flagged`, `total`. |
| `POST` | `/api/signout` | `{ token }` → `{ ok: true }` |
| `GET` | `/api/pdf/<ticker>/<path>` | Serves a PDF. Requires the `session_token` cookie (`401` without it) and `<ticker>` in the user's `document_ids` (`403` otherwise). The file must also belong to `<ticker>`. Department-limited accounts get `403` for `Supplier_Contracts/` files. |
| `GET` | `/api/audit` | Recent audit entries. Requires the `X-Audit-Token` header; disabled unless the `AUDIT_TOKEN` environment variable is set. |

For the JSON endpoints the token is accepted either from the `session_token` cookie or from the `token` field in the JSON body. `/api/pdf` accepts the cookie only, because URLs end up in logs and browser history.

### Security features

| Feature | Behaviour |
|---------|-----------|
| **Password storage** | bcrypt hashes in SQLite. No plain-text passwords in the database. |
| **Sessions** | Random 32-byte hex tokens, 1-hour TTL, stored in memory. Sessions are lost when the server restarts. |
| **Lockout** | After 3 failed sign-ins for the same ID (case-insensitive; the free-text company field is ignored, so it cannot be used to reset the counter), further attempts return `LOCKED` until an administrator unlocks the account or the server is restarted. |
| **Unknown accounts** | Signing in with an ID that does not exist costs the same bcrypt check as a wrong password, so response time does not reveal which IDs exist. |
| **Browser protections** | Every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: no-referrer` and a Content-Security-Policy (same-origin only, no framing by other sites). `/api/*` responses are `no-store`. The session cookie is `HttpOnly` and `SameSite=Strict`. |
| **Debug mode** | Off. The Flask debugger allows code execution, so it only starts when the `FETCH_DEBUG=1` environment variable is set. |
| **Rate limiting** | Max 30 `/api/retrieve` calls per token per 60 seconds. |
| **Document ACLs** | Each user can only search, and download PDFs for, the tickers in their `document_ids`. Knowing another company's PDF URL is not enough: the download is refused with `403`. |
| **Record-level limits** | A user's `owners` list restricts contract data to those departments. It is applied inside the SQL query (`owner IN (...)`) for every analytics call, and any filter the user adds can only narrow it further. The company-wide register PDFs, which list every department, are hidden from limited users' search results and refused on download. |
| **Administrators** | Administration is a separate `is_admin` flag, checked on every `/api/admin/*` call. Admin passwords need 10+ characters, are never logged, and a password change, delete or demotion ends the account's sessions. |
| **Saved views** | Only a screen name and whitelisted filters are stored, never results, so opening a view cannot exceed the user's current access. |
| **Audit log** | `signin`, `signin_fail`, `signout`, `refresh`, `retrieve`, `rate_limited`, `pdf_view`, `pdf_denied`, `analyze`, `analyze_denied`, `view_saved`, `view_deleted`, `admin_denied` and the four `admin_user_*` events are written to `instance/audit.log` as JSON lines. |

The demo passwords above are written in the source code. Change them before deploying anywhere beyond a local demo.

**Before using this beyond a local demo**, these are known limits of the sample:

- The demo accounts (including the administrator `ADM-01`) are created on a fresh database with passwords printed in this README. Replace `DEFAULT_ACCOUNTS` or delete those accounts first.
- The server speaks plain HTTP, so the session cookie has no `Secure` flag. Put it behind HTTPS (and add `Secure` and HSTS) for anything other than `localhost`.
- Flask's built-in server is for development. Use a production WSGI server (for example gunicorn or waitress) behind a reverse proxy.
- Sessions, lockout counters and rate limits live in memory: they reset on restart and are not shared between workers. A lockout also lets anyone who knows an ID lock that account until an administrator unlocks it.
- The sign-in endpoint has no per-address rate limit, only the per-account lockout.
- The page needs `'unsafe-inline'` scripts because the UI is a single file, which limits what the Content-Security-Policy can prevent.
- `instance/audit.log` grows without limit and `instance/*.db` are plain files; protect them with file permissions and back them up.

### Extending

- **New data backend** — subclass `Source`, implement `fetch(query) → list[Document]`.
- **Better relevance** (BM25, embeddings, reranker) — subclass `Ranker`, implement `rank(query, docs)`.
- **Persistent cache** (Redis, Memcached) — match the `TTLCache.get / set` interface.
- **Different user database** (Postgres, etc.) — keep the `get_user` / `register_user` interface in `db_users.py` and swap the SQLite calls inside it.
- **Persistent sessions** — `auth.py` keeps sessions in a per-process dictionary. For multiple workers or restarts, move `_SESSIONS` into a shared store.
