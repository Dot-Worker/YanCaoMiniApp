# 烟悦网事实资料采集工具（先资料，后界面）

> **当前来源访问仍暂停。** 已核验的一期受限 CI 探测返回 `source_http_403`：2 次请求、73 字节响应体、0 个成功解析页面/产品、0 张图片。不是全量资料或完整小程序。此次仅提交离线兼容性修复；它们不是 403 成因证明，也不构成恢复访问授权。不要运行来源采集、`resume-source`、切换环境/浏览器身份或重用会话来重试。下一步需要站方提供的合法导出或明确允许的入口。

兼容性补丁范围及离线测试见根目录 `COMPATIBILITY.md`。

当前版本是**已观察目录链接的、有预算、可暂停的采集器**，不是已完成全站数据。代码不枚举连续 ID，不读取论坛、账户、评论、购物渠道或价格排行。来源站资料由网友提供，不标注为厂家认证。

## 已实现与尚未实现

- 已实现：`/tobacco` 品牌分组、实际品牌列表和分页、`/product/{id}` 基本信息；品牌重复分组保留为集合。数值图保留证据 URL，值为 `null`，不按文件名、alt 文本或相似产品猜数字。
- 已实现：官方 Playwright 自建临时 Chromium 上下文、普通页面 JavaScript 加载、单工作者、全局至少 5 秒间隔、请求/页数/接收字节/墙钟时间预算、SQLite 断点与最后成功记录、URL/内容哈希缓存、严格 JSON 检查点。
- 已实现：图片域名/路径白名单、每图 5 MiB 上限、MIME/实际格式/完整解码/像素数检查；图片原样保存在本地待审目录，不去水印。
- **未声称完成**：成功的实际采集验证（已执行的受限探测被源站 403 拒绝）、全站产品计数、全部详情页、数值图人工核验导入、原文内容区/图片集 DOM 解析、跨 Actions 运行的图片二进制持久化、资料发布或微信平台审核。

用户现已明确声明拥有相关第三方图片及原文的完整授权。授权边界与代码许可证分开记录；不是独立核验每位作者的证据。`original_text`/`gallery_status` 目前明确为待范围核查、未采集，不填假数据，也不把整页脚本、隐藏会话参数或无关帖子当原文入库。

## 离线验证（不访问来源站）

```sh
python -m pip install -r tools/requirements-test.txt
PYTHONPATH=tools python -m unittest discover -s tools/tests -v
PYTHONPATH=tools python -m yanyue.cli --help
PYTHONPATH=tools python -m yanyue.cli crawl --state-dir data/runtime
```

最后一条只显示计划；不带 `--execute` 永远不发起来源请求。依赖版本为开发执行器已安装且测试过的固定版本，不代表声称“最新版本”。

## 未来重新获准访问后的微型试跑示例（当前不得运行）

```sh
python -m pip install -r tools/requirements.txt
python -m playwright install --with-deps chromium
PYTHONPATH=tools python -m yanyue.cli crawl --execute \
  --state-dir data/runtime --max-pages 3 --max-requests 5 \
  --max-images 0 --max-bytes 10485760 --max-seconds 180 --interval 5
PYTHONPATH=tools python -m yanyue.cli status --state-dir data/runtime
```

`max-requests` 包含 robots、初始文档、站点自己发起的加载导航、规范页重新进入、图片 robots 和图片；已阻止的子资源没有发出，不计请求。5 次请求可能只足够解析一页；不能拿 `max-pages` 当成功页数。普通预算到达为“部分完成”，来源暂停/中断退出码为 2，不能把 Actions 状态等同于资料收集完成。

现有 403 队列保持暂停；下面的研究索引导入只处理本地文件，不恢复来源访问：

```sh
PYTHONPATH=tools python -m yanyue.cli import-observed-index \
  --input /path/to/brand-index-live-2026-10-26.json --state-dir data/runtime
```

该导入保留原始日期精度（只有日期就不伪造午夜时间），不假装是新抓取；没有原始 HTML 哈希时明确为 `null`。`--only-brand 13` 只过滤已经发现的队列，绝不生成品牌或产品 ID。`--phase discover` 仅处理目录/列表；`--phase products` 仅处理已发现详情。

