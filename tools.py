"""AstrBot function tool exposed by the plugin."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

try:
    from .client import QBittorrentError
    from .service import format_preview, format_search_results
except ImportError:  # See the loading compatibility note in main.py.
    from client import QBittorrentError
    from service import format_preview, format_search_results


def _format_add_result(result: dict[str, Any] | str) -> str:
    if isinstance(result, dict):
        return json.dumps(
            {
                "success": True,
                "success_count": result.get("success_count", 0),
                "pending_count": result.get("pending_count", 0),
                "failure_count": result.get("failure_count", 0),
                "torrent_ids": result.get("added_torrent_ids", []),
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {"success": True, "message": str(result) or "已提交下载"}, ensure_ascii=False
    )


@dataclass
class QBittorrentTool(FunctionTool[AstrAgentContext]):
    service: Any = Field(default=None, exclude=True)
    name: str = "qbittorrent"
    description: str = (
        "管理 qBittorrent：搜索、预览、添加、重命名、分类、标签、分享率、"
        "上传限速、启停或删除下载条目。"
        "预览后用 preview_token 和 1-based file_indexes 选择文件。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "search",
                        "preview",
                        "add",
                        "delete",
                        "rename",
                        "set_category",
                        "set_tags",
                        "set_ratio",
                        "set_upload_limit",
                        "start",
                        "stop",
                    ],
                    "description": "要执行的操作。",
                },
                "query": {"type": "string", "description": "搜索关键词。"},
                "magnet": {"type": "string", "description": "完整 magnet:? 磁力链。"},
                "preview_token": {
                    "type": "string",
                    "description": "preview 返回的短令牌。",
                },
                "torrent_hash": {
                    "type": "string",
                    "description": "任务操作目标的完整 hash 或唯一 hash 前缀。",
                },
                "new_name": {
                    "type": "string",
                    "description": "rename 操作要设置的新任务显示名称。",
                },
                "category": {
                    "type": "string",
                    "description": "set_category 操作要设置的已有分类；空字符串表示清除分类。",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "set_tags 操作要设置的完整标签集合；空数组表示清空标签。",
                },
                "ratio_limit": {
                    "type": "number",
                    "minimum": -2,
                    "description": "set_ratio 的分享率；-2 使用默认设置，-1 表示无限，其他值必须非负。",
                },
                "upload_limit_kib": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "set_upload_limit 的 KiB/s 整数；0 取消任务专属限速并沿用全局设置。",
                },
                "file_indexes": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "要下载的文件编号，使用 preview 返回的 1-based 编号。",
                },
                "limit": {
                    "type": "integer",
                    "description": "搜索结果数量；省略时使用插件配置。",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "开启删除已下载文件后，删除操作必须显式设为 true。",
                },
            },
            "required": ["action"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        try:
            event = context.context.event
            uid = self.service.assert_authorized(event.get_sender_id())
            session = event.unified_msg_origin
            action = str(kwargs.get("action", "")).strip().lower()

            if action == "search":
                limit = kwargs.get("limit")
                torrents = await self.service.search(
                    str(kwargs.get("query", "")), None if limit is None else int(limit)
                )
                return format_search_results(torrents)

            if action == "preview":
                magnet = str(kwargs.get("magnet", "")).strip()
                if not magnet:
                    raise QBittorrentError("preview 操作必须提供 magnet")
                preview = await self.service.preview(magnet, uid, session)
                return format_preview(preview)

            if action == "add":
                source = str(
                    kwargs.get("preview_token") or kwargs.get("magnet") or ""
                ).strip()
                if not source:
                    raise QBittorrentError("add 操作必须提供 magnet 或 preview_token")
                result = await self.service.add(
                    source,
                    kwargs.get("file_indexes"),
                    uid,
                    session,
                )
                return _format_add_result(result)

            if action == "delete":
                torrent_hash = str(kwargs.get("torrent_hash", "")).strip()
                if not torrent_hash:
                    raise QBittorrentError("delete 操作必须提供 torrent_hash")
                resolved_hash, name = await self.service.delete(
                    torrent_hash, confirmed=bool(kwargs.get("confirm", False))
                )
                return json.dumps(
                    {
                        "success": True,
                        "name": name,
                        "torrent_hash": resolved_hash,
                        "downloaded_files_deleted": self.service.delete_files,
                    },
                    ensure_ascii=False,
                )

            if action == "rename":
                torrent_hash = str(kwargs.get("torrent_hash", "")).strip()
                new_name = str(kwargs.get("new_name", "")).strip()
                if not torrent_hash:
                    raise QBittorrentError("rename 操作必须提供 torrent_hash")
                if not new_name:
                    raise QBittorrentError("rename 操作必须提供 new_name")
                resolved_hash, old_name, normalized_name = await self.service.rename(
                    torrent_hash, new_name
                )
                return json.dumps(
                    {
                        "success": True,
                        "old_name": old_name,
                        "new_name": normalized_name,
                        "torrent_hash": resolved_hash,
                    },
                    ensure_ascii=False,
                )

            if action == "set_category":
                torrent_hash = str(kwargs.get("torrent_hash", "")).strip()
                if not torrent_hash:
                    raise QBittorrentError("set_category 操作必须提供 torrent_hash")
                if "category" not in kwargs:
                    raise QBittorrentError("set_category 操作必须提供 category")
                resolved_hash, torrent_name, category = await self.service.set_category(
                    torrent_hash, str(kwargs.get("category", ""))
                )
                return json.dumps(
                    {
                        "success": True,
                        "name": torrent_name,
                        "category": category,
                        "torrent_hash": resolved_hash,
                    },
                    ensure_ascii=False,
                )

            if action == "set_tags":
                torrent_hash = str(kwargs.get("torrent_hash", "")).strip()
                if not torrent_hash:
                    raise QBittorrentError("set_tags 操作必须提供 torrent_hash")
                if "tags" not in kwargs:
                    raise QBittorrentError("set_tags 操作必须提供 tags")
                resolved_hash, torrent_name, tags = await self.service.set_tags(
                    torrent_hash, kwargs.get("tags", [])
                )
                return json.dumps(
                    {
                        "success": True,
                        "name": torrent_name,
                        "tags": tags,
                        "torrent_hash": resolved_hash,
                    },
                    ensure_ascii=False,
                )

            if action == "set_ratio":
                torrent_hash = str(kwargs.get("torrent_hash", "")).strip()
                if not torrent_hash:
                    raise QBittorrentError("set_ratio 操作必须提供 torrent_hash")
                if "ratio_limit" not in kwargs:
                    raise QBittorrentError("set_ratio 操作必须提供 ratio_limit")
                resolved_hash, torrent_name, ratio_limit = await self.service.set_ratio(
                    torrent_hash, kwargs.get("ratio_limit")
                )
                mode = (
                    "default"
                    if ratio_limit == -2
                    else "unlimited"
                    if ratio_limit == -1
                    else "custom"
                )
                return json.dumps(
                    {
                        "success": True,
                        "name": torrent_name,
                        "ratio_limit": ratio_limit,
                        "ratio_limit_mode": mode,
                        "torrent_hash": resolved_hash,
                    },
                    ensure_ascii=False,
                )

            if action == "set_upload_limit":
                torrent_hash = str(kwargs.get("torrent_hash", "")).strip()
                if not torrent_hash:
                    raise QBittorrentError("set_upload_limit 操作必须提供 torrent_hash")
                if "upload_limit_kib" not in kwargs:
                    raise QBittorrentError(
                        "set_upload_limit 操作必须提供 upload_limit_kib"
                    )
                (
                    resolved_hash,
                    torrent_name,
                    limit_kib,
                ) = await self.service.set_upload_limit(
                    torrent_hash, kwargs.get("upload_limit_kib")
                )
                return json.dumps(
                    {
                        "success": True,
                        "name": torrent_name,
                        "upload_limit_kib": limit_kib,
                        "uses_global_limit": limit_kib == 0,
                        "torrent_hash": resolved_hash,
                    },
                    ensure_ascii=False,
                )

            if action in {"start", "stop"}:
                torrent_hash = str(kwargs.get("torrent_hash", "")).strip()
                if not torrent_hash:
                    raise QBittorrentError(f"{action} 操作必须提供 torrent_hash")
                operation = (
                    self.service.start if action == "start" else self.service.stop
                )
                resolved_hash, torrent_name = await operation(torrent_hash)
                return json.dumps(
                    {
                        "success": True,
                        "name": torrent_name,
                        "state": "started" if action == "start" else "stopped",
                        "torrent_hash": resolved_hash,
                    },
                    ensure_ascii=False,
                )

            raise QBittorrentError(
                "action 必须是 search、preview、add、delete、rename、set_category、"
                "set_tags、set_ratio、set_upload_limit、start 或 stop"
            )
        except QBittorrentError as exc:
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
        except Exception:
            logger.exception("qBittorrent AI tool failed")
            return json.dumps(
                {
                    "success": False,
                    "error": "qBittorrent 操作失败，请检查插件日志和连接配置。",
                },
                ensure_ascii=False,
            )
