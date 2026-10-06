"""
Git Metrics Engine - Parses git history and computes all RAT metrics.

Uses `git log --numstat` for efficient single-pass parsing with rename detection.
Computes File, Directory, Repository, Commit Set, and Author metrics per spec.
"""

import subprocess
import os
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Any


def run_git(repo_path: str, args: list) -> str:
    """Run a git command in the given repo path and return stdout."""
    result = subprocess.run(
        ["git"] + args,
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=300
    )
    if result.returncode != 0:
        raise RuntimeError(f"Git command failed: {' '.join(args)}\n{result.stderr}")
    return result.stdout


def get_commit_count(repo_path: str) -> int:
    """Get total number of non-merge commits."""
    out = run_git(repo_path, ["rev-list", "--no-merges", "--count", "HEAD"])
    return int(out.strip())


def parse_log_with_numstat(repo_path: str) -> List[Dict]:
    """
    Parse git log with numstat in reverse chronological order (newest first).
    
    Returns list of commit dicts:
    {
        'hash': str,
        'author_name': str,
        'author_email': str,
        'committer_date': int (unix timestamp),
        'prev_hash': str or None,
        'files': [
            {
                'path': str,          # new path (after rename)
                'added': int,
                'removed': int,
                'is_binary': bool,
                'is_rename': bool,
                'old_path': str or None,
            }, ...
        ]
    }
    
    Uses --find-renames=50 for rename detection at 50% similarity threshold.
    Binary files are detected via '-' in numstat output.
    """
    # Format: hash|author_name|author_email|committer_timestamp|prev_hash
    fmt = "%H|%an|%ae|%ct|%P"
    cmd = [
        "log",
        "--no-merges",
        "--reverse",           # oldest first (so we can track file existence)
        f"--format={fmt}",
        "--numstat",
        "--find-renames=50",
        "--diff-filter=ACDMRT",  # All change types
        "HEAD"
    ]
    
    raw = run_git(repo_path, cmd)
    lines = raw.split("\n")
    
    commits = []
    current_commit = None
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Check if this is a commit header line (contains | separators)
        if "|" in line and not line.startswith("\t"):
            parts = line.split("|")
            if len(parts) >= 5:
                commit_hash = parts[0].strip()
                author_name = parts[1].strip()
                author_email = parts[2].strip()
                committer_ts = int(parts[3].strip())
                parents = parts[4].strip().split()
                prev_hash = parents[0] if parents else None
                
                current_commit = {
                    "hash": commit_hash,
                    "author_name": author_name,
                    "author_email": author_email,
                    "committer_date": committer_ts,
                    "prev_hash": prev_hash,
                    "files": []
                }
                commits.append(current_commit)
                i += 1
                continue
        
        # Check if this is a numstat line (starts with tab or has tab-separated numbers)
        if current_commit is not None and ("\t" in line or line.startswith("-")):
            parts = line.split("\t")
            if len(parts) >= 3:
                added_str = parts[0].strip()
                removed_str = parts[1].strip()
                path = parts[2].strip()
                
                # Handle binary files (shown as "-")
                is_binary = (added_str == "-" and removed_str == "-")
                added = 0 if is_binary else int(added_str)
                removed = 0 if is_binary else int(removed_str)
                
                # Detect renames: path contains "=>" 
                is_rename = " => " in path
                old_path = None
                if is_rename:
                    old_path, path = _parse_rename_path(path)
                
                # Skip entries with 0 added and 0 removed (no actual content change)
                # This includes pure renames - they don't contribute to line metrics
                if added == 0 and removed == 0 and not is_binary:
                    i += 1
                    continue
                
                current_commit["files"].append({
                    "path": path,
                    "added": added,
                    "removed": removed,
                    "is_binary": is_binary,
                    "is_rename": is_rename,
                    "old_path": old_path,
                })
        
        i += 1
    
    return commits


def _parse_rename_path(path: str) -> Tuple[str, str]:
    """
    Parse a git rename path like 'old.txt => new.txt' or '{a => b}/c.txt'.
    Returns (old_path, new_path).
    """
    if " => " not in path:
        return None, path
    
    # Simple case: "old.txt => new.txt"
    if "{" not in path:
        parts = path.split(" => ")
        return parts[0].strip(), parts[1].strip()
    
    # Brace case: "{old => new}/suffix.txt" or "prefix/{a => b}.txt"
    brace_start = path.index("{")
    brace_end = path.index("}")
    prefix = path[:brace_start]
    suffix = path[brace_end + 1:]
    middle = path[brace_start + 1:brace_end]
    
    if " => " in middle:
        old_part, new_part = middle.split(" => ")
        old_path = prefix + old_part.strip() + suffix
        new_path = prefix + new_part.strip() + suffix
        return old_path, new_path
    
    return None, path


