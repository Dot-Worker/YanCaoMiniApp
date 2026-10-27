# 已结束小批次的离线增量导入包装器

此工具复用本仓库唯一的 `tools/yanyue/` 快照适配器，不复制第二套解析器或数据库。启动先核验固定的代码/schema 哈希，再串行调用 `import_snapshot`，沿用现有 SQLite、白名单、null 数值、来源暂停、last-good 与幂等规则，不接管浏览器、不请求任何网址、不下载图片。

只有操作员明确确认这一批本地快照已经保存完毕后才能运行；文件夹还在新增时不要启动。每次开始枚举实际存在的 `product-{id}.json`，最多100份、每份64 KiB，不生成来源 ID，不读取其他文件。未知属性由已审计适配器拒绝，报告失败，不自动映射或填数值。

```sh
python tools/offline_batch/import_batch.py \
  --input-dir /path/to/completed-snapshots \
  --state-dir /path/to/isolated-state \
  --output /path/to/new-batch-report.json --batch-complete
```

- 启动先核验 `ADAPTER_SHA256.json` 中全部适配器 Python 和 schema 的冻结哈希。适配器有改动先审查，不自动接受新代码。
- 每个文件以 O_NOFOLLOW、非阻塞打开并核验为正规文件，有实际64 KiB读取上限；拒绝 symlink、FIFO、设备和过大文件。捕获的**同一份原始字节**放入私有临时文件后交适配器，不重新序列化 JSON，也不会因源文件随后变化而导入另一份数据。
- 正常大小文件的报告包含原字节 SHA256，成功产品也保留相同哈希。不可安全完整读取的文件（如过大/FIFO）报告 hash=null，不为获得哈希而突破上限。
- 文件名 ID 与包装中的来源产品 ID 必须一致。结果分别为 imported、already_imported、rejected；任何拒绝使CLI返回2，同时保留其他已成功记录。现存不同记录不覆盖。
- 来源队列、pause、budget、last_result不改变；每批结束再次逐项对照原状态。来源记录状态漂移或中断也返回非零，不假装完整成功。
- 报告及其原子写入 `.tmp` 路径必须新建，并且不得位于输入目录或状态目录内（包括符号链接别名）；在打开 SQLite 前检查，避免报告覆盖数据库/缓存/输入。每处理一份通过独占创建的随机临时文件原子保存一次，不使用可能被插入的固定 `.tmp` 文件；不覆盖旧报告或输入。中断可能发生在当前产品已落库而报告还没更新时，下次按幂等规则核对，不能凭报告计数推断数据库损失。
- 本批结果只表示现存本地快照导入，不等于目录详情全读、数值核验、图片存在或全站完成。56条索引对照仍用已审计 `browser-index-status`，不在此复制新的统计协议。

在仓库根目录运行离线测试：`PYTHONPATH=tools python -m unittest discover -s tools/tests -v`。批量导入测试已放入同一测试入口，既有推送 CI 不需要修改。测试覆盖有限脱敏样本、原字节哈希、报告路径隔离、符号链接/FIFO、幂等及暂停状态保护；真实运行数据库和批次报告不随源码发布。
