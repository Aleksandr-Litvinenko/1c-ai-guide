#!/usr/bin/env python3
"""Validate local Markdown links and high-risk wording in the guide."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"(?<!!)\[[^]]*\]\(([^)]+)\)")
BANNED_CLAIMS = {
    "production-grade": "avoid unqualified production-readiness claims",
    "полный технический каталог": "a curated catalog cannot guarantee completeness",
    "complete technical catalog": "a curated catalog cannot guarantee completeness",
    "OData + read-only MCP": "OData is not read-only by itself",
    "OData + a read-only MCP server": "OData is not read-only by itself",
    "защищённый шлюз к данным": "security properties require independent verification",
    "A protected gateway to business data": "security properties require independent verification",
}


def main() -> int:
    errors: list[str] = []
    markdown_files = sorted(path for path in ROOT.rglob("*.md") if ".git" not in path.parts)

    for path in markdown_files:
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        for link in LINK_RE.findall(text):
            target = link.strip().split(maxsplit=1)[0].strip("<>")
            if not target or target.startswith("#"):
                continue
            parsed = urlparse(target)
            if parsed.scheme in {"http", "https", "mailto"}:
                continue
            if parsed.scheme:
                errors.append(f"{relative}: unsupported link scheme in {target!r}")
                continue
            local = (path.parent / unquote(parsed.path)).resolve()
            if ROOT not in local.parents and local != ROOT:
                errors.append(f"{relative}: link escapes repository: {target!r}")
            elif not local.exists():
                errors.append(f"{relative}: missing local target: {target!r}")

        for claim, reason in BANNED_CLAIMS.items():
            if claim in text:
                errors.append(f"{relative}: banned wording {claim!r}: {reason}")

    catalog = json.loads((ROOT / "catalog" / "tools.json").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for tool in catalog["tools"]:
        if tool["repository"] not in readme:
            errors.append(f"README.md: catalog repository is not represented: {tool['repository']}")

    for recipe in sorted((ROOT / "recipes").glob("*.md")):
        text = recipe.read_text(encoding="utf-8")
        if "## Статус проверки" not in text:
            errors.append(f"{recipe.relative_to(ROOT)}: missing '## Статус проверки'")

    if errors:
        for error in errors:
            print(f"guide error: {error}", file=sys.stderr)
        return 1

    print(f"guide ok: {len(markdown_files)} Markdown files, local links and wording checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
