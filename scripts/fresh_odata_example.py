#!/usr/bin/env python3
"""Safe-by-default examples for the standard 1C:Fresh OData interface."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import ssl
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


ENTITY_RE = re.compile(r"^[A-Za-z]+_[A-Za-zА-Яа-яЁё0-9_]+$")
FIELD_RE = re.compile(r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
FORBIDDEN_CREATE_FIELDS = {"Ref_Key", "DataVersion", "Number", "odata.metadata"}
FORBIDDEN_MARKER_FIELDS = FORBIDDEN_CREATE_FIELDS | {
    "Posted",
    "DeletionMark",
    "Date",
}


class ODataError(RuntimeError):
    """An OData transport or API error without credentials in the message."""


class RejectRedirects(HTTPRedirectHandler):
    """Reject redirects so Basic Authorization never crosses an origin boundary."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ODataError(f"Не задана переменная окружения {name}.")
    return value


def validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ODataError("ONEC_ODATA_BASE_URL должен быть абсолютным HTTPS-адресом.")
    if parsed.query or parsed.fragment:
        raise ODataError("ONEC_ODATA_BASE_URL не должен содержать query или fragment.")
    hostname = (parsed.hostname or "").lower()
    if hostname != "1cfresh.com" and not hostname.endswith(".1cfresh.com"):
        raise ODataError("Этот пример принимает только endpoint домена 1cfresh.com.")
    try:
        port = parsed.port
    except ValueError as error:
        raise ODataError("ONEC_ODATA_BASE_URL содержит некорректный порт.") from error
    if parsed.username or parsed.password or (port not in {None, 443}):
        raise ODataError("В адресе 1С:Фреш нельзя указывать credentials или нестандартный порт.")
    if not parsed.path.rstrip("/").endswith("/odata/standard.odata"):
        raise ODataError("Адрес должен оканчиваться на /odata/standard.odata.")
    return value.rstrip("/")


