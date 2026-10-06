"""
Database layer for RAT - manages repositories, metrics storage, and author merging.
"""

import aiosqlite
import os
from typing import List, Dict, Optional, Any
from datetime import datetime


DB_PATH = os.path.join(os.path.dirname(__file__), "..", "rat.db")


async def init_db():
    """Initialize the database schema."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS repos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                path TEXT NOT NULL,
                ref_sha TEXT,
                commit_count INTEGER DEFAULT 0,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS commits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL,
                hash TEXT NOT NULL,
                prev_hash TEXT,
                author_name TEXT NOT NULL,
                author_email TEXT NOT NULL,
                committer_date INTEGER NOT NULL,
                FOREIGN KEY (repo_id) REFERENCES repos(id),
                UNIQUE(repo_id, hash)
            );

            CREATE TABLE IF NOT EXISTS commit_file_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL,
                commit_hash TEXT NOT NULL,
                file_path TEXT NOT NULL,
                added INTEGER DEFAULT 0,
                removed INTEGER DEFAULT 0,
                is_binary INTEGER DEFAULT 0,
                FOREIGN KEY (repo_id) REFERENCES repos(id),
                FOREIGN KEY (commit_hash) REFERENCES commits(hash)
            );

            CREATE TABLE IF NOT EXISTS object_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL,
                object_type TEXT NOT NULL,  -- 'file', 'directory', 'repository'
                object_path TEXT NOT NULL,
                author_key TEXT NOT NULL,
                added INTEGER DEFAULT 0,
                removed INTEGER DEFAULT 0,
                growth INTEGER DEFAULT 0,
                churn INTEGER DEFAULT 0,
                modifications INTEGER DEFAULT 0,
                ownership REAL DEFAULT 0,
                FOREIGN KEY (repo_id) REFERENCES repos(id),
                UNIQUE(repo_id, object_type, object_path, author_key)
            );

            CREATE TABLE IF NOT EXISTS author_merges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL,
                source_name TEXT NOT NULL,
                source_email TEXT NOT NULL,
                target_name TEXT NOT NULL,
                target_email TEXT NOT NULL,
                FOREIGN KEY (repo_id) REFERENCES repos(id),
                UNIQUE(repo_id, source_name, source_email)
            );

            CREATE INDEX IF NOT EXISTS idx_commits_repo ON commits(repo_id);
            CREATE INDEX IF NOT EXISTS idx_commits_date ON commits(committer_date);
            CREATE INDEX IF NOT EXISTS idx_cfc_repo ON commit_file_changes(repo_id);
            CREATE INDEX IF NOT EXISTS idx_cfc_commit ON commit_file_changes(commit_hash);
            CREATE INDEX IF NOT EXISTS idx_om_repo ON object_metrics(repo_id);
            CREATE INDEX IF NOT EXISTS idx_om_object ON object_metrics(object_type, object_path);
        """)
        await db.commit()


