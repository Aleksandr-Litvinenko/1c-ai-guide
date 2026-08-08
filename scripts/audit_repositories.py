#!/usr/bin/env python3
"""Compare catalog claims with current GitHub repository metadata."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "tools.json"
API = "https://api.github.com"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check pinned catalog revisions and current GitHub repository metadata."
    )
    parser.add_argument(
        "--strict-upstream",
        action="store_true",
        help="fail when a tracked branch or pushed_at value moved after the catalog review",
    )
    return parser.parse_args()


def request_json(path: str) -> dict[str, object]:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-x-1c-guide-audit",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{API}{path}", headers=headers)
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def main() -> int:
    args = parse_args()
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    errors: list[str] = []
    updates: list[str] = []

    for tool in payload["tools"]:
        error_count_before = len(errors)
        parsed = urlparse(tool["repository"])
        owner, repo = parsed.path.strip("/").split("/")
        slug = f"{owner}/{repo}"
        try:
            metadata = request_json(f"/repos/{quote(owner)}/{quote(repo)}")
            branch_commit = request_json(
                f"/repos/{quote(owner)}/{quote(repo)}/commits/{quote(tool['source_ref'])}"
            )
            pinned_commit = request_json(
                f"/repos/{quote(owner)}/{quote(repo)}/commits/{quote(tool['source_revision'])}"
            )
        except (HTTPError, URLError, TimeoutError) as exc:
            errors.append(f"{tool['id']}: GitHub request failed: {exc}")
            continue

        canonical = metadata.get("full_name")
        if canonical != slug:
            errors.append(f"{tool['id']}: canonical repository changed: {canonical!r}")
        if bool(metadata.get("archived")) != tool["maintenance"]["archived"]:
            errors.append(f"{tool['id']}: archived status changed")
        if metadata.get("default_branch") != tool["source_ref"]:
            errors.append(
                f"{tool['id']}: default branch changed from {tool['source_ref']!r} "
                f"to {metadata.get('default_branch')!r}"
            )

        pushed_at = metadata.get("pushed_at")
        if not isinstance(pushed_at, str) or pushed_at[:10] != tool["maintenance"]["last_push"]:
            updates.append(
                f"{tool['id']}: last push drifted from {tool['maintenance']['last_push']!r} "
                f"to {pushed_at!r}"
            )

        if pinned_commit.get("sha") != tool["source_revision"]:
            errors.append(f"{tool['id']}: pinned source_revision is not retrievable")

        if branch_commit.get("sha") != tool["source_revision"]:
            updates.append(
                f"{tool['id']}: {tool['source_ref']} moved from "
                f"{tool['source_revision'][:7]} to {str(branch_commit.get('sha'))[:7]}; "
                "the catalog remains pinned until a documentation review"
            )

        api_license = metadata.get("license")
        api_spdx = api_license.get("spdx_id") if isinstance(api_license, dict) else None
        expected_spdx = tool["license"]["spdx"]
        if tool["license"]["status"] == "file" and api_spdx != expected_spdx:
            errors.append(
                f"{tool['id']}: GitHub license changed from {expected_spdx!r} to {api_spdx!r}"
            )

        if len(errors) == error_count_before:
            print(f"repository ok: {tool['id']} pinned @ {tool['source_revision'][:7]}")

    for update in updates:
        print(f"repository update: {update}", file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::warning title=Catalog upstream update::{update}")

    if args.strict_upstream:
        errors.extend(f"strict upstream: {update}" for update in updates)

    if errors:
        for error in errors:
            print(f"repository drift: {error}", file=sys.stderr)
        return 1

    print(
        "repository audit ok: "
        f"{len(payload['tools'])} pinned revisions are retrievable, "
        f"{len(updates)} upstream update(s) reported"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
