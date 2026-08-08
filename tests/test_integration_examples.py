from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


odata = load_script("fresh_odata_example")
bitrix = load_script("bitrix24_webhook_example")


class FreshODataExampleTests(unittest.TestCase):
    def test_build_url_encodes_cyrillic_and_query(self) -> None:
        url = odata.build_url(
            "https://1cfresh.com/a/sbm/example/odata/standard.odata",
            "Document_УчетВремени",
            {"$top": "10", "$select": "Ref_Key,Number"},
        )
        self.assertIn("Document_%D0%A3%D1%87%D0%B5%D1%82%D0%92%D1%80%D0%B5%D0%BC%D0%B5%D0%BD%D0%B8", url)
        self.assertIn("$top=10", url)
        self.assertNotIn("УчетВремени", url)

    def test_unwraps_modern_and_legacy_collections(self) -> None:
        self.assertEqual(odata.unwrap_collection({"value": [{"Ref_Key": "a"}]}), [{"Ref_Key": "a"}])
        self.assertEqual(
            odata.unwrap_collection({"d": {"results": [{"Ref_Key": "b"}]}}),
            [{"Ref_Key": "b"}],
        )
        with self.assertRaises(odata.ODataError):
            odata.unwrap_collection({"unexpected": []})
        with self.assertRaises(odata.ODataError):
            odata.unwrap_collection({"value": ["not-an-object"]})

    def test_fresh_client_rejects_an_arbitrary_https_origin(self) -> None:
        with self.assertRaises(odata.ODataError):
            odata.validate_base_url(
                "https://other.example.invalid/a/sbm/test/odata/standard.odata"
            )

    def test_default_odata_output_redacts_nested_business_values(self) -> None:
        redacted = odata.redact_odata(
            {
                "Ref_Key": "11111111-1111-1111-1111-111111111111",
                "Description": "Секретное имя",
                "Rows": [{"Comment": "Коммерческая тайна", "Amount": 100}],
            }
        )
        serialized = json.dumps(redacted, ensure_ascii=False)
        self.assertIn("11111111-1111-1111-1111-111111111111", serialized)
        self.assertNotIn("Секретное имя", serialized)
        self.assertNotIn("Коммерческая тайна", serialized)
        self.assertNotIn("100", serialized)

    def test_redirects_are_rejected_before_forwarding_basic_auth(self) -> None:
        request = odata.Request(
            "https://1cfresh.com/a/sbm/example/odata/standard.odata/$metadata",
            headers={"Authorization": "Basic redacted"},
        )
        redirected = odata.RejectRedirects().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://other.example.invalid/collect",
        )
        self.assertIsNone(redirected)

    def test_create_fixture_rejects_server_fields_and_posted_true(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps({"Posted": False, "Ref_Key": "secret"}), encoding="utf-8")
            with self.assertRaises(odata.ODataError):
                odata.load_fixture(path)
            path.write_text(json.dumps({"Posted": True}), encoding="utf-8")
            with self.assertRaises(odata.ODataError):
                odata.load_fixture(path)

    def test_odata_write_gate_is_bound_to_current_endpoint(self) -> None:
        base_url = "https://1cfresh.com/a/sbm/test-copy/odata/standard.odata"
        args = type("Args", (), {"confirm_test_write": True})()
        with patch.dict(
            os.environ,
            {
                "ONEC_ODATA_BASE_URL": base_url,
                "ONEC_ODATA_ALLOW_WRITE": "DISPOSABLE_TEST_BASE_ONLY:wrong-target",
            },
            clear=True,
        ):
            with patch.object(odata, "request_json") as request_mock:
                with self.assertRaises(odata.ODataError):
                    odata.command_create(args)
        request_mock.assert_not_called()

    def test_odata_preflight_prevents_duplicate_post(self) -> None:
        base_url = "https://1cfresh.com/a/sbm/test-copy/odata/standard.odata"
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.json"
            fixture.write_text(
                json.dumps({"Posted": False, "Комментарий": "placeholder"}),
                encoding="utf-8",
            )
            args = type(
                "Args",
                (),
                {
                    "entity": "Document_УчетВремени",
                    "fixture": fixture,
                    "request_id": "stable-request-1",
                    "marker_field": "Комментарий",
                    "confirm_test_write": True,
                },
            )()
            with patch.dict(
                os.environ,
                {
                    "ONEC_ODATA_BASE_URL": base_url,
                    "ONEC_ODATA_ALLOW_WRITE": odata.write_confirmation(base_url),
                },
                clear=True,
            ):
                existing = {"Ref_Key": "existing", "Posted": False}
                with patch.object(odata, "find_by_marker", return_value=[existing]):
                    with patch.object(odata, "request_json") as request_mock:
                        with redirect_stdout(io.StringIO()):
                            odata.command_create(args)
        request_mock.assert_not_called()

    def test_odata_marker_cannot_override_safety_or_server_fields(self) -> None:
        for field in ("Posted", "DeletionMark", "Ref_Key", "Number", "DataVersion"):
            with self.subTest(field=field):
                with self.assertRaises(odata.ODataError):
                    odata.validate_marker_field(field)