def target_fingerprint(base_url: str) -> str:
    normalized = validate_base_url(base_url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def write_confirmation(base_url: str) -> str:
    return f"DISPOSABLE_TEST_BASE_ONLY:{target_fingerprint(base_url)}"


def validate_entity(value: str) -> str:
    if not ENTITY_RE.fullmatch(value):
        raise ODataError(
            "Имя сущности должно выглядеть как Document_ЗаказПокупателя "
            "или Catalog_Сотрудники."
        )
    return value


def build_url(base_url: str, path: str, params: dict[str, str] | None = None) -> str:
    encoded_path = quote(path.lstrip("/"), safe="/_$()',")
    query = urlencode(params or {}, safe="',$()")
    return f"{base_url.rstrip('/')}/{encoded_path}" + (f"?{query}" if query else "")


def open_request(request: Request, timeout: int):
    context = ssl.create_default_context()
    opener = build_opener(HTTPSHandler(context=context), RejectRedirects())
    return opener.open(request, timeout=timeout)


def extract_error(body: bytes, status: int) -> str:
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return f"HTTP {status}"

    paths = (
        ("error", "message", "value"),
        ("error", "message"),
        ("odata.error", "message", "value"),
        ("odata.error", "message"),
    )
    for path in paths:
        value: Any = decoded
        for key in path:
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        else:
            if isinstance(value, (str, int, float)) and str(value).strip():
                return str(value)
    return f"HTTP {status}"


def request_bytes(
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    accept: str = "application/json",
) -> bytes:
    base_url = validate_base_url(require_env("ONEC_ODATA_BASE_URL"))
    user = require_env("ONEC_ODATA_USER")
    password = require_env("ONEC_ODATA_PASSWORD")
    credentials = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    headers = {
        "Accept": accept,
        "Authorization": f"Basic {credentials}",
        "User-Agent": "ai-1c-guide/0.3",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    request = Request(
        build_url(base_url, path, params),
        data=data,
        headers=headers,
        method=method,
    )

    try:
        # TLS verification stays enabled; redirects are rejected before credentials
        # could be forwarded to another origin.
        with open_request(request, timeout=60) as response:
            return response.read()
    except HTTPError as error:
        raise ODataError(f"1С OData: {extract_error(error.read(), error.code)}") from None
    except URLError as error:
        raise ODataError(f"Не удалось подключиться к 1С OData: {error.reason}") from None


def request_json(
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = request_bytes(method, path, params=params, payload=payload)
    try:
        decoded = json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ODataError("1С OData вернула некорректный JSON.") from error
    if not isinstance(decoded, dict):
        raise ODataError("1С OData вернула JSON неожиданного типа.")
    return decoded


def unwrap_collection(response: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(response.get("value"), list):
        items = response["value"]
        if not all(isinstance(item, dict) for item in items):
            raise ODataError("Коллекция value содержит элемент неожиданного типа.")
        return items
    legacy = response.get("d")
    if isinstance(legacy, dict) and isinstance(legacy.get("results"), list):
        items = legacy["results"]
        if not all(isinstance(item, dict) for item in items):
            raise ODataError("Коллекция d.results содержит элемент неожиданного типа.")
        return items
    raise ODataError("1С OData вернула коллекцию в неизвестной JSON-обёртке.")


def unwrap_entity(response: dict[str, Any]) -> dict[str, Any]:
    legacy = response.get("d")
    if isinstance(legacy, dict) and "results" not in legacy:
        return legacy
    return response


def entity_path(entity: str, ref_key: str | None = None) -> str:
    entity = validate_entity(entity)
    if ref_key is None:
        return entity
    try:
        normalized = str(uuid.UUID(ref_key))
    except ValueError as error:
        raise ODataError("Ref_Key должен быть UUID.") from error
    return f"{entity}(guid'{normalized}')"


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def redact_odata(value: Any) -> Any:
    """Keep structure and object keys while hiding business values by default."""
    if isinstance(value, list):
        return [redact_odata(item) for item in value]
    if not isinstance(value, dict):
        return "[скрыто; добавьте --show-sensitive]"
    redacted: dict[str, Any] = {}
    for key, nested in value.items():
        if key in {"Ref_Key", "Posted", "DeletionMark"}:
            redacted[key] = nested
        elif isinstance(nested, (dict, list)):
            redacted[key] = redact_odata(nested)
        elif nested is None:
            redacted[key] = None
        else:
            redacted[key] = "[скрыто; добавьте --show-sensitive]"
    return redacted


def command_entity_sets(args: argparse.Namespace) -> None:
    body = request_bytes("GET", "$metadata", accept="application/xml")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as error:
        raise ODataError("1С OData вернула некорректный XML metadata.") from error
    names = sorted(
        {
            element.attrib["Name"]
            for element in root.findall(".//{*}EntitySet")
            if element.attrib.get("Name")
        }
    )
    if args.contains:
        needle = args.contains.casefold()
        names = [name for name in names if needle in name.casefold()]
    print_json({"count": len(names), "entity_sets": names})


def command_list(args: argparse.Namespace) -> None:
    if not 1 <= args.top <= 1000:
        raise ODataError("--top должен быть от 1 до 1000.")
    params = {
        "$format": "json",
        "$select": args.select,
        "$top": str(args.top),
        "$skip": str(args.skip),
    }
    if args.filter:
        params["$filter"] = args.filter
    if args.orderby:
        params["$orderby"] = args.orderby
    items = unwrap_collection(request_json("GET", entity_path(args.entity), params=params))
    visible = items if args.show_sensitive else redact_odata(items)
    print_json({"count": len(items), "items": visible})


def command_get(args: argparse.Namespace) -> None:
    params = {"$format": "json", "$select": args.select}
    item = unwrap_entity(
        request_json("GET", entity_path(args.entity, args.ref_key), params=params)
    )
    print_json(item if args.show_sensitive else redact_odata(item))


def load_fixture(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ODataError(f"Не удалось прочитать JSON fixture: {error}") from error
    if not isinstance(payload, dict):
        raise ODataError("Fixture должен содержать один JSON-объект.")
    forbidden = sorted(FORBIDDEN_CREATE_FIELDS.intersection(payload))
    if forbidden:
        raise ODataError("Удалите служебные поля из fixture: " + ", ".join(forbidden))
    if payload.get("Posted") is not False:
        raise ODataError('Fixture должен явно содержать "Posted": false.')
    return payload


def test_marker(request_id: str) -> str:
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise ODataError(
            "--request-id: 1–64 символа; разрешены латиница, цифры, '.', '_', ':' и '-'."
        )
    return f"AI_1C_GUIDE_TEST:{request_id}"


def validate_marker_field(value: str) -> str:
    if not FIELD_RE.fullmatch(value):
        raise ODataError("Недопустимое имя поля marker.")
    forbidden = {field.casefold() for field in FORBIDDEN_MARKER_FIELDS}
    if value.casefold() in forbidden or value.casefold().endswith("_key"):
        raise ODataError("Marker нельзя записывать в служебное поле или ссылочный ключ.")
    return value


def find_by_marker(entity: str, marker_field: str, marker: str) -> list[dict[str, Any]]:
    marker_field = validate_marker_field(marker_field)
    escaped = marker.replace("'", "''")
    response = request_json(
        "GET",
        entity_path(entity),
        params={
            "$format": "json",
            "$select": f"Ref_Key,Number,Posted,{marker_field}",
            "$filter": f"{marker_field} eq '{escaped}'",
            "$top": "2",
        },
    )
    items = unwrap_collection(response)
    if len(items) > 1:
        raise ODataError("Найдено несколько документов с тем же request-id; запись остановлена.")
    return items


def print_existing_document(item: dict[str, Any], *, recovered: bool = False) -> None:
    print_json(
        {
            "created": False,
            "existing_ref_key": item.get("Ref_Key"),
            "posted": item.get("Posted"),
            "recovered_after_error": recovered,
        }
    )


def command_write_fingerprint(args: argparse.Namespace) -> None:
    del args
    base_url = validate_base_url(require_env("ONEC_ODATA_BASE_URL"))
    print(write_confirmation(base_url))


def command_create(args: argparse.Namespace) -> None:
    base_url = validate_base_url(require_env("ONEC_ODATA_BASE_URL"))
    expected_confirmation = write_confirmation(base_url)
    if os.environ.get("ONEC_ODATA_ALLOW_WRITE") != expected_confirmation:
        raise ODataError(
            "Подтверждение записи не совпадает с текущим endpoint. Получите его "
            "командой write-fingerprint только для одноразовой тестовой базы."
        )
    if not args.confirm_test_write:
        raise ODataError("Добавьте --confirm-test-write после проверки snapshot.")

    entity = validate_entity(args.entity)
    if not entity.startswith("Document_"):
        raise ODataError("Команда create разрешает только сущности Document_*.")
    marker_field = validate_marker_field(args.marker_field)

    payload = load_fixture(args.fixture)
    if marker_field not in payload or not isinstance(payload[marker_field], str):
        raise ODataError(
            "--marker-field должен быть существующим строковым реквизитом fixture, "
            "проверенным по $metadata."
        )
    marker = test_marker(args.request_id)
    payload["DeletionMark"] = False
    payload["Posted"] = False
    payload[marker_field] = marker
    if payload.get("Posted") is not False or payload.get("DeletionMark") is not False:
        raise ODataError("Нарушен инвариант безопасной тестовой записи.")

    existing = find_by_marker(entity, marker_field, marker)
    if existing:
        print_existing_document(existing[0])
        return

    try:
        created = unwrap_entity(
            request_json(
                "POST",
                entity_path(entity),
                params={"$format": "json"},
                payload=payload,
            )
        )
    except ODataError as create_error:
        try:
            recovered = find_by_marker(entity, marker_field, marker)
        except ODataError:
            raise ODataError(
                "Результат POST неизвестен. Не меняйте request-id и не повторяйте "
                "запись, пока поиск marker снова не станет доступен."
            ) from create_error
        if recovered:
            print_existing_document(recovered[0], recovered=True)
            return
        raise ODataError(
            "POST завершился ошибкой, marker пока не найден. Не меняйте request-id; "
            "после проверки стенда повторите ту же команду для безопасного preflight."
        ) from create_error
    ref_key = created.get("Ref_Key")
    if not isinstance(ref_key, str) or not ref_key:
        raise ODataError("1С не вернула Ref_Key созданного документа.")

    verified = unwrap_entity(
        request_json(
            "GET",
            entity_path(entity, ref_key),
            params={
                "$format": "json",
                "$select": f"Ref_Key,Number,Posted,{marker_field}",
            },
        )
    )
    if verified.get("Posted") is not False:
        raise ODataError("Созданный документ неожиданно оказался проведён.")
    if verified.get(marker_field) != marker:
        raise ODataError("Тестовый marker не совпал после повторного чтения.")
    print_json(
        {
            "created": True,
            "ref_key": ref_key,
            "posted": False,
            "marker_verified": True,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Примеры чтения и тестового создания через OData 1С:Фреш."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    metadata = subparsers.add_parser("entity-sets", help="Прочитать список EntitySet из $metadata.")
    metadata.add_argument("--contains", help="Оставить имена, содержащие эту строку.")
    metadata.set_defaults(handler=command_entity_sets)

    listing = subparsers.add_parser("list", help="Прочитать ограниченный список объектов.")
    listing.add_argument("entity")
    listing.add_argument("--select", default="Ref_Key")
    listing.add_argument("--filter")
    listing.add_argument("--orderby")
    listing.add_argument("--top", type=int, default=10)
    listing.add_argument("--skip", type=int, default=0)
    listing.add_argument("--show-sensitive", action="store_true")
    listing.set_defaults(handler=command_list)

    single = subparsers.add_parser("get", help="Прочитать объект по Ref_Key.")
    single.add_argument("entity")
    single.add_argument("ref_key")
    single.add_argument("--select", default="Ref_Key,Number,Date,Posted,Комментарий")
    single.add_argument("--show-sensitive", action="store_true")
    single.set_defaults(handler=command_get)

    fingerprint = subparsers.add_parser(
        "write-fingerprint",
        help="Получить привязанное к текущему test endpoint подтверждение записи.",
    )
    fingerprint.set_defaults(handler=command_write_fingerprint)

    create = subparsers.add_parser(
        "create", help="Создать непроведённый документ только в одноразовой тестовой базе."
    )
    create.add_argument("entity")
    create.add_argument("fixture", type=Path)
    create.add_argument("--request-id", required=True)
    create.add_argument("--marker-field", default="Комментарий")
    create.add_argument("--confirm-test-write", action="store_true")
    create.set_defaults(handler=command_create)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.handler(args)
        return 0
    except ODataError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