async def add_repo(name: str, path: str, ref_sha: str = None, commit_count: int = 0) -> int:
    """Add a repository record. Returns repo id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT OR REPLACE INTO repos (name, path, ref_sha, commit_count) VALUES (?, ?, ?, ?)",
            (name, path, ref_sha, commit_count)
        )
        await db.commit()
        return cursor.lastrowid


async def get_repos() -> List[Dict]:
    """Get all repositories."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM repos ORDER BY added_at DESC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_repo_by_name(name: str) -> Optional[Dict]:
    """Get a repository by name."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM repos WHERE name = ?", (name,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def store_commits(repo_id: int, commits: List[Dict]):
    """Store commits and their file changes using batch inserts for performance."""
    commit_rows = []
    fc_rows = []
    for commit in commits:
        commit_rows.append((
            repo_id, commit["hash"], commit.get("prev_hash"),
            commit["author_name"], commit["author_email"], commit["committer_date"]
        ))
        for fc in commit["files"]:
            if fc["is_binary"]:
                continue
            fc_rows.append((
                repo_id, commit["hash"], fc["path"],
                fc["added"], fc["removed"], 1 if fc["is_binary"] else 0
            ))
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT OR IGNORE INTO commits (repo_id, hash, prev_hash, author_name, author_email, committer_date) VALUES (?, ?, ?, ?, ?, ?)",
            commit_rows
        )
        await db.executemany(
            "INSERT INTO commit_file_changes (repo_id, commit_hash, file_path, added, removed, is_binary) VALUES (?, ?, ?, ?, ?, ?)",
            fc_rows
        )
        await db.commit()


async def store_metrics(repo_id: int, metrics_data: List[Dict]):
    """Store pre-computed object metrics (clears existing rows for the repo first)."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Clear existing metrics for this repo (needed for recompute after merges)
        await db.execute("DELETE FROM object_metrics WHERE repo_id = ?", (repo_id,))
        rows = []
        for row in metrics_data:
            ownership = row.get("ownership", "")
            ownership_val = float(ownership) if ownership != "" else 0.0
            rows.append((repo_id, row["object_type"], row["path"], row["author"],
                         row["added"], row["removed"], row["growth"], row["churn"],
                         row["modifications"], ownership_val))
        await db.executemany(
            """INSERT OR REPLACE INTO object_metrics 
               (repo_id, object_type, object_path, author_key, added, removed, growth, churn, modifications, ownership) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows
        )
        await db.commit()


async def get_metrics(repo_id: int, object_type: str = None, path: str = None, 
                      author: str = None, time_from: int = None, time_to: int = None,
                      commit_hashes: List[str] = None) -> List[Dict]:
    """
    Get metrics with optional filtering.
    
    For time/commit filtering, we need to recompute from raw data since
    stored metrics are for 'all' commits.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # If no filtering needed, return stored metrics directly
        if time_from is None and time_to is None and commit_hashes is None:
            query = "SELECT * FROM object_metrics WHERE repo_id = ?"
            params = [repo_id]
            
            if object_type:
                query += " AND object_type = ?"
                params.append(object_type)
            if path:
                query += " AND object_path = ?"
                params.append(path)
            if author and author != "ALL":
                query += " AND author_key = ?"
                params.append(author)
            
            query += " ORDER BY object_type, object_path, author_key"
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
        
        # For filtered queries, recompute from raw commit data
        return await _compute_filtered_metrics(db, repo_id, object_type, path, author, 
                                                time_from, time_to, commit_hashes)


async def _compute_filtered_metrics(db, repo_id, object_type, path, author, 
                                     time_from, time_to, commit_hashes):
    """Compute metrics from raw data for filtered commit sets."""
    # Build commit filter
    commit_query = "SELECT hash, author_name, author_email, committer_date FROM commits WHERE repo_id = ?"
    commit_params = [repo_id]
    
    if time_from is not None:
        commit_query += " AND committer_date >= ?"
        commit_params.append(time_from)
    if time_to is not None:
        commit_query += " AND committer_date < ?"
        commit_params.append(time_to)
    if commit_hashes:
        placeholders = ",".join("?" for _ in commit_hashes)
        commit_query += f" AND hash IN ({placeholders})"
        commit_params.extend(commit_hashes)
    
    async with db.execute(commit_query, commit_params) as cursor:
        filtered_commits = await cursor.fetchall()
    
    if not filtered_commits:
        return []
    
    commit_set = {r["hash"] for r in filtered_commits}
    commit_count = len(commit_set)
    
    # Get file changes for these commits
    hash_placeholders = ",".join("?" for _ in commit_set)
    fc_query = f"""
        SELECT commit_hash, file_path, added, removed 
        FROM commit_file_changes 
        WHERE repo_id = ? AND commit_hash IN ({hash_placeholders})
    """
    async with db.execute(fc_query, [repo_id] + list(commit_set)) as cursor:
        file_changes = await cursor.fetchall()
    
    # Aggregate metrics
    metrics = {}  # (object_type, path, author) -> {added, removed, mods}
    
    # O(1) author lookup per file-change (avoids an O(files x commits) scan)
    author_by_hash = {r["hash"]: (r["author_name"], r["author_email"]) for r in filtered_commits}
    
    for fc in file_changes:
        commit_hash = fc["commit_hash"]
        file_path = fc["file_path"]
        added = fc["added"]
        removed = fc["removed"]
        
        author_name, author_email = author_by_hash[commit_hash]
        auth_key = f"{author_name} <{author_email}>"
        
        # File metric
        key = ("file", file_path, auth_key)
        if key not in metrics:
            metrics[key] = {"added": 0, "removed": 0, "mods": set()}
        metrics[key]["added"] += added
        metrics[key]["removed"] += removed
        metrics[key]["mods"].add(commit_hash)
        
        # Directory metrics (all ancestors)
        parts = file_path.split("/")
        for i in range(len(parts)):
            dir_path = "/".join(parts[:i])
            dkey = ("directory", dir_path, auth_key)
            if dkey not in metrics:
                metrics[dkey] = {"added": 0, "removed": 0, "mods": set()}
            metrics[dkey]["added"] += added
            metrics[dkey]["removed"] += removed
            metrics[dkey]["mods"].add(commit_hash)
        
        # Repository metric (root)
        rkey = ("repository", "/", auth_key)
        if rkey not in metrics:
            metrics[rkey] = {"added": 0, "removed": 0, "mods": set()}
        metrics[rkey]["added"] += added
        metrics[rkey]["removed"] += removed
        metrics[rkey]["mods"].add(commit_hash)
    
    # Build result rows
    rows = []
    for (obj_type, obj_path, auth_key), m in sorted(metrics.items()):
        if object_type and obj_type != object_type:
            continue
        if path and obj_path != path:
            continue
        if author and author != "ALL" and auth_key != author:
            continue
        
        mod_count = len(m["mods"])
        mod_freq = mod_count / commit_count if commit_count > 0 else 0
        churn = m["added"] + m["removed"]
        churn_rate = churn / commit_count if commit_count > 0 else 0
        
        rows.append({
            "object_type": obj_type,
            "object_path": obj_path,
            "author_key": auth_key,
            "added": m["added"],
            "removed": m["removed"],
            "growth": m["added"] - m["removed"],
            "churn": churn,
            "modifications": mod_count,
            "modification_frequency": round(mod_freq, 15),
            "churn_rate": round(churn_rate, 15),
        })
    
    # Add ALL rows
    all_metrics = {}
    for (obj_type, obj_path, auth_key), m in metrics.items():
        key = (obj_type, obj_path)
        if key not in all_metrics:
            all_metrics[key] = {"added": 0, "removed": 0, "mods": set(), "author_churn": {}}
        all_metrics[key]["added"] += m["added"]
        all_metrics[key]["removed"] += m["removed"]
        all_metrics[key]["mods"] |= m["mods"]
        all_metrics[key]["author_churn"][auth_key] = m["added"] + m["removed"]
    
    for (obj_type, obj_path), am in sorted(all_metrics.items()):
        if object_type and obj_type != object_type:
            continue
        if path and obj_path != path:
            continue
        
        mod_count = len(am["mods"])
        total_churn = am["added"] + am["removed"]
        mod_freq = mod_count / commit_count if commit_count > 0 else 0
        churn_rate = total_churn / commit_count if commit_count > 0 else 0
        
        rows.append({
            "object_type": obj_type,
            "object_path": obj_path,
            "author_key": "ALL",
            "added": am["added"],
            "removed": am["removed"],
            "growth": am["added"] - am["removed"],
            "churn": total_churn,
            "modifications": mod_count,
            "modification_frequency": round(mod_freq, 15),
            "churn_rate": round(churn_rate, 15),
        })
    
    return rows


async def get_authors(repo_id: int) -> List[str]:
    """Get unique authors for a repository."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT author_key FROM object_metrics WHERE repo_id = ? AND author_key != 'ALL' ORDER BY author_key",
            (repo_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def get_files(repo_id: int) -> List[str]:
    """Get unique file paths for a repository."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT object_path FROM object_metrics WHERE repo_id = ? AND object_type = 'file' ORDER BY object_path",
            (repo_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def get_directories(repo_id: int) -> List[str]:
    """Get unique directory paths for a repository."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT object_path FROM object_metrics WHERE repo_id = ? AND object_type = 'directory' ORDER BY object_path",
            (repo_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def add_author_merge(repo_id: int, source_name: str, source_email: str, 
                           target_name: str, target_email: str):
    """Add an author merge rule."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO author_merges 
               (repo_id, source_name, source_email, target_name, target_email) 
               VALUES (?, ?, ?, ?, ?)""",
            (repo_id, source_name, source_email, target_name, target_email)
        )
        await db.commit()


async def get_author_merges(repo_id: int) -> List[Dict]:
    """Get author merge rules for a repository."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM author_merges WHERE repo_id = ?", (repo_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def delete_repo(repo_id: int):
    """Delete a repository and all its data."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM object_metrics WHERE repo_id = ?", (repo_id,))
        await db.execute("DELETE FROM commit_file_changes WHERE repo_id = ?", (repo_id,))
        await db.execute("DELETE FROM commits WHERE repo_id = ?", (repo_id,))
        await db.execute("DELETE FROM author_merges WHERE repo_id = ?", (repo_id,))
        await db.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
        await db.commit()
