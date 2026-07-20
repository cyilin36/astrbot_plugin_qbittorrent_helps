from __future__ import annotations

import json
import unittest
from pathlib import Path

import aiohttp

from client import QBittorrentClient, QBittorrentVersionError
from service import (
    AuthorizationError,
    QBittorrentError,
    QBittorrentService,
    SelectionError,
    format_preview,
    format_search_results,
)


class FakeClient:
    def __init__(self):
        self.metadata = {
            "torrent_id": "a" * 40,
            "info": {
                "name": "Example",
                "length": 6000,
                "files": [
                    {"path": "Example/a.mkv", "length": 1000},
                    {"path": "Example/b.srt", "length": 2000},
                    {"path": "Example/c.txt", "length": 3000},
                ],
            },
        }
        self.torrents = []
        self.add_calls = []
        self.delete_calls = []
        self.closed = False

    async def fetch_metadata(self, magnet):
        self.last_magnet = magnet
        return self.metadata

    async def list_torrents(self):
        return list(self.torrents)

    async def add_torrent(self, magnet, priorities=None):
        self.add_calls.append((magnet, priorities))
        return {
            "success_count": 1,
            "pending_count": 0,
            "failure_count": 0,
            "added_torrent_ids": ["a" * 40],
        }

    async def delete_torrent(self, torrent_hash, delete_files):
        self.delete_calls.append((torrent_hash, delete_files))

    async def close(self):
        self.closed = True


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeClient()
        self.service = QBittorrentService(self.client, ["10001"])

    def test_uid_authorization_is_deny_by_default(self):
        denied = QBittorrentService(self.client, [])
        with self.assertRaises(AuthorizationError):
            denied.assert_authorized("10001")
        self.assertEqual(self.service.assert_authorized(10001), "10001")
        with self.assertRaises(AuthorizationError):
            self.service.assert_authorized("10002")

    def test_uid_string_configuration_is_normalized(self):
        service = QBittorrentService(self.client, "10001, 10002\n10003")
        self.assertEqual(service.assert_authorized("10002"), "10002")

    def test_selection_parser(self):
        self.assertEqual(self.service.parse_selection("1,3-5,3", 5), [1, 3, 4, 5])
        self.assertEqual(self.service.parse_selection([3, 1, 3], 3), [1, 3])
        self.assertIsNone(self.service.parse_selection("", 3))
        with self.assertRaises(SelectionError):
            self.service.parse_selection("3-1", 3)
        with self.assertRaises(SelectionError):
            self.service.parse_selection("1,a", 3)
        with self.assertRaises(SelectionError):
            self.service.parse_selection("4", 3)

    async def test_preview_formats_names_and_selected_add_priorities(self):
        preview = await self.service.preview(
            "magnet:?xt=urn:btih:abc", "10001", "session-a"
        )
        text = format_preview(preview)
        self.assertIn("名称: Example", text)
        self.assertIn("1. Example/a.mkv", text)
        self.assertIn("3. Example/c.txt", text)

        await self.service.add(preview.token, "1,3", "10001", "session-a")
        self.assertEqual(
            self.client.add_calls,
            [("magnet:?xt=urn:btih:abc", [1, 0, 1])],
        )
        with self.assertRaises(QBittorrentError):
            self.service.get_preview(preview.token, "10001", "session-a")

    async def test_direct_magnet_add_downloads_every_file(self):
        await self.service.add("magnet:?xt=urn:btih:abc", "", "10001", "session-a")
        self.assertEqual(
            self.client.add_calls,
            [("magnet:?xt=urn:btih:abc", None)],
        )

    async def test_preview_token_is_bound_to_uid_and_session(self):
        preview = await self.service.preview(
            "magnet:?xt=urn:btih:abc", "10001", "session-a"
        )
        with self.assertRaises(AuthorizationError):
            self.service.get_preview(preview.token, "10002", "session-a")
        with self.assertRaises(AuthorizationError):
            self.service.get_preview(preview.token, "10001", "session-b")

    async def test_preview_token_expires(self):
        now = [10.0]
        service = QBittorrentService(self.client, ["10001"], clock=lambda: now[0])
        preview = await service.preview("magnet:?xt=urn:btih:abc", "10001", "session-a")
        now[0] += service.PREVIEW_TTL_SECONDS
        with self.assertRaises(QBittorrentError):
            service.get_preview(preview.token, "10001", "session-a")

    async def test_search_filters_and_limits_results(self):
        self.client.torrents = [
            {"name": "Ubuntu ISO", "hash": "a" * 40, "state": "downloading"},
            {"name": "Movie", "hash": "b" * 40, "category": "Linux"},
            {"name": "Book", "hash": "c" * 40, "state": "stoppedDL"},
        ]
        results = await self.service.search("linux", 1)
        self.assertEqual([item["name"] for item in results], ["Movie"])
        self.assertIn("Hash:", format_search_results(results))

    async def test_hash_resolution_rejects_ambiguous_prefix(self):
        self.client.torrents = [
            {"name": "One", "hash": "abc1" + "0" * 36},
            {"name": "Two", "hash": "abc2" + "0" * 36},
        ]
        with self.assertRaisesRegex(QBittorrentError, "不唯一"):
            await self.service.resolve_hash("abc")
        torrent_hash, name = await self.service.resolve_hash("abc1")
        self.assertEqual(name, "One")
        self.assertTrue(torrent_hash.startswith("abc1"))

    async def test_delete_file_setting_requires_confirmation(self):
        self.client.torrents = [{"name": "One", "hash": "a" * 40}]
        protected = QBittorrentService(self.client, ["10001"], delete_files=True)
        with self.assertRaisesRegex(QBittorrentError, "confirm=true"):
            await protected.delete("aaaa")
        await protected.delete("aaaa", confirmed=True)
        self.assertEqual(self.client.delete_calls, [("a" * 40, True)])

    async def test_close_delegates_to_client(self):
        await self.service.close()
        self.assertTrue(self.client.closed)


