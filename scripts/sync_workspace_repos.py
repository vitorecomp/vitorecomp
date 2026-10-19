#!/usr/bin/env python3
"""Organizes and clones repositories listed in documentation/repo_stats.csv into ~/workspace.

Behavior:
1. Reads `documentation/repo_stats.csv`.
2. Ignores any row where `folder` is empty, as well as any local folder/repo in
   `~/workspace` that is not described with a target `folder` in the CSV.
3. Scans `~/workspace` for already-cloned repositories and moves them to
   `~/workspace/<folder>/<name>` if they are currently in a different folder.
4. Clones any remaining repositories that have a `folder` assigned in the CSV
   into `~/workspace/<folder>/<name>`.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from pathlib import Path
import shutil
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV_PATH = REPO_ROOT / "documentation" / "repo_stats.csv"
DEFAULT_WORKSPACE = Path.home() / "workspace"


def load_csv_repos(csv_path: Path) -> dict[str, dict[str, str]]:
    if not csv_path.exists():
        sys.exit(f"CSV file not found: {csv_path}")

    repos: dict[str, dict[str, str]] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            name = (row.get("name") or "").strip()
            folder = (row.get("folder") or "").strip()
            # Only manage repos that have both a name and a target folder in the CSV.
            if not name or not folder:
                continue
            repos[name] = {
                "name": name,
                "folder": folder,
                "link": (row.get("link") or "").strip(),
                "ssh_link": (row.get("ssh link") or "").strip(),
            }
    return repos


def find_existing_cloned_repos(workspace: Path) -> dict[str, list[Path]]:
    """Finds git repositories under ~/workspace (up to 2 levels deep: ~/workspace/<repo> or ~/workspace/<folder>/<repo>)."""
    found: dict[str, list[Path]] = {}
    if not workspace.exists():
        return found

    for child in sorted(workspace.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue

        # Direct repo at ~/workspace/<repo>
        if (child / ".git").exists():
            found.setdefault(child.name, []).append(child)
            continue

        # Category folder at ~/workspace/<folder>/<repo>
        for grandchild in sorted(child.iterdir()):
            if not grandchild.is_dir() or grandchild.name.startswith("."):
                continue
            if (grandchild / ".git").exists():
                found.setdefault(grandchild.name, []).append(grandchild)

    return found


def clone_repo(repo: dict[str, str], target_path: Path, dry_run: bool = False) -> bool:
    clone_url = repo["ssh_link"] or repo["link"]
    if not clone_url:
        print(f"[skip] {repo['name']}: no clone URL in CSV", file=sys.stderr)
        return False

    if dry_run:
        print(f"[dry-run] Would clone {clone_url} -> {target_path}")
        return True

    target_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[clone] Cloning {clone_url} -> {target_path} ...")
    result = subprocess.run(
        ["git", "clone", clone_url, str(target_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True

    # Fallback to HTTPS link if SSH clone failed and HTTPS link is available.
    if repo["ssh_link"] and repo["link"] and clone_url != repo["link"]:
        print(
            f"[warn] SSH clone failed for {repo['name']}, retrying with HTTPS ({repo['link']})..."
        )
        result_https = subprocess.run(
            ["git", "clone", repo["link"], str(target_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result_https.returncode == 0:
            return True
        print(
            f"[error] Failed to clone {repo['name']}: {result_https.stderr.strip()}",
            file=sys.stderr,
        )
        return False

    print(
        f"[error] Failed to clone {repo['name']}: {result.stderr.strip()}",
        file=sys.stderr,
    )
    return False


def sync_workspace(
    csv_path: Path, workspace: Path, dry_run: bool = False, jobs: int = 4
) -> None:
    managed_repos = load_csv_repos(csv_path)
    workspace.mkdir(parents=True, exist_ok=True)

    existing_repos = find_existing_cloned_repos(workspace)

    already_in_place = 0
    moved_count = 0
    cloned_count = 0
    failed_count = 0

    # Step 1: Organize already-cloned repos that are described in the CSV.
    for name, repo in managed_repos.items():
        target_dir = workspace / repo["folder"] / name
        locations = existing_repos.get(name, [])

        if not locations:
            continue

        if target_dir in locations:
            already_in_place += 1
            continue

        source_dir = locations[0]
        if target_dir.exists():
            print(
                f"[skip] Target {target_dir} already exists; leaving {source_dir} as is."
            )
            continue

        if dry_run:
            print(f"[dry-run] Would move {source_dir} -> {target_dir}")
            moved_count += 1
        else:
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_dir), str(target_dir))
            print(f"[move] Moved {source_dir} -> {target_dir}")
            moved_count += 1

    # Refresh existing locations after moves.
    if not dry_run:
        existing_repos = find_existing_cloned_repos(workspace)

    # Step 2: Clone missing repos that have a target folder in the CSV.
    to_clone: list[tuple[dict[str, str], Path]] = []
    for name, repo in managed_repos.items():
        target_dir = workspace / repo["folder"] / name
        if target_dir.exists():
            continue
        if dry_run and name in existing_repos:
            continue
        to_clone.append((repo, target_dir))

    if dry_run or jobs <= 1:
        for repo, target_dir in to_clone:
            if clone_repo(repo, target_dir, dry_run=dry_run):
                cloned_count += 1
            else:
                failed_count += 1
    elif to_clone:
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = {
                executor.submit(clone_repo, repo, target_dir, False): repo["name"]
                for repo, target_dir in to_clone
            }
            for future in as_completed(futures):
                if future.result():
                    cloned_count += 1
                else:
                    failed_count += 1

    print(
        f"\nSummary: configured_in_csv={len(managed_repos)}, "
        f"already_in_place={already_in_place}, moved={moved_count}, "
        f"cloned={cloned_count}, failed={failed_count}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Organize and clone repositories in ~/workspace based on documentation/repo_stats.csv."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"Path to repo_stats.csv (default: {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=DEFAULT_WORKSPACE,
        help=f"Path to workspace directory (default: {DEFAULT_WORKSPACE})",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="Number of concurrent git clone operations (default: 4).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview moves and clones without making changes.",
    )
    args = parser.parse_args()
    sync_workspace(
        args.csv.resolve(),
        args.workspace.expanduser().resolve(),
        dry_run=args.dry_run,
        jobs=args.jobs,
    )


if __name__ == "__main__":
    main()