class Bitrix24ExampleTests(unittest.TestCase):
    def test_webhook_is_validated_without_exposing_secret(self) -> None:
        webhook = "https://portal.example.invalid" + "/rest/7/" + "a-secret-value/"
        with patch.dict(
            os.environ,
            {
                "BITRIX_WEBHOOK": webhook,
                "BITRIX24_EXPECTED_HOST": "portal.example.invalid",
            },
            clear=True,
        ):
            self.assertEqual(
                bitrix.method_url("tasks.task.list"),
                webhook.rstrip("/") + "/tasks.task.list",
            )
        with patch.dict(os.environ, {"BITRIX_WEBHOOK": "https://example.test/api"}, clear=True):
            with self.assertRaises(bitrix.BitrixError):
                bitrix.require_webhook()

        with patch.dict(
            os.environ,
            {
                "BITRIX_WEBHOOK": webhook,
                "BITRIX24_EXPECTED_HOST": "wrong.example.invalid",
            },
            clear=True,
        ):
            with self.assertRaises(bitrix.BitrixError):
                bitrix.require_webhook()

    def test_nested_form_encoding_matches_classic_api(self) -> None:
        pairs = list(
            bitrix.flatten_form(
                "",
                {
                    "filter": {"REAL_STATUS": [2, 3]},
                    "select": ["ID", "TITLE"],
                    "start": 0,
                },
            )
        )
        self.assertIn(("filter[REAL_STATUS][]", "2"), pairs)
        self.assertIn(("select[]", "TITLE"), pairs)
        self.assertIn(("start", "0"), pairs)

    def test_task_list_uses_real_status_only_as_filter_and_redacts_output(self) -> None:
        args = type(
            "Args",
            (),
            {
                "statuses": "2,3",
                "responsible_id": None,
                "max_pages": 1,
                "show_sensitive": False,
            },
        )()
        response = {
            "result": {
                "tasks": [
                    {
                        "id": "1",
                        "title": "Коммерческая задача",
                        "status": "2",
                        "responsibleId": "7",
                    }
                ]
            }
        }
        with patch.object(bitrix, "call", return_value=response) as call_mock:
            output = io.StringIO()
            with redirect_stdout(output):
                bitrix.command_tasks_list(args)
        params = call_mock.call_args.args[1]
        self.assertIn("REAL_STATUS", params["filter"])
        self.assertNotIn("REAL_STATUS", params["select"])
        self.assertNotIn("Коммерческая задача", output.getvalue())

    def test_task_get_default_output_is_an_allowlist(self) -> None:
        args = type("Args", (), {"task_id": 1, "show_sensitive": False})()
        response = {
            "result": {
                "task": {
                    "id": "1",
                    "status": "2",
                    "title": "Секретная задача",
                    "creator": {"name": "Иван", "workPosition": "Директор"},
                    "customUnexpected": "неожиданное поле",
                }
            }
        }
        output = io.StringIO()
        with patch.object(bitrix, "call", return_value=response):
            with redirect_stdout(output):
                bitrix.command_task_get(args)
        serialized = output.getvalue()
        self.assertIn('"id": "1"', serialized)
        self.assertNotIn("Секретная задача", serialized)
        self.assertNotIn("Иван", serialized)
        self.assertNotIn("неожиданное поле", serialized)

    def test_bitrix_redirects_are_rejected(self) -> None:
        request = bitrix.Request(
            "https://portal.example.invalid/rest/7/redacted/tasks.task.list"
        )
        redirected = bitrix.RejectRedirects().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://other.example.invalid/collect",
        )
        self.assertIsNone(redirected)

    def test_operating_time_limit_is_not_retried_immediately(self) -> None:
        class Response:
            status = 429

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            @staticmethod
            def read() -> bytes:
                return json.dumps(
                    {
                        "error": "OPERATION_TIME_LIMIT",
                        "error_description": "wait for operating_reset_at",
                    }
                ).encode()

        webhook = "https://portal.example.invalid" + "/rest/7/" + "a-secret-value/"
        with patch.dict(
            os.environ,
            {
                "BITRIX_WEBHOOK": webhook,
                "BITRIX24_EXPECTED_HOST": "portal.example.invalid",
            },
            clear=True,
        ):
            with patch.object(bitrix, "open_request", return_value=Response()) as request_mock:
                with patch.object(bitrix.time, "sleep") as sleep_mock:
                    with self.assertRaises(bitrix.BitrixError):
                        bitrix.call("tasks.task.list", {}, attempts=5)

        request_mock.assert_called_once()
        sleep_mock.assert_not_called()

    def test_overload_limit_is_not_retried_as_generic_503(self) -> None:
        class Response:
            status = 503

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            @staticmethod
            def read() -> bytes:
                return json.dumps(
                    {
                        "error": "OVERLOAD_LIMIT",
                        "error_description": "method manually blocked",
                    }
                ).encode()

        webhook = "https://portal.example.invalid" + "/rest/7/" + "a-secret-value/"
        with patch.dict(
            os.environ,
            {
                "BITRIX_WEBHOOK": webhook,
                "BITRIX24_EXPECTED_HOST": "portal.example.invalid",
            },
            clear=True,
        ):
            with patch.object(bitrix, "open_request", return_value=Response()) as request_mock:
                with patch.object(bitrix.time, "sleep") as sleep_mock:
                    with self.assertRaises(bitrix.BitrixError):
                        bitrix.call("tasks.task.list", {}, attempts=5)

        request_mock.assert_called_once()
        sleep_mock.assert_not_called()

    def test_lead_preview_masks_contacts_and_uses_universal_api_fields(self) -> None:
        args = type(
            "Args",
            (),
            {
                "request_id": "request-1",
                "title": "[TEST] Lead",
                "name": "Test",
                "comments": "Synthetic",
                "phone": "+70000000000",
                "email": "qa@example.invalid",
            },
        )()
        payload = bitrix.lead_payload(args)
        masked = bitrix.masked_lead(payload)
        self.assertEqual(payload["entityTypeId"], 1)
        self.assertEqual(payload["fields"]["originId"], "request-1")
        self.assertEqual(payload["fields"]["opened"], "N")
        self.assertNotIn("+70000000000", json.dumps(masked))
        self.assertNotIn("qa@example.invalid", json.dumps(masked))
        self.assertNotIn("[TEST] Lead", json.dumps(masked))
        self.assertNotIn("Synthetic", json.dumps(masked))
        self.assertNotIn("request-1", json.dumps(masked))

    def test_lead_request_id_must_be_opaque(self) -> None:
        for value in ("person@example.invalid", "+70000000000", "contains space"):
            with self.subTest(value=value):
                with self.assertRaises(bitrix.BitrixError):
                    bitrix.validate_request_id(value)

    def test_lead_preview_never_opens_network(self) -> None:
        args = type(
            "Args",
            (),
            {
                "request_id": "request-preview",
                "title": "[TEST] Lead",
                "name": "Test",
                "comments": "Synthetic",
                "phone": "+70000000000",
                "email": "qa@example.invalid",
                "show_sensitive": False,
            },
        )()
        with patch.object(bitrix, "open_request") as request_mock:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                bitrix.command_lead_preview(args)
        request_mock.assert_not_called()

    def test_existing_lead_fails_closed_on_unknown_success_shape(self) -> None:
        with patch.object(bitrix, "call", return_value={"result": {}}):
            with self.assertRaises(bitrix.BitrixError):
                bitrix.existing_lead("request-unknown-shape")

    def test_bitrix_write_gate_is_bound_to_current_webhook(self) -> None:
        webhook = "https://portal.example.invalid" + "/rest/7/" + "a-secret-value/"
        args = type("Args", (), {})()
        with patch.dict(
            os.environ,
            {
                "BITRIX_WEBHOOK": webhook,
                "BITRIX24_EXPECTED_HOST": "portal.example.invalid",
                "BITRIX24_ALLOW_WRITE": "TEST_PORTAL_ONLY:wrong-target",
            },
            clear=True,
        ):
            with patch.object(bitrix, "existing_lead") as lookup_mock:
                with self.assertRaises(bitrix.BitrixError):
                    bitrix.command_lead_create(args)
        lookup_mock.assert_not_called()

    def test_lead_write_is_not_retried_blindly_after_unknown_result(self) -> None:
        args = type(
            "Args",
            (),
            {
                "request_id": "request-2",
                "title": "[TEST] Lead",
                "name": "Test",
                "comments": "Synthetic",
                "phone": "+70000000000",
                "email": "qa@example.invalid",
                "confirm_test_write": True,
            },
        )()
        webhook = "https://portal.example.invalid" + "/rest/7/" + "a-secret-value/"
        with patch.dict(
            os.environ,
            {
                "BITRIX_WEBHOOK": webhook,
                "BITRIX24_EXPECTED_HOST": "portal.example.invalid",
                "BITRIX24_ALLOW_WRITE": bitrix.write_confirmation(webhook.rstrip("/")),
            },
            clear=True,
        ):
            with patch.object(bitrix, "existing_lead", side_effect=[None, None]):
                with patch.object(bitrix, "call", side_effect=bitrix.BitrixError("timeout")) as call_mock:
                    with self.assertRaises(bitrix.BitrixError):
                        bitrix.command_lead_create(args)
        call_mock.assert_called_once()
        self.assertEqual(call_mock.call_args.kwargs["attempts"], 1)

    def test_lead_verification_requires_the_same_origin_id(self) -> None:
        args = type(
            "Args",
            (),
            {
                "request_id": "request-verify",
                "title": "[TEST] Lead",
                "name": "Test",
                "comments": "Synthetic",
                "phone": "+70000000000",
                "email": "qa@example.invalid",
                "confirm_test_write": True,
            },
        )()
        webhook = "https://portal.example.invalid" + "/rest/7/" + "a-secret-value/"
        responses = [
            {"result": {"item": {"id": 42}}},
            {
                "result": {
                    "item": {
                        "id": 42,
                        "originatorId": "ai-1c-guide",
                        "originId": "different-request",
                    }
                }
            },
        ]
        with patch.dict(
            os.environ,
            {
                "BITRIX_WEBHOOK": webhook,
                "BITRIX24_EXPECTED_HOST": "portal.example.invalid",
                "BITRIX24_ALLOW_WRITE": bitrix.write_confirmation(webhook.rstrip("/")),
            },
            clear=True,
        ):
            with patch.object(bitrix, "existing_lead", return_value=None):
                with patch.object(bitrix, "call", side_effect=responses):
                    with self.assertRaises(bitrix.BitrixError):
                        bitrix.command_lead_create(args)


if __name__ == "__main__":
    unittest.main()
