#!/usr/bin/env python3
"""Creates or updates documentation/repo_stats.csv with all GitHub repositories.

Columns:
    name, link, ssh link, last updated, is a fork, folder, is-custom, is-template

When documentation/repo_stats.csv already exists, existing rows are matched by
`name` and only changed GitHub metadata fields (`link`, `ssh link`,
`last updated`, `is a fork`) are updated, preserving manual values in
`folder`, `is-custom`, and `is-template`.
"""

import csv
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

USER = "vitorecomp"
REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = REPO_ROOT / "documentation"
CSV_PATH = DOC_DIR / "repo_stats.csv"

FIELDNAMES = [
    "name",
    "link",
    "ssh link",
    "last updated",
    "is a fork",
    "folder",
    "is-custom",
    "is-template",
]

TRACKED_FIELDS = ("link", "ssh link", "last updated", "is a fork")
EMPTY_FIELDS = ("folder", "is-custom", "is-template")


def fetch_page(url: str, token: str | None) -> list[dict]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "vitorecomp-repo-stats-script",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_all_repos(user: str, token: str | None) -> list[dict]:
    repos: list[dict] = []
    page = 1
    use_authenticated_endpoint = bool(token)

    while True:
        if use_authenticated_endpoint:
            params = urllib.parse.urlencode({
                "per_page": 100,
                "affiliation": "owner",
                "sort": "updated",
                "direction": "desc",
                "page": page,
            })
            url = f"https://api.github.com/user/repos?{params}"
        else:
            params = urllib.parse.urlencode({
                "per_page": 100,
                "type": "owner",
                "sort": "updated",
                "direction": "desc",
                "page": page,
            })
            url = f"https://api.github.com/users/{user}/repos?{params}"

        try:
            batch = fetch_page(url, token)
        except urllib.error.HTTPError as err:
            if use_authenticated_endpoint and err.code in (401, 403):
                # Fallback to public user endpoint if token lacks /user/repos access
                use_authenticated_endpoint = False
                continue
            raise

        if not batch:
            break

        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    return repos


def load_existing_csv(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []

    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            rows.append({
                field: row.get(field, "") or "" for field in FIELDNAMES
            })
    return rows


def build_repo_row(repo: dict) -> dict[str, str]:
    return {
        "name": repo["name"],
        "link": repo["html_url"],
        "ssh link": repo["ssh_url"],
        "last updated": repo.get("updated_at") or repo.get("pushed_at") or "",
        "is a fork": str(bool(repo.get("fork", False))),
        "folder": "",
        "is-custom": "",
        "is-template": "",
    }


def update_repo_stats() -> None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("METRICS_TOKEN")
    DOC_DIR.mkdir(parents=True, exist_ok=True)

    file_existed = CSV_PATH.exists()
    existing_rows = load_existing_csv(CSV_PATH)

    repos = fetch_all_repos(USER, token)
    fresh_by_name = {repo["name"]: build_repo_row(repo) for repo in repos}

    added_count = 0
    updated_count = 0
    unchanged_count = 0

    final_rows: list[dict[str, str]] = []
    seen_names: set[str] = set()

    # Update existing rows in place so row ordering and custom columns are preserved.
    for row in existing_rows:
        name = row["name"]
        seen_names.add(name)
        fresh = fresh_by_name.get(name)
        if not fresh:
            final_rows.append(row)
            unchanged_count += 1
            continue

        changed = False
        for field in TRACKED_FIELDS:
            if row.get(field, "") != fresh[field]:
                row[field] = fresh[field]
                changed = True
        for field in EMPTY_FIELDS:
            row.setdefault(field, "")

        if changed:
            updated_count += 1
        else:
            unchanged_count += 1
        final_rows.append(row)

    # Append newly discovered repositories.
    for repo in repos:
        name = repo["name"]
        if name not in seen_names:
            seen_names.add(name)
            added_count += 1
            final_rows.append(fresh_by_name[name])

    if not file_existed or added_count > 0 or updated_count > 0:
        with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=FIELDNAMES, lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(final_rows)

    print(
        f"{CSV_PATH}: total={len(final_rows)}, "
        f"added={added_count}, updated={updated_count}, unchanged={unchanged_count}"
    )


if __name__ == "__main__":
    update_repo_stats()