## 停止和访问规则

- 只使用采集器新建的临时 Playwright Chromium（当前实现为 bundled chromium-headless-shell，不是已验证的系统 Google Chrome 或所有 Chrome 模式）；不连接用户或平台管理的浏览器，不读取/导出 cookie，不导入浏览器档案，不导出网络头或 HAR，不轮换身份/代理，不绕过登录、验证码、403 或 429。
- 浏览器自己执行站点的普通 JavaScript 加载。瞬态 `loading` 查询只留在当前浏览器，工具不生成、不保存、不重放。返回真实内容后仅可正常重新访问原始规范 URL 一次；页面仍非规范或链接结构异常就暂停。
- 官方 Chromium CDP `Fetch` 在请求发送前做范围/预算检查，在响应头阶段拒绝全部 HTTP 3xx、错误状态及压缩内容。`IO.read` 每次最多 64 KiB，有单文档 2 MiB 与总字节上限；不调用无界 `response.body()`。
- `Network.setAcceptedEncodings([])` 仅请求无压缩传输以限制解压风险；不改变 User-Agent、cookie 或身份。必需 CDP 功能不可用时停止，不降级到无界获取。官方协议参考：[Fetch](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/Fetch.pdl)、[IO](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/IO.pdl)。该编码设置也在固定 Playwright 1.62.0 自带的 `protocol.d.ts` 中核对过。
- 静态子资源/跨帧/非 GET/异站导航全部阻止；WebSocket、Service Worker 和弹窗不用于采集。当前只解析已验证的服务器 DOM。未来若页面依赖新脚本或接口，先停再审，不猜接口。
- robots 每个来源读取并每小时刷新；404/410 仅表示未找到该文件，不代表授权。保守支持普通前缀 Allow/Disallow 和小数 Crawl-delay，遇到通配符、`$`、编码路径等未实现语法直接暂停，不当作允许。
- 401/403/429/5xx、验证码、登录、异常结构均暂停并保留队列；遵守 Retry-After。恢复须操作员确认：`resume-source --acknowledge-source-review`；修好解析器后才可加 `--retry-failed`。不自动重试。
- POSIX `setitimer` 约束整个网络运行（包括慢连接/流读取），SQLite 每个成功页面持久化；SIGTERM/SIGINT 尽力记录停止状态。硬终止仍保留之前已提交的记录，不能保证保存正在传输的那一页。

## 数据和检查点

`data/runtime/state.sqlite` 是本地工作状态；`cache/` 只保存白名单解析 JSON，不保存原始 HTML。`assets/` 是有来源记录的图片待审区。它们都在 `.gitignore` 中。

```sh
PYTHONPATH=tools python -m yanyue.cli checkpoint export \
  --state-dir data/runtime --output public-checkpoint
PYTHONPATH=tools python -m yanyue.cli checkpoint restore \
  --input public-checkpoint --state-dir data/restored-runtime
```

导出只包含一个经过严格模式/语义验证的 `checkpoint.json`，展开体积最多 20 MiB，不含 SQLite、浏览器数据、原始 HTML 或图片二进制。恢复到空状态目录，拒绝符号链接、非规范 URL、身份冲突、NaN、未知字段、预算计数越界和缺失完成记录。Actions 运行须匹配同一个源提交 SHA。每页源内容哈希保留；原始图不是代码许可的一部分。

**图片恢复限制：** 当前 JSON 检查点不带图片文件或已下载状态，恢复时会把图片证据重新列为待采集；所以这版跨 Actions 检查点只用于 `max-images=0` 阶段。全量图片阶段需要先批准并实现独立的尺寸/哈希/清单验证存储方案，不能将本地图片缓存误称为已跨运行保存。

覆盖率区分“发现链接”“已解析”“待处理”“失败”“暂停”和品牌源站声称数量；`whole_website_complete`、`field_review_complete`、`publication_ready` 均不因队列暂空自动变成 true。
