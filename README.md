# retrieval-framework(Fetch)

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
  users.py          UserRecord store with hashed passwords + ticker ACLs
  pull_request.py   PullRequestAgent — ownership-aware retrieval helper

server.py           Flask HTTP server (see Endpoints below)
Data Pull UI.html   Single-page front-end
```

### Request flow

```
Browser ──POST /api/signin──► server.py ──► auth.login() ──► session token
Browser ──POST /api/retrieve─► server.py ──► validate_token()
                                           ──► per-user PDFSource(s)
                                           ──► Retriever (fan-out + rank + cache)
                                           ──► JSON rows table
```

---

## Data

Three fictional companies, each with quarterly PDF reports from Q3-2023 to Q2-2026 plus a summary:

| Ticker | Company |
|--------|---------|
| BLFD | BrightLeaf Foods Co. |
| HDYN | Halcyon Dynamics Inc. |
| MCLD | Meridian Cloudworks Corp. |

PDFs live under `{retrieval\sources,tests,examples}/individual/<TICKER>/`.  
Filename convention: `<TICKER>_<PERIOD>_<ReportType>.pdf`

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Serves `Data Pull UI.html` |
| `POST` | `/api/signin` | `{ company, id, password }` → `{ name, id, company, token }` |
| `POST` | `/api/retrieve` | `{ query, token }` → `{ title, subtitle, columns, rows, note }` |
| `POST` | `/api/signout` | `{ token }` → `{ ok: true }` |

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
pip install flask pypdf
```

**4. Start the server**

```bash
PYTHONPATH=. python server.py
```

**5. Open the app**

Navigate to [http://localhost:5000](http://localhost:5000) in your browser.

**6. Sign in**

Use one of the credentials below:

| ID | Password | Access |
|----|----------|--------|
| `NL-2048` | `password1234` | All companies (BLFD, HDYN, MCLD) |
| `BLFD-01` | `blfd2024` | BrightLeaf Foods only |
| `HDYN-01` | `hdyn2024` | Halcyon Dynamics only |
| `MCLD-01` | `mcld2024` | Meridian Cloudworks only |

**7. Run a query**

Type a search term (e.g. `revenue`, `Q1 2025`, `operating expenses`) and click **Pull**. The results table will populate with matching quarterly reports from your authorised companies.

### Running Tests

```bash
pytest
```

---

## Extending

- **New data backend** — subclass `Source`, implement `fetch(query) → list[Document]`.
- **Better relevance** (BM25, embeddings, reranker) — subclass `Ranker`, implement `rank(docs, query)`.
- **Persistent cache** (Redis, Memcached) — match the `TTLCache.get / set` interface.
- **Real user store** — replace the `_USER_DB` dict in `users.py` with a database call inside `get_user()`.

---

## IBM Bob — AI Pair Programmer

[IBM Bob](https://www.ibm.com/bob) is the AI coding assistant used throughout the development of this project. Bob operates directly inside the workspace and understands the full codebase — files, symbols, and their relationships — without needing manual context-pasting.

### How Bob helped build this application

| Area | What Bob did |
|------|-------------|
| **PDFSource** | Wrote `pdf.py` — lazy directory indexing, filename parsing, `pypdf` text extraction, and per-ticker ACL filtering |
| **Auth layer** | Designed `auth.py` — random 32-byte session tokens, TTL expiry, brute-force lockout counter in `server.py` |
| **Server wiring** | Built `server.py` — Flask routes, per-user scoped `Retriever` construction, cookie + JSON token dual-auth, structured `rows`/`note` response shape |
| **User store** | Seeded `users.py` with SHA-256 hashed passwords and ticker-based document ACLs |
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
