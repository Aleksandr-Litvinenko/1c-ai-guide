#!/usr/bin/env python3
"""Read Bitrix24 tasks and preview/create a test lead through an incoming webhook."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import ssl
import sys
import time
import uuid
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


METHOD_RE = re.compile(r"^[a-z][a-z0-9.]+$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
RETRYABLE_CODES = {"QUERY_LIMIT_EXCEEDED"}


class BitrixError(RuntimeError):
    """A Bitrix24 transport or API error without the webhook in the message."""


class RejectRedirects(HTTPRedirectHandler):
    """Reject redirects so a secret webhook is never sent to a new target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def require_webhook() -> str:
    value = os.environ.get("BITRIX_WEBHOOK", "").strip().rstrip("/")
    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or len(parts) != 3
        or parts[0] != "rest"
        or not parts[1].isdigit()
        or not parts[2]
        or parsed.query
        or parsed.fragment
    ):
        raise BitrixError(
            "BITRIX_WEBHOOK должен выглядеть как "
            "https://<portal>.bitrix24.ru/rest/<user>/<secret>/"
        )
    try:
        port = parsed.port
    except ValueError as error:
        raise BitrixError("BITRIX_WEBHOOK содержит некорректный порт.") from error
    if parsed.username or parsed.password or port not in {None, 443}:
        raise BitrixError("Webhook не должен содержать credentials или нестандартный порт.")
    expected_host = os.environ.get("BITRIX24_EXPECTED_HOST", "").strip().lower()
    if not expected_host:
        raise BitrixError("Задайте BITRIX24_EXPECTED_HOST отдельно от URL webhook.")
    if expected_host != (parsed.hostname or "").lower():
        raise BitrixError("Хост webhook не совпадает с BITRIX24_EXPECTED_HOST.")
    return value


def target_fingerprint(webhook: str) -> str:
    return hashlib.sha256(webhook.rstrip("/").encode("utf-8")).hexdigest()[:16]


def write_confirmation(webhook: str) -> str:
    return f"TEST_PORTAL_ONLY:{target_fingerprint(webhook)}"


def method_url(method: str) -> str:
    if not METHOD_RE.fullmatch(method):
        raise BitrixError("Недопустимое имя REST-метода Bitrix24.")
    return f"{require_webhook()}/{method}"


def open_request(request: Request, timeout: int):
    context = ssl.create_default_context()
    opener = build_opener(HTTPSHandler(context=context), RejectRedirects())
    return opener.open(request, timeout=timeout)


