# AstrBot qBittorrent 管理

通过 `/qbt` 指令和 `qbittorrent` AI tool 管理 qBittorrent 下载任务。要求 qBittorrent Web API `2.11.9+`、AstrBot `4.5.7+`。

## 配置

- `base_url`：qBittorrent WebUI 地址。
- `username` / `password`：WebUI 账号。
- `delete_files`：删除任务时是否同时删除文件。
- `default_search_limit`：搜索结果数量，默认 10。
- `authorized_uids`：允许操作的 UID 列表（`/sid` 获取）。

## 指令

```text
/qbt search [关键词] [数量]
/qbt info <hash 或唯一 hash 前缀> [files]
/qbt preview <磁力链>
/qbt add <磁力链或预览令牌> [文件选择]
/qbt delete <hash 或唯一 hash 前缀> [确认]
/qbt rename <hash 或唯一 hash 前缀> <新名称>
/qbt renameitem <hash 或唯一 hash 前缀> <编号> <新名称>
/qbt category <hash 或唯一 hash 前缀> <分类|清空>
/qbt tags <hash 或唯一 hash 前缀> <标签1,标签2|清空>
/qbt ratio <hash 或唯一 hash 前缀> <分享率|默认|无限>
/qbt upload <hash 或唯一 hash 前缀> <KiB/s整数|默认|无限>
/qbt start|stop <hash 或唯一 hash 前缀>
```

## AI tool

统一使用 `qbittorrent` tool，通过 `action` 调用对应操作。
