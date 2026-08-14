from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
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


bridge = load_script("connect_jira_bridge_example")


# The shape below follows the published PartnerWebAPI2 response: ResultData is a
# ValueTable where `column` fixes the field order and each `row` is positional.
SOAP_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
  <soap:Body>
    <m:ServiceRequestReadResponse xmlns:m="http://buhphone.com/PartnerWebAPI2">
      <m:return xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
        <Property name="ResultCode" xmlns="http://v8.1c.ru/8.1/data/core">
          <Value xsi:type="xs:string">SUCCESS</Value>
        </Property>
        <Property name="ResultData" xmlns="http://v8.1c.ru/8.1/data/core">
          <Value xsi:type="ValueTable">
            <column><Name>ServiceRequestID</Name></column>
            <column><Name>Number</Name></column>
            <column><Name>ResultValidation</Name></column>
            <column><Name>Summary</Name></column>
            <column><Name>Field1</Name></column>
            <row>
              <Value xsi:type="xs:string">721b4536-df5c-11e9-721b-721b5a7280d8</Value>
              <Value xsi:type="xs:string">AAKUT-0000083</Value>
              <Value xsi:type="xs:string">NO_VALIDATION</Value>
              <Value xsi:type="xs:string">Не работает 1С</Value>
              <Value xsi:type="xs:string">JIRA:KAFKA-1 B24-55</Value>
            </row>
            <row>
              <Value xsi:type="xs:string">821b4536-df5c-11e9-721b-721b5a7280d9</Value>
              <Value xsi:type="xs:string">AAKUT-0000084</Value>
              <Value xsi:type="xs:string">CONFIRMED</Value>
              <Value xsi:type="xs:string">Отчёт не сходится</Value>
              <Value xsi:type="xs:string"></Value>
            </row>
          </Value>
        </Property>
      </m:return>
    </m:ServiceRequestReadResponse>
  </soap:Body>
