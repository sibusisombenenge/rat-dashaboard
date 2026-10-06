"""
Repository Manager - handles zip extraction, remote cloning, and mailmap parsing.
"""

import subprocess
import os
import shutil
import zipfile
import tempfile
from typing import Optional, Tuple


REPOS_DIR = os.path.join(os.path.dirname(__file__), "repos")


def ensure_repos_dir():
    """Ensure the repos directory exists."""
    os.makedirs(REPOS_DIR, exist_ok=True)


def extract_zip(zip_path: str, repo_name: str) -> str:
    """
    Extract a zip file to the repos directory.
    The zip should contain a .git directory (either at root or one level deep).
    Returns the path to the extracted git repo.
    """
    ensure_repos_dir()
    dest = os.path.join(REPOS_DIR, repo_name)
    
    # Clean existing
    if os.path.exists(dest):
        shutil.rmtree(dest)
    
    os.makedirs(dest, exist_ok=True)
    
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(dest)
    
    # Find the .git directory - might be at root or one level deep
    git_dir = os.path.join(dest, ".git")
    if not os.path.exists(git_dir):
        # Check if there's a single subdirectory containing .git
        entries = os.listdir(dest)
        if len(entries) == 1 and os.path.isdir(os.path.join(dest, entries[0])):
            subdir = os.path.join(dest, entries[0])
            if os.path.exists(os.path.join(subdir, ".git")):
                return subdir
    
    return dest


def clone_remote(url: str, repo_name: str) -> str:
    """
    Deep clone a remote repository.
    Returns the path to the cloned repo.
    """
    ensure_repos_dir()
    dest = os.path.join(REPOS_DIR, repo_name)
    
    # Clean existing
    if os.path.exists(dest):
        shutil.rmtree(dest)
    
    result = subprocess.run(
        ["git", "clone", "--depth=10000", url, dest],
        capture_output=True,
        text=True,
        timeout=600
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"Failed to clone {url}: {result.stderr}")
    
    return dest


def get_ref_sha(repo_path: str) -> str:
    """Get the HEAD commit SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True
    )
    return result.stdout.strip()


def parse_mailmap(repo_path: str) -> dict:
    """
    Parse .mailmap file if it exists.
    Returns dict mapping (name, email) -> (canonical_name, canonical_email).
    """
    mailmap_path = os.path.join(repo_path, ".mailmap")
    if not os.path.exists(mailmap_path):
        return {}
    
    mappings = {}
    with open(mailmap_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Format: Proper Name <proper@email> <commit@email>
            # or: Proper Name <proper@email> Commit Name <commit@email>
            parts = line.split()
            if len(parts) < 2:
                continue
            
            # Extract canonical (first) author
            canonical_name, canonical_email = _extract_author(parts, 0)
            
            # Extract commit author(s) - everything after canonical
            idx = _find_email_end(parts, 0) + 1
            while idx < len(parts):
                commit_name, commit_email = _extract_author(parts, idx)
                if commit_email:
                    mappings[(commit_name, commit_email)] = (canonical_name, canonical_email)
                idx = _find_email_end(parts, idx) + 1
    
    return mappings


def _extract_author(parts: list, start: int) -> Tuple[str, str]:
    """Extract name and email from parts starting at given index."""
    # Find the email (in angle brackets)
    email = None
    name_parts = []
    i = start
    while i < len(parts):
        if parts[i].startswith('<') and parts[i].endswith('>'):
            email = parts[i][1:-1]
            name = " ".join(name_parts) if name_parts else ""
            return name, email
        elif parts[i].startswith('<'):
            # Multi-part email
            email_parts = [parts[i][1:]]
            i += 1
            while i < len(parts) and not parts[i].endswith('>'):
                email_parts.append(parts[i])
                i += 1
            if i < len(parts):
                email_parts.append(parts[i][:-1])
            email = " ".join(email_parts)
            name = " ".join(name_parts) if name_parts else ""
            return name, email
        else:
            name_parts.append(parts[i])
        i += 1
    
    return " ".join(name_parts), email


def _find_email_end(parts: list, start: int) -> int:
    """Find the index after the email address starting from given position."""
    i = start
    while i < len(parts):
        if parts[i].endswith('>'):
            return i
        i += 1
    return len(parts) - 1


def cleanup_repo(repo_name: str):
    """Remove a repo from the repos directory."""
    dest = os.path.join(REPOS_DIR, repo_name)
    if os.path.exists(dest):
        shutil.rmtree(dest)


from typing import Tuple
