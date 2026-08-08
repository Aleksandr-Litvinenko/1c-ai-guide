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
SECRET_PATTERNS = {
    "Bitrix24 webhook-shaped secret": re.compile(
        r"https://[^/\s<>]+/rest/\d+/[A-Za-z0-9_-]{10,}/?", re.IGNORECASE
    ),
    "1C:Fresh application id": re.compile(
        r"https://1cfresh\.com/a/[A-Za-z0-9_-]+/"
        r"(?!<|example(?:/|\b)|test(?:-copy)?(?:/|\b))"
        r"[A-Za-z0-9_-]{4,}(?:/|\b)",
        re.IGNORECASE,
    ),
    "GitHub token": re.compile(
        r"(?:gh[opsu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
    ),
    "literal integration credential assignment": re.compile(
        r"^\s*(?:export\s+)?"
        r"(?:ONEC_ODATA_PASSWORD|BITRIX_WEBHOOK|BITRIX24_ALLOW_WRITE)\s*=\s*"
        r"[\"']?(?!<|\$\(|\$\{|example|redacted|changeme)[^\s#\"']{6,}",
        re.IGNORECASE | re.MULTILINE,
    ),
    "private key": re.compile(
        r"-{5}BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-{5}", re.IGNORECASE
    ),
    "literal Basic Authorization": re.compile(
        r"Authorization\s*[:=]\s*[\"']?Basic\s+[A-Za-z0-9+/]{16,}={0,2}",
        re.IGNORECASE,
    ),
}
SPLIT_BITRIX_WEBHOOK_RE = re.compile(
    r"https://[^/]*\.bitrix24\.[^/]+/rest/\d+/[A-Za-z0-9_-]{10,}/?",
    re.IGNORECASE,
)
MAX_SCAN_BYTES = 2_000_000


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

    text_files = sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and path.stat().st_size <= MAX_SCAN_BYTES
    )
    for path in text_files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(ROOT)
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{relative}: possible {label}; replace it with a placeholder")
        compact = re.sub(r"[\"'\s+]", "", text)
        if SPLIT_BITRIX_WEBHOOK_RE.search(compact):
            errors.append(
                f"{relative}: possible split Bitrix24 webhook; replace it with a placeholder"
            )

    catalog = json.loads((ROOT / "catalog" / "tools.json").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for tool in catalog["tools"]:
        if tool["repository"] not in readme:
            errors.append(f"README.md: catalog repository is not represented: {tool['repository']}")

    for guide in sorted((ROOT / "guides").glob("*.md")):
        text = guide.read_text(encoding="utf-8")
        heading = (
            "## Verification status"
            if guide.name.endswith(".en.md")
            else "## Статус проверки"
        )
        if heading not in text:
            errors.append(f"{guide.relative_to(ROOT)}: missing {heading!r}")

    for russian in sorted((ROOT / "guides").glob("*.md")):
        if russian.name.endswith(".en.md"):
            continue
        english = russian.with_name(f"{russian.stem}.en.md")
        if not english.exists():
            continue
        for source, other in ((russian, english), (english, russian)):
            if f"({other.name})" not in source.read_text(encoding="utf-8"):
                errors.append(
                    f"{source.relative_to(ROOT)}: missing language switch to {other.name}"
                )

    if errors:
        for error in errors:
            print(f"guide error: {error}", file=sys.stderr)
        return 1

    print(f"guide ok: {len(markdown_files)} Markdown files, local links and wording checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
