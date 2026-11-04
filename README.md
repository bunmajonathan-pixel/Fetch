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
4. [Manage users and permissions](#4-manage-users-and-permissions)
5. [Add or regenerate data](#5-add-or-regenerate-data)
6. [Run the tests](#6-run-the-tests)
7. [Troubleshooting](#7-troubleshooting)

### 1. Install and start the app

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
| `NL-2048` | `password1234` | All three companies |

`alice` and `bob` also exist as unit-test accounts. They are tied to test document IDs rather than company tickers, so they can sign in but searches return no results.

Type a search into the box and click **Pull**. Try `revenue`, `Q1 2025`, or `operating expenses`. The results table lists the matching reports from your authorised companies:

| Column | Meaning |
|--------|---------|
| Report | Company, period and report type |
| Category | The data folder the PDF came from |
| Matched text | Excerpt around the first hit, matched terms in bold |
| Status | Availability of the file |
| View | Opens the full PDF |

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

### 4. Manage users and permissions

Accounts live in the `users` table of `instance/users.db`:

| Column | Meaning |
|--------|---------|
| `username` | The sign-in ID (case-sensitive) |
| `password_hash` | bcrypt hash |
| `document_ids` | JSON array of tickers the user may search, e.g. `["BLFD","HDYN"]` |

**Change the default accounts.** The defaults are defined once, in `DEFAULT_ACCOUNTS` in `retrieval/users.py`. They are inserted only when the table is empty, so to apply a change delete `instance/users.db` and restart the server.

**Add a user.** Save this as a script in the project root (next to `server.py`) and run it:

```python
from retrieval import db_users

db_users.init_db("instance/users.db")
db_users.register_user("ACME-01", "a-strong-password", ["BLFD", "HDYN"])
```

**Remove a user.**

```bash
python -c "import sqlite3; c=sqlite3.connect('instance/users.db'); c.execute('DELETE FROM users WHERE username=?',('ACME-01',)); c.commit()"
```

If the account is one of the defaults, also remove it from `DEFAULT_ACCOUNTS`, otherwise it comes back when the database is recreated.

### 5. Add or regenerate data

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

The contracts are hand-written in the script, so the output is identical every time. Edit the `CONTRACTS` table to change the sample data.

**Data on disk**

| Ticker | Company | Industry |
|--------|---------|----------|
| BLFD | BrightLeaf Foods Co. | Consumer Packaged Goods |
| HDYN | Halcyon Dynamics Inc. | Industrial Automation |
| MCLD | Meridian Cloudworks Corp. | Enterprise Software |

All three are fictional. `data/` holds quarterly reports (Q3-2023 to Q2-2026) plus summaries, customer-behaviour reports, topic reports, and the supplier-contract registers.

### 6. Run the tests

```bash
pip install pytest
pytest
```

Run it from the project root. `pyproject.toml` already sets the test folder and import path. The suite writes its user database and audit log to a throw-away temp folder (via `FETCH_INSTANCE_DIR`), so it never touches your real `instance/` data.

### 7. Troubleshooting

**Sign-in says "ID or password is incorrect".**
Run the backend check from the project root:

```bash
python tools/check_login.py
```

It prints the database path, the users in it, whether `BLFD-01` exists, whether the password matches, and the result of a full `auth.login()`. Pass your own account to test it: `python tools/check_login.py MY-ID my-password`. If the check works, the problem is between the browser and the server; look for the `[signin] ...` line in the server terminal after clicking Sign in.

**"Missing credentials" (HTTP 400).**
The server received an empty ID or password. Check the **Network** tab in browser dev tools and look at the `signin` request payload.

**"Account locked".**
Three failed attempts lock the account until the server restarts.

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
  db_users.py             SQLite user store (bcrypt hashes + ticker ACLs)
  users.py                UserRecord, DEFAULT_ACCOUNTS, in-memory fallback store
  audit.py                Append-only access log
  pull_request.py         PullRequestAgent — ownership-aware retrieval helper

ui/index.html             Single-page front-end
data/                     PDF reports, one sub-folder per category
tools/                    generate_supplier_contracts.py, check_login.py
examples/demo.py          Auth + retrieval demo (PYTHONPATH=. python examples/demo.py)
tests/                    pytest suite
instance/                 Runtime files: users.db, audit.log (git-ignored, created on first run)
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
| `POST` | `/api/signin` | `{ company, id, password }` → `{ name, id, company, token }`. Also sets an HttpOnly `session_token` cookie. |
| `POST` | `/api/retrieve` | `{ query, token }` → `{ title, subtitle, columns, rows, note }`. Rate limited. |
| `POST` | `/api/refresh` | `{ token }` → `{ token }`. Issues a new token and invalidates the old one. |
| `POST` | `/api/signout` | `{ token }` → `{ ok: true }` |
| `GET` | `/api/pdf/<ticker>/<path>` | Serves a PDF. Requires the `session_token` cookie (`401` without it) and `<ticker>` in the user's `document_ids` (`403` otherwise). The file must also belong to `<ticker>`. |
| `GET` | `/api/audit` | Recent audit entries. Requires the `X-Audit-Token` header; disabled unless the `AUDIT_TOKEN` environment variable is set. |

For the JSON endpoints the token is accepted either from the `session_token` cookie or from the `token` field in the JSON body. `/api/pdf` accepts the cookie only, because URLs end up in logs and browser history.

### Security features

| Feature | Behaviour |
|---------|-----------|
| **Password storage** | bcrypt hashes in SQLite. No plain-text passwords in the database. |
| **Sessions** | Random 32-byte hex tokens, 1-hour TTL, stored in memory. Sessions are lost when the server restarts. |
| **Lockout** | After 3 failed sign-ins for the same company + ID, further attempts return `LOCKED` until the server is restarted. |
| **Rate limiting** | Max 30 `/api/retrieve` calls per token per 60 seconds. |
| **Document ACLs** | Each user can only search, and download PDFs for, the tickers in their `document_ids`. Knowing another company's PDF URL is not enough: the download is refused with `403`. |
| **Audit log** | `signin`, `signin_fail`, `signout`, `refresh`, `retrieve`, `rate_limited`, `pdf_view` and `pdf_denied` events are written to `instance/audit.log` as JSON lines. |

The demo passwords above are written in the source code. Change them before deploying anywhere beyond a local demo.

### Extending

- **New data backend** — subclass `Source`, implement `fetch(query) → list[Document]`.
- **Better relevance** (BM25, embeddings, reranker) — subclass `Ranker`, implement `rank(query, docs)`.
- **Persistent cache** (Redis, Memcached) — match the `TTLCache.get / set` interface.
- **Different user database** (Postgres, etc.) — keep the `get_user` / `register_user` interface in `db_users.py` and swap the SQLite calls inside it.
- **Persistent sessions** — `auth.py` keeps sessions in a per-process dictionary. For multiple workers or restarts, move `_SESSIONS` into a shared store.
