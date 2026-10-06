"""
RAT - Repo Analysis Tool - Main FastAPI Application
"""

import os
import sys
import json
import csv
import io
import time
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import (
    init_db, add_repo, get_repos, get_repo_by_name, get_metrics,
    get_authors, get_files, get_directories, add_author_merge,
    get_author_merges, delete_repo, store_commits, store_metrics
)
from app.metrics import parse_log_with_numstat, compute_metrics, export_to_csv
from app.repo_manager import (
    extract_zip, clone_remote, get_ref_sha, parse_mailmap, cleanup_repo, REPOS_DIR
)

app = FastAPI(title="RAT - Repo Analysis Tool")

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.on_event("startup")
async def startup():
    await init_db()


async def build_author_mapping(repo_id: int, repo_path: str) -> Dict:
    """Build combined author mapping from .mailmap + manual merges."""
    mapping = {}
    # Apply .mailmap
    mailmap = parse_mailmap(repo_path)
    mapping.update(mailmap)
    # Apply manual merges
    merges = await get_author_merges(repo_id)
    for m in merges:
        mapping[(m["source_name"], m["source_email"])] = (m["target_name"], m["target_email"])
    return mapping


async def recompute_metrics(repo_id: int, repo_name: str, repo_path: str, ref_sha: str):
    """Recompute and store metrics for a repo (used after author merges)."""
    commits = parse_log_with_numstat(repo_path)
    mapping = await build_author_mapping(repo_id, repo_path)
    metrics = compute_metrics(commits, mapping)
    csv_rows = export_to_csv(metrics, repo_name, ref_sha)
    await store_metrics(repo_id, csv_rows)
    return metrics


# ─── Pages ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    repos = await get_repos()
    return templates.TemplateResponse("index.html", {"request": request, "repos": repos})


@app.get("/repo/{repo_name}", response_class=HTMLResponse)
async def repo_dashboard(request: Request, repo_name: str):
    repo = await get_repo_by_name(repo_name)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    authors = await get_authors(repo["id"])
    file_list = await get_files(repo["id"])
    dir_list = await get_directories(repo["id"])
    merges = await get_author_merges(repo["id"])
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "repo": repo,
        "authors": authors,
        "files": file_list,
        "directories": dir_list,
        "merges": merges,
    })


# ─── API: Repository Management ─────────────────────────────────────────────

@app.post("/api/repos/upload")
async def upload_repo(
    file: UploadFile = File(...),
    repo_name: str = Form(...)
):
    """Upload a zip file of a git repository."""
    if not file.filename.endswith('.zip'):
        raise HTTPException(status_code=400, detail="Only .zip files are accepted")
    
    # Save uploaded file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        repo_path = extract_zip(tmp_path, repo_name)
        ref_sha = get_ref_sha(repo_path)
        
        # Parse and compute metrics (with .mailmap applied)
        commits = parse_log_with_numstat(repo_path)
        repo_id = await add_repo(repo_name, repo_path, ref_sha, len(commits))
        mapping = await build_author_mapping(repo_id, repo_path)
        metrics = compute_metrics(commits, mapping)
        
        # Store in database
        await store_commits(repo_id, commits)
        
        # Export and store metrics
        csv_rows = export_to_csv(metrics, repo_name, ref_sha)
        await store_metrics(repo_id, csv_rows)
        
        return {"status": "success", "repo_name": repo_name, "commit_count": len(commits)}
    except Exception as e:
        cleanup_repo(repo_name)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        os.unlink(tmp_path)


@app.post("/api/repos/clone")
async def clone_repo(
    url: str = Form(...),
    repo_name: str = Form(...)
):
    """Clone a remote repository."""
    try:
        repo_path = clone_remote(url, repo_name)
        ref_sha = get_ref_sha(repo_path)
        
        commits = parse_log_with_numstat(repo_path)
        repo_id = await add_repo(repo_name, repo_path, ref_sha, len(commits))
        mapping = await build_author_mapping(repo_id, repo_path)
        metrics = compute_metrics(commits, mapping)
        
        await store_commits(repo_id, commits)
        
        csv_rows = export_to_csv(metrics, repo_name, ref_sha)
        await store_metrics(repo_id, csv_rows)
        
        return {"status": "success", "repo_name": repo_name, "commit_count": len(commits)}
    except Exception as e:
        cleanup_repo(repo_name)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/repos/{repo_name}")
async def remove_repo(repo_name: str):
    """Delete a repository."""
    repo = await get_repo_by_name(repo_name)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    cleanup_repo(repo_name)
    await delete_repo(repo["id"])
    return {"status": "deleted"}


@app.get("/api/repos")
async def list_repos():
    """List all repositories."""
    return await get_repos()


# ─── API: Metrics ────────────────────────────────────────────────────────────

