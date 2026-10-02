"""Read-only import monitoring tools."""

import base64
import json
import time
import unittest
from unittest.mock import Mock, patch

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from jsm_asset_mcp import client as client_module
from jsm_asset_mcp.client import AssetsClient
from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.server import create_server
from jsm_asset_mcp.tools import Dependencies, Toolset, _json_size, _redact_secrets, _redact_text

SOURCE = "0b6ee1c8-2d4c-4a5e-9f1a-3c7d8e9f0a1b"
EXECUTION = "5f2a9c3e-7b1d-4e8f-a6c2-9d0e1f2a3b4c"
REDACTED = "***redacted***"
IMPORT_TOOLS = {
    "list_import_sources",
    "get_import_source",
    "get_import_config_status",
    "get_last_import_execution",
    "get_import_execution_status",
    "get_import_progress",
}


class RecordingClient:
    def __init__(self, response: object = None) -> None:
        self.gets: list[str] = []
        self.response = {"status": "IDLE"} if response is None else response

    def get(self, path: str, params=None) -> object:
        self.gets.append(path)
        return self.response


def _toolset(client: RecordingClient, **settings: object) -> Toolset:
    return Toolset(Dependencies(settings=Settings(**settings), client=client, schema=None))


def _calls(toolset: Toolset) -> dict:
    """One call per tool, all with valid IDs."""
    return {
        "list_import_sources": lambda: toolset.list_import_sources("5"),
        "get_import_source": lambda: toolset.get_import_source(SOURCE),
        "get_import_config_status": lambda: toolset.get_import_config_status(SOURCE),
        "get_last_import_execution": lambda: toolset.get_last_import_execution(SOURCE),
        "get_import_execution_status": lambda: toolset.get_import_execution_status(SOURCE, EXECUTION),
        "get_import_progress": lambda: toolset.get_import_progress(SOURCE),
    }


class ImportToolPathTests(unittest.TestCase):
    def test_each_tool_calls_its_documented_endpoint(self) -> None:
        expected = {
            "list_import_sources": "/importsource/objectschema/5",
            "get_import_source": f"/importsource/{SOURCE}",
            "get_import_config_status": f"/importsource/{SOURCE}/configstatus",
            "get_last_import_execution": f"/importsource/{SOURCE}/executions/status",
            "get_import_execution_status": f"/importsource/{SOURCE}/executions/{EXECUTION}/status",
            "get_import_progress": f"/progress/category/imports/{SOURCE}",
        }
        for name, path in expected.items():
            with self.subTest(tool=name):
                client = RecordingClient()
                _calls(_toolset(client))[name]()
                self.assertEqual(client.gets, [path])

    def test_import_tools_are_read_tools(self) -> None:
        toolset = _toolset(RecordingClient())
        self.assertLessEqual(IMPORT_TOOLS, {tool.__name__ for tool in toolset.read_tools})
        self.assertEqual(IMPORT_TOOLS & {tool.__name__ for tool in toolset.write_tools}, set())


class ImportIdValidationTests(unittest.TestCase):
    BAD_IDS = (
        "", "../objectschema/1", "a/b", "X/executions/status", "1?x=2", "id with space",
        "abc\n", "１２", "%2e%2e", ".", "..", "a.b", "a" * 129, 123, None,
    )

    @staticmethod
    def _id_params(toolset: Toolset) -> list:
        """(parameter name, call) for every ID parameter of every tool."""
        return [
            ("import_source_id", lambda v: toolset.get_import_source(v)),
            ("import_source_id", lambda v: toolset.get_import_config_status(v)),
            ("import_source_id", lambda v: toolset.get_last_import_execution(v)),
            ("import_source_id", lambda v: toolset.get_import_execution_status(v, EXECUTION)),
            ("execution_id", lambda v: toolset.get_import_execution_status(SOURCE, v)),
            ("import_source_id", lambda v: toolset.get_import_progress(v)),
        ]

    def test_unsafe_ids_never_reach_the_client(self) -> None:
        client = RecordingClient()
        toolset = _toolset(client)
        for index, (param, call) in enumerate(self._id_params(toolset)):
            for bad in self.BAD_IDS:
                with self.subTest(call=index, bad=bad):
                    with self.assertRaisesRegex(ValueError, param):
                        call(bad)
        self.assertEqual(client.gets, [])

    def test_valid_ids_are_accepted(self) -> None:
        for good in ("42", "a" * 128, "a_b-C9", SOURCE):
            client = RecordingClient()
            toolset = _toolset(client)
            for index, (_, call) in enumerate(self._id_params(toolset)):
                with self.subTest(call=index, good=good):
                    call(good)
            self.assertEqual(len(client.gets), 6)

    def test_schema_id_must_be_numeric(self) -> None:
        client = RecordingClient([])
        toolset = _toolset(client)
        for bad in ("../1", "5/x", SOURCE, "", "5.0", 5):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "schema_id"):
                    toolset.list_import_sources(bad)
        self.assertEqual(client.gets, [])


