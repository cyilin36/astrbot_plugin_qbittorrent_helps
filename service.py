"""Business rules shared by AstrBot commands and the AI tool."""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Iterable

try:
    from .client import QBittorrentClient, QBittorrentError
except ImportError:  # Allow direct loading in lightweight test runners.
    from client import QBittorrentClient, QBittorrentError


class AuthorizationError(QBittorrentError):
    pass


class SelectionError(QBittorrentError):
    pass


@dataclass(slots=True)
class PreviewFile:
    index: int
    path: str
    size: int


@dataclass(slots=True)
class Preview:
    token: str
    owner_uid: str
    owner_session: str
    magnet: str
    name: str
    total_size: int
    torrent_hash: str
    files: list[PreviewFile]
    expires_at: float


class QBittorrentService:
    PREVIEW_TTL_SECONDS = 15 * 60
    MAX_SEARCH_LIMIT = 100

    def __init__(
        self,
        client: QBittorrentClient,
        authorized_uids: Iterable[Any],
        *,
        delete_files: bool = False,
        default_search_limit: int = 10,
        clock: Any = time.monotonic,
    ) -> None:
        self.client = client
        if isinstance(authorized_uids, str):
            authorized_uids = re.split(r"[,\n\s]+", authorized_uids)
        self.authorized_uids = {
            str(uid).strip() for uid in (authorized_uids or []) if str(uid).strip()
        }
        self.delete_files = bool(delete_files)
        self.default_search_limit = self._normalize_limit(default_search_limit)
        self._clock = clock
        self._previews: dict[str, Preview] = {}

    def assert_authorized(self, uid: Any) -> str:
        normalized = str(uid).strip()
        if not self.authorized_uids or normalized not in self.authorized_uids:
            raise AuthorizationError(
                "当前 UID 未被授权。请先用 /sid 获取 UID，并在插件 WebUI 配置中加入 authorized_uids。"
            )
        return normalized

    @classmethod
    def _normalize_limit(cls, value: Any) -> int:
        try:
            limit = int(value)
        except (TypeError, ValueError):
            limit = 10
        return min(cls.MAX_SEARCH_LIMIT, max(1, limit))

    async def search(
        self, query: str = "", limit: int | None = None
    ) -> list[dict[str, Any]]:
        torrents = await self.client.list_torrents()
        needle = (query or "").strip().casefold()
        if needle:
            fields = ("name", "hash", "category", "state", "tags")
            torrents = [
                torrent
                for torrent in torrents
                if any(
                    needle in str(torrent.get(field, "")).casefold() for field in fields
                )
            ]
        count = (
            self.default_search_limit if limit is None else self._normalize_limit(limit)
        )
        return torrents[:count]

    def _purge_expired_previews(self) -> None:
        now = self._clock()
        expired = [
            token
            for token, preview in self._previews.items()
            if preview.expires_at <= now
        ]
        for token in expired:
            self._previews.pop(token, None)

    @staticmethod
    def _metadata_hash(metadata: dict[str, Any]) -> str:
        return str(
            metadata.get("torrent_id")
            or metadata.get("hash")
            or metadata.get("infohash_v1")
            or metadata.get("infohash_v2")
            or ""
        )

    async def preview(self, magnet: str, uid: str, session: str) -> Preview:
        if not magnet.lower().startswith("magnet:?"):
            raise QBittorrentError("只支持 magnet:? 开头的磁力链")
        metadata = await self.client.fetch_metadata(magnet)
        info = metadata.get("info")
        if not isinstance(info, dict):
            raise QBittorrentError("qBittorrent 返回的磁力链元数据中缺少 info")
        raw_files = info.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise QBittorrentError("磁力链元数据中没有可下载文件")

        files: list[PreviewFile] = []
        for index, item in enumerate(raw_files, start=1):
            if not isinstance(item, dict):
                raise QBittorrentError("磁力链文件列表格式无效")
            files.append(
                PreviewFile(
                    index=index,
                    path=str(item.get("path", f"文件 {index}")),
                    size=max(0, int(item.get("length", 0))),
                )
            )

        self._purge_expired_previews()
        token = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8]
        while token in self._previews:
            token = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8]
        preview = Preview(
            token=token,
            owner_uid=str(uid),
            owner_session=str(session),
            magnet=magnet,
            name=str(info.get("name", "未命名种子")),
            total_size=max(
                0, int(info.get("length", sum(file.size for file in files)))
            ),
            torrent_hash=self._metadata_hash(metadata),
            files=files,
            expires_at=self._clock() + self.PREVIEW_TTL_SECONDS,
        )
        self._previews[token] = preview
        return preview

    def get_preview(self, token: str, uid: str, session: str) -> Preview:
        self._purge_expired_previews()
        preview = self._previews.get(token)
        if preview is None:
            raise QBittorrentError("预览令牌不存在或已过期，请重新执行 preview")
        if preview.owner_uid != str(uid) or preview.owner_session != str(session):
            raise AuthorizationError("该预览令牌不属于当前用户或会话")
        return preview

    @staticmethod
    def parse_selection(
        value: str | Iterable[Any] | None, file_count: int
    ) -> list[int] | None:
        if value is None or value == "":
            return None
        selected: set[int] = set()
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            for part in text.split(","):
                part = part.strip()
                if not part:
                    raise SelectionError("文件选择格式无效，请使用 1,3-5")
                match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
                if not match:
                    raise SelectionError("文件选择格式无效，请使用 1,3-5")
                start = int(match.group(1))
                end = int(match.group(2) or start)
                if end < start:
                    raise SelectionError(f"文件范围 {part} 的结束编号小于开始编号")
                selected.update(range(start, end + 1))
        else:
            try:
                selected.update(int(index) for index in value)
            except (TypeError, ValueError) as exc:
                raise SelectionError("文件编号必须是整数") from exc

        if not selected:
            raise SelectionError("至少选择一个文件")
        invalid = sorted(index for index in selected if index < 1 or index > file_count)
        if invalid:
            raise SelectionError(
                f"文件编号超出范围: {', '.join(str(index) for index in invalid)}；有效范围为 1-{file_count}"
            )
        return sorted(selected)

    async def add(
        self,
        source: str,
        selection: str | Iterable[Any] | None,
        uid: str,
        session: str,
    ) -> dict[str, Any] | str:
        preview: Preview | None = None
        if source.lower().startswith("magnet:?"):
            if selection not in (None, "", []):
                preview = await self.preview(source, uid, session)
            magnet = source
        else:
            preview = self.get_preview(source, uid, session)
            magnet = preview.magnet

        priorities: list[int] | None = None
        if preview is not None:
            selected = self.parse_selection(selection, len(preview.files))
            if selected is not None:
                selected_set = set(selected)
                priorities = [
                    1 if index in selected_set else 0
                    for index in range(1, len(preview.files) + 1)
                ]

        result = await self.client.add_torrent(magnet, priorities)
        if preview is not None:
            self._previews.pop(preview.token, None)
        return result

    async def resolve_hash(self, hash_or_prefix: str) -> tuple[str, str]:
        candidate = hash_or_prefix.strip().casefold()
        if not candidate or not re.fullmatch(r"[0-9a-f]+", candidate):
            raise QBittorrentError("hash 必须是十六进制字符串")
        torrents = await self.client.list_torrents()
        matches = [
            torrent
            for torrent in torrents
            if str(torrent.get("hash", "")).casefold().startswith(candidate)
        ]
        if not matches:
            raise QBittorrentError("没有找到匹配该 hash 的条目")
        if len(matches) > 1:
            options = ", ".join(
                f"{item.get('hash', '')[:12]} ({item.get('name', '未命名')})"
                for item in matches[:5]
            )
            raise QBittorrentError(f"hash 前缀不唯一，请提供更多字符。候选: {options}")
        torrent = matches[0]
        return str(torrent.get("hash", "")), str(torrent.get("name", "未命名"))

    async def delete(
        self, hash_or_prefix: str, *, confirmed: bool = False
    ) -> tuple[str, str]:
        if self.delete_files and not confirmed:
            raise QBittorrentError(
                "当前配置会同时删除已下载文件。指令请追加“确认”，AI tool 请设置 confirm=true。"
            )
        torrent_hash, name = await self.resolve_hash(hash_or_prefix)
        await self.client.delete_torrent(torrent_hash, self.delete_files)
        return torrent_hash, name

    async def rename(self, hash_or_prefix: str, new_name: str) -> tuple[str, str, str]:
        normalized_name = str(new_name).strip()
        if not normalized_name:
            raise QBittorrentError("新任务名称不能为空")
        torrent_hash, old_name = await self.resolve_hash(hash_or_prefix)
        await self.client.rename_torrent(torrent_hash, normalized_name)
        return torrent_hash, old_name, normalized_name

    async def close(self) -> None:
        self._previews.clear()
        await self.client.close()


