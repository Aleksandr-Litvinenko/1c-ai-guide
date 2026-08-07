#!/usr/bin/env python3
"""Validate the machine-readable AI x 1C tool catalog."""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "tools.json"
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REQUIRED = {
    "id",
    "name",
    "repository",
    "category",
    "use_case",
    "language",
    "license",
    "reviewed_on",
}


def fail(message: str) -> None:
    print(f"catalog error: {message}", file=sys.stderr)


def main() -> int:
    try:
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(str(exc))
        return 1

    tools = payload.get("tools")
    if payload.get("schema_version") != 1:
        fail("schema_version must be 1")
        return 1
    if not isinstance(tools, list) or not tools:
        fail("tools must be a non-empty list")
        return 1

    errors: list[str] = []
    ids: set[str] = set()
    repositories: set[str] = set()

    for index, tool in enumerate(tools, start=1):
        missing = REQUIRED - set(tool)
        if missing:
            errors.append(f"entry {index}: missing {', '.join(sorted(missing))}")
            continue

        tool_id = tool["id"]
        if not ID_RE.fullmatch(tool_id):
            errors.append(f"entry {index}: invalid id {tool_id!r}")
        if tool_id in ids:
            errors.append(f"entry {index}: duplicate id {tool_id!r}")
        ids.add(tool_id)

        repository = tool["repository"]
        parsed = urlparse(repository)
        if parsed.scheme != "https" or parsed.netloc != "github.com":
            errors.append(f"entry {index}: repository must be an https://github.com URL")
        if repository in repositories:
            errors.append(f"entry {index}: duplicate repository {repository!r}")
        repositories.add(repository)

        try:
            date.fromisoformat(tool["reviewed_on"])
        except (TypeError, ValueError):
            errors.append(f"entry {index}: reviewed_on must use YYYY-MM-DD")

        for field in REQUIRED - {"reviewed_on"}:
            if not isinstance(tool[field], str) or not tool[field].strip():
                errors.append(f"entry {index}: {field} must be a non-empty string")

    if errors:
        for error in errors:
            fail(error)
        return 1

    print(f"catalog ok: {len(tools)} tools, {len(repositories)} unique repositories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