class RedactionTests(unittest.TestCase):
    def test_secret_keys_are_masked_whatever_the_value_type(self) -> None:
        payload = {
            "password": ["l"],
            "credentials": {"user": "svc", "pass": "k"},
            "pin_secret": 1234,
            "privateKey": {"value": "k"},
            "tokenGenerated": True,
            "emptySecret": "",
            "authToken": None,
        }
        result = _redact_secrets(payload)
        for key in ("password", "credentials", "pin_secret", "privateKey"):
            with self.subTest(key=key):
                self.assertEqual(result[key], REDACTED)
        self.assertIs(result["tokenGenerated"], True)
        self.assertEqual(result["emptySecret"], "")
        self.assertIsNone(result["authToken"])

    def test_secret_key_variants_are_matched(self) -> None:
        for key in (
            "password", "PASSWORD", "passwd", "pwd", "bindPass", "passphrase", "secret",
            "clientSecret", "token", "accessToken", "credential", "credentials", "auth",
            "authorization", "bearer", "cookie", "api_key", "api-key", "API_KEY", "apiKey",
            "accessKey", "sharedKey", "sasKey", "privateKey", "private_key", "signingKey",
            "encryption_key",
            "connectionString", "conn_str", "dsn", "jdbcUrl", "certificate", "signature",
        ):
            with self.subTest(key=key):
                self.assertEqual(_redact_secrets({key: "x"})[key], REDACTED)

    def test_neutral_keys_are_kept(self) -> None:
        for key in ("host", "name", "author", "importSourceModuleKey", "objectTypeKey", "bypassed", "key"):
            with self.subTest(key=key):
                self.assertEqual(_redact_secrets({key: "x"})[key], "x")

    def test_credentials_inside_string_values_are_masked(self) -> None:
        result = _redact_secrets({
            "url": "ldap://svc:hunter2@ldap.example.com:636/dc=example",
            "message": "Login failed: Server=sql01;User Id=sa;Password=f00;Database=cmdb",
            "note": "token: abc123 rejected",
        })
        self.assertEqual(result["url"], f"ldap://{REDACTED}@ldap.example.com:636/dc=example")
        self.assertNotIn("f00", result["message"])
        self.assertIn(f"Password={REDACTED}", result["message"])
        self.assertIn("Server=sql01", result["message"])
        self.assertNotIn("abc123", result["note"])

    def test_common_inline_credential_forms_are_masked(self) -> None:
        cases = {
            "db_password=hunter2": "db_password=***redacted***",
            '{"password": "hunter2"}': '{"password": "***redacted***"}',
            "{'pwd': 'hunter2'}": "{'pwd': '***redacted***'}",
            'password="a b c" ok': 'password="***redacted***" ok',
            "client_secret=hunter2": "client_secret=***redacted***",
            "clientSecret: hunter2": "clientSecret: ***redacted***",
            "refresh_token=hunter2&x=1": "refresh_token=***redacted***&x=1",
            "my-api-key: hunter2": "my-api-key: ***redacted***",
            "pass=hunter2": "pass=***redacted***",
            "passphrase=hunter2": "passphrase=***redacted***",
            "credentials=hunter2": "credentials=***redacted***",
            "AccountKey=hunter2==;SharedAccessKey=hunter2": "AccountKey=***redacted***;SharedAccessKey=***redacted***",
            "?sv=1&sig=hunter2&se=2": "?sv=1&sig=***redacted***&se=2",
            "Authorization: Basic aHVudGVyMg==": "Authorization: Basic ***redacted***",
            "authorization=hunter2": "authorization=***redacted***",
            "sent Bearer eyJhbGciOiJIUzI1NiJ9.hunter2": "sent Bearer ***redacted***",
            "https://ghp_hunter2@github.com/x": "https://***redacted***@github.com/x",
            "ldap://svc:hun@ter2@dc01/x": "ldap://***redacted***@dc01/x",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(_redact_text(raw), expected)

    def test_quoted_authorization_values_are_masked(self) -> None:
        basic = base64.b64encode(b"svc:hunter2").decode()
        cases = {
            f'{{"Authorization": "Basic {basic}"}}': '{"Authorization": "Basic ***redacted***"}',
            f"{{'Authorization': 'Basic {basic}'}}": "{'Authorization': 'Basic ***redacted***'}",
            f'{{"authorization":"Basic {basic}"}}': '{"authorization":"Basic ***redacted***"}',
            f'{{"Proxy-Authorization": "Basic {basic}"}}': '{"Proxy-Authorization": "Basic ***redacted***"}',
            f'{{\\"Authorization\\": \\"Basic {basic}\\"}}': '{\\"Authorization\\": \\"Basic ***redacted***\\"}',
            '{"Authorization": "Digest username=\\"svc\\", response=\\"hunter2\\""}':
                '{"Authorization": "Digest ***redacted***"}',
            '{"Authorization": "hunter2 hunter3"}': '{"Authorization": "***redacted***"}',
            "headers={'Authorization': 'NTLM hunter2', 'Accept': 'json'}":
                "headers={'Authorization': 'NTLM ***redacted***', 'Accept': 'json'}",
            "Authorization: Basic hunter2, retrying": "Authorization: Basic ***redacted***, retrying",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(_redact_text(raw), expected)

    def test_quoted_values_end_at_their_unescaped_closing_quote(self) -> None:
        cases = {
            '{"password": "hun\\"ter2", "user": "svc"}': '{"password": "***redacted***", "user": "svc"}',
            "{'password': 'hun\\'ter2', 'user': 'svc'}": "{'password': '***redacted***', 'user': 'svc'}",
            '{\\"password\\": \\"hunter2\\", \\"user\\": \\"svc\\"}':
                '{\\"password\\": \\"***redacted***\\", \\"user\\": \\"svc\\"}',
            # An unterminated value cannot be told apart from what follows it.
            'login failed {"password": "hunter2 and the rest': 'login failed {"password": "***redacted***',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(_redact_text(raw), expected)

    def test_credentials_are_masked_whole_whatever_their_length(self) -> None:
        # Both sides of the former 256/512/4096-character bounds.
        for length in (8, 255, 256, 257, 511, 512, 513, 4095, 4096, 4097, 20_000):
            secret = ("eyJhbGciOiJSUzI1NiJ9." + "A" * length)[:length].ljust(8, "A")
            forms = (
                f'{{"access_token": "{secret}"}}',
                f"{{'access_token': '{secret}'}}",
                f"access_token={secret}&next=1",
                f'{{"Authorization": "Bearer {secret}"}}',
                f"Authorization: Basic {secret}",
                f"sent Bearer {secret} upstream",
                f"ldap://{secret}@dc01/x",
            )
            for raw in forms:
                with self.subTest(length=length, form=raw[:24]):
                    result = _redact_text(raw)
                    self.assertNotIn(secret[-8:], result)
                    self.assertIn(REDACTED, result)
                    self.assertLess(len(result), 80)

    def test_ordinary_text_is_left_alone(self) -> None:
        for text in (
            "passed: 3 tests", "design: modern", "total tokens: 5", "Server=sql01;Database=cmdb",
            "see https://example.com/path?a=b", "user@example.com wrote",
        ):
            with self.subTest(text=text):
                self.assertEqual(_redact_text(text), text)

    def test_redaction_time_is_linear_on_adversarial_strings(self) -> None:
        # ~100 KB each; the unbounded URL pattern took about 15 s on the first.
        for text in (
            "a." * 50_000, "a+" * 50_000, "a-" * 50_000, "a://" * 25_000, "x_" * 50_000 + "=",
            "password" * 12_000, "db_password=" * 8_000, "authorization:" * 7_000, "bearer " * 14_000,
            'password="' * 10_000, "token:'" * 14_000, 'password="a" ' * 8_000, '\\"token\\": \\"' * 6_000,
            'pwd="' + "\\x" * 50_000, "authorization: basic " * 5_000, "x://" + "a" * 100_000,
            "bearer " + "A" * 100_000,
        ):
            with self.subTest(text=text[:16]):
                start = time.perf_counter()
                _redact_text(text)
                self.assertLess(time.perf_counter() - start, 0.5)

    def test_name_value_pairs_naming_a_secret_are_masked(self) -> None:
        result = _redact_secrets([
            {"name": "password", "value": "m"},
            {"key": "apiToken", "defaultValue": "t"},
            {"name": "host", "value": "ldap.example.com"},
            {"field": "key", "value": "k"},
            {"label": "bindPassword", "value": "p"},
        ])
        self.assertEqual(result[0]["value"], REDACTED)
        self.assertEqual(result[1]["defaultValue"], REDACTED)
        # The field naming the secret stays readable.
        self.assertEqual(result[1]["key"], "apiToken")
        self.assertEqual(result[2]["value"], "ldap.example.com")
        self.assertEqual(result[3]["value"], REDACTED)
        self.assertEqual(result[4]["value"], REDACTED)

    def test_every_import_tool_redacts_its_response(self) -> None:
        response = {
            "status": "FAILED",
            "resultMessage": "bind failed for ldap://svc:hunter2@ldap.example.com",
            "resultData": {"password": "hunter2"},
            "values": [{"id": "1", "clientSecret": "hunter2"}],
        }
        for name, call in _calls(_toolset(RecordingClient(response))).items():
            with self.subTest(tool=name):
                self.assertNotIn("hunter2", repr(call()))


class ImportSourceTests(unittest.TestCase):
    def test_only_safe_fields_are_returned(self) -> None:
        client = RecordingClient({
            "id": SOURCE,
            "name": "LDAP directory",
            "objectSchemaId": "5",
            "importSourceModuleKey": "ldap-import",
            "tokenGenerated": True,
            "isImportSourceSchedulingEnabled": True,
            "scheduledImportDetails": {"runFrequency": "HOURLY", "nextScheduledTime": "2026-09-29T14:00Z"},
            "importExecutionType": "SCHEDULED",
            "importStatus": {
                "configurationAuiLozenge": "aui-lozenge-success",
                "configurationStatusType": "ENABLED",
                "validationStatusType": "VALID",
            },
            "dateFormat": "dd/MM/yyyy",
            "importSpecificConfiguration": {"host": "ldap.example.com", "bindDn": "cn=svc", "filter": "(o=x)"},
        })
        result = _toolset(client).get_import_source(SOURCE)
        self.assertEqual(result["name"], "LDAP directory")
        self.assertEqual(result["importSourceModuleKey"], "ldap-import")
        self.assertIs(result["tokenGenerated"], True)
        self.assertEqual(result["scheduledImportDetails"]["runFrequency"], "HOURLY")
        self.assertNotIn("importSpecificConfiguration", result)
        self.assertEqual(result["importSpecificConfigurationKeys"], ["bindDn", "filter", "host"])
        self.assertEqual(result["_omitted_fields"], ["dateFormat"])
        self.assertEqual(result["importExecutionType"], "SCHEDULED")
        self.assertEqual(result["importStatus"], {"configuration": "ENABLED", "validation": "VALID"})
        self.assertNotIn("cn=svc", repr(result))

    def test_non_dict_response_is_passed_through_redacted(self) -> None:
        result = _toolset(RecordingClient(["ldap://svc:hunter2@h"])).get_import_source(SOURCE)
        self.assertEqual(result, [f"ldap://{REDACTED}@h"])


class ListImportSourcesTests(unittest.TestCase):
    # Shape of GET /importsource/objectschema/{id}, as returned by a live Assets workspace.
    SOURCES = [
        {
            "workspaceId": "w", "globalId": "w:1", "id": "7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f",
            "collectionId": "c", "name": "Device inventory - Laptop", "objectSchemaId": "5",
            "importSourceModuleKey": "rlabs-import-type-dm-csv", "importExecutionType": "SCHEDULED",
            "isImportSourceSchedulingEnabled": True, "updated": "2026-09-28T20:30:14Z",
            "importStatus": {"configurationStatusType": "ENABLED", "validationStatusType": "VALID",
                             "validationAuiLozenge": "aui-lozenge-success"},
            "importSpecificConfiguration": {"password": "hunter2", "filename": "devices.csv"},
            "importSourceOTEntries": [{"objectTypeId": "1081"}],
            "scheduledImportDetails": {"runFrequency": "DAILY"},
        },
        {
            "id": "9e8d7c6b-5a49-4382-a716-0f1e2d3c4b5a", "name": "Device inventory CSV - Laptop",
            "importSourceModuleKey": "rlabs-import-type-csv", "importExecutionType": "MANUAL",
            "isImportSourceSchedulingEnabled": True,
        },
    ]

    def test_sources_are_reduced_to_what_picks_one(self) -> None:
        result = _toolset(RecordingClient(self.SOURCES)).list_import_sources("5")
        self.assertEqual(
            result[0],
            {
                "id": "7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f",
                "name": "Device inventory - Laptop",
                "importSourceModuleKey": "rlabs-import-type-dm-csv",
                "importExecutionType": "SCHEDULED",
                "isImportSourceSchedulingEnabled": True,
                "updated": "2026-09-28T20:30:14Z",
                "scheduledImportDetails": {"runFrequency": "DAILY"},
                "importStatus": {"configuration": "ENABLED", "validation": "VALID"},
            },
        )
        self.assertEqual(result[1]["importExecutionType"], "MANUAL")
        self.assertNotIn("hunter2", repr(result))
        self.assertNotIn("devices.csv", repr(result))

    def test_unexpected_shape_is_passed_through_redacted(self) -> None:
        result = _toolset(RecordingClient({"values": [{"password": "hunter2"}]})).list_import_sources("5")
        self.assertEqual(result, {"values": [{"password": REDACTED}]})


class LastImportExecutionTests(unittest.TestCase):
    # Shape of GET /importsource/{id}/executions/status, as returned by a live Assets workspace.
    RESPONSE = {
        "status": "DONE",
        "progressResult": {
            "type": "IMPORT",
            "started": "2026-09-18T18:17:25.835+00:00",
            "ended": "2026-09-18T18:17:38.584+00:00",
            "result": "OK",
            "status": "FINISHED",
            "infoMessage": "No data to import",
            "jobId": EXECUTION,
            "importSourceId": SOURCE,
            "objectTypeResultMap": {
                "1081": {"objectTypeName": "Laptop", "objectsUpdated": 290, "objectsCreated": 561, "errorCount": 0},
            },
        },
        "executionId": EXECUTION,
    }

    def test_last_execution_is_returned_as_sent(self) -> None:
        result = _toolset(RecordingClient(self.RESPONSE)).get_last_import_execution(SOURCE)
        self.assertEqual(result, self.RESPONSE)


class ImportSizeTests(unittest.TestCase):
    def test_every_import_tool_enforces_the_size_limit(self) -> None:
        big = {"status": "DONE", "description": "d" * 5000, "resultMessage": "x" * 5000, "values": [{"id": "1", "note": "y" * 5000}]}
        for name, call in _calls(_toolset(RecordingClient(big), max_result_bytes=1024)).items():
            with self.subTest(tool=name):
                with self.assertRaisesRegex(ValueError, "JSM_MAX_RESULT_BYTES"):
                    call()

    def test_limit_is_inclusive(self) -> None:
        response = {"status": "IDLE", "detail": "z" * 200}
        size = _json_size(response)
        self.assertEqual(
            _toolset(RecordingClient(response), max_result_bytes=size).get_import_config_status(SOURCE),
            response,
        )
        with self.assertRaisesRegex(ValueError, "JSM_MAX_RESULT_BYTES"):
            _toolset(RecordingClient(response), max_result_bytes=size - 1).get_import_config_status(SOURCE)

    def test_oversized_raw_response_is_refused_before_redaction(self) -> None:
        client = RecordingClient({"status": "DONE", "resultMessage": "x" * 5000})
        with patch("jsm_asset_mcp.tools._redact_secrets", side_effect=AssertionError("redacted")) as redact:
            with self.assertRaisesRegex(ValueError, "JSM_MAX_RESULT_BYTES"):
                _toolset(client, max_result_bytes=1024).get_import_progress(SOURCE)
        redact.assert_not_called()

    def test_limit_applies_to_the_redacted_response(self) -> None:
        # Masking "x" makes the payload longer than what the API sent.
        response = {"status": "IDLE", "pwd": "x"}
        toolset = _toolset(RecordingClient(response), max_result_bytes=_json_size(response))
        with self.assertRaisesRegex(ValueError, "redacted import configuration status"):
            toolset.get_import_config_status(SOURCE)

    def test_size_error_suggests_a_way_out(self) -> None:
        client = RecordingClient({"status": "DONE", "note": "n" * 2000})
        with self.assertRaisesRegex(ValueError, "Use get_import_config_status"):
            _toolset(client, max_result_bytes=500).get_last_import_execution(SOURCE)


class UserRecordTests(unittest.TestCase):
    def test_nested_user_record_is_reduced_to_its_display_name(self) -> None:
        # Shape of GET /progress/category/imports/{id}, as returned by a live Assets workspace.
        client = RecordingClient({
            "status": "FINISHED",
            "resultData": {
                "executedType": "MANUAL",
                "executedAsUser": {
                    "avatarUrl": "https://avatar.example/x", "displayName": "Alex Doe",
                    "emailAddress": "m@example.com", "key": "557058:abc", "name": "Alex Doe",
                },
            },
            "actor": "557058:abc",
        })
        result = _toolset(client).get_import_progress(SOURCE)
        self.assertEqual(result["resultData"]["executedAsUser"], "Alex Doe")
        self.assertEqual(result["resultData"]["executedType"], "MANUAL")
        self.assertNotIn("m@example.com", repr(result))
        # A bare account ID is kept: it identifies the runner without contact data.
        self.assertEqual(result["actor"], "557058:abc")

    def test_user_records_are_reduced_in_every_tool(self) -> None:
        user = {"displayName": "Alex Doe", "emailAddress": "m@example.com"}
        response = {"status": "DONE", "description": "x", "actor": user, "values": [{"owner": user}]}
        for name, call in _calls(_toolset(RecordingClient(response))).items():
            with self.subTest(tool=name):
                self.assertNotIn("m@example.com", repr(call()))

    def test_user_record_falls_back_to_name(self) -> None:
        client = RecordingClient({"actor": {"name": "svc-import", "emailAddress": "m@example.com"}})
        self.assertEqual(_toolset(client).get_import_progress(SOURCE)["actor"], "svc-import")

    def test_user_record_without_a_name_is_dropped_entirely(self) -> None:
        client = RecordingClient({"actor": {"emailAddress": "m@example.com", "accountId": "557058:abc"}})
        result = _toolset(client).get_import_progress(SOURCE)
        self.assertIsNone(result["actor"])
        self.assertNotIn("m@example.com", repr(result))
        self.assertNotIn("557058", repr(result))


class FakeAssetsClient:
    """Stands in for AssetsClient inside a real FastMCP server."""

    response: object = {"status": "IDLE"}
    gets: list = []

    def __init__(self, settings: Settings | None = None) -> None:
        pass

    def get(self, path: str, params=None) -> object:
        FakeAssetsClient.gets.append(path)
        return FakeAssetsClient.response

    def close(self) -> None:
        pass


class ImportToolServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeAssetsClient.gets = []
        FakeAssetsClient.response = {"status": "IDLE"}

    async def _server(self, **settings: object):
        with patch("jsm_asset_mcp.server.AssetsClient", FakeAssetsClient):
            return create_server(Settings(schema_prefetch=False, **settings))

    async def test_import_tools_are_registered_in_both_modes(self) -> None:
        for read_only in (False, True):
            with self.subTest(read_only=read_only):
                server = await self._server(read_only=read_only)
                names = {tool.name for tool in await server.list_tools()}
                self.assertLessEqual(IMPORT_TOOLS, names)

    async def test_unsafe_import_id_is_refused_before_any_call(self) -> None:
        server = await self._server(read_only=True)
        with self.assertRaisesRegex(ToolError, "import_source_id"):
            await server.call_tool("get_import_execution_status", {"import_source_id": "../x", "execution_id": "e"})
        with self.assertRaisesRegex(ToolError, "schema_id"):
            await server.call_tool("list_import_sources", {"schema_id": "5/x"})
        self.assertEqual(FakeAssetsClient.gets, [])

    async def test_redaction_and_user_reduction_survive_serialisation(self) -> None:
        FakeAssetsClient.response = {
            "status": "FINISHED",
            "resultMessage": "bind failed for ldap://svc:hunter2@ldap.example.com",
            "resultData": {"executedAsUser": {"displayName": "Alex Doe", "emailAddress": "a@example.com"}},
        }
        server = await self._server(read_only=True)
        result = await server.call_tool("get_import_progress", {"import_source_id": SOURCE})
        text = result[0].text
        self.assertNotIn("hunter2", text)
        self.assertNotIn("a@example.com", text)
        self.assertEqual(json.loads(text)["resultData"]["executedAsUser"], "Alex Doe")


class ImportRoutePathTests(unittest.TestCase):
    def test_client_accepts_import_routes(self) -> None:
        settings = Settings(
            jira_email="me@example.com",
            jira_api_token="t",
            jira_cloud_id="11111111-2222-3333-4444-555555555555",
            jira_workspace_id="66666666-7777-8888-9999-aaaaaaaaaaaa",
        )
        client = AssetsClient(settings)
        client._http = Mock()
        client._http.get.return_value = Mock(status_code=200, json=Mock(return_value={}))
        for path in (
            "/importsource/objectschema/5",
            f"/importsource/{SOURCE}",
            f"/importsource/{SOURCE}/configstatus",
            f"/importsource/{SOURCE}/executions/status",
            f"/importsource/{SOURCE}/executions/{EXECUTION}/status",
            f"/progress/category/imports/{SOURCE}",
        ):
            with self.subTest(path=path):
                client.get(path)
                self.assertTrue(client._http.get.call_args.args[0].endswith(f"/v1{path}"))


class RedactionThroughServerTests(unittest.IsolatedAsyncioTestCase):
    """Serialised connector credentials, from the HTTP response to the host."""

    BASIC = base64.b64encode(b"svc-import:hunter2-basic").decode()
    # JWT-shaped, longer than every former scan bound.
    TOKEN = "eyJhbGciOiJSUzI1NiJ9." + "eyJzdWIiOiJzdmMifQ" * 100 + ".hunter2sig"
    MESSAGES = (
        f'request failed with headers {{"Authorization": "Basic {BASIC}"}}',
        f"request failed with headers {{'Authorization': 'Basic {BASIC}'}}",
        f'token refresh returned {{"access_token": "{TOKEN}"}}',
        f"token refresh returned {{'access_token': '{TOKEN}'}}",
        f"token refresh returned access_token={TOKEN}",
    )
    TOOLS = {
        "get_import_progress": {"import_source_id": SOURCE},
        "get_last_import_execution": {"import_source_id": SOURCE},
        "get_import_execution_status": {"import_source_id": SOURCE, "execution_id": EXECUTION},
        "get_import_source": {"import_source_id": SOURCE},
    }

    def _server(self, message: str):
        def respond(request: httpx.Request) -> httpx.Response:
            body = {"id": SOURCE, "status": "FAILED", "resultMessage": message, "description": message}
            return httpx.Response(200, json=body)

        real_client = httpx.Client

        def mocked_client(**kwargs: object) -> httpx.Client:
            return real_client(transport=httpx.MockTransport(respond), **kwargs)

        settings = Settings(
            jira_domain="example.atlassian.net",
            jira_email="user@example.com",
            jira_api_token="api-token",
            jira_cloud_id="0b6ee1c8-2d4c-4a5e-9f1a-3c7d8e9f0a10",
            jira_workspace_id="0b6ee1c8-2d4c-4a5e-9f1a-3c7d8e9f0a11",
            read_only=True,
            schema_prefetch=False,
        )
        with patch.object(client_module.httpx, "Client", mocked_client):
            return create_server(settings)

    async def test_import_tools_mask_serialised_credentials(self) -> None:
        for message in self.MESSAGES:
            server = self._server(message)
            for tool, arguments in self.TOOLS.items():
                with self.subTest(tool=tool, message=message[:48]):
                    result = await server.call_tool(tool, arguments)
                    text = result[0].text
                    self.assertLess(len(text.encode()), Settings().max_result_bytes)
                    self.assertNotIn(self.BASIC, text)
                    self.assertNotIn("hunter2", text)
                    self.assertNotIn("eyJzdWIiOiJzdmMifQ", text)
                    self.assertIn(REDACTED, json.loads(text)["description"])


if __name__ == "__main__":
    unittest.main()