def flatten_form(prefix: str, value: Any) -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, nested in value.items():
            name = f"{prefix}[{key}]" if prefix else str(key)
            yield from flatten_form(name, nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from flatten_form(f"{prefix}[]", nested)
    elif isinstance(value, bool):
        yield prefix, "1" if value else "0"
    elif value is not None:
        yield prefix, str(value)


def parse_response(raw: bytes, status: int) -> dict[str, Any]:
    try:
        response = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BitrixError(f"Bitrix24 вернул некорректный JSON (HTTP {status}).") from error
    if not isinstance(response, dict):
        raise BitrixError("Bitrix24 вернул JSON неожиданного типа.")
    return response


def call(
    method: str,
    params: dict[str, Any],
    *,
    transport: str = "form",
    attempts: int = 5,
) -> dict[str, Any]:
    if transport == "json":
        body = json.dumps(params, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        content_type = "application/json; charset=utf-8"
    elif transport == "form":
        body = urlencode(list(flatten_form("", params))).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    else:
        raise BitrixError("Неизвестный transport.")

    for attempt in range(attempts):
        request = Request(
            method_url(method),
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": content_type,
                "User-Agent": "ai-1c-guide/0.3",
            },
            method="POST",
        )
        try:
            with open_request(request, timeout=30) as result:
                status = result.status
                raw = result.read()
        except HTTPError as error:
            status = error.code
            raw = error.read()
        except URLError as error:
            if attempt + 1 < attempts:
                time.sleep(min(2**attempt + random.random(), 10))
                continue
            raise BitrixError(f"Не удалось подключиться к Bitrix24: {error.reason}") from None

        response = parse_response(raw, status)
        error_code = str(response.get("error", ""))
        # Retry the documented transient quota error only. Other 503 errors,
        # including OVERLOAD_LIMIT, need operator action rather than a loop.
        retryable = error_code in RETRYABLE_CODES
        if retryable and attempt + 1 < attempts:
            time.sleep(min(2**attempt + random.random(), 10))
            continue
        if error_code or status < 200 or status >= 300:
            description = str(response.get("error_description") or error_code or f"HTTP {status}")
            raise BitrixError(f"Bitrix24 REST: {description}")
        return response

    raise BitrixError("Bitrix24 не ответил после ограниченного числа повторов.")


def task_value(task: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in task:
            return task[name]
    return None


def command_tasks_list(args: argparse.Namespace) -> None:
    statuses = [int(value) for value in args.statuses.split(",") if value.strip()]
    if not statuses:
        raise BitrixError("Укажите хотя бы один статус.")
    filter_value: dict[str, Any] = {"REAL_STATUS": statuses}
    if args.responsible_id:
        filter_value["RESPONSIBLE_ID"] = args.responsible_id

    tasks: list[dict[str, Any]] = []
    start = 0
    for _ in range(args.max_pages):
        response = call(
            "tasks.task.list",
            {
                "order": {"ID": "asc"},
                "filter": filter_value,
                "select": [
                    "ID",
                    "TITLE",
                    "STATUS",
                    "RESPONSIBLE_ID",
                    "DEADLINE",
                    "GROUP_ID",
                ],
                "start": start,
            },
        )
        result = response.get("result")
        page = result.get("tasks", []) if isinstance(result, dict) else []
        tasks.extend(item for item in page if isinstance(item, dict))
        next_value = response.get("next")
        if next_value is None:
            break
        next_start = int(next_value)
        if next_start <= start:
            raise BitrixError("Bitrix24 вернул некорректное значение next.")
        start = next_start
    else:
        raise BitrixError("Достигнут --max-pages; сузьте фильтр или увеличьте лимит.")

    rows = [
        {
            "id": task_value(task, "id", "ID"),
            "title": (
                task_value(task, "title", "TITLE")
                if args.show_sensitive
                else "[скрыто; добавьте --show-sensitive]"
            ),
            "status": task_value(task, "realStatus", "REAL_STATUS", "status", "STATUS"),
            "responsible_id": (
                task_value(task, "responsibleId", "RESPONSIBLE_ID")
                if args.show_sensitive
                else "[скрыто]"
            ),
            "deadline": (
                task_value(task, "deadline", "DEADLINE")
                if args.show_sensitive
                else "[скрыто]"
            ),
            "group_id": (
                task_value(task, "groupId", "GROUP_ID")
                if args.show_sensitive
                else "[скрыто]"
            ),
        }
        for task in tasks
    ]
    print(json.dumps({"count": len(rows), "tasks": rows}, ensure_ascii=False, indent=2))


def command_task_get(args: argparse.Namespace) -> None:
    response = call(
        "tasks.task.get",
        {
            "taskId": args.task_id,
            "select": [
                "ID",
                "TITLE",
                "STATUS",
                "RESPONSIBLE_ID",
                "DEADLINE",
                "GROUP_ID",
                "UF_CRM_TASK",
            ],
        },
    )
    result = response.get("result")
    task = result.get("task") if isinstance(result, dict) else None
    if not isinstance(task, dict):
        raise BitrixError("Задача не найдена или недоступна текущему пользователю webhook.")
    if args.show_sensitive:
        visible = task
    else:
        visible = {
            "id": task_value(task, "id", "ID"),
            "status": task_value(task, "status", "STATUS"),
            "sensitive_fields_redacted": True,
        }
    print(json.dumps(visible, ensure_ascii=False, indent=2))


def validate_request_id(value: str) -> str:
    if not REQUEST_ID_RE.fullmatch(value):
        raise BitrixError(
            "request-id: 1–64 символа; разрешены латиница, цифры, '.', '_', ':' и '-'. "
            "Не используйте телефон, email или ID клиента."
        )
    return value


def lead_payload(args: argparse.Namespace) -> dict[str, Any]:
    request_id = validate_request_id(args.request_id or f"preview-{uuid.uuid4().hex}")
    fields: dict[str, Any] = {
        "title": args.title,
        "name": args.name,
        "comments": args.comments,
        "sourceId": "WEB",
        "sourceDescription": "AI × 1C Guide",
        "opened": "N",
        "originatorId": "ai-1c-guide",
        "originId": request_id,
        "fm": [
            {"typeId": "PHONE", "valueType": "WORK", "value": args.phone},
        ],
    }
    if args.email:
        fields["fm"].append(
            {"typeId": "EMAIL", "valueType": "WORK", "value": args.email}
        )
    return {"entityTypeId": 1, "fields": fields}


def masked_lead(payload: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(payload, ensure_ascii=False))
    fields = clone.get("fields", {})
    for key in ("title", "name", "comments", "sourceDescription", "originId"):
        if fields.get(key):
            fields[key] = "[скрыто; добавьте --show-sensitive]"
    for item in fields.get("fm", []):
        value = str(item.get("value", ""))
        item["value"] = (value[:2] + "***" + value[-2:]) if len(value) > 4 else "***"
    return clone


def command_lead_preview(args: argparse.Namespace) -> None:
    payload = lead_payload(args)
    visible = payload if args.show_sensitive else masked_lead(payload)
    print(json.dumps(visible, ensure_ascii=False, indent=2))
    print("preview only: запрос в Bitrix24 не отправлялся", file=sys.stderr)


def existing_lead(request_id: str) -> dict[str, Any] | None:
    response = call(
        "crm.item.list",
        {
            "entityTypeId": 1,
            "select": ["id", "originatorId", "originId"],
            "filter": {
                "=originatorId": "ai-1c-guide",
                "=originId": request_id,
            },
            "start": 0,
        },
        transport="json",
    )
    result = response.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise BitrixError("crm.item.list вернул ответ без result.items; запись остановлена.")
    items = result["items"]
    if not items:
        return None
    if not isinstance(items[0], dict):
        raise BitrixError("crm.item.list вернул элемент неожиданного типа; запись остановлена.")
    return items[0]


def command_write_fingerprint(args: argparse.Namespace) -> None:
    del args
    print(write_confirmation(require_webhook()))


def lead_reference(item: dict[str, Any]) -> dict[str, Any]:
    """Return only non-contact identifiers needed to confirm idempotency."""
    return {
        "id": item.get("id"),
        "origin_id_present": bool(item.get("originId")),
    }


def command_lead_create(args: argparse.Namespace) -> None:
    webhook = require_webhook()
    expected_confirmation = write_confirmation(webhook)
    if os.environ.get("BITRIX24_ALLOW_WRITE") != expected_confirmation:
        raise BitrixError(
            "Подтверждение записи не совпадает с webhook. Получите его командой "
            "write-fingerprint только для тестового портала."
        )
    if not args.confirm_test_write:
        raise BitrixError("Добавьте --confirm-test-write после проверки preview.")
    if not args.request_id:
        raise BitrixError("Для защиты от повторов укажите стабильный --request-id.")
    request_id = validate_request_id(args.request_id)
    if not args.title.startswith("[TEST]"):
        raise BitrixError('Заголовок тестового лида должен начинаться с "[TEST]".')

    duplicate = existing_lead(request_id)
    if duplicate:
        print(
            json.dumps(
                {"created": False, "existing": lead_reference(duplicate)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    try:
        # A timed-out write has an unknown result, so never retry crm.item.add blindly.
        response = call(
            "crm.item.add",
            lead_payload(args),
            transport="json",
            attempts=1,
        )
    except BitrixError as create_error:
        try:
            recovered = existing_lead(request_id)
        except BitrixError:
            raise BitrixError(
                "Результат crm.item.add неизвестен. Не повторяйте запись: "
                "сначала найдите лид по originId после восстановления связи."
            ) from create_error
        if recovered:
            print(
                json.dumps(
                    {
                        "created": "unknown",
                        "recovered_after_error": lead_reference(recovered),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        raise create_error
    result = response.get("result")
    item = result.get("item") if isinstance(result, dict) else None
    lead_id = item.get("id") if isinstance(item, dict) else None
    if not lead_id:
        raise BitrixError("Bitrix24 не вернул ID созданного лида.")

    check = call(
        "crm.item.get",
        {"entityTypeId": 1, "id": int(lead_id)},
        transport="json",
    )
    checked_result = check.get("result")
    checked = checked_result.get("item") if isinstance(checked_result, dict) else None
    if (
        not isinstance(checked, dict)
        or str(checked.get("id")) != str(lead_id)
        or checked.get("originatorId") != "ai-1c-guide"
        or checked.get("originId") != request_id
    ):
        raise BitrixError("Созданный лид не прошёл контрольное чтение.")
    print(
        json.dumps(
            {
                "created": True,
                "id": lead_id,
                "origin_id_verified": True,
                "verified_by_get": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def add_lead_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--title", default="[TEST] Заявка AI × 1C")
    parser.add_argument("--name", default="Тестовый пользователь")
    parser.add_argument("--phone", default="+70000000000")
    parser.add_argument("--email", default="qa@example.invalid")
    parser.add_argument("--comments", default="Синтетическая проверка интеграции")
    parser.add_argument("--request-id")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only задачи и явно подтверждаемое создание тестового лида Bitrix24."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    tasks = subparsers.add_parser("tasks-list", help="Прочитать доступные задачи.")
    tasks.add_argument("--responsible-id", type=int)
    tasks.add_argument("--statuses", default="2,3,6")
    tasks.add_argument("--max-pages", type=int, default=20)
    tasks.add_argument("--show-sensitive", action="store_true")
    tasks.set_defaults(handler=command_tasks_list)

    task = subparsers.add_parser("task-get", help="Прочитать одну задачу по ID.")
    task.add_argument("task_id", type=int)
    task.add_argument("--show-sensitive", action="store_true")
    task.set_defaults(handler=command_task_get)

    fingerprint = subparsers.add_parser(
        "write-fingerprint",
        help="Получить привязанное к текущему test webhook подтверждение записи.",
    )
    fingerprint.set_defaults(handler=command_write_fingerprint)

    preview = subparsers.add_parser("lead-preview", help="Показать маскированный payload без сети.")
    add_lead_arguments(preview)
    preview.add_argument("--show-sensitive", action="store_true")
    preview.set_defaults(handler=command_lead_preview)

    create = subparsers.add_parser("lead-create", help="Создать и перечитать тестовый лид.")
    add_lead_arguments(create)
    create.add_argument("--confirm-test-write", action="store_true")
    create.set_defaults(handler=command_lead_create)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.handler(args)
        return 0
    except (BitrixError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
