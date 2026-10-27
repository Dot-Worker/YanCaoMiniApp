# 已加载浏览器字段快照 → 现有存储（离线导入）

本功能在已发布的兼容性修复上增量集成，不复制适配器，不修改来源网络实现、Actions 工作流或生产数据库。仓库只附四份脱敏产品字段快照和单品牌索引作为有限回归夹具；不发布真实运行数据库、完整批次快照或批次报告。导入器本身**不打开浏览器、不访问源站、不下载图片**。此前 Actions 的 `source_http_403`（2 请求、73 字节、0 页面/产品）暂停不被清除；另行获准的正常浏览器观察不等于 Actions 已恢复或全站成功。

## 最小复用与真实范围

- 既有 `import-observed-index` 只接受品牌总索引，不能直接导入本次 `{fields, photo}`，不强行将其当网页或完整产品。
- 新 `import-browser-snapshot` 是严格边界适配器，支持原 `{fields, photo}` 和新增严格 `{source_url, product_name, observed_at, snapshot}` 包装；共享原有事实构造器、产品 schema、SQLite `Store.save_page`、图片引用队列及 JSON checkpoint，未建立第二套数据库。
- `Store.save_page(..., update_job=False)` 不插入/完成来源任务，不改变现有任务的 status、error、attempts、HTML hash、缓存引用，也不改 pause、budget 或 last_result。已有不同产品记录拒绝覆盖；完全相同的快照/元数据重导入幂等。新的图片记录只是 `pending` 引用，不声称存在图片字节。
- 产品记录明确标记 `imported_loaded_browser_field_snapshot`；来源 `www.yanyue.cn`、记录 ID 442、名称 `中华(硬)`、实际观察时刻 `2026-10-27T05:13:19+00:00` 保留。该精确时刻由根任务确认，不从文件名猜测，不使用导入时刻冒充观察时刻。
- `snapshot_provenance.input_sha256` 是**输入字段 JSON 的字节哈希**，不是网页 HTML 哈希；`original_html_sha256` / 页面 `source_content_sha256` 为 `null`。身份/时间属于 `operator_attested_from_loaded_page`；工具并不能认证任意本地 JSON 的真实性。
- 442 样本有 5 个实际文本属性、8 个图片数值、2 个条码项目和 1 个产品主图引用。8 项数值、2 项条码全部 `value=null`，状态 `needs_review/image_not_read`；“更多”不作为条码。重复证据 URL 按已有队列去重为 **11 个待核验/待下载图片引用**。另外三份实际观察到「烟支: 中支/细支」，映射为独立纯文本 `cigarette_format`，不是长度或健康含义；缺失颜色/尺寸保留 missing/null。四份合计 **28 个唯一待核验图片引用，0 张下载**。
- 当前导入只接受实际样本的规范 `https://www.yanyue.cn/product/{id}`，以及原有 `res.yanyue.cn` 受审缩略图路径。这是用户允许的 `yanyue.cn/www.yanyue.cn/res.yanyue.cn` 的更窄已核验子集；没有新增根域路由、外域或任何网络入口。
- `browser-index-status` 只读验证本地56条单品牌索引并与指定数据库对照。若只导入仓库四份回归样本，结果是56个唯一索引链接、4个详情字段记录、52个缺少详情记录；不会把索引链接冒充详情。它不新增源任务、不存储第二套索引数据库、不伪造源页面完成状态；原始索引文件仍是持久证据。索引日期保持 day 精度；输入的 expected_records/pages 标注为操作者报告，不伪称聚合文件包含源页面总数原文。

## 本轮隔离验证的实际范围

2026-10-27 的正常浏览器字段观察已在仓库外的隔离状态中完成中华单品牌 **56 份字段快照**的导入核验：与56条本地索引逐条对齐，**148 个待核验图片引用、0 张下载、0 个拒绝**。主图和规格/条码图只有引用；图片中的数值与条码仍为 `null`，未做 OCR 或人工数字核验。产品原文介绍未读取，完整图片集未采集。

这些数值描述隔离验证结果，不表示仓库内含56份产品数据库，也不表示全站完成。批量工具见 [`tools/offline_batch/README.md`](tools/offline_batch/README.md)。导入时保留现有来源队列与暂停；不重启原 Actions 403 采集路径。

## 离线使用

只对隔离状态目录运行，不指向生产状态：

```sh
PYTHONPATH=tools python -m yanyue.cli import-browser-snapshot \
  --input tools/tests/fixtures/current-product-442-fields-20261027.json \
  --source-url https://www.yanyue.cn/product/442 --source-id 442 \
  --name '中华(硬)' --observed-at 2026-10-27T05:13:19Z \
  --state-dir /path/to/isolated-state
```

`--observed-at` 要求实际 UTC 秒或三位毫秒精度，接受 `Z` 或 `+00:00`，仅规范化时区后缀，毫秒 `.436/.617/.340` 不截断或补零伪精度。仅有日期或未知时刻的详情快照不适用本窄协议，直接拒绝，不补午夜。页面 SEO 标题不能充当产品名。

包装文件自带实际元数据时可直接 `--input tools/tests/fixtures/browser-product-3358.json --state-dir /path/to/isolated-state`，不用猜测名称/时间；如果同时提供CLI元数据，必须与包装一致，否则拒绝。包装原文件整体字节哈希保留。

```sh
PYTHONPATH=tools python -m yanyue.cli browser-index-status \
  --input tools/tests/fixtures/brand13-browser-index-20261027.json \
  --state-dir /path/to/isolated-state
```

输入上限 64 KiB，仅正规 JSON 文件；拒绝符号链接、FIFO、重复 JSON 键、NaN、未知键/标签、价格/评论字段、HTML/会话内容、外域/查询/片段 URL、图片类别错配、OCR/人为填写的数值和条码。图片 URL 只是保存证据地址，导入不请求这些地址。所有内容仍需产品事实与中立性审核，不具厂家认证。

原有 checkpoint 导出/恢复复用新产品 schema，并保留导入哈希、时间和来源方法；JSON 检查点仍不含图片二进制、SQLite、浏览器档案、cookie 或原始 HTML。相同代码 SHA 的规则不变，新格式不能交给旧 schema 读取。

## 验证

```sh
PYTHONPATH=tools python -m unittest discover -s tools/tests -v
python -m compileall -q tools/yanyue
```

针对真实脱敏样本测试身份/时间、数值未知、两个条码图片保留、外域和会话查询拒绝、价格/评论/伪造数字拒绝、文件界限、无网络、既有暂停任务逐列保留、幂等及最后成功记录保护、checkpoint 往返与语义篡改拒绝。独立审计结果另报。

**非目标：** 本补丁不是源站访问策略、Actions 恢复、代理/身份切换、浏览器接管、全站采集、数值人工/OCR审核、原图下载或完整小程序。
