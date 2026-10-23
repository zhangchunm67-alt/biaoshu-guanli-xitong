# 官方信用记录查询与截图

## 当前交付状态

用户选择“必须全自动，否则先不启用”。据此接入的是可运行的条件检测与截图脚本，以及通用材料中的状态卡片；**当前自动归档未启用，也没有取得三项无记录证明**。

2026-10-16 通过实际浏览器访问并验证：

| 查询项 | 官方入口 | 本次观察 |
| --- | --- | --- |
| 失信被执行人 | https://zxgk.court.gov.cn/shixin/ | 当前网络返回 403 / GeoBL，查询未完成 |
| 重大税收违法失信主体 | https://www.creditchina.gov.cn/zhuanxiangchaxun/zhongdashuishouweifaanjian/ | 提交北京腾宝科技有限公司后要求验证码，未处理 |
| 政府采购严重违法失信行为记录名单 | https://www.creditchina.gov.cn/zhuanxiangchaxun/zhengfucaigouyanzhongweifashixinmingdan/ | 提交同一主体后要求验证码，未处理 |

三个入口均来自信用中国“专项查询”。没有使用代理切换、验证码识别、第三方打码或隐藏数据接口。网站可访问性可能变化，界面显示的是最近一次本机检测结果。

## 系统内使用

进入“通用材料” → “无违法违规记录查询截图” → “检测全自动条件”。当前工作空间沿用北京腾宝科技有限公司这一投标主体。检测在后台运行，不阻塞其他材料操作；一般在 100 秒内返回，无法完成时记录失败原因。

功能使用 3 个独立受管栏目：`credit_dishonest`、`credit_tax_violation`、`credit_procurement_violation`。这三个栏目停用时不参与 Word 匹配，也不计入原 7 栏目的待补清单。普通编辑接口不能直接启用栏目或伪造其材料。

只有三个来源同时满足以下条件，后端才会归档并启用：

1. 固定官方来源、本次查询主体一致，查询确实完成。
2. 独立结果区域明确显示未检出记录，不能由空白表格或 HTTP 200 推断。
3. 截图与查询结果均已验证，PNG 内容和 SHA-256 校验一致。
4. 结果来自本机后台脚本，前端不能提交“检测成功”。

查询出现记录时保留真实状态并停止无记录归档。检测失败或重新检测时，相关栏目停用，旧信用材料标记为需复核，关联项目确认失效。业务材料只有通过完整门槛后才生成；诊断图片不会进入标书。

## 独立脚本

需要 Node.js、Playwright 和 Chrome（或 Playwright Chromium）。本机优先复用 Codex 已安装的依赖和系统 Chrome，不自动下载软件。其他电脑可以在本目录运行 `npm install`，没有 Chrome 时再按 [Playwright 官方说明](https://playwright.dev/docs/browsers) 安装 Chromium。

```sh
node scripts/credit_capture.cjs \
  --subject '北京腾宝科技有限公司' \
  --output-dir "$PWD/data/diagnostics/credit-manual-20261016"
```

每次使用一个新的空目录。独立脚本允许传入其他企业名称；管理系统当前只支持本工作空间的投标主体。脚本将返回 JSON，并在输出目录保存 `report.json` 和能够取得的原始网页 PNG。报告区分 `no_records`、`records_found`、`blocked`、`unverified` 和 `error`。脚本正常退出仅表示报告写入成功，不代表查询成功。

诊断目录含查询对象和网页截图，仅供本地核查，不作为无违法违规证明。三个来源的真实成功结果结构均因访问限制或验证码未完成验证，成功结果适配器在代码层保持关闭。即使官网取消障碍，仍需验证真实的有记录、无记录页面及本次查询完成信号，再更新适配器；不能仅手动修改开关启用。

## 测试与证据边界

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_credit_capture.cjs
```

隔离测试覆盖验证码、403、空结果、旧结果、主体不一致、官方地址检查、后台运行、重启持久化、失败不归档和成功条件下的归档逻辑。成功支路使用明确标识的测试数据，不等同于三个真实官网已完成全自动查询。官方截图采集方式参照 [Playwright 截图说明](https://playwright.dev/docs/screenshots)。

通过查询门槛也仅能说明查询时点的官方页面结果；投标要求的查询日期、适用性及正文签章仍由标书核对流程处理。
