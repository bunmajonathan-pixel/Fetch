# retrieval-framework (Fetch)

A SaaS-style financial document retrieval platform. Users sign in with company credentials, run natural-language queries against their authorised quarterly PDF reports, and get a structured results table back in real time.

---

## Architecture

```
retrieval/
  types.py          Query, Document
  sources/
    base.py         Source ABC
    memory.py       In-memory source
    files.py        Plain-file source
    http.py         HTTP source
    pdf.py          PDFSource — indexes *.pdf directories lazily
    user.py         User-scoped source wrapper
  ranking.py        Ranker ABC + KeywordRanker
  cache.py          TTLCache (in-memory, configurable TTL)
  retriever.py      Pipeline: fan-out → dedupe → rank → cache
  auth.py           Session tokens (login / validate / logout)
  db_users.py       SQLite-backed user store (bcrypt hashes + ticker ACLs)
  users.py          UserRecord dataclass + in-memory fallback store
  audit.py          Append-only access log (audit.log + in-memory buffer)
  pull_request.py   PullRequestAgent — ownership-aware retrieval helper

server.py           Flask HTTP server (see Endpoints below)
Data Pull UI.html   Single-page front-end
test_login.py       Backend diagnostic script (see Troubleshooting)
users.db            SQLite user database (created on first run)
audit.log           JSON-lines access log (created on first run)
```

`users.db` and `audit.log` are created at runtime next to `server.py`. Add them to `.gitignore`.

### Request flow

```
Browser ──POST /api/signin──► server.py ──► auth.login() ──► db_users.get_user() ──► session token
Browser ──POST /api/retrieve─► server.py ──► validate_token() + rate limit
                                           ──► db_users.get_user() (ticker ACL)
                                           ──► per-user PDFSource(s)
                                           ──► Retriever (fan-out + rank + cache)
                                           ──► JSON rows table
```

`server.py` calls `db_users.init_db(...)` once at startup. If `init_db()` has not been called, `db_users` silently falls back to the in-memory store in `users.py`, which is only intended for unit tests.

---

## Data

Three fictional companies, each with quarterly PDF reports from Q3-2023 to Q2-2026 plus a summary:

| Ticker | Company |
|--------|---------|
| BLFD | BrightLeaf Foods Co. |
| HDYN | Halcyon Dynamics Inc. |
| MCLD | Meridian Cloudworks Corp. |

PDFs are read from `DATA_ROOT`, set near the top of `server.py`. As shipped, that folder is `{retrieval\sources,tests,examples}`, with the files under `individual/<TICKER>/`.
Filename convention: `<TICKER>_<PERIOD>_<ReportType>.pdf`

If searches return no rows, check that `DATA_ROOT` points at the folder that actually contains your PDFs.

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Serves `Data Pull UI.html` |
| `POST` | `/api/signin` | `{ company, id, password }` → `{ name, id, company, token }`. Also sets an HttpOnly `session_token` cookie. |
| `POST` | `/api/retrieve` | `{ query, token }` → `{ title, subtitle, columns, rows, note }`. Rate limited. |
| `POST` | `/api/refresh` | `{ token }` → `{ token }`. Issues a new token and invalidates the old one. |
| `POST` | `/api/signout` | `{ token }` → `{ ok: true }` |
| `GET` | `/api/pdf/<ticker>/<path>` | Serves a PDF to a user. The file must belong to `<ticker>`. |
| `GET` | `/api/audit` | Recent audit entries. Requires the `X-Audit-Token` header; disabled unless the `AUDIT_TOKEN` environment variable is set. |

The token is accepted either from the `session_token` cookie or from the `token` field in the JSON body.

---

## Security features

| Feature | Behaviour |
|---------|-----------|
| **Password storage** | bcrypt hashes in SQLite. No plain-text passwords in the database. |
| **Sessions** | Random 32-byte hex tokens, 1-hour TTL, stored in memory. Sessions are lost when the server restarts. |
| **Lockout** | After 3 failed sign-ins for the same company + ID, further attempts return `LOCKED` until the server is restarted. |
| **Rate limiting** | Max 30 `/api/retrieve` calls per token per 60 seconds. |
| **Document ACLs** | Each user can only search the tickers in their `document_ids`. |
| **Audit log** | `signin`, `signin_fail`, `signout`, `refresh`, `retrieve` and `rate_limited` events are written to `audit.log` as JSON lines. |

The demo passwords below are written in the source code. Change them before deploying anywhere beyond a local demo.

---

## Getting Started

### Prerequisites