def format_size(value: Any) -> str:
    try:
        size = max(0.0, float(value))
    except (TypeError, ValueError):
        size = 0.0
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"


def format_preview(preview: Preview) -> str:
    lines = [
        f"名称: {preview.name}",
        f"总大小: {format_size(preview.total_size)}",
        f"Hash: {preview.torrent_hash or '未知'}",
        f"预览令牌: {preview.token}（15 分钟内有效）",
        "文件:",
    ]
    lines.extend(
        f"{file.index}. {file.path} ({format_size(file.size)})"
        for file in preview.files
    )
    lines.append(f"选择下载: /qbt add {preview.token} 1,3-5")
    return "\n".join(lines)


def format_search_results(torrents: list[dict[str, Any]]) -> str:
    if not torrents:
        return "未找到匹配的 qBittorrent 条目。"
    lines = [f"找到 {len(torrents)} 个条目:"]
    for index, torrent in enumerate(torrents, start=1):
        progress = max(0.0, min(1.0, float(torrent.get("progress", 0)))) * 100
        lines.extend(
            (
                f"{index}. {torrent.get('name', '未命名')}",
                f"   状态: {torrent.get('state', 'unknown')} | 进度: {progress:.1f}% | "
                f"下载: {format_size(torrent.get('dlspeed', 0))}/s | 上传: {format_size(torrent.get('upspeed', 0))}/s",
                f"   Hash: {torrent.get('hash', '')}",
            )
        )
    return "\n".join(lines)
