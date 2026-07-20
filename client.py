"""Small asynchronous client for the qBittorrent Web API v2."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable

import aiohttp


class QBittorrentError(RuntimeError):
    """Base exception for user-facing qBittorrent failures."""


class QBittorrentHTTPError(QBittorrentError):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"qBittorrent API returned HTTP {status}: {message}")


class QBittorrentVersionError(QBittorrentError):
    pass


class QBittorrentClient:
    """Authenticated, retrying qBittorrent API client.

    ``session`` and ``sleep`` are injectable so the API behavior can be tested
    without a live qBittorrent process.
    """

    MIN_API_VERSION = (2, 11, 9)

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        session: aiohttp.ClientSession | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        timeout: float = 15.0,
    ) -> None:
        base_url = (base_url or "").strip().rstrip("/")
        self.base_url = base_url
        self._base_url_valid = bool(
            re.match(r"^https?://[^\s]+$", base_url, re.IGNORECASE)
        )
        self.username = username
        self.password = password
        self._session = session
        self._owns_session = session is None
        self._sleep = sleep
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._login_lock = asyncio.Lock()
        self._logged_in = False
        self._version_checked = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
            self._owns_session = True
        return self._session

    def _url(self, endpoint: str) -> str:
        if not self._base_url_valid:
            raise QBittorrentError("qBittorrent 地址必须以 http:// 或 https:// 开头")
        return f"{self.base_url}/api/v2/{endpoint.lstrip('/')}"

    @staticmethod
    async def _response_payload(response: Any) -> Any:
        try:
            return await response.json(content_type=None)
        except (ValueError, TypeError, aiohttp.ContentTypeError):
            try:
                return await response.text()
            except Exception:
                return ""

    async def _raw_request(
        self, method: str, endpoint: str, **kwargs: Any
    ) -> tuple[int, Any]:
        session = await self._get_session()
        try:
            async with session.request(
                method, self._url(endpoint), **kwargs
            ) as response:
                payload = await self._response_payload(response)
                return response.status, payload
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise QBittorrentError(f"无法连接 qBittorrent: {exc}") from exc

    async def login(self) -> None:
        async with self._login_lock:
            if self._logged_in:
                return
            status, payload = await self._raw_request(
                "POST",
                "auth/login",
                data={"username": self.username, "password": self.password},
            )
            if (
                status < 200
                or status >= 300
                or (isinstance(payload, str) and payload.strip() not in {"", "Ok."})
            ):
                message = str(payload).strip() or "用户名或密码错误"
                raise QBittorrentHTTPError(status, message)
            self._logged_in = True

    async def request(
        self, method: str, endpoint: str, **kwargs: Any
    ) -> tuple[int, Any]:
        await self.login()
        status, payload = await self._raw_request(method, endpoint, **kwargs)
        if status in (401, 403):
            self._logged_in = False
            await self.login()
            status, payload = await self._raw_request(method, endpoint, **kwargs)
        if status < 200 or status >= 300:
            message = (
                payload.get("message")
                if isinstance(payload, dict)
                else str(payload).strip()
            )
            raise QBittorrentHTTPError(status, message or "未知错误")
        return status, payload

    @staticmethod
    def _parse_version(value: Any) -> tuple[int, ...]:
        match = re.search(r"(\d+(?:\.\d+)+)", str(value))
        if not match:
            raise QBittorrentVersionError(f"无法识别 qBittorrent Web API 版本: {value}")
        return tuple(int(part) for part in match.group(1).split("."))

    async def ensure_api_version(self) -> str:
        _, payload = await self.request("GET", "app/webapiVersion")
        version = str(payload).strip().strip('"')
        if self._parse_version(version) < self.MIN_API_VERSION:
            raise QBittorrentVersionError(
                f"qBittorrent Web API {version} 太旧，需要至少 2.11.9"
            )
        self._version_checked = True
        return version

    async def prepare(self) -> None:
        if not self._version_checked:
            await self.ensure_api_version()

    async def list_torrents(self) -> list[dict[str, Any]]:
        await self.prepare()
        _, payload = await self.request(
            "GET",
            "torrents/info",
            params={"limit": "0", "sort": "added_on", "reverse": "true"},
        )
        if not isinstance(payload, list):
            raise QBittorrentError("qBittorrent 返回的条目列表格式无效")
        return [item for item in payload if isinstance(item, dict)]

    async def fetch_metadata(
        self,
        source: str,
        *,
        timeout: float = 45.0,
        poll_interval: float = 1.0,
    ) -> dict[str, Any]:
        await self.prepare()
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            status, payload = await self.request(
                "POST", "torrents/fetchMetadata", data={"source": source}
            )
            if (
                status == 200
                and isinstance(payload, dict)
                and isinstance(payload.get("info"), dict)
            ):
                return payload
            if asyncio.get_running_loop().time() >= deadline:
                raise QBittorrentError("qBittorrent 获取磁力链元数据超时，请稍后重试")
            await self._sleep(poll_interval)

    async def add_torrent(
        self,
        magnet: str,
        file_priorities: list[int] | None = None,
    ) -> dict[str, Any] | str:
        await self.prepare()
        data: dict[str, str] = {"urls": magnet}
        if file_priorities is not None:
            data["filePriorities"] = ",".join(
                str(priority) for priority in file_priorities
            )
        _, payload = await self.request("POST", "torrents/add", data=data)
        return payload

    async def delete_torrent(self, torrent_hash: str, delete_files: bool) -> None:
        await self.prepare()
        await self.request(
            "POST",
            "torrents/delete",
            data={
                "hashes": torrent_hash,
                "deleteFiles": "true" if delete_files else "false",
            },
        )

    async def close(self) -> None:
        if (
            self._session is not None
            and self._owns_session
            and not self._session.closed
        ):
            await self._session.close()