- Python 3.9+
- [pip](https://pip.pypa.io/)

### Installation

**1. Clone the repository**

```bash
git clone <repo-url>
cd retrieval_framework
```

**2. Create and activate a virtual environment**

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

**3. Install dependencies**

```bash
pip install flask pypdf bcrypt
```

**4. Start the server**

Run it from the `retrieval_framework` folder (the one that contains `server.py` and the `retrieval/` package).

```bash
# macOS / Linux
PYTHONPATH=. python server.py
```

```powershell
# Windows PowerShell
$env:PYTHONPATH="."; python server.py
```

On first run the server creates `users.db` and seeds the demo accounts.

**5. Open the app**

Navigate to [http://localhost:5000](http://localhost:5000) in your browser. Always use this address; opening the HTML file directly or through another dev server will not reach the API.

**6. Sign in**

The sign-in form starts empty. Use one of the credentials below:

| ID | Password | Access |
|----|----------|--------|
| `BLFD-01` | `blfd2024` | BrightLeaf Foods only |
| `HDYN-01` | `hdyn2024` | Halcyon Dynamics only |
| `MCLD-01` | `mcld2024` | Meridian Cloudworks only |

`alice` and `bob` also exist as unit-test accounts. They are tied to test document IDs rather than company tickers, so they can sign in but searches return no results.

The sign-in page now tells the difference between failures: a wrong ID or password shows "incorrect", a locked account shows "Account locked", and a connection or server error shows "Can't reach the server" with the HTTP status.

**7. Run a query**

Type a search term (e.g. `revenue`, `Q1 2025`, `operating expenses`) and click **Pull**. The results table will populate with matching quarterly reports from your authorised companies.

### Running Tests

```bash
pytest
```

---

## Managing users

Accounts live in the `users` table of `users.db`:

| Column | Meaning |
|--------|---------|
| `username` | The sign-in ID (case-sensitive) |
| `password_hash` | bcrypt hash |
| `document_ids` | JSON array of tickers the user may search, e.g. `["BLFD","HDYN"]` |

**Seeding.** The default accounts in `_seed()` (`db_users.py`) are inserted only when the table is completely empty. To reseed, delete `users.db` and restart the server.

**Add a user.** Save this as a script in the project root (next to `server.py`, not inside `retrieval/`) and run it:

```python
from retrieval import db_users

db_users.init_db("users.db")
db_users.register_user("ACME-01", "a-strong-password", ["BLFD", "HDYN"])
```

**Remove a user.**

```bash
python -c "import sqlite3; c=sqlite3.connect('users.db'); c.execute('DELETE FROM users WHERE username=?',('ACME-01',)); c.commit()"
```

If the account is one of the seeded defaults, also remove it from the `defaults` list in `_seed()` in `db_users.py` and from `_USER_DB` in `users.py`. Otherwise it comes back when the database is recreated or the in-memory fallback is used.

---

## Troubleshooting

**Sign-in says "ID or password is incorrect".**
Run the backend check from the project root:

```bash
python test_login.py
```

It prints the database path, the users in it, whether `BLFD-01` exists, whether the password matches, and the result of a full `auth.login()`. If that works, the problem is between the browser and the server. Look for the `[signin] ...` line in the server terminal after clicking Sign in; it shows the ID the server received, the password length, and whether the user was found.

**"Missing credentials" (HTTP 400).**
The server received an empty ID or password. Check the **Network** tab in browser dev tools and look at the `signin` request payload.

**"Account locked".**
Three failed attempts lock the account until the server restarts.

**`ImportError: attempted relative import with no known parent package`.**
You ran a file inside `retrieval/` directly. Modules in that folder use relative imports and must be imported, not run. Run `server.py` or `test_login.py` from the project root instead.

**VS Code shows `Import "retrieval.users" could not be resolved`.**
Open the `retrieval_framework` folder (not `retrieval`) as the workspace, and make sure `retrieval/__init__.py` exists.

**Changes don't seem to take effect, or the server won't start on port 5000.**
An old server process may still be holding the port. On Windows:

```powershell
netstat -ano | findstr :5000
taskkill /PID <pid> /F
```

Repeat until `netstat` prints nothing, then start the server again. If a ghost entry lingers, change the port in the last line of `server.py`.

**Searches return no rows.**
Check `DATA_ROOT` in `server.py` and confirm the user has the right tickers in `document_ids`.

---

## Extending

- **New data backend** — subclass `Source`, implement `fetch(query) → list[Document]`.
- **Better relevance** (BM25, embeddings, reranker) — subclass `Ranker`, implement `rank(docs, query)`.
- **Persistent cache** (Redis, Memcached) — match the `TTLCache.get / set` interface.
- **Different user database** (Postgres, etc.) — keep the `get_user` / `register_user` interface in `db_users.py` and swap the SQLite calls inside it.
- **Persistent sessions** — `auth.py` keeps sessions in a per-process dictionary. For multiple workers or restarts, move `_SESSIONS` into a shared store.

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