def compute_metrics(commits: List[Dict], author_mapping: Dict = None) -> Dict[str, Any]:
    """
    Compute all metrics from parsed commits.
    
    author_mapping: optional dict mapping (name, email) -> (canonical_name, canonical_email)
                    used for .mailmap and manual author merging.
    
    Returns a dict with:
    - 'commit_count': total commits
    - 'file_metrics': {(path, author_key): {added, removed, growth, churn, modifications}}
    - 'dir_metrics': {(dir_path, author_key): {added, removed, growth, churn, modifications}}
    - 'repo_metrics': {author_key: {added, removed, growth, churn, modifications}}
    - 'authors': set of author keys
    - 'files': set of file paths
    - 'dirs': set of directory paths
    """
    if author_mapping is None:
        author_mapping = {}
    
    # Track file existence across commits (for deletion detection)
    known_files = set()  # files that exist after processing each commit
    
    # Accumulators: (object_path, author_key) -> metrics
    file_added = defaultdict(int)
    file_removed = defaultdict(int)
    file_modifications = defaultdict(set)  # track which commits touched each object
    
    author_keys = {}  # (name, email) -> formatted key
    
    def get_author_key(name: str, email: str) -> str:
        # Apply author mapping (mailmap / manual merge)
        if (name, email) in author_mapping:
            name, email = author_mapping[(name, email)]
        key = f"{name} <{email}>"
        author_keys[(name, email)] = key
        return key
    
    def get_dir(path: str) -> str:
        """Get the immediate parent directory of a path."""
        if "/" not in path:
            return ""  # root directory
        return "/".join(path.split("/")[:-1])
    
    def get_all_dirs(path: str) -> List[str]:
        """Get all ancestor directories including root."""
        dirs = [""]
        parts = path.split("/")
        for i in range(1, len(parts)):
            dirs.append("/".join(parts[:i]))
        return dirs
    
    # Process commits in order (oldest first, as returned by parse_log_with_numstat)
    for commit in commits:
        author_key = get_author_key(commit["author_name"], commit["author_email"])
        commit_hash = commit["hash"]
        
        # Build set of files modified in this commit
        modified_paths = set()
        
        for file_change in commit["files"]:
            path = file_change["path"]
            added = file_change["added"]
            removed = file_change["removed"]
            
            if file_change["is_binary"]:
                continue  # Skip binary files per spec
            
            modified_paths.add(path)
            
            # File-level metrics
            file_added[(path, author_key)] += added
            file_removed[(path, author_key)] += removed
            file_modifications[(path, author_key)].add(commit_hash)
            
            # Directory-level metrics: attribute to all ancestor directories
            # get_all_dirs already includes root ("") so no separate repo-level add needed
            for dir_path in get_all_dirs(path):
                file_added[(f"__dir__:{dir_path}", author_key)] += added
                file_removed[(f"__dir__:{dir_path}", author_key)] += removed
                file_modifications[(f"__dir__:{dir_path}", author_key)].add(commit_hash)
        
        # Update known files
        for fc in commit["files"]:
            if not fc["is_binary"]:
                if fc["old_path"]:
                    known_files.discard(fc["old_path"])
                known_files.add(fc["path"])
    
    # Build result structures
    commit_count = len(commits)
    
    # Collect all unique files, dirs, authors
    all_files = set()
    all_dirs = set([""])  # root always exists
    all_authors = set(author_keys.values())
    
    for (path, auth) in file_added.keys():
        if path.startswith("__dir__:"):
            dir_path = path[len("__dir__:"):]
            all_dirs.add(dir_path)
        else:
            all_files.add(path)
    
    # Build file metrics
    file_metrics = {}
    for (path, auth) in file_added.keys():
        if path.startswith("__dir__:"):
            continue
        mods_set = file_modifications[(path, auth)]
        added = file_added[(path, auth)]
        removed = file_removed[(path, auth)]
        file_metrics[(path, auth)] = {
            "added": added,
            "removed": removed,
            "growth": added - removed,
            "churn": added + removed,
            "modifications": len(mods_set),
            "modifications_set": mods_set,
        }
    
    # Build directory metrics
    dir_metrics = {}
    for (path, auth) in file_added.keys():
        if not path.startswith("__dir__:"):
            continue
        dir_path = path[len("__dir__:"):]
        mods_set = file_modifications[(path, auth)]
        added = file_added[(path, auth)]
        removed = file_removed[(path, auth)]
        dir_metrics[(dir_path, auth)] = {
            "added": added,
            "removed": removed,
            "growth": added - removed,
            "churn": added + removed,
            "modifications": len(mods_set),
            "modifications_set": mods_set,
        }
    
    # Build repository metrics (same as root directory "")
    repo_metrics = {}
    for auth in all_authors:
        key = ("", auth)
        if key in dir_metrics:
            repo_metrics[auth] = dir_metrics[key]
    
    return {
        "commit_count": commit_count,
        "file_metrics": file_metrics,
        "dir_metrics": dir_metrics,
        "repo_metrics": repo_metrics,
        "authors": all_authors,
        "files": all_files,
        "dirs": all_dirs,
        "author_keys": author_keys,
    }


