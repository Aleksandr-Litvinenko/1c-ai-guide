#!/usr/bin/env python3
"""Read-only reconciliation across 1C-Connect, Jira and Bitrix24.

The script never calls a write operation in any of the three systems: the
allowlists below are the only entry points, and every one of them reads.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from xml.etree import ElementTree

SOAP_NS = "http://www.w3.org/2003/05/soap-envelope"
PARTNER_NS = "http://buhphone.com/PartnerWebAPI2"
CORE_NS = "http://v8.1c.ru/8.1/data/core"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

# Only read operations. Adding a mutating name here would defeat the guarantee
# this example is built on, so the set is checked on every call.
CONNECT_READ_OPERATIONS = {"ServiceRequestRead", "ServiceRequestHistory"}
BITRIX_READ_METHODS = {"tasks.task.list"}

# Hourly caps published in the 1C-Connect API documentation (daytime values).
CONNECT_HOURLY_LIMITS = {"ServiceRequestRead": 120, "ServiceRequestHistory": 50}

JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
BITRIX_KEY_RE = re.compile(r"\bB24[-:](\d+)\b", re.IGNORECASE)
SENSITIVE_TICKET_FIELDS = ("Description", "Result", "Summary", "Deadline")

OPEN_TICKET_VALIDATIONS = {"NO_VALIDATION", "REJECTED"}


class BridgeError(RuntimeError):
    """A transport or API error that never carries a secret in its message."""


class RejectRedirects(HTTPRedirectHandler):
    """Reject redirects so credentials are never replayed to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def opener():
    context = ssl.create_default_context()
    return build_opener(HTTPSHandler(context=context), RejectRedirects())


def read_url(name: str, *, required: bool = True) -> str:
    value = os.environ.get(name, "").strip().rstrip("/")
    if not value:
        if required:
            raise BridgeError(f"Переменная окружения {name} не задана.")
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise BridgeError(f"{name} должен быть https-адресом без пути авторизации.")
    if parsed.username or parsed.password:
        raise BridgeError(f"{name} не должен содержать логин или пароль в URL.")
    return value


# --------------------------------------------------------------------------
# Rate budget
# --------------------------------------------------------------------------


class RateBudget:
    """Local hourly call budget for the 1C-Connect operations.

    The service enforces its own limits; this only stops a local agent loop
    from burning the whole hour before a human notices.
    """

    def __init__(self, path: str, limits: dict[str, int], clock=time.time) -> None:
        self.path = path
        self.limits = limits
        self.clock = clock

    def _load(self) -> dict[str, list[float]]:
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(key): [float(item) for item in value]
            for key, value in raw.items()
            if isinstance(value, list)
        }

    def _save(self, data: dict[str, list[float]]) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def remaining(self, operation: str) -> int:
        limit = self.limits.get(operation, 0)
        cutoff = self.clock() - 3600
        used = [stamp for stamp in self._load().get(operation, []) if stamp > cutoff]
        return max(0, limit - len(used))

    def consume(self, operation: str) -> None:
        limit = self.limits.get(operation)
        if limit is None:
            raise BridgeError(f"Для операции {operation} не задан часовой лимит.")
        now = self.clock()
        data = self._load()
        used = [stamp for stamp in data.get(operation, []) if stamp > now - 3600]
        if len(used) >= limit:
            raise BridgeError(
                f"Локальный часовой бюджет {operation} исчерпан: {limit} вызовов. "
                "Подождите до освобождения окна вместо повторов."
            )
        used.append(now)
        data[operation] = used
        self._save(data)


# --------------------------------------------------------------------------
# 1C-Connect SOAP
# --------------------------------------------------------------------------


def build_soap_envelope(operation: str, params: dict[str, tuple[str, str]]) -> bytes:
    """Build a SOAP 1.2 request for PartnerWebAPI2.

    `params` maps a parameter name to an (xsi type, literal value) pair, which
    is how the 1C XDTO Structure is serialised on the wire.
    """
    if operation not in CONNECT_READ_OPERATIONS:
        raise BridgeError(f"Операция {operation} не входит в список только на чтение.")
    properties = []
    for name, (xsi_type, value) in params.items():
        properties.append(
            f'<Property xmlns="{CORE_NS}" name="{name}">'
            f'<Value xsi:type="{xsi_type}">{value}</Value>'
            "</Property>"
        )
    envelope = (
        f'<soap:Envelope xmlns:soap="{SOAP_NS}" xmlns:par="{PARTNER_NS}" '
        f'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="{XSI_NS}">'
        "<soap:Header/><soap:Body>"
        f"<par:{operation}><par:Params>{''.join(properties)}</par:Params></par:{operation}>"
        "</soap:Body></soap:Envelope>"
    )
    return envelope.encode("utf-8")


