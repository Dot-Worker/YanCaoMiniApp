# 公开单产品图片证据：只读自包含验证

此入口仅提供只读的公开证据验证：验证公开的固定规则、单产品 610 字段快照与 9 张原图片，**不提供公开的端到端 import / bundle 复现**。

在仓库根执行：

```sh
PYTHONPATH=tools python -m yanyue.public_image_evidence \
  --snapshot tools/tests/fixtures/public-reviewed/product-610-fields.json \
  --images WeChatAPP/miniprogram/assets/reviewed
```

依赖为既有离线测试依赖；不需要数据库、完整 82 产品 checkpoint、内部人工审核 manifest 或下载元数据。这里没有网络、浏览器、state-dir、下载或写库参数。失败返回 2 且输出脱敏 JSON。

验证内容：

- 固定 `reviewed_image_public_policy_1` 的公开 profile，SHA256 `0c6be63aaaa0d8933e199208996a1ff252bfb19f3cb7cb54ef484d2083a488dc`；不接受调用者替换 profile。
- 单 610 原字段快照字节 SHA256 `3448d16e48149b4d15b1b1ad6d34b26b92e73ecc24bd6f35db09c0f627a39693`、规范来源 URL、原时间精度及严格字段契约。
- 恰好 9 张图片：规范原 URL 与快照真实引用集合相等、内容寻址文件名、字节数、SHA256、完整解码后的格式/MIME/尺寸/帧数/模式。
- 已公开的 6 个数值槽位和 3 个条码变体，逐一绑定原标签、来源、时间、字段上下文单位及图像序号；包装数量的原图未打印单位时 `raw_unit=null`，不伪称图片上有“支/盒”。
- 目录/文件描述符防路径替换；拒绝 symlink、hardlink、FIFO、缺图、额外条目、错图或超长输入。

输出中的 `published_reviewed_facts` 是已经审阅后发布的固定规则，不是本次重新 OCR 或人工看图的结果。该入口不生成或冒充旧私有 `image_review` 对象，不验证未发布的私有审核记录内容，不把数据写入库、不标 downloaded、不碰来源暂停。`new_visual_review_performed`、`private_review_provenance_validated`、`state_changed`、`manufacturer_verified` 均为 false。

来源仍为用户贡献资料，用户声明原文和图像有完整使用授权，不代表制造商认证或将第三方内容套用代码许可。此验证不表示全站采集完成、来源恢复访问、图片网络试点成功或微信端完整验收。

## 离线测试

```sh
PYTHONPATH=tools python -m unittest discover -s tools/tests -p 'test_public_image_evidence.py' -v
```

测试覆盖公开文件和 9 图的无网络/无 SQLite/不调用导入 API 验证、单位和条码变体、篡改、缺失/额外、symlink/hardlink/FIFO、描述符目录替换及安全失败输出。该入口不修改既有私有导入/bundle 边界或任何生产状态。