</soap:Envelope>
""".encode("utf-8")


class SoapEnvelopeTests(unittest.TestCase):
    def test_envelope_carries_operation_and_typed_parameter(self) -> None:
        body = bridge.build_soap_envelope(
            "ServiceRequestRead", {"PeriodFrom": ("xs:dateTime", "2026-08-01T00:00:00")}
        ).decode("utf-8")
        self.assertIn("<par:ServiceRequestRead>", body)
        self.assertIn('name="PeriodFrom"', body)
        self.assertIn('xsi:type="xs:dateTime"', body)
        self.assertIn("2026-08-01T00:00:00", body)
        self.assertIn("http://buhphone.com/PartnerWebAPI2", body)
        self.assertIn("http://v8.1c.ru/8.1/data/core", body)

    def test_write_operations_are_refused(self) -> None:
        for operation in ("ServiceRequestWrite", "ServiceRequestDelete", "EmployeeEdit"):
            with self.assertRaises(bridge.BridgeError):
                bridge.build_soap_envelope(operation, {})

    def test_allowlist_holds_only_read_operations(self) -> None:
        for operation in bridge.CONNECT_READ_OPERATIONS:
            self.assertTrue(operation.endswith(("Read", "History")), operation)
        self.assertEqual(bridge.BITRIX_READ_METHODS, {"tasks.task.list"})


class SoapParsingTests(unittest.TestCase):
    def test_value_table_rows_map_to_column_names(self) -> None:
        code, rows = bridge.parse_soap_result(SOAP_RESPONSE)
        self.assertEqual(code, "SUCCESS")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["Number"], "AAKUT-0000083")
        self.assertEqual(rows[0]["ResultValidation"], "NO_VALIDATION")
        self.assertEqual(rows[0]["Field1"], "JIRA:KAFKA-1 B24-55")
        self.assertEqual(rows[1]["Field1"], "")

    def test_missing_result_code_is_an_error(self) -> None:
        with self.assertRaises(bridge.BridgeError):
            bridge.parse_soap_result(b"<soap:Envelope xmlns:soap='x'/>")

    def test_broken_xml_is_an_error(self) -> None:
        with self.assertRaises(bridge.BridgeError):
            bridge.parse_soap_result(b"<not xml")


class JiraTests(unittest.TestCase):
    def test_flavor_detection_splits_cloud_from_data_center(self) -> None:
        self.assertEqual(bridge.jira_flavor("https://acme.atlassian.net"), "cloud")
        self.assertEqual(bridge.jira_flavor("https://issues.apache.org/jira"), "datacenter")
        self.assertEqual(bridge.jira_flavor("https://jira.internal.lan"), "datacenter")

    def test_auth_header_picks_basic_or_bearer_or_anonymous(self) -> None:
        with patch.dict(os.environ, {"JIRA_TOKEN": "t", "JIRA_EMAIL": "a@b.c"}, clear=True):
            self.assertTrue(bridge.jira_auth_header()["Authorization"].startswith("Basic "))
        with patch.dict(os.environ, {"JIRA_TOKEN": "t"}, clear=True):
            self.assertEqual(bridge.jira_auth_header(), {"Authorization": "Bearer t"})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(bridge.jira_auth_header(), {})

    def test_issue_parsing_reads_status_category_and_cursor(self) -> None:
        payload = {
            "issues": [
                {
                    "key": "KAFKA-1",
                    "fields": {
                        "summary": "s",
                        "updated": "2026-08-13T13:45:52.000+0000",
                        "status": {"name": "Done", "statusCategory": {"key": "done"}},
                    },
                }
            ],
            "nextPageToken": "abc",
        }
        issues, cursor = bridge.parse_jira_issues(payload)
        self.assertEqual(cursor, "abc")
        self.assertEqual(issues[0]["key"], "KAFKA-1")
        self.assertEqual(issues[0]["category"], "done")

    def test_empty_payload_is_not_an_error(self) -> None:
        self.assertEqual(bridge.parse_jira_issues({}), ([], None))


class CorrelationTests(unittest.TestCase):
    def test_external_keys_are_extracted_from_the_marker(self) -> None:
        self.assertEqual(
            bridge.external_keys("JIRA:KAFKA-1 B24-55"),
            {"jira": "KAFKA-1", "bitrix": "55"},
        )
        self.assertEqual(bridge.external_keys("b24:77"), {"bitrix": "77"})
        self.assertEqual(bridge.external_keys(""), {})
        self.assertEqual(bridge.external_keys("нет ключа"), {})

    def test_ticket_open_state_follows_validation_result(self) -> None:
        self.assertTrue(bridge.ticket_is_open({"ResultValidation": "NO_VALIDATION"}))
        self.assertTrue(bridge.ticket_is_open({"ResultValidation": "REJECTED"}))
        self.assertTrue(bridge.ticket_is_open({}))
        self.assertFalse(bridge.ticket_is_open({"ResultValidation": "CONFIRMED"}))

    def test_reconcile_reports_each_kind_of_divergence(self) -> None:
        _, tickets = bridge.parse_soap_result(SOAP_RESPONSE)
        issues = [{"key": "KAFKA-1", "summary": "s", "status": "Done", "category": "done"}]
        tasks = [{"id": "55", "title": "t"}]

        report = bridge.reconcile(tickets, issues, tasks, key_field="Field1")

        self.assertEqual([e["number"] for e in report["untracked"]], ["AAKUT-0000084"])
        self.assertEqual(
            [e["number"] for e in report["issue_done_ticket_open"]], ["AAKUT-0000083"]
        )
        self.assertEqual(report["missing_bitrix_task"], [])
        self.assertEqual(report["ticket_closed_issue_open"], [])

    def test_bitrix_id_absent_from_task_list_is_reported(self) -> None:
        tickets = [{"Number": "A-1", "ServiceRequestID": "x", "Field1": "KAFKA-9 B24-999"}]
        report = bridge.reconcile(tickets, [], [{"id": "55"}], key_field="Field1")
        self.assertEqual([e["number"] for e in report["missing_bitrix_task"]], ["A-1"])
        self.assertEqual(report["untracked"], [])


class RateBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "budget.json")
        self.now = 1_000_000.0

    def budget(self, limit: int = 3) -> object:
        return bridge.RateBudget(self.path, {"ServiceRequestRead": limit}, clock=lambda: self.now)

    def test_consume_stops_at_the_hourly_limit(self) -> None:
        budget = self.budget(limit=2)
        budget.consume("ServiceRequestRead")
        budget.consume("ServiceRequestRead")
        self.assertEqual(budget.remaining("ServiceRequestRead"), 0)
        with self.assertRaises(bridge.BridgeError):
            budget.consume("ServiceRequestRead")

    def test_window_frees_up_after_an_hour(self) -> None:
        budget = self.budget(limit=1)
        budget.consume("ServiceRequestRead")
        self.assertEqual(budget.remaining("ServiceRequestRead"), 0)
        self.now += 3601
        self.assertEqual(budget.remaining("ServiceRequestRead"), 1)
        budget.consume("ServiceRequestRead")

    def test_unknown_operation_has_no_budget(self) -> None:
        with self.assertRaises(bridge.BridgeError):
            self.budget().consume("SomethingElse")

    def test_corrupt_budget_file_does_not_crash(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(self.budget(limit=5).remaining("ServiceRequestRead"), 5)

    def test_published_limits_match_the_documentation(self) -> None:
        self.assertEqual(bridge.CONNECT_HOURLY_LIMITS["ServiceRequestRead"], 120)
        self.assertEqual(bridge.CONNECT_HOURLY_LIMITS["ServiceRequestHistory"], 50)


class RedactionTests(unittest.TestCase):
    def test_values_are_hidden_unless_explicitly_requested(self) -> None:
        self.assertEqual(bridge.redact("тема заявки", show=False), "<скрыто:11>")
        self.assertEqual(bridge.redact("тема заявки", show=True), "тема заявки")
        self.assertEqual(bridge.redact("", show=False), "")

    def test_url_env_rejects_credentials_and_plain_http(self) -> None:
        with patch.dict(os.environ, {"JIRA_BASE_URL": "http://jira.example"}, clear=True):
            with self.assertRaises(bridge.BridgeError):
                bridge.read_url("JIRA_BASE_URL")
        with patch.dict(
            os.environ, {"JIRA_BASE_URL": "https://u:p@jira.example"}, clear=True
        ):
            with self.assertRaises(bridge.BridgeError):
                bridge.read_url("JIRA_BASE_URL")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(bridge.read_url("JIRA_BASE_URL", required=False), "")


if __name__ == "__main__":
    unittest.main()
