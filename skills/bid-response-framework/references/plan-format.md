# 按采购原件编排的计划格式

`inspect` 按 `word/document.xml` 的直接子块给出序号（从 0 起），包括嵌套表格所在的外层表、段落和内容控件。两个不同发布版本的序号不能混用。打开原文件核对浮框、条款上下文、表头行和附件。计划顶层必含 `source_sha256`（inspect 输出的完整值）和有序 `blocks`。可附 `strategy`、`project` 以及 `review`（需告知用户的附件和条件）。所有下列数字仅示意。

```json
{
  "source_sha256": "替换为当前采购原件的 SHA-256",
  "strategy": "reviewed-plan",
  "review": [{"source_index": 91, "text": "最终报价表由授权代表手持单独提交。"}],
  "blocks": [
    {"kind": "cover_badge", "text": "正本/副本"},
    {"kind": "cover_space", "count": 2},
    {"kind": "cover_meta", "text": "经核对的项目名称"},
    {"kind": "cover_space", "count": 1},
    {"kind": "cover_meta", "text": "标包名称（如有）：/"},
    {"kind": "cover_space", "count": 1},
    {"kind": "cover_meta", "text": "项目编号：________"},
    {"kind": "cover_space", "count": 4},
    {"kind": "title", "text": "响应文件"},
    {"kind": "cover_space", "count": 8},
    {"kind": "cover_field", "label": "投标人名称：", "value": "________"},
    {"kind": "cover_space", "count": 1},
    {"kind": "cover_field", "label": "投标人地址：", "value": "________"},
    {"kind": "cover_space", "count": 1},
    {"kind": "cover_field", "label": "日期：", "value": "________"},
    {"kind": "break"},
    {"kind": "toc"},
    {"kind": "break"},
    {"kind": "heading", "level": 1, "text": "商务部分", "source_index": 49},
    {"kind": "heading", "level": 2, "text": "报价函", "source_index": 57},
    {"kind": "copy", "start": 58, "end": 65},
    {"kind": "heading", "level": 1, "text": "服务方案", "source_index": 24},
    {"kind": "slot", "text": "【待编】逐项回应招标文件所列的服务内容。"},
    {"kind": "heading", "level": 1, "text": "评标索引表", "source_index": 24},
    {"kind": "score", "index": 24, "header_row": 0}
  ]
}
```

- `copy` 闭区间按原序保留段落、表格、部分图框和签章文字。只取真实应答内容。跨章引用合同、报价或附件时单独指定范围，并确认引用版本。遇到保密、廉洁或表式条款，避免用文字摘要替代原文。
- 单表的 `copy` 可加 `"cells":[{"row":1,"col":0,"text":""}]` 清除样本填值。行列均从 0 起，按照 OOXML 的实有单元格计数，注意合并单元格。仅清空已核定的实例，保留固定采购参数、采购人字段和评分阈值。
- 固定表式需要扩展空白明细行时，`row_order` 列出原表的行号，按所列顺序复制。随后 `cells` 的行号指向复制后的表。保留表头，核对纵向合并边界。单元格含多段时，可用 `{"row":0,"col":0,"paragraph_match":"项目编号：","text":"项目编号：……"}` 仅更新与原文字唯一匹配的普通段落；签章线、其他段落及非文本对象须保留。
- `score` 在原表附可回填页码列，`header_row` 指真正的表头行。资格审查、废标情形和价格分值汇总不应误作技术细分。
- `heading` 的 `level` 从 1 起。正式文件所要求的首页或卷间分页用 `break`，应答标题和普通段落分开。使用 `slot` 给材料留位。只有原件无固定格式且确有证明需求时，才以 `{"kind":"table","rows":[["字段一","字段二"],["",""]]}` 补自拟可编辑表。
- 计划只存放在临时目录，传给 `generate.py --plan`。如果执行后发现误判，修计划并重建，不直接修 Word 文件来掩盖生成环节的失败。

当采购方明确禁止不同子项目合并编报时，在顶层使用 `parts`。主 `blocks` 是编制及递交清单，`parts` 中每项有唯一的 `name`（不含扩展名）和独立的 `blocks`。每件分别编排本项目的适用条件、可填写内容及签章位置。采购方给出的预算填在预算说明处，报价栏留空。

## 五类要求的原文复核

需要修正自动识别时，可在同一来源指纹的计划中增加 `metric_edits` 列表。类别值依次为 `redline`、`qualification`、`score`、`spec`、`format`，分别对应废标红线条款、强制资格要求、评分细则要点、商务技术规范和指定格式。新增用 `{"action":"add","category":"spec","part":"主件","locator":"原件中该段或表行的定位","text":"该位置的连续原文","label":"用于核对的短名称"}`。组件名及定位来自 `metric_source.read_units(source)`。删除用 `{"action":"drop","category":"spec","id":"汇总页条目的 data-id"}`。修订后通过默认入口重建，五张卡片按最终明细重新计数。
