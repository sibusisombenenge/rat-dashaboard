# RAT - Repo Analysis Tool

A web-app dashboard for measuring and visualizing Git repository metrics. Built for COMS3011A.

RAT ingests Git repositories (zip upload or remote clone), computes the full set of
file / directory / repository / commit-set / author metrics defined in the course
specification, and presents them in an interactive, filterable dashboard with charts.

## Features

- **Repository Ingestion**
  - Upload a `.zip` of a repository (must contain the `.git` directory)
  - Clone from a remote URL (e.g. `https://github.com/user/repo.git`)
- **Multi-Repository Support**: ingest, list, switch between, and delete many repositories
- **Author Merging**
  - Automatic via a repository's `.mailmap`
  - Manual merge rules (source identity → target identity) with full metric recomputation
- **Filtering**
  - By repository, author, file/directory
  - By commit set: a time period (from/to) **or** a manually selected list of commit SHAs
- **Metric Categories**
  - File metrics (added/removed lines, growth, churn, modifications, frequency, churn rate)
  - Directory metrics (aggregated over all contained files, recursively)
  - Repository metrics (root-level aggregation)
  - Commit-set metrics (metrics over any filtered commit range)
  - Author metrics (per-author churn, modifications, and ownership)
- **Visualizations**: bar (top objects by churn), doughnut (author ownership), line (churn over time)
- **Usability**
  - Metrics table contained in a scrollable view with a sticky header and row count
  - CSV export of any filtered metric set
  - Clear error handling and loading states throughout

## Quick Start

```bash
pip install -r requirements.txt
python -m app
```

Then open http://localhost:8000 in your browser.

## Usage

1. **Add a Repository**: upload a `.zip` (with `.git`) or paste a remote URL to clone.
2. **View Metrics**: open a repository dashboard to see all metric categories and charts.
3. **Filter**: narrow by object type, author, file/directory, time period, or a manual commit list.
4. **Merge Authors**: combine duplicate author identities; metrics recompute automatically.
5. **Export**: download the current metric set as CSV.

## Metrics

All metrics follow the formal definitions from the COMS3011A specification:

| Metric | Description |
|--------|-------------|
| Added Lines | Lines added in a commit |
| Removed Lines | Lines removed in a commit |
| Growth | Net change in lines (added − removed) |
| Churn | Total changed lines (added + removed) |
| Modifications | Number of commits touching an object |
| Modification Frequency | Modifications / total commits (\|H\|) |
| Churn Rate | Churn / total commits (\|H\|) |
| Ownership | Author's churn / total churn on the object |

Binary files are excluded from line metrics; renames are detected at a 50% similarity
threshold so history is followed across file moves.

## Architecture & Performance

- **Backend**: Python + FastAPI (async), SQLite via `aiosqlite`.
- **Git processing**: a single `git log --numstat --find-renames=50` pass parsed in one
  streaming loop — no repeated subprocess calls per commit.
- **Storage**: batched `executemany` inserts and database indexes on the hot columns
  (repo, commit hash, date, object path) keep ingestion fast on large histories.
- **Metrics**: pre-computed on ingest and stored; filtered commit-set queries recompute
  only the requested slice from raw commit data.
- Designed to stay responsive on repositories with ~100,000 commits.

## Project Structure

```
app/
  main.py          # FastAPI app: pages + REST API
  metrics.py       # Git parsing + metric computation engine
  database.py      # SQLite schema + queries
  repo_manager.py  # zip extraction, cloning, .mailmap parsing
  templates/       # Jinja2 HTML pages (index, dashboard)
  static/          # CSS + JS + assets
test_metrics.py    # Validates computed metrics against reference CSVs
```

## Testing

`test_metrics.py` clones a pinned reference commit and compares every computed metric
value against the provided reference CSV to confirm correctness.

## Tech Stack

- **Backend**: Python + FastAPI
- **Database**: SQLite (via aiosqlite)
- **Git Processing**: Git CLI (subprocess)
- **Frontend**: HTML + CSS + JavaScript (vanilla) + Chart.js
