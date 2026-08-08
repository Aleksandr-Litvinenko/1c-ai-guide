#!/usr/bin/env python3
"""Compare catalog claims with current GitHub repository metadata."""

from __future__ import annotations

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
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    errors: list[str] = []

    for tool in payload["tools"]:
        parsed = urlparse(tool["repository"])
        owner, repo = parsed.path.strip("/").split("/")
        slug = f"{owner}/{repo}"
        try:
            metadata = request_json(f"/repos/{quote(owner)}/{quote(repo)}")
            commit = request_json(
                f"/repos/{quote(owner)}/{quote(repo)}/commits/{quote(tool['source_ref'])}"
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
            errors.append(
                f"{tool['id']}: last push drifted from {tool['maintenance']['last_push']!r} "
                f"to {pushed_at!r}"
            )

        if commit.get("sha") != tool["source_revision"]:
            errors.append(
                f"{tool['id']}: {tool['source_ref']} moved; documentation review is required"
            )

        api_license = metadata.get("license")
        api_spdx = api_license.get("spdx_id") if isinstance(api_license, dict) else None
        expected_spdx = tool["license"]["spdx"]
        if tool["license"]["status"] == "file" and api_spdx != expected_spdx:
            errors.append(
                f"{tool['id']}: GitHub license changed from {expected_spdx!r} to {api_spdx!r}"
            )

        if not any(error.startswith(f"{tool['id']}:") for error in errors):
            print(f"repository ok: {tool['id']} @ {tool['source_revision'][:7]}")

    if errors:
        for error in errors:
            print(f"repository drift: {error}", file=sys.stderr)
        return 1

    print(f"repository audit ok: {len(payload['tools'])} entries match GitHub metadata")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