def parse_soap_result(raw: bytes) -> tuple[str, list[dict[str, str]]]:
    """Return (ResultCode, rows) from a PartnerWebAPI2 response.

    ResultData arrives as a ValueTable: `column` elements define the field
    order, and each `row` carries values positionally.
    """
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as error:
        raise BridgeError("Ответ 1С-Коннект не является валидным XML.") from error

    result_code = ""
    table: list[dict[str, str]] = []
    for prop in root.iter(f"{{{CORE_NS}}}Property"):
        name = prop.get("name")
        value = prop.find(f"{{{CORE_NS}}}Value")
        if value is None:
            continue
        if name == "ResultCode":
            result_code = (value.text or "").strip()
        elif name == "ResultData":
            columns = [
                (column.findtext(f"{{{CORE_NS}}}Name") or "").strip()
                for column in value.findall(f"{{{CORE_NS}}}column")
            ]
            for row in value.findall(f"{{{CORE_NS}}}row"):
                cells = [
                    (cell.text or "").strip()
                    for cell in row.findall(f"{{{CORE_NS}}}Value")
                ]
                table.append(dict(zip(columns, cells)))
    if not result_code:
        raise BridgeError("В ответе 1С-Коннект нет поля ResultCode.")
    return result_code, table


def connect_call(
    operation: str,
    params: dict[str, tuple[str, str]],
    *,
    budget: RateBudget,
    timeout: int = 60,
) -> list[dict[str, str]]:
    base = read_url("CONNECT_WS_BASE_URL")
    user = os.environ.get("CONNECT_WS_USER", "").strip()
    password = os.environ.get("CONNECT_WS_PASSWORD", "")
    if not user or not password:
        raise BridgeError("CONNECT_WS_USER и CONNECT_WS_PASSWORD должны быть заданы.")

    budget.consume(operation)
    body = build_soap_envelope(operation, params)
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    request = Request(
        f"{base}/PartnerWebAPI2",
        data=body,
        method="POST",
        headers={
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{operation}"',
            "Authorization": f"Basic {token}",
        },
    )
    try:
        with opener().open(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as error:
        raise BridgeError(f"1С-Коннект вернул HTTP {error.code}.") from error
    except URLError as error:
        raise BridgeError("Не удалось соединиться с 1С-Коннект.") from error

    code, rows = parse_soap_result(raw)
    if code != "SUCCESS":
        raise BridgeError(f"1С-Коннект вернул ResultCode={code}.")
    return rows


# --------------------------------------------------------------------------
# Jira
# --------------------------------------------------------------------------


def jira_flavor(base_url: str) -> str:
    """Cloud and Data Center do not share a search endpoint any more."""
    host = urlparse(base_url).netloc.lower()
    return "cloud" if host.endswith(".atlassian.net") else "datacenter"


def jira_auth_header() -> dict[str, str]:
    token = os.environ.get("JIRA_TOKEN", "").strip()
    if not token:
        return {}
    email = os.environ.get("JIRA_EMAIL", "").strip()
    if email:
        pair = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {pair}"}
    return {"Authorization": f"Bearer {token}"}


def parse_jira_issues(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    issues = []
    for issue in payload.get("issues") or []:
        fields = issue.get("fields") or {}
        status = fields.get("status") or {}
        category = status.get("statusCategory") or {}
        issues.append(
            {
                "key": issue.get("key", ""),
                "summary": fields.get("summary", ""),
                "status": status.get("name", ""),
                "category": category.get("key", ""),
                "updated": fields.get("updated", ""),
            }
        )
    return issues, payload.get("nextPageToken")


def jira_search(jql: str, *, limit: int, timeout: int = 60) -> list[dict[str, Any]]:
    base = read_url("JIRA_BASE_URL")
    flavor = jira_flavor(base)
    headers = {"Accept": "application/json", **jira_auth_header()}
    fields = ["summary", "status", "updated"]

    if flavor == "cloud":
        # The startAt-based /rest/api/3/search was removed in 2025.
        url = f"{base}/rest/api/3/search/jql"
        body = json.dumps(
            {"jql": jql, "maxResults": min(limit, 100), "fields": fields}
        ).encode("utf-8")
        request = Request(
            url, data=body, method="POST",
            headers={**headers, "Content-Type": "application/json"},
        )
    else:
        query = urlencode(
            {
                "jql": jql,
                "maxResults": min(limit, 100),
                "startAt": 0,
                "fields": ",".join(fields),
            }
        )
        url = f"{base}/rest/api/2/search?{query}"
        request = Request(url, method="GET", headers=headers)

    try:
        with opener().open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = ""
        try:
            body = json.loads(error.read().decode("utf-8"))
            detail = "; ".join(body.get("errorMessages") or [])
        except Exception:  # noqa: BLE001 - the status code is the useful part
            detail = ""
        raise BridgeError(f"Jira вернула HTTP {error.code}. {detail}".strip()) from error
    except URLError as error:
        raise BridgeError("Не удалось соединиться с Jira.") from error

    issues, _ = parse_jira_issues(payload)
    return issues[:limit]


# --------------------------------------------------------------------------
# Bitrix24
# --------------------------------------------------------------------------


def bitrix_tasks(*, limit: int, timeout: int = 60) -> list[dict[str, Any]]:
    webhook = os.environ.get("BITRIX_WEBHOOK", "").strip().rstrip("/")
    if not webhook:
        return []
    expected = os.environ.get("BITRIX24_EXPECTED_HOST", "").strip()
    host = urlparse(webhook).netloc
    if not expected or host != expected:
        raise BridgeError("Хост BITRIX_WEBHOOK не совпадает с BITRIX24_EXPECTED_HOST.")
    method = "tasks.task.list"
    if method not in BITRIX_READ_METHODS:  # pragma: no cover - defensive
        raise BridgeError(f"Метод {method} не разрешён.")

    body = urlencode(
        [
            ("select[]", "ID"),
            ("select[]", "TITLE"),
            ("select[]", "STATUS"),
            ("select[]", "DEADLINE"),
            ("start", "0"),
        ]
    ).encode("utf-8")
    request = Request(
        f"{webhook}/{method}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with opener().open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise BridgeError(f"Bitrix24 вернул HTTP {error.code}.") from error
    except URLError as error:
        raise BridgeError("Не удалось соединиться с Bitrix24.") from error

    result = payload.get("result") or {}
    tasks = result.get("tasks") if isinstance(result, dict) else None
    return list(tasks or [])[:limit]


# --------------------------------------------------------------------------
# Correlation
# --------------------------------------------------------------------------


def external_keys(value: str) -> dict[str, str]:
    """Pull the Jira key and the Bitrix24 task id out of a marker field."""
    text = value or ""
    keys: dict[str, str] = {}
    jira = JIRA_KEY_RE.search(text)
    if jira:
        keys["jira"] = jira.group(1)
    bitrix = BITRIX_KEY_RE.search(text)
    if bitrix:
        keys["bitrix"] = bitrix.group(1)
    return keys


def ticket_is_open(ticket: dict[str, str]) -> bool:
    return (ticket.get("ResultValidation") or "NO_VALIDATION") in OPEN_TICKET_VALIDATIONS


def reconcile(
    tickets: list[dict[str, str]],
    issues: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    *,
    key_field: str,
) -> dict[str, list[dict[str, str]]]:
    """Compare the three systems and report only the divergences."""
    issues_by_key = {issue["key"]: issue for issue in issues if issue.get("key")}
    task_ids = {str(task.get("id") or task.get("ID") or "") for task in tasks}
    task_ids.discard("")

    report: dict[str, list[dict[str, str]]] = {
        "untracked": [],
        "missing_jira_issue": [],
        "missing_bitrix_task": [],
        "issue_done_ticket_open": [],
        "ticket_closed_issue_open": [],
    }
    for ticket in tickets:
        marker = ticket.get(key_field, "")
        keys = external_keys(marker)
        entry = {
            "number": ticket.get("Number", ""),
            "ticket_id": ticket.get("ServiceRequestID", ""),
            "marker": marker,
        }
        if not keys:
            report["untracked"].append(entry)
            continue
        if "jira" not in keys:
            report["missing_jira_issue"].append(entry)
        if "bitrix" not in keys or keys["bitrix"] not in task_ids:
            report["missing_bitrix_task"].append(entry)

        issue = issues_by_key.get(keys.get("jira", ""))
        if issue is None:
            continue
        issue_done = issue.get("category") == "done"
        if issue_done and ticket_is_open(ticket):
            report["issue_done_ticket_open"].append({**entry, "issue": issue["key"]})
        if not issue_done and not ticket_is_open(ticket):
            report["ticket_closed_issue_open"].append({**entry, "issue": issue["key"]})

    return report


def redact(value: str, *, show: bool) -> str:
    if show:
        return value
    if not value:
        return ""
    return f"<скрыто:{len(value)}>"


def iso_period_from(days: int) -> str:
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def make_budget(args: argparse.Namespace) -> RateBudget:
    return RateBudget(args.budget_file, CONNECT_HOURLY_LIMITS)


def command_budget(args: argparse.Namespace) -> None:
    budget = make_budget(args)
    for operation in sorted(CONNECT_HOURLY_LIMITS):
        print(
            f"{operation}: осталось {budget.remaining(operation)} "
            f"из {CONNECT_HOURLY_LIMITS[operation]} вызовов в час"
        )


def command_connect_tickets(args: argparse.Namespace) -> None:
    rows = connect_call(
        "ServiceRequestRead",
        {"PeriodFrom": ("xs:dateTime", iso_period_from(args.days))},
        budget=make_budget(args),
    )
    print(f"заявок за период: {len(rows)}")
    for ticket in rows[: args.top]:
        print(
            f"  {ticket.get('Number','')} | "
            f"валидация={ticket.get('ResultValidation','')} | "
            f"приоритет={ticket.get('Priority','')} | "
            f"тема={redact(ticket.get('Summary',''), show=args.show_sensitive)} | "
            f"{args.key_field}={ticket.get(args.key_field,'')}"
        )


def command_jira_issues(args: argparse.Namespace) -> None:
    issues = jira_search(args.jql, limit=args.top)
    base = read_url("JIRA_BASE_URL")
    print(f"Jira ({jira_flavor(base)}): найдено {len(issues)}")
    for issue in issues:
        print(
            f"  {issue['key']} | {issue['status']} ({issue['category']}) | "
            f"{redact(issue['summary'], show=args.show_sensitive)}"
        )


def command_reconcile(args: argparse.Namespace) -> None:
    tickets = connect_call(
        "ServiceRequestRead",
        {"PeriodFrom": ("xs:dateTime", iso_period_from(args.days))},
        budget=make_budget(args),
    )
    issues = jira_search(args.jql, limit=args.top)
    tasks = bitrix_tasks(limit=args.top)
    report = reconcile(tickets, issues, tasks, key_field=args.key_field)

    titles = {
        "untracked": "Заявки без внешнего ключа",
        "missing_jira_issue": "Заявки без задачи Jira",
        "missing_bitrix_task": "Заявки без задачи Bitrix24",
        "issue_done_ticket_open": "Задача Jira закрыта, заявка ещё открыта",
        "ticket_closed_issue_open": "Заявка закрыта, задача Jira ещё в работе",
    }
    print(
        f"заявок: {len(tickets)} | задач Jira: {len(issues)} | "
        f"задач Bitrix24: {len(tasks)}\n"
    )
    for bucket, title in titles.items():
        entries = report[bucket]
        print(f"{title}: {len(entries)}")
        for entry in entries[: args.top]:
            issue = f" → {entry['issue']}" if "issue" in entry else ""
            print(
                f"  {entry['number']}{issue} | "
                f"маркер={redact(entry['marker'], show=args.show_sensitive)}"
            )
        print()


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--top", type=int, default=10, help="Сколько строк показать.")
    parser.add_argument(
        "--show-sensitive",
        action="store_true",
        help="Показать темы и маркеры. Вывод попадёт в контекст агента.",
    )
    parser.add_argument(
        "--budget-file",
        default=os.environ.get("CONNECT_BUDGET_FILE", ".connect-budget.json"),
        help="Файл локального часового бюджета вызовов 1С-Коннект.",
    )
    parser.add_argument(
        "--key-field",
        default="Field1",
        choices=[f"Field{index}" for index in range(1, 6)],
        help="Дополнительное поле заявки, в котором хранится внешний ключ.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Только чтение: 1С-Коннект, Jira и Bitrix24 в одном отчёте."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    budget = subparsers.add_parser("budget", help="Показать остаток часового бюджета.")
    add_common(budget)

    tickets = subparsers.add_parser("connect-tickets", help="Заявки 1С-Коннект.")
    tickets.add_argument("--days", type=int, default=7, help="Глубина периода в днях.")
    add_common(tickets)

    issues = subparsers.add_parser("jira-issues", help="Задачи Jira по JQL.")
    issues.add_argument("--jql", required=True, help="JQL-запрос только на чтение.")
    add_common(issues)

    both = subparsers.add_parser("reconcile", help="Сверить три системы.")
    both.add_argument("--days", type=int, default=7, help="Глубина периода в днях.")
    both.add_argument("--jql", required=True, help="JQL-запрос только на чтение.")
    add_common(both)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    handlers = {
        "budget": command_budget,
        "connect-tickets": command_connect_tickets,
        "jira-issues": command_jira_issues,
        "reconcile": command_reconcile,
    }
    try:
        handlers[args.command](args)
    except BridgeError as error:
        print(f"ошибка: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
