# 本地预览数据导出（离线，不爬取）

命令均在仓库的 `WeChatAPP/` 项目根执行（从仓库根先运行 `cd WeChatAPP`）；默认输出也是该项目根。

`build_data.py` 只增加客户端 data/assets，不改现有 Python collector、工作流、来源快照或 SQLite。输入路径通过命令行显式传入，不在公开源码内保存私有机器绝对路径。

## 固定输入

- `neutral_catalog_view_v1`：522 品牌 / 82 产品摘要 / 明确列表关系，SHA256 `8ed413ea54eed9bb311b7a0d48c2801497a03f6a089ea28994bdfa972a9ed7d8`。
- 82 产品基础 checkpoint：SHA256 `7919b95519ae925f2c556bc2bc4a3ada4f26ede7571ec31428356750bafed517`。
- 可选产品610独立视觉审核manifest：SHA256 `423946df3bc973d6d93349ded43ac95e2ef4ee6d9497a2f37cbd56e6f3c4c15e`。

目录与来源字段分开关联，品牌ID来自关系证据，不靠文字猜测。不是泛用“所有未来快照自动接受”脚本；未来输入必须重新审核与修改固定合同。

```sh
python preview-tools/build_data.py \
  --catalog /path/to/reviewed/neutral-catalog-v1.json \
  --checkpoint /path/to/reviewed/checkpoint.json
```

上例不含图像读数，保留空值。只有 **图像导入实现也已完成独立审计** 后，维护者才能显式追加：

```sh
  --include-reviewed-images --image-application-audit-confirmed \
  --image-manifest /path/to/reviewed/review-manifest.json \
  --reviewed-db /path/to/reviewed/state.sqlite \
  --asset-dir /path/to/reviewed/assets
```

该确认参数是人工流程门槛，不是独立审计本身。不得对待审输入使用它。导出只读打开 SQLite，确认82条集合相同、其余81条与基础快照完全相等；只允许610的规格/条码变更，并逐值比对manifest hash、来源product ID、候选值、图像 SHA。未知值保持 `null`。

610先以固定基础快照+固定manifest完整重建唯一允许结果：数值键绑定候选 `target_field`，条码 `kind` 绑定候选目标并保留原变体顺序；全部非批准字段必须与基础快照一致。使用规范JSON比较区分布尔值与数字，拒绝完整字段互换、去掉已审读数、伪missing状态、额外字段、非批准文本/图像/来源元数据变更。

资产文件名限定内容地址形式；每个文件必须在固定manifest中，SHA/长度一致、原字节不变、已解码、无审核发现的敏感元数据。仅复制9张实际审核图，不读取带有下载临时路径的元数据文件，不根据相同URL向其他产品传播读数。

完整内部 `image_review` 只作为私有输入校验，不装入公开客户端。公开字段改为最小 `review_provenance`：目标字段、产品ID、来源/图像/快照hash、单位依据、版本索引、日期和原完整对象的规范JSON SHA256（UTF-8、`ensure_ascii=False`、键排序、无空白、拒绝NaN）。不公开candidate对象、内部候选状态、审阅方法或工作流记录。

生成的 `miniprogram/data/catalog.js` 是白名单数据模块，不含价格、宣传原文、账号或私有路径。`asset-manifest.json` 仅列纳入客户端的实际文件；`provenance.json` 记录输入hash、范围、网络调用0和未跑微信阶段。来源 URL 为惰性文字证据，客户端不请求这些 URL。

导出后必须重跑：

```sh
node --test preview-tests/*.test.js
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s preview-tests -p 'test_*.py' -v
python preview-tests/validate_static.py
```

导出器不下载任何东西、不转码、没有 OCR/模型调用，不恢复受限网络采集。