def format_author_for_csv(author_key: str) -> str:
    """Format author key for CSV output."""
    if author_key == "ALL":
        return "ALL"
    return author_key


def export_to_csv(metrics: Dict, repo_name: str, ref_sha: str, commit_set: str = "all") -> List[Dict]:
    """
    Export metrics to CSV rows matching the reference format.
    
    Columns: repo,ref_sha,commit_set,commit_count,object_type,path,author,
             added,removed,growth,churn,modifications,modification_frequency,churn_rate,ownership
    """
    rows = []
    commit_count = metrics["commit_count"]
    all_authors = sorted(metrics["authors"])
    
    # Helper to compute aggregate metrics across all authors for an object
    def get_all_metrics(obj_key_prefix: str, metrics_dict: dict) -> Dict:
        """Sum metrics across all authors for a given object."""
        total = {"added": 0, "removed": 0, "growth": 0, "churn": 0, "modifications": set()}
        for (obj, auth), m in metrics_dict.items():
            if obj == obj_key_prefix or (obj_key_prefix.startswith("__dir__:") and obj == obj_key_prefix):
                total["added"] += m["added"]
                total["removed"] += m["removed"]
                total["growth"] += m["growth"]
                total["churn"] += m["churn"]
                total["modifications"] |= m.get("modifications_set", set())
        return total
    
    # Repository ALL row - need union of commit sets across all authors
    repo_total = {"added": 0, "removed": 0, "growth": 0, "churn": 0, "mods": set()}
    for auth in all_authors:
        if auth in metrics["repo_metrics"]:
            m = metrics["repo_metrics"][auth]
            repo_total["added"] += m["added"]
            repo_total["removed"] += m["removed"]
            repo_total["growth"] += m["growth"]
            repo_total["churn"] += m["churn"]
            # Union of modification commit sets
            repo_total["mods"] |= m.get("modifications_set", set())
    
    # ALL row for repository
    mod_freq = 0 if commit_count == 0 else len(repo_total["mods"]) / commit_count
    churn_rate = 0 if commit_count == 0 else repo_total["churn"] / commit_count
    rows.append({
        "repo": repo_name,
        "ref_sha": ref_sha,
        "commit_set": commit_set,
        "commit_count": commit_count,
        "object_type": "repository",
        "path": "/",
        "author": "ALL",
        "added": repo_total["added"],
        "removed": repo_total["removed"],
        "growth": repo_total["growth"],
        "churn": repo_total["churn"],
        "modifications": len(repo_total["mods"]),
        "modification_frequency": round(mod_freq, 15) if mod_freq > 0 else 0,
        "churn_rate": round(churn_rate, 15) if churn_rate > 0 else 0,
        "ownership": "",
    })
    
    # Per-author repository rows
    for auth in all_authors:
        if auth in metrics["repo_metrics"]:
            m = metrics["repo_metrics"][auth]
            ownership = round(m["churn"] / repo_total["churn"], 15) if repo_total["churn"] > 0 else 0
            rows.append({
                "repo": repo_name,
                "ref_sha": ref_sha,
                "commit_set": commit_set,
                "commit_count": commit_count,
                "object_type": "repository",
                "path": "/",
                "author": auth,
                "added": m["added"],
                "removed": m["removed"],
                "growth": m["growth"],
                "churn": m["churn"],
                "modifications": m["modifications"],
                "modification_frequency": "",
                "churn_rate": "",
                "ownership": round(ownership, 15) if ownership > 0 else 0,
            })
    
    # Directory metrics
    # Group dir_metrics by directory path
    dir_paths = set()
    for (d, auth) in metrics["dir_metrics"].keys():
        dir_paths.add(d)
    
    for dir_path in sorted(dir_paths):
        if dir_path == "":
            continue  # Root is handled as repository
        
        # Compute ALL totals for this directory - need union of commit sets
        dir_total = {"added": 0, "removed": 0, "growth": 0, "churn": 0, "mods": set()}
        dir_auth_metrics = {}
        for auth in all_authors:
            key = (dir_path, auth)
            if key in metrics["dir_metrics"]:
                m = metrics["dir_metrics"][key]
                dir_total["added"] += m["added"]
                dir_total["removed"] += m["removed"]
                dir_total["growth"] += m["growth"]
                dir_total["churn"] += m["churn"]
                # Union of modification commit sets
                dir_total["mods"] |= m.get("modifications_set", set())
                dir_auth_metrics[auth] = m
        
        display_path = dir_path  # No trailing slash - matches reference format
        
        # ALL row
        mod_freq = 0 if commit_count == 0 else len(dir_total["mods"]) / commit_count
        churn_rate = 0 if commit_count == 0 else dir_total["churn"] / commit_count
        rows.append({
            "repo": repo_name,
            "ref_sha": ref_sha,
            "commit_set": commit_set,
            "commit_count": commit_count,
            "object_type": "directory",
            "path": display_path,
            "author": "ALL",
            "added": dir_total["added"],
            "removed": dir_total["removed"],
            "growth": dir_total["growth"],
            "churn": dir_total["churn"],
            "modifications": len(dir_total["mods"]),
            "modification_frequency": round(mod_freq, 15) if mod_freq > 0 else 0,
            "churn_rate": round(churn_rate, 15) if churn_rate > 0 else 0,
            "ownership": "",
        })
        
        # Per-author rows
        for auth in all_authors:
            if auth in dir_auth_metrics:
                m = dir_auth_metrics[auth]
                ownership = round(m["churn"] / dir_total["churn"], 15) if dir_total["churn"] > 0 else 0
                rows.append({
                    "repo": repo_name,
                    "ref_sha": ref_sha,
                    "commit_set": commit_set,
                    "commit_count": commit_count,
                    "object_type": "directory",
                    "path": display_path,
                    "author": auth,
                    "added": m["added"],
                    "removed": m["removed"],
                    "growth": m["growth"],
                    "churn": m["churn"],
                    "modifications": m["modifications"],
                    "modification_frequency": "",
                    "churn_rate": "",
                    "ownership": round(ownership, 15) if ownership > 0 else 0,
                })
    
    # File metrics
    file_paths = set()
    for (f, auth) in metrics["file_metrics"].keys():
        file_paths.add(f)
    
    for file_path in sorted(file_paths):
        file_total = {"added": 0, "removed": 0, "growth": 0, "churn": 0, "mods": set()}
        file_auth_metrics = {}
        for auth in all_authors:
            key = (file_path, auth)
            if key in metrics["file_metrics"]:
                m = metrics["file_metrics"][key]
                file_total["added"] += m["added"]
                file_total["removed"] += m["removed"]
                file_total["growth"] += m["growth"]
                file_total["churn"] += m["churn"]
                # Union of modification commit sets
                file_total["mods"] |= m.get("modifications_set", set())
                file_auth_metrics[auth] = m
        
        # ALL row
        mod_freq = 0 if commit_count == 0 else len(file_total["mods"]) / commit_count
        churn_rate = 0 if commit_count == 0 else file_total["churn"] / commit_count
        rows.append({
            "repo": repo_name,
            "ref_sha": ref_sha,
            "commit_set": commit_set,
            "commit_count": commit_count,
            "object_type": "file",
            "path": file_path,
            "author": "ALL",
            "added": file_total["added"],
            "removed": file_total["removed"],
            "growth": file_total["growth"],
            "churn": file_total["churn"],
            "modifications": len(file_total["mods"]),
            "modification_frequency": round(mod_freq, 15) if mod_freq > 0 else 0,
            "churn_rate": round(churn_rate, 15) if churn_rate > 0 else 0,
            "ownership": "",
        })
        
        # Per-author rows
        for auth in all_authors:
            if auth in file_auth_metrics:
                m = file_auth_metrics[auth]
                ownership = round(m["churn"] / file_total["churn"], 15) if file_total["churn"] > 0 else 0
                rows.append({
                    "repo": repo_name,
                    "ref_sha": ref_sha,
                    "commit_set": commit_set,
                    "commit_count": commit_count,
                    "object_type": "file",
                    "path": file_path,
                    "author": auth,
                    "added": m["added"],
                    "removed": m["removed"],
                    "growth": m["growth"],
                    "churn": m["churn"],
                    "modifications": m["modifications"],
                    "modification_frequency": "",
                    "churn_rate": "",
                    "ownership": round(ownership, 15) if ownership > 0 else 0,
                })
    
    return rows
