"""AstrBot qBittorrent management plugin entrypoint."""

from __future__ import annotations

from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr

try:
    from .client import QBittorrentClient, QBittorrentError
    from .service import QBittorrentService, format_preview, format_search_results
    from .tools import QBittorrentTool
except ImportError:  # AstrBot installations may load a plugin's main.py directly.
    from client import QBittorrentClient, QBittorrentError
    from service import QBittorrentService, format_preview, format_search_results
    from tools import QBittorrentTool


class QBittorrentPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        client = QBittorrentClient(
            str(config.get("base_url", "http://127.0.0.1:8080")),
            str(config.get("username", "admin")),
            str(config.get("password", "")),
        )
        self.service = QBittorrentService(
            client,
            config.get("authorized_uids", []),
            delete_files=bool(config.get("delete_files", False)),
            default_search_limit=config.get("default_search_limit", 10),
        )
        self.context.add_llm_tools(QBittorrentTool(service=self.service))

    @filter.command_group("qbt", alias={"qb"})
    def qbt(self):
        """管理 qBittorrent 下载任务。"""

    def _identity(self, event: AstrMessageEvent) -> tuple[str, str]:
        uid = self.service.assert_authorized(event.get_sender_id())
        return uid, event.unified_msg_origin

    @staticmethod
    def _add_result_text(result: dict[str, Any] | str) -> str:
        if not isinstance(result, dict):
            return str(result).strip() or "已向 qBittorrent 提交下载。"
        success = int(result.get("success_count", 0) or 0)
        pending = int(result.get("pending_count", 0) or 0)
        failure = int(result.get("failure_count", 0) or 0)
        ids = result.get("added_torrent_ids", [])
        lines = [f"已提交下载：成功 {success}，等待处理 {pending}，失败 {failure}。"]
        if ids:
            lines.append("Hash: " + ", ".join(str(item) for item in ids))
        return "\n".join(lines)

    async def _command_error(
        self, event: AstrMessageEvent, operation: str, exc: Exception
    ):
        if not isinstance(exc, QBittorrentError):
            logger.exception("qBittorrent %s failed", operation)
            message = "操作失败，请检查 AstrBot 日志和 qBittorrent 连接配置。"
        else:
            message = str(exc)
        return event.plain_result(message)

    @qbt.command("search")
    async def qbt_search(
        self, event: AstrMessageEvent, query: str = "", limit: int = 0
    ):
        """搜索 qBittorrent 条目。"""
        try:
            self._identity(event)
            torrents = await self.service.search(query, None if limit <= 0 else limit)
            yield event.plain_result(format_search_results(torrents))
        except Exception as exc:
            yield await self._command_error(event, "search", exc)

    @qbt.command("preview")
    async def qbt_preview(self, event: AstrMessageEvent, magnet: str):
        """预览磁力链中的文件。"""
        try:
            uid, session = self._identity(event)
            preview = await self.service.preview(magnet, uid, session)
            yield event.plain_result(format_preview(preview))
        except Exception as exc:
            yield await self._command_error(event, "preview", exc)

    @qbt.command("add")
    async def qbt_add(self, event: AstrMessageEvent, source: str, selection: str = ""):
        """添加磁力链，或按预览令牌选择文件后添加。"""
        try:
            uid, session = self._identity(event)
            result = await self.service.add(source, selection, uid, session)
            yield event.plain_result(self._add_result_text(result))
        except Exception as exc:
            yield await self._command_error(event, "add", exc)

    @qbt.command("delete")
    async def qbt_delete(
        self, event: AstrMessageEvent, torrent_hash: str, confirmation: str = ""
    ):
        """按完整 hash 或唯一 hash 前缀删除条目。"""
        try:
            self._identity(event)
            confirmed = confirmation.strip() == "确认"
            resolved_hash, name = await self.service.delete(
                torrent_hash, confirmed=confirmed
            )
            suffix = (
                "，并已删除已下载文件"
                if self.service.delete_files
                else "，已保留已下载文件"
            )
            yield event.plain_result(
                f"已删除条目：{name}{suffix}\nHash: {resolved_hash}"
            )
        except Exception as exc:
            yield await self._command_error(event, "delete", exc)

    @qbt.command("rename")
    async def qbt_rename(
        self, event: AstrMessageEvent, torrent_hash: str, new_name: GreedyStr
    ):
        """修改 qBittorrent 任务显示名称。"""
        try:
            self._identity(event)
            resolved_hash, old_name, normalized_name = await self.service.rename(
                torrent_hash, new_name
            )
            yield event.plain_result(
                f"已重命名任务：{old_name} → {normalized_name}\nHash: {resolved_hash}"
            )
        except Exception as exc:
            yield await self._command_error(event, "rename", exc)

    @qbt.command("category")
    async def qbt_category(
        self, event: AstrMessageEvent, torrent_hash: str, category: GreedyStr
    ):
        """设置或清空 qBittorrent 任务分类。"""
        try:
            self._identity(event)
            (
                resolved_hash,
                torrent_name,
                normalized_category,
            ) = await self.service.set_category(torrent_hash, category)
            display_category = normalized_category or "未分类"
            yield event.plain_result(
                f"已设置任务分类：{torrent_name} → {display_category}\nHash: {resolved_hash}"
            )
        except Exception as exc:
            yield await self._command_error(event, "category", exc)

    @qbt.command("tags")
    async def qbt_tags(
        self, event: AstrMessageEvent, torrent_hash: str, tags: GreedyStr
    ):
        """整体替换或清空 qBittorrent 任务标签。"""
        try:
            self._identity(event)
            resolved_hash, torrent_name, normalized_tags = await self.service.set_tags(
                torrent_hash, tags
            )
            display_tags = "、".join(normalized_tags) or "无"
            yield event.plain_result(
                f"已设置任务标签：{torrent_name} → {display_tags}\nHash: {resolved_hash}"
            )
        except Exception as exc:
            yield await self._command_error(event, "tags", exc)

    @qbt.command("ratio")
    async def qbt_ratio(
        self, event: AstrMessageEvent, torrent_hash: str, ratio_limit: str
    ):
        """设置任务分享率，或恢复默认/无限。"""
        try:
            self._identity(event)
            (
                resolved_hash,
                torrent_name,
                normalized_ratio,
            ) = await self.service.set_ratio(torrent_hash, ratio_limit)
            if normalized_ratio == -2:
                display_ratio = "默认"
            elif normalized_ratio == -1:
                display_ratio = "无限"
            else:
                display_ratio = f"{normalized_ratio:.2f}"
            yield event.plain_result(
                f"已设置任务分享率：{torrent_name} → {display_ratio}\nHash: {resolved_hash}"
            )
        except Exception as exc:
            yield await self._command_error(event, "ratio", exc)

    @qbt.command("upload")
    async def qbt_upload(
        self, event: AstrMessageEvent, torrent_hash: str, limit_kib: str
    ):
        """设置任务上传限速，单位为 KiB/s。"""
        try:
            self._identity(event)
            (
                resolved_hash,
                torrent_name,
                normalized_limit,
            ) = await self.service.set_upload_limit(torrent_hash, limit_kib)
            display_limit = (
                "默认（受全局限速约束）"
                if normalized_limit == 0
                else f"{normalized_limit} KiB/s"
            )
            yield event.plain_result(
                f"已设置任务上传限速：{torrent_name} → {display_limit}\nHash: {resolved_hash}"
            )
        except Exception as exc:
            yield await self._command_error(event, "upload", exc)

    @qbt.command("start")
    async def qbt_start(self, event: AstrMessageEvent, torrent_hash: str):
        """启动 qBittorrent 任务。"""
        try:
            self._identity(event)
            resolved_hash, torrent_name = await self.service.start(torrent_hash)
            yield event.plain_result(
                f"已启动任务：{torrent_name}\nHash: {resolved_hash}"
            )
        except Exception as exc:
            yield await self._command_error(event, "start", exc)

    @qbt.command("stop")
    async def qbt_stop(self, event: AstrMessageEvent, torrent_hash: str):
        """停止 qBittorrent 任务。"""
        try:
            self._identity(event)
            resolved_hash, torrent_name = await self.service.stop(torrent_hash)
            yield event.plain_result(
                f"已停止任务：{torrent_name}\nHash: {resolved_hash}"
            )
        except Exception as exc:
            yield await self._command_error(event, "stop", exc)

    async def terminate(self):
        await self.service.close()