class ClientAndSchemaTests(unittest.TestCase):
    def test_base_url_preserves_proxy_subpath(self):
        client = QBittorrentClient("https://example.com/qbt/", "admin", "secret")
        self.assertEqual(
            client._url("torrents/info"),
            "https://example.com/qbt/api/v2/torrents/info",
        )

    def test_invalid_base_url_fails_when_used_not_when_config_is_loaded(self):
        client = QBittorrentClient("localhost:8080", "admin", "secret")
        with self.assertRaisesRegex(QBittorrentError, "http://"):
            client._url("torrents/info")

    def test_version_parsing_and_minimum(self):
        self.assertEqual(QBittorrentClient._parse_version("2.11.9"), (2, 11, 9))
        self.assertLess((2, 11, 8), QBittorrentClient.MIN_API_VERSION)
        with self.assertRaises(QBittorrentVersionError):
            QBittorrentClient._parse_version("unknown")

    def test_config_schema_defaults(self):
        schema_path = Path(__file__).parents[1] / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertFalse(schema["delete_files"]["default"])
        self.assertEqual(schema["default_search_limit"]["default"], 10)
        self.assertEqual(schema["authorized_uids"]["default"], [])
        self.assertEqual(schema["authorized_uids"]["type"], "list")


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        if isinstance(self.payload, str):
            raise ValueError
        return self.payload

    async def text(self):
        return str(self.payload)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(*response)


class ClientRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_and_authentication_retry(self):
        session = FakeSession(
            [
                (200, "Ok."),
                (403, "Forbidden"),
                (200, "Ok."),
                (200, []),
            ]
        )
        client = QBittorrentClient(
            "http://localhost:8080", "admin", "password", session=session
        )
        status, payload = await client.request("GET", "torrents/info")
        self.assertEqual((status, payload), (200, []))
        self.assertEqual(
            [call[0] for call in session.calls], ["POST", "GET", "POST", "GET"]
        )
        self.assertEqual(session.calls[0][2]["data"]["username"], "admin")

    async def test_metadata_polls_202_until_200(self):
        session = FakeSession(
            [
                (200, "Ok."),
                (200, "2.16.0"),
                (202, {"torrent_id": "abc"}),
                (200, {"info": {"name": "Example", "files": []}}),
            ]
        )
        sleeps = []

        async def sleep(seconds):
            sleeps.append(seconds)

        client = QBittorrentClient(
            "http://localhost:8080", "admin", "password", session=session, sleep=sleep
        )
        result = await client.fetch_metadata(
            "magnet:?xt=urn:btih:abc", poll_interval=0.25
        )
        self.assertEqual(result["info"]["name"], "Example")
        self.assertEqual(sleeps, [0.25])

    async def test_network_errors_are_user_facing(self):
        session = FakeSession([aiohttp.ClientConnectionError("offline")])
        client = QBittorrentClient(
            "http://localhost:8080", "admin", "password", session=session
        )
        with self.assertRaisesRegex(QBittorrentError, "无法连接"):
            await client.login()


if __name__ == "__main__":
    unittest.main()
