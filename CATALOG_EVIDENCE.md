# 离线品牌身份与目录关联

品牌模块只处理已保存证据，不访问浏览器、网站或代理，不更改产品数值、图片或来源暂停。本轮原生预览冻结82产品；产品610图像的单独审核模块与此目录关联分工明确，不将图片复用当作其他产品已核验。

## 最小数据增量

- 复用原 `brands` 存522个当前目录实际品牌ID/名称/URL；ID取自观察到的 `/sort/{id}`，不由名称推测。
- 复用原 `edges` 存82条显式 `listed_under_brand` 关系：中华13→56、黄鹤楼131→26。不会制造queue、complete任务或brand_reports。
- 产品5914的名称是英文 `Chunghwa (Thailand)`，仍由实际目录链接归到13；没有中文前缀筛选。结构允许以后经证据支持的多品牌关系，不给child URL添加唯一品牌限制。
- 当前522目录扁平快照没有分类组。数据库的兼容字段 `source_groups=[]` 仅意为本次未采集；证据明确 `not_collected_in_current_flat_directory`，中立导出为null。没有将2026-10-26旧分组伪装成今日分类，也没有推断地区或产地。

## 自含、可重验的来源证据

同一数据库metadata中加入 `catalog_evidence`，checkpoint可选显式导出/恢复。每份证据只包含经过严格格式/字段白名单检查的**原始脱敏JSON文本**及其精确UTF-8字节SHA256，不是网页HTML，也没有脚本、cookie、价格、评论或隐藏查询。恢复时会重新校验哈希、实际品牌ID/URL、页号、计数、产品名一致性和JSON pointer，再允许品牌→产品非queue边。旧crawler边仍必须在queue两端有节点，不能用新分支放行任意断开的边。

真实来源共30份：

1. `brand-directory-browser-20261027.json`：522唯一品牌，原观察时间 **2026-10-27T05:48:18.728Z** 原样保留；595为观察链接数，不是595唯一品牌。
2. `brand13-browser-index-20261027.json`：56产品链接，只有 **2026-10-27（日精度）**。不会补午夜或伪造6页各自时间；该文件没有页面URL，关系引用的source_url为null，品牌引用来自匹配明确brand_id的目录URL。输入expected_records/pages分别标为操作者报告，不假称当前源DOM总数原文。
3. `browser-listings-20261027/brand-131-page-*.json`：使用实际28个已保存页面，逐页精确毫秒时间/URL/原文总数/产品指针保留。无时间的131合并索引不用来补日期。277个索引链接中现有26份详情，**另251份没有详情**。

SHA指的是上述已保存JSON原字节，`original_html_sha256`仍为null。来源用户贡献，不是厂家认证；用户声明的内容授权独立于代码许可证。

## 初始化、幂等与冲突

入口为 `python -m yanyue.catalog_cli`，独立于原采集CLI。`init`只接受固定已审核82产品checkpoint（SHA256 `7919b95519ae925f2c556bc2bc4a3ada4f26ede7571ec31428356750bafed517`），先完成所有离线来源校验再创建新目录。恢复基线时使用已捕获的同一份字节，不重读可变源路径。

重跑同一证据在既有catalog状态上仅作只读核对，返回already_imported；不同证据、品牌/名称/分组冲突或遗漏产品关系都拒绝，不替换last-good。不能把原82产品源状态目录直接当导入目标。query/export全程只读，未合并WAL数据时拒绝，避免读旧快照或改变源库。

```sh
PYTHONPATH=tools python -m yanyue.catalog_cli init \
  --checkpoint /path/to/reviewed82/checkpoint.json \
  --state-dir /path/to/new-catalog-state \
  --directory /path/to/brand-directory-browser-20261027.json \
  --brand-index /path/to/brand13-browser-index-20261027.json \
  --listing-page /path/to/actually-saved-page-1.json
# 实际28页需要逐份提供--listing-page；参数只列已有本地文件，不产生来源请求。

PYTHONPATH=tools python -m yanyue.catalog_cli query \
  --state-dir /path/to/catalog-state --brand-id 13 --text Chunghwa
PYTHONPATH=tools python -m yanyue.catalog_cli export \
  --state-dir /path/to/catalog-state --output /path/to/new-neutral-catalog.json
```

数据导出提供522品牌列表、82详情名称/ID、品牌关系、来源引用与覆盖计数，可作为以后按品牌列表和名称子串搜索的中立基础。名称/ID排序不是评分或推荐，没有购物渠道、排行、价格或健康效益断言。未知品牌的目录产品总量为null，不把“没有详情”显示为“产品数为0”。

输入每份128KiB、最多100份、总8MiB；拒绝未知字段、重复JSON键、非有限数、外域/会话查询、错误页号、伪造计数、异常时间、符号链接/FIFO等。新导出文件必须在状态目录外且不存在，以独占发布方式避免覆盖数据库或输入。

## 验证与实际范围

品牌模块79项项目测试及16项独立检查已通过；组合树另行执行完整离线测试。真实隔离验证目录 `offline-brand-catalog-validation-20261027/`：522品牌、82关联、333已索引关系、82已读详情，251目录关系仍缺详情。幂等重跑、checkpoint往返、原82产品/暂停以及原验证文件哈希已核对；未增加来源任务，未修改brand_reports或数值。

这是数据关联基础，不是完整小程序或全站采集；`whole_website_complete`、`field_review_complete`、`publication_ready`均为false。本轮仅供原生源码本地预览，未发布微信版本。
