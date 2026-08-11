#!/usr/bin/env python3
"""Regenerates README.md with GitHub stats rendered as native markdown.

Fetches data from the GitHub GraphQL API (works with the default Actions
token for public data) and writes the full README. Run from the repo root:

    GITHUB_TOKEN=<token> python3 .github/scripts/update_readme.py
"""

import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

USER = "vitorecomp"
TIMEZONE = "America/Sao_Paulo"
API = "https://api.github.com/graphql"

LANGUAGES_LIMIT = 8
TOPICS_LIMIT = 20
BAR_WIDTH = 20

# Contribution graph geometry (isometric 3D towers).
ISO_A = 10        # half-width of a ground tile
ISO_SCALE = 0.85  # tile fill ratio; the rest becomes the gap between tiles
MAX_BAR = 42      # height of the tallest tower

CALENDAR_THEMES = {
    "light": {
        "text": "#57606a",
        "levels": {
            "NONE": "#ebedf0",
            "FIRST_QUARTILE": "#9be9a8",
            "SECOND_QUARTILE": "#40c463",
            "THIRD_QUARTILE": "#30a14e",
            "FOURTH_QUARTILE": "#216e39",
        },
    },
    "dark": {
        "text": "#8b949e",
        "levels": {
            "NONE": "#161b22",
            "FIRST_QUARTILE": "#0e4429",
            "SECOND_QUARTILE": "#006d32",
            "THIRD_QUARTILE": "#26a641",
            "FOURTH_QUARTILE": "#39d353",
        },
    },
}

PROFILE_QUERY = """
query($login: String!) {
  user(login: $login) {
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    repositoriesContributedTo(
      contributionTypes: [COMMIT, PULL_REQUEST, ISSUE, REPOSITORY]
    ) { totalCount }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount contributionLevel } }
      }
    }
  }
}
"""