@app.get("/api/repos/{repo_name}/metrics")
async def get_repo_metrics(
    repo_name: str,
    object_type: Optional[str] = Query(None),
    path: Optional[str] = Query(None),
    author: Optional[str] = Query(None),
    time_from: Optional[int] = Query(None),
    time_to: Optional[int] = Query(None),
    commits: Optional[str] = Query(None),
):
    """Get metrics for a repository with optional filtering."""
    repo = await get_repo_by_name(repo_name)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    commit_hashes = None
    if commits:
        commit_hashes = [c.strip() for c in commits.split(",") if c.strip()]
    
    metrics = await get_metrics(
        repo["id"],
        object_type=object_type,
        path=path,
        author=author,
        time_from=time_from,
        time_to=time_to,
        commit_hashes=commit_hashes,
    )
    
    return {"metrics": metrics, "commit_count": repo["commit_count"]}


@app.get("/api/repos/{repo_name}/metrics/csv")
async def get_metrics_csv(
    repo_name: str,
    object_type: Optional[str] = Query(None),
    path: Optional[str] = Query(None),
    author: Optional[str] = Query(None),
):
    """Export metrics as CSV."""
    repo = await get_repo_by_name(repo_name)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    metrics = await get_metrics(
        repo["id"],
        object_type=object_type,
        path=path,
        author=author,
    )
    
    commit_count = repo["commit_count"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["object_type", "path", "author", "added", "removed", 
                      "growth", "churn", "modifications", "modification_frequency", "churn_rate", "ownership"])
    
    for m in metrics:
        mod_freq = ""
        churn_rate = ""
        ownership = m.get("ownership", "")
        if m["author_key"] == "ALL":
            mod_freq = round(m["modifications"] / commit_count, 15) if commit_count > 0 else 0
            churn_rate = round(m["churn"] / commit_count, 15) if commit_count > 0 else 0
            ownership = ""
        writer.writerow([
            m["object_type"], m["object_path"], m["author_key"],
            m["added"], m["removed"], m["growth"], m["churn"],
            m["modifications"], mod_freq, churn_rate, ownership
        ])
    
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={repo_name}_metrics.csv"}
    )


# ─── API: Visualizations ────────────────────────────────────────────────────

@app.get("/api/repos/{repo_name}/timeline")
async def get_timeline(repo_name: str, buckets: int = Query(50)):
    """Get churn over time (grouped into buckets)."""
    repo = await get_repo_by_name(repo_name)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    import aiosqlite
    from app.database import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Get all commits with their total churn
        async with db.execute("""
            SELECT c.committer_date, COALESCE(SUM(cfc.added + cfc.removed), 0) as churn
            FROM commits c
            LEFT JOIN commit_file_changes cfc ON c.hash = cfc.commit_hash AND cfc.repo_id = c.repo_id
            WHERE c.repo_id = ?
            GROUP BY c.hash
            ORDER BY c.committer_date
        """, (repo["id"],)) as cursor:
            rows = await cursor.fetchall()
    
    if not rows:
        return {"timeline": []}
    
    # Group into buckets
    n = len(rows)
    bucket_size = max(1, n // buckets)
    timeline = []
    for i in range(0, n, bucket_size):
        chunk = rows[i:i+bucket_size]
        ts = chunk[len(chunk)//2]["committer_date"]
        churn = sum(r["churn"] for r in chunk)
        timeline.append({"timestamp": ts, "churn": churn, "commits": len(chunk)})
    
    return {"timeline": timeline}


# ─── API: Authors ────────────────────────────────────────────────────────────

@app.get("/api/repos/{repo_name}/authors")
async def list_authors(repo_name: str):
    """Get unique authors for a repository."""
    repo = await get_repo_by_name(repo_name)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    return await get_authors(repo["id"])


@app.post("/api/repos/{repo_name}/authors/merge")
async def merge_authors(
    repo_name: str,
    source_name: str = Form(...),
    source_email: str = Form(...),
    target_name: str = Form(...),
    target_email: str = Form(...)
):
    """Add an author merge rule and recompute metrics."""
    repo = await get_repo_by_name(repo_name)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    await add_author_merge(repo["id"], source_name, source_email, target_name, target_email)
    # Recompute metrics with the new mapping
    await recompute_metrics(repo["id"], repo_name, repo["path"], repo["ref_sha"])
    return {"status": "merged"}


@app.get("/api/repos/{repo_name}/authors/merges")
async def list_merges(repo_name: str):
    """Get author merge rules."""
    repo = await get_repo_by_name(repo_name)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    return await get_author_merges(repo["id"])


# ─── API: Files & Directories ────────────────────────────────────────────────

@app.get("/api/repos/{repo_name}/files")
async def list_files(repo_name: str):
    repo = await get_repo_by_name(repo_name)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    return await get_files(repo["id"])


@app.get("/api/repos/{repo_name}/directories")
async def list_directories(repo_name: str):
    repo = await get_repo_by_name(repo_name)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    return await get_directories(repo["id"])



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
