# AstrBot qBittorrent 管理

通过 AstrBot 管理 qBittorrent 下载任务。插件提供 `/qbt` 指令组和一个名为 `qbittorrent` 的 AI tool。

## 前置条件

- qBittorrent 已启用 WebUI，并允许 AstrBot 所在机器访问。
- qBittorrent Web API 至少为 `2.11.9`。磁力链文件预览依赖 `torrents/fetchMetadata`。
- AstrBot 至少为 `4.5.7`。
- 插件 WebUI 配置中必须填写 `authorized_uids`。使用 AstrBot `/sid` 获取 UID；空列表默认拒绝所有操作。

## 配置

在 AstrBot WebUI 的插件配置中设置：

- `base_url`：例如 `http://127.0.0.1:8080`。反向代理部署时填写完整子路径，例如 `https://example.com/qbt`。
- `username` 和 `password`：qBittorrent WebUI 账号。
- `delete_files`：是否删除条目对应的已下载文件，默认关闭。
- `default_search_limit`：未指定数量时的搜索结果数，默认 10。
- `authorized_uids`：允许操作的 AstrBot UID 列表。

插件不会把密码写入消息或日志。

搜索结果按添加时间倒序排列，最新添加到 qBittorrent 的条目优先显示。

## 指令

```text
/qbt search [关键词] [数量]
/qbt preview <磁力链>
/qbt add <磁力链或预览令牌> [文件选择]
/qbt delete <hash 或唯一 hash 前缀> [确认]
/qbt rename <hash 或唯一 hash 前缀> <新任务名称>
/qbt category <hash 或唯一 hash 前缀> <已有分类名|清空>
/qbt tags <hash 或唯一 hash 前缀> <标签1,标签2|清空>
```

例如：

1. `/qbt preview magnet:?xt=...`
2. 根据返回的种子名称和文件清单选择文件。
3. `/qbt add AbCdEf12 1,3-5`

文件编号从 1 开始，支持逗号和范围。直接执行 `/qbt add magnet:?xt=...` 会添加全部文件。预览令牌默认 15 分钟有效，并绑定创建它的 UID 和会话。

删除时默认保留文件。若开启 `delete_files`，指令必须追加文字 `确认`；AI tool 必须传入 `confirm=true`。

重命名只修改 qBittorrent 列表中的任务显示名称，不会修改下载文件。新名称可以包含空格，例如：

```text
/qbt rename abcd1234 我的新任务名称
```

分类只能设置为 qBittorrent 中已经存在的分类；使用 `清空` 可恢复为未分类。任务开启自动管理时，修改分类可能根据 qBittorrent 配置移动下载目录。

标签采用整体替换方式。例如 `/qbt tags abcd1234 电影,已整理` 会把任务标签设置为这两个标签；使用 `清空` 删除全部标签。搜索结果会直接显示每个任务当前的分类和标签。

## AI tool

AI 使用统一的 `qbittorrent` tool，通过 `action` 选择：`search`、`preview`、`add`、`delete`、`rename`、`set_category` 或 `set_tags`。预览后，模型应使用返回的 `preview_token` 和 1-based `file_indexes` 调用 `add`；修改分类或标签时提供 `torrent_hash` 以及 `category` 或 `tags`。

## 排错

- `401/403`：检查用户名、密码和 qBittorrent WebUI 的认证设置。
- `404`：检查 `base_url` 是否包含正确的反向代理路径，且没有重复 `/api/v2`。
- API 版本过低：升级 qBittorrent 到支持 Web API `2.11.9` 或更高版本。
- 元数据超时：磁力链需要等待 DHT/Tracker 获取元数据，稍后重新执行 `preview`。
- 未授权：确认 `/sid` 输出的 UID 已作为字符串加入 `authorized_uids`，保存配置后重载插件。