REPOS_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    repositories(
      first: 40
      after: $cursor
      ownerAffiliations: OWNER
      orderBy: {field: PUSHED_AT, direction: DESC}
    ) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        isFork
        stargazerCount
        forkCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
        repositoryTopics(first: 20) { nodes { topic { name } } }
      }
    }
  }
}
"""


def graphql(token, query, variables):
    payload = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        API,
        data=payload,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    if body.get("errors"):
        raise RuntimeError(f"GraphQL errors: {body['errors']}")
    return body["data"]


def fetch_repos(token):
    nodes, cursor = [], None
    while True:
        data = graphql(token, REPOS_QUERY, {"login": USER, "cursor": cursor})
        repos = data["user"]["repositories"]
        nodes.extend(repos["nodes"])
        if not repos["pageInfo"]["hasNextPage"]:
            return repos["totalCount"], nodes
        cursor = repos["pageInfo"]["endCursor"]


def usage_bar(percentage):
    filled = round(percentage / 100 * BAR_WIDTH)
    filled = max(filled, 1) if percentage > 0 else 0
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def shade(color, factor):
    channels = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(channel * factor):02x}" for channel in channels)


def polygon(points, fill):
    coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f'<polygon points="{coords}" fill="{fill}"/>'


def render_calendar_svg(calendar, theme):
    levels = theme["levels"]
    days = []
    for column, week in enumerate(calendar["weeks"]):
        for day in week["contributionDays"]:
            # Row from the actual date: the first week can start mid-week.
            row = (datetime.fromisoformat(day["date"]).weekday() + 1) % 7
            days.append((column, row, day["contributionCount"], day["contributionLevel"]))
    max_count = max(day[2] for day in days) or 1
    columns = len(calendar["weeks"])

    a = ISO_A
    half_w = a * ISO_SCALE      # tile half-width on screen
    half_h = half_w / 2         # tile half-height on screen
    pad = 6
    x0 = -6 * a - half_w - pad
    x1 = (columns - 1) * a + half_w + pad
    y0 = -MAX_BAR - half_h - pad
    y1 = (columns - 1 + 6) * a / 2 + half_h + pad
    width, height = x1 - x0, y1 - y0

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="{x0:.1f} {y0:.1f} {width:.1f} {height:.1f}">'
    ]

    # Painter's algorithm: column + row grows toward the viewer.
    for column, row, count, level in sorted(days, key=lambda day: day[0] + day[1]):
        x = (column - row) * a
        y = (column + row) * a / 2
        top_color = levels[level]
        if count == 0:
            parts.append(polygon(
                [(x, y - half_h), (x + half_w, y), (x, y + half_h), (x - half_w, y)],
                top_color,
            ))
            continue
        bar = max(3, MAX_BAR * math.sqrt(count / max_count))
        left, right = (x - half_w, y - bar), (x + half_w, y - bar)
        back, front = (x, y - bar - half_h), (x, y - bar + half_h)
        parts.append(polygon(
            [left, front, (front[0], y + half_h), (left[0], y)], shade(top_color, 0.70)
        ))
        parts.append(polygon(
            [front, right, (right[0], y), (front[0], y + half_h)], shade(top_color, 0.50)
        ))
        parts.append(polygon([back, right, front, left], top_color))

    parts.append("</svg>")
    return "\n".join(parts)


def write_calendar_svgs(calendar):
    for name, theme in CALENDAR_THEMES.items():
        suffix = "" if name == "light" else f"-{name}"
        with open(f"contribution-graph{suffix}.svg", "w", encoding="utf-8") as handle:
            handle.write(render_calendar_svg(calendar, theme))


def aggregate_languages(repos):
    totals = {}
    for repo in repos:
        if repo["isFork"]:
            continue
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            totals[name] = totals.get(name, 0) + edge["size"]
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    grand_total = sum(totals.values()) or 1
    return [
        (name, size, 100 * size / grand_total)
        for name, size in ranked[:LANGUAGES_LIMIT]
    ]


def collect_topics(repos):
    topics = []
    for repo in repos:  # already ordered by most recently pushed
        if repo["isFork"]:
            continue
        for node in repo["repositoryTopics"]["nodes"]:
            name = node["topic"]["name"]
            if name not in topics:
                topics.append(name)
    return topics[:TOPICS_LIMIT]


# GitHub topic aliases: starring both members of a pair lists both on the
# stars page, so collapse them to one key here.
TOPIC_ALIASES = {"golang": "go", "cplusplus": "c++", "cpp": "c++"}


def dedupe_topics(names):
    def key(name):
        normalized = re.sub(r"[^a-z0-9+]", "", name.lower())
        return TOPIC_ALIASES.get(normalized, normalized)

    def is_curated(name):  # curated topics get capitalized/spaced display names
        return name != name.lower() or " " in name

    best, order = {}, []
    for name in names:
        if key(name) not in best:
            order.append(key(name))
            best[key(name)] = name
        elif is_curated(name) and not is_curated(best[key(name)]):
            best[key(name)] = name
    return [best[k] for k in order]


def fetch_starred_topics():
    """Starred topics aren't exposed by the API; scrape the public stars page.

    Returns display names (e.g. "C++", "Node.js").
    """
    names = []
    for page in range(1, 11):
        request = urllib.request.Request(
            f"https://github.com/stars/{USER}/topics?page={page}",
            headers={"User-Agent": "update-readme-script"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read().decode()
        cards = re.findall(
            r'data-ga-click="Explore, go to topic, text:[^"]*"'
            r'.*?<p class="f3[^"]*">\s*([^<]+?)\s*</p>',
            html,
            re.S,
        )
        if not cards:
            break
        names.extend(name for name in cards if name not in names)
        if f"page={page + 1}" not in html:
            break
    return dedupe_topics(names)[:TOPICS_LIMIT]


def build_readme(profile, repo_count, repos):
    calendar = profile["contributionsCollection"]["contributionCalendar"]
    stars = sum(repo["stargazerCount"] for repo in repos)
    forks = sum(repo["forkCount"] for repo in repos)
    languages = aggregate_languages(repos)
    try:
        topics = fetch_starred_topics()
    except Exception as error:  # page layout changes must not break the build
        print(f"warning: starred-topics scrape failed: {error}", file=sys.stderr)
        topics = []
    topics = topics or collect_topics(repos)
    updated = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M %Z")

    parts = [
        '<h1 align="left">Hey, I\'m Vitor Vieira</h1>',
        "",
        '<p align="left">',
        '  Software Engineer @ <a href="https://github.com/google">Google</a> ·',
        '  <a href="https://vitorx86.dev">vitorx86.dev</a>',
        "</p>",
        "",
        "Senior Software Engineer and Architect with a proven track record of driving "
        "digital transformation for large-scale enterprises. Currently an Engineer at "
        "Google, I specialize in scaling cloud-native ecosystems and implementing "
        "high-impact AI solutions, such as the integration of core corporate systems "
        "with modern GenAI stacks.",
        "",
        "## 📊 Overview",
        "",
        "| 📦 Repositories | ⭐ Stars | 🍴 Forks | 👥 Followers | 🔀 Pull requests | 🐛 Issues | 🤝 Contributed to |",
        "| :-: | :-: | :-: | :-: | :-: | :-: | :-: |",
        f"| {repo_count} | {stars} | {forks} | {profile['followers']['totalCount']} "
        f"| {profile['pullRequests']['totalCount']} | {profile['issues']['totalCount']} "
        f"| {profile['repositoriesContributedTo']['totalCount']} |",
        "",
        f"## 🗓️ {calendar['totalContributions']} contributions in the last year",
        "",
        "<picture>",
        '  <source media="(prefers-color-scheme: dark)" srcset="contribution-graph-dark.svg">',
        '  <img src="contribution-graph.svg" alt="Contribution graph" width="100%">',
        "</picture>",
        "",
        "## 💻 Most used languages",
        "",
        "| Language | Usage | Share |",
        "| :-- | :-- | --: |",
    ]
    for name, _, percentage in languages:
        parts.append(
            f"| {name} | `{usage_bar(percentage)}` | {percentage:.1f}% |"
        )
    if topics:
        parts += ["", "## 🧰 Technologies & topics", ""]
        parts.append(" ".join(f"`{topic}`" for topic in topics))
    parts += [
        "",
        "## 🌐 Socials",
        "",
        '<p align="left">',
        '  <a href="https://www.linkedin.com/in/vieiravitor/">'
        '<img src="https://img.shields.io/badge/LinkedIn-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white" alt="LinkedIn"></a>',
        '  <a href="https://www.instagram.com/vitorx86.dev/">'
        '<img src="https://img.shields.io/badge/Instagram-E4405F?style=for-the-badge&logo=instagram&logoColor=white" alt="Instagram"></a>',
        '  <a href="https://medium.com/@vitorx86">'
        '<img src="https://img.shields.io/badge/Medium-12100E?style=for-the-badge&logo=medium&logoColor=white" alt="Medium"></a>',
        "</p>",
        "",
        "<p align=\"center\">",
        f"  <sub>Updated daily by GitHub Actions · last update {updated}</sub>",
        "</p>",
        "",
    ]
    return "\n".join(parts)


def main():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("METRICS_TOKEN")
    if not token:
        sys.exit("Set GITHUB_TOKEN (or METRICS_TOKEN) with a GitHub API token.")
    profile = graphql(token, PROFILE_QUERY, {"login": USER})["user"]
    repo_count, repos = fetch_repos(token)
    write_calendar_svgs(profile["contributionsCollection"]["contributionCalendar"])
    readme = build_readme(profile, repo_count, repos)
    with open("README.md", "w", encoding="utf-8") as handle:
        handle.write(readme)
    print(f"README.md and contribution-graph SVGs updated ({len(readme)} bytes).")


if __name__ == "__main__":
    main()
