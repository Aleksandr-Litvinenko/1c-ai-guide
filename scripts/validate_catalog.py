#!/usr/bin/env python3
"""Validate schema v2 of the machine-readable AI x 1C catalog."""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "tools.json"
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
GITHUB_RE = re.compile(r"^https://github\.com/[^/]+/[^/?#]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

REQUIRED = {
    "id",
    "name",
    "repository",
    "source_ref",
    "source_revision",
    "category",
    "use_case",
    "runtimes",
    "license",
    "source_available",
    "access",
    "platforms",
    "prerequisites",
    "maturity",
    "maintenance",
    "verification",
}
ACCESS_FIELDS = {
    "configuration_source",
    "configuration_metadata",
    "business_data",
    "external_services",
    "default_mode",
    "mutating_operations",
    "destructive_operations",
}
ACCESS_VALUES = {"none", "read", "read-write", "configurable", "unknown"}
DEFAULT_MODES = {"read-only", "read-write", "configurable", "unknown"}
LICENSE_STATUSES = {"file", "readme-only", "not-declared", "not-applicable"}
SOURCE_STATUSES = {"yes", "partial", "no", "not-applicable", "unknown"}
MATURITY_VALUES = {"stable", "beta", "experimental", "pre-1.0", "not-applicable", "unknown"}
MAINTENANCE_STATUSES = {"recent", "stale", "live-service", "archived", "unknown"}
VERIFICATION_LEVELS = {"metadata", "docs", "artifact", "cli-smoke", "live-smoke", "e2e"}


def fail(message: str) -> None:
    print(f"catalog error: {message}", file=sys.stderr)


def parse_date(value: object, field: str, errors: list[str]) -> date | None:
    try:
        parsed = date.fromisoformat(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        errors.append(f"{field} must use YYYY-MM-DD")
        return None
    if parsed > date.today():
        errors.append(f"{field} cannot be in the future")
    return parsed


def require_non_empty_strings(value: object, field: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{field} must be a non-empty list")
        return
    if any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{field} must contain only non-empty strings")


def valid_http_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def main() -> int:
    try:
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(str(exc))
        return 1

    errors: list[str] = []
    if not isinstance(payload, dict):
        fail("catalog root must be an object")
        return 1
    if payload.get("schema_version") != 2:
        errors.append("schema_version must be 2")

    reviewed_on = parse_date(payload.get("reviewed_on"), "reviewed_on", errors)
    tools = payload.get("tools")
    if not isinstance(tools, list) or not tools:
        errors.append("tools must be a non-empty list")
        tools = []

    ids: set[str] = set()
    repositories: set[str] = set()

    for index, tool in enumerate(tools, start=1):
        prefix = f"entry {index}"
        if not isinstance(tool, dict):
            errors.append(f"{prefix}: must be an object")
            continue

        missing = REQUIRED - set(tool)
        if missing:
            errors.append(f"{prefix}: missing {', '.join(sorted(missing))}")
            continue

        for field in {"id", "name", "source_ref", "category", "use_case"}:
            if not isinstance(tool[field], str) or not tool[field].strip():
                errors.append(f"{prefix}: {field} must be a non-empty string")

        tool_id = tool["id"]
        if isinstance(tool_id, str):
            if not ID_RE.fullmatch(tool_id):
                errors.append(f"{prefix}: invalid id {tool_id!r}")
            if tool_id in ids:
                errors.append(f"{prefix}: duplicate id {tool_id!r}")
            ids.add(tool_id)

        repository = tool["repository"]
        if not isinstance(repository, str) or not GITHUB_RE.fullmatch(repository):
            errors.append(f"{prefix}: repository must be exactly https://github.com/owner/repo")
        elif repository in repositories:
            errors.append(f"{prefix}: duplicate repository {repository!r}")
        else:
            repositories.add(repository)

        if not isinstance(tool["source_revision"], str) or not SHA_RE.fullmatch(tool["source_revision"]):
            errors.append(f"{prefix}: source_revision must be a full lowercase commit SHA")

        require_non_empty_strings(tool["runtimes"], f"{prefix}: runtimes", errors)
        require_non_empty_strings(tool["platforms"], f"{prefix}: platforms", errors)
        require_non_empty_strings(tool["prerequisites"], f"{prefix}: prerequisites", errors)

        source_available = tool["source_available"]
        if source_available not in SOURCE_STATUSES:
            errors.append(f"{prefix}: invalid source_available {source_available!r}")
        maturity = tool["maturity"]
        if maturity not in MATURITY_VALUES:
            errors.append(f"{prefix}: invalid maturity {maturity!r}")

        license_info = tool["license"]
        if not isinstance(license_info, dict):
            errors.append(f"{prefix}: license must be an object")
        else:
            if set(license_info) != {"spdx", "status", "source_url"}:
                errors.append(f"{prefix}: license must contain spdx, status and source_url")
            status = license_info.get("status")
            spdx = license_info.get("spdx")
            if status not in LICENSE_STATUSES:
                errors.append(f"{prefix}: invalid license.status {status!r}")
            if not isinstance(spdx, str) or not spdx.strip():
                errors.append(f"{prefix}: license.spdx must be a non-empty string")
            if status == "file" and spdx == "unknown":
                errors.append(f"{prefix}: license file cannot have unknown SPDX id")
            if not valid_http_url(license_info.get("source_url")):
                errors.append(f"{prefix}: license.source_url must be an HTTP(S) URL")

        access = tool["access"]
        if not isinstance(access, dict):
            errors.append(f"{prefix}: access must be an object")
        else:
            if set(access) != ACCESS_FIELDS:
                errors.append(f"{prefix}: access fields do not match schema")
            for field in ACCESS_FIELDS - {"default_mode", "mutating_operations", "destructive_operations"}:
                if access.get(field) not in ACCESS_VALUES:
                    errors.append(f"{prefix}: invalid access.{field} {access.get(field)!r}")
            if access.get("default_mode") not in DEFAULT_MODES:
                errors.append(f"{prefix}: invalid access.default_mode {access.get('default_mode')!r}")
            for field in ("mutating_operations", "destructive_operations"):
                value = access.get(field)
                if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                    errors.append(f"{prefix}: access.{field} must be a list of non-empty strings")
            if access.get("default_mode") == "read-only" and access.get("mutating_operations"):
                errors.append(f"{prefix}: read-only default cannot list mutating operations")

        maintenance = tool["maintenance"]
        last_push: date | None = None
        if not isinstance(maintenance, dict):
            errors.append(f"{prefix}: maintenance must be an object")
        else:
            if set(maintenance) != {"last_push", "archived", "status"}:
                errors.append(f"{prefix}: maintenance fields do not match schema")
            last_push = parse_date(maintenance.get("last_push"), f"{prefix}: maintenance.last_push", errors)
            if not isinstance(maintenance.get("archived"), bool):
                errors.append(f"{prefix}: maintenance.archived must be boolean")
            status = maintenance.get("status")
            if status not in MAINTENANCE_STATUSES:
                errors.append(f"{prefix}: invalid maintenance.status {status!r}")
            if maintenance.get("archived") and status != "archived":
                errors.append(f"{prefix}: archived repositories must use maintenance.status=archived")
            if reviewed_on and last_push:
                age = reviewed_on - last_push
                if status == "recent" and age > timedelta(days=180):
                    errors.append(f"{prefix}: recent status requires a push within 180 days")
                if status == "stale" and age <= timedelta(days=365):
                    errors.append(f"{prefix}: stale status requires more than 365 days without a push")

        verification = tool["verification"]
        if not isinstance(verification, dict):
            errors.append(f"{prefix}: verification must be an object")
        else:
            expected = {"level", "checked_on", "version", "evidence_urls", "notes"}
            if set(verification) != expected:
                errors.append(f"{prefix}: verification fields do not match schema")
            level = verification.get("level")
            if level not in VERIFICATION_LEVELS:
                errors.append(f"{prefix}: invalid verification.level {level!r}")
            checked_on = parse_date(verification.get("checked_on"), f"{prefix}: verification.checked_on", errors)
            if reviewed_on and checked_on and checked_on > reviewed_on:
                errors.append(f"{prefix}: verification.checked_on cannot exceed catalog reviewed_on")
            for field in ("version", "notes"):
                if not isinstance(verification.get(field), str) or not verification.get(field, "").strip():
                    errors.append(f"{prefix}: verification.{field} must be a non-empty string")
            evidence = verification.get("evidence_urls")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"{prefix}: verification.evidence_urls must be non-empty")
            elif any(not valid_http_url(url) for url in evidence):
                errors.append(f"{prefix}: every evidence URL must be HTTP(S)")

    if errors:
        for error in errors:
            fail(error)
        return 1

    print(f"catalog ok: schema v2, {len(tools)} tools, {len(repositories)} unique repositories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
