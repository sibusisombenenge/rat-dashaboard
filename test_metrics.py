"""Test script to validate metrics engine against reference CSVs."""
import sys
import os
import csv
import subprocess

sys.path.insert(0, os.path.dirname(__file__))

from app.metrics import parse_log_with_numstat, compute_metrics, export_to_csv


def clone_repo(url, dest, ref_sha=None):
    """Clone a repo, optionally checking out a specific commit."""
    import shutil
    if os.path.exists(dest):
        shutil.rmtree(dest)
    subprocess.run(["git", "clone", url, dest], check=True, capture_output=True)
    if ref_sha:
        subprocess.run(["git", "checkout", ref_sha], cwd=dest, check=True, capture_output=True)


def load_reference_csv(path):
    """Load reference CSV into list of dicts."""
    rows = []
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def compare_metrics(computed_rows, reference_rows, tolerance=0.001):
    """Compare computed metrics against reference."""
    # Index reference by (object_type, path, author)
    ref_index = {}
    for row in reference_rows:
        key = (row['object_type'], row['path'], row['author'])
        ref_index[key] = row
    
    computed_index = {}
    for row in computed_rows:
        key = (row['object_type'], row['path'], row['author'])
        computed_index[key] = row
    
    print(f"Reference rows: {len(reference_rows)}")
    print(f"Computed rows: {len(computed_rows)}")
    print(f"Reference keys: {len(ref_index)}")
    print(f"Computed keys: {len(computed_index)}")
    
    # Check for missing keys
    missing = set(ref_index.keys()) - set(computed_index.keys())
    extra = set(computed_index.keys()) - set(ref_index.keys())
    
    if missing:
        print(f"\nMissing {len(missing)} rows:")
        for k in sorted(missing)[:10]:
            print(f"  {k}")
    
    if extra:
        print(f"\nExtra {len(extra)} rows:")
        for k in sorted(extra)[:10]:
            print(f"  {k}")
    
    # Compare values
    numeric_fields = ['added', 'removed', 'growth', 'churn', 'modifications', 
                      'modification_frequency', 'churn_rate', 'ownership']
    
    mismatches = []
    for key in ref_index:
        if key not in computed_index:
            continue
        ref = ref_index[key]
        comp = computed_index[key]
        
        for field in numeric_fields:
            ref_val = ref.get(field, '')
            comp_val = comp.get(field, '')
            
            if ref_val == '' and comp_val == '':
                continue
            if ref_val == '' or comp_val == '':
                mismatches.append(f"{key}: {field} ref='{ref_val}' comp='{comp_val}'")
                continue
            
            try:
                ref_f = float(ref_val)
                comp_f = float(comp_val)
                if abs(ref_f - comp_f) > tolerance * max(1, abs(ref_f)):
                    mismatches.append(f"{key}: {field} ref={ref_f} comp={comp_f} diff={abs(ref_f-comp_f)}")
            except ValueError:
                if str(ref_val) != str(comp_val):
                    mismatches.append(f"{key}: {field} ref='{ref_val}' comp='{comp_val}'")
    
    if mismatches:
        print(f"\n{len(mismatches)} value mismatches:")
        for m in mismatches[:20]:
            print(f"  {m}")
    else:
        print("\nAll values match!")
    
    return len(mismatches) == 0


def test_cjson():
    """Test against cJSON reference."""
    print("=" * 60)
    print("Testing cJSON...")
    print("=" * 60)
    
    repo_url = "https://github.com/DaveGamble/cJSON.git"
    repo_dest = "/tmp/test_cjson"
    ref_csv = "/home/vmuser/Documents/Qoder/2026-10-06/chat-1/repo-references/cJSON_6d9f2443ab07.csv"
    
    ref_sha = "6d9f2443ab071f86e5d9b43025a40929ec41c46c"
    print("Cloning cJSON...")
    clone_repo(repo_url, repo_dest, ref_sha)
    
    print("Parsing git log...")
    commits = parse_log_with_numstat(repo_dest)
    print(f"Parsed {len(commits)} commits")
    
    print("Computing metrics...")
    metrics = compute_metrics(commits)
    
    print("Exporting to CSV format...")
    computed_rows = export_to_csv(metrics, "cJSON", ref_sha)
    
    print("Loading reference...")
    reference_rows = load_reference_csv(ref_csv)
    
    print("\nComparing...")
    return compare_metrics(computed_rows, reference_rows)


if __name__ == "__main__":
    success = test_cjson()
    sys.exit(0 if success else 1)
