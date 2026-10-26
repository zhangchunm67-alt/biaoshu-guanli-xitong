# 本地标书管理系统实现约定

本文件是开发接口约定。用户已选择完整材料管理、上传 Word、匹配、人工确认及导出。原始目录只读，所有运行数据在本目录 data/，本机 127.0.0.1 服务，无云服务。默认七个通用栏目，专用层初始化四行业及16栏目，迁移标识 special_v1。前端原生 HTML/CSS/JS，后端 Python 标准库 HTTP + SQLite，Word 使用 python-docx/Pillow。

## API

全部 JSON，错误 {error:中文说明}，POST/PATCH 要求同源；无跨源开放。文件上传 JSON base64（data 不带 data URL 前缀）。每项写入真实持久化。ID 为不透明字符串。

- GET /api/bootstrap -> {categories:[],materials:[],projects:[],exports:[],industries:[],stats:{}}
- GET /api/projects/:id -> 完整 project 对象（下述结构）
- POST /api/categories {name,aliases:[],layer:'common',enabled:true} -> category
- PATCH /api/categories/:id 修改 name,aliases,enabled（原栏目 id 不变）
- POST /api/industries {name} -> industry（同名行业不可重复）
- POST /api/materials {name,category_id,subject,version,years:[],valid_until,mode:'page'|'side_by_side'|'text',content,description,files:[{name,data}]} -> material
- PATCH /api/materials/:id 同上元数据（不含files），可含 file_order:[file_id], rotations:{file_id:顺时针角度}; 修改后取消相关项目确认
- POST /api/materials/:id/files {files:[{name,data}]} -> material
- GET /api/files/:file_id 图片原件（可用 ?thumb=1 缩略图），仅支持已登记 ID，不接收磁盘路径
- POST /api/projects {name,filename,data} 或 {name,use_sample:true} -> 完整 project（创建时扫描）
  - 接受 `.doc` / `.docx`，原文件及转换后的工作模板均不得超过 30 MB。实际为 OOXML 的 `.doc` 保持字节不变；OLE 旧版 Word 通过本机 LibreOffice 转换，拒绝宏、嵌入文件、加密和损坏文档，转换最多 60 秒。
  - project.template_import_kind 为 docx / docx_with_doc_suffix / converted_doc；template_hash 是工作模板指纹，template_source_hash 是原上传指纹。旧版/后缀不符文件额外保留 templates/<id>.original.doc，存储路径不公开。转换警示进入项目页面和导出报告；转换临时目录自动清理，失败不留下项目或模板副本。
- POST /api/projects/:id/scan {} -> 完整 project（重扫会清除确认）
- POST /api/projects/:id/text-materials {} -> 完整 project。图片材料核对后直接进入文字材料核对，由后端加载已启用的文书模板栏目及有未归档文字材料的栏目，不要求选择行业；重复进入保留已选材料、位置与备注。没有已启用文字栏目时返回空文字核对列表，仍可继续导出。界面流程为“上传标书 → 核对图片材料 → 核对文字材料 → 导出标书”；旧 `special-categories` 接口仅保留兼容，新界面不再调用。
- POST /api/projects/:id/location-preview {mapping_id,locator_id,material_ids:[],action} -> {preview_id,page_count,initial_page,target_pages:[],markers:[{page,x,y,kind,label}],title,description,template_name}。使用当前未保存选择，仅允许本项目核对项、位置及材料范围；不写项目、不清除确认。locator_id 允许空字符串，此时从第 1 页展示原文，target_pages 和 markers 为空；非空位置仍须属于当前项目。x/y 为页面左上角归一化坐标，page 从 1 开始，kind 为 after/cell/table。
- GET /api/projects/:id/location-preview/:preview_id/pages/:page -> image/png。只允许当前项目模板对应的缓存及有效页码；过期或无法可靠定位时返回明确错误，不用猜测页码。
  - 在临时 Word 副本的非空段落中添加不可见定位书签，映射至 PDF 页面后显示原页及独立标记层；源模板不变。空白单元格与表格后追加从实际 PDF 边框定位，避免书签撑高空白表格；无边框、边界不完整或无法可靠区分的复杂表格会明确报错。复用导出的正文锚点与空白表格选择规则。渲染在数据库锁外运行，最多 90 秒，最多 600 页 / 15000 段；缓存保留最近两份模板，页面图片按需生成，服务关闭清理。
- PATCH /api/projects/:id/mappings {mappings:[{id,locator_id,material_ids:[],action:'fill'|'missing'|'skip',confirmed:true|false,note}]} -> 完整 project。未知id拒绝；后端重新校验匹配。
- PATCH /api/projects/:id/special-categories {industry_ids:[],no_special_materials:false} -> 完整 project。通用核对完成后才能提交；后端将所选行业展开为其全部启用专用栏目。空行业必须显式 no_special_materials:true；未知、重复行业拒绝。保存后生成核对项，保留通用核对；栏目范围变化取消专用确认。兼容旧客户端的 category_ids，但两种参数不可同时提交。
- POST /api/projects/:id/export {mode:'draft'|'reviewed'} -> export。draft 不要求逐项确认，允许缺项/跳过并在报告/文档标明；所有模式拒绝材料或位置错误，reviewed 要求全部就绪且确认。
- GET /api/exports/:id/download -> docx
- GET /api/exports/:id/report -> 可下载 JSON 核对报告（含来源、结果、缺项；不要绝对路径）

## 对象

### 官方信用查询扩展

- `GET /api/credit-automation` 返回最近一次条件检测状态。
- `POST /api/credit-automation/check {subject}` 后台启动条件检测，返回 202。仅当前投标主体；拒绝客户端提交 URL、路径、来源或成功标志。
- `bootstrap.credit_automation` 同步返回 `policy, enabled, status, checked_at, run_id, subject, sources, message, materials`。
- 三个受管 common 栏目初始 disabled，固定 ID 为 `credit_dishonest / credit_tax_violation / credit_procurement_violation`；普通分类、材料接口不能绕过检测门槛。
- `sources` 每项包含 `id,name,url,status,reason,query_completed,subject_verified,result_verified,screenshot_verified`。失败不是“无记录”。
- 成功归档材料携带 `credit_evidence`：查询时间、主体、来源地址、结果状态、验证状态、run ID、截图 SHA-256；这些字段进入导出报告。
- 当前真实官网验证受阻，自动归档保持停用。三个来源的成功结果结构尚未完成实站验证，不能以模拟测试替代真实验收。

category: {id,name,aliases:[],layer:'common',enabled:true,sort_order}
material: {id,category_id,name,subject,version,years:[],valid_until,mode,content,description,revision,files:[{id,name,rotation:0,width,height,url}],created_at,updated_at}
project: {id,name,template_name,template_hash,created_at,updated_at,status,mappings:[],headings:[],warnings:[],exports:[]}
mapping: {id,category_id,category_name,title,locator_id,material_ids:[],action:'fill'|'missing'|'skip',confirmed:false,note,required_years:[],issues:[],status:'ready'|'missing'|'blocked'|'skipped',existing_content:false}
heading: {id,title,level,preview,kind:'heading'|'paragraph'|'placeholder'}
export: {id,project_id,project_name,filename,mode,created_at,filled_count,missing_count,skipped_count,download_url,report_url,warnings:[]}
industry: {id,name,created_at}

## engine.py 公开函数（无数据库依赖）

- scan_document(template_path,categories,materials,special_category_ids=()) -> {headings:[],mappings:[],warnings:[]}; 默认只匹配启用 common 栏目，显式选择的 special 分类纳入扫描但不自动选择材料；为每栏目至少保留一行，同标题多处需保留独立定位，不自动使用目录。定位 id 由原文档结构生成并保持稳定，禁止使用可变页码。
- validate_mappings(mappings,materials,headings,categories=None) -> 重新计算的 mapping 列表，category/year/file/expiry/subject 为强约束，缺失年度完整集合必须发现。material内部files包含path绝对路径，仅引擎使用，不给前端。
- export_document(template_path,output_path,mappings,materials,mode='draft') -> {results:[],filled_count,missing_count,skipped_count,warnings:[]}; 每次从原模板开始，按精确 locator_id 操作，禁止越界填充；draft 不要求确认，缺项插红色待补；reviewed 拒绝未确认、缺项和跳过。保持原内容，新增图片/文本，按材料顺序和file_order，应用单图旋转，不修改源图。标题定位只在当前节内，避免混到子节。设置Word更新目录域，明确不自动更新人工写死页码。

默认七栏目 id: legal_identity, authorization, business_license, audit_report, tax_social, company_profile, iso_cert。审计栏目名称“2023-2024年财务审计报告”，别名含“财务审计报告”“审计报告”；required_years 从原文财务提交条款及目标标题提取，不从栏目配置名称推断。明确“2024或2025”记录为 year_options 的替代集合，不强制全部年度；未指定时不默认2023/2024。材料年度只信years元数据，不能信别名绕过。材料 subject 非空（种子用北京腾宝科技有限公司）；其他主体阻止自动匹配。缺失有效期标为待核对提示，不伪称有效。

## 种子接入（server）

读取父目录 标书自动填充/示例/材料台账.xlsx 5条材料，严格沿E列文件顺序，自然排序；复制55张到data/materials，生成稳定id与来源相对路径metadata，不更改原件。2024审计附带会计师事务所营业执照只作为该报告附件。身份证两组对应身份证明/授权栏目，说明“证件附件，需核对模板证明或授权正文”。公司简介不虚构。税社保/公司简介/ISO无种子材料。保留原台账旋转（顺时针转换）。sample模板父目录填充测试文档.docx，用户点击使用示例才创建项目。

## 材料与已有项目联动

材料新增、修改或追加图片后，校验已有项目并清除受影响项的确认。读取旧项目时同步修复未选材料的自动缺项：唯一适用材料或年度不冲突的组合自动填入，多版本冲突转为待人工选择。校验继续使用主体、年度、有效期、文件和位置约束。已选材料及 skip 保持不变；用户改变 action 或 material_ids 后记录 auto_match:false，保护手动选择。未受影响的确认、备注、模板位置及历史导出保持不变。

## 材料保存前校验

POST /api/materials/validate 接收材料表单字段、可选 material_id 与 upload_count，返回 errors（按字段）、warnings（按字段）、project_warnings；不写入数据。新增和元数据保存复用字段校验，拒绝无效日期、已过期材料、主体不符、审计年度缺失、正文或图片为空。空有效期与临近到期在保存前提示。编辑既有材料时预览关联项目的校验结果；具体模板约束仍在项目匹配时校验。


## 专用层接口与边界

- GET /api/materials：专用材料分页。参数 industry_id、category_id、q、review_status、archived、page、page_size（最多50）。栏目查询包含跨行业关联；响应 items、total、page、page_size、counts。
- GET /api/materials/{id}：材料详情及有序图片、来源、核对事项。
- PATCH /api/materials/{id}：沿用材料编辑；增加 client_name、case_name、subject_kind、review_status（pending/checked/reference）、linked_category_ids、archived、restore_file_ids。移除保留原件，恢复仅限本材料已移除图片。
- bootstrap.materials 仅加载通用材料；专用候选列表分页读取，项目返回 selected_special_materials 供预览已选项。专用材料不参与通用自动选择，但已选专用材料的修改、归档、换页与旋转同样取消关联项目确认；不允许跨层移动材料。
- SpecialLibrary.import_special 使用稳定 import_key 和逐材料事务；已有记录不覆盖；失败回滚本材料。交付 scripts/import_special.py 支持从PNG ZIP及JSON清单恢复导入。
- 人员/合作方的实际主体、历史过期材料允许专用归档并提示；通用材料的主体、有效期及Word输出校验保持原规则。
- project 持久化展开后的 special_category_ids 与 special_selection_confirmed，响应中按栏目所属行业派生 special_industry_ids，供行业选择页回显。旧项目读取不自动扩展或改写原核对项；重新保存行业时按新范围展开。未完成选择的项目缺省为 []/false，禁止新导出。重扫保留所选分类，但清空映射调整与确认。
- mapping.layer 由服务端设置。专用校验接受主分类或 linked_category_ids，允许实际人员/合作方主体；缺主体、图片不可用、归档停用、资格过期或位置冲突阻止确认。历史业绩过期作为提醒；人工确认表示本项目已核验，不能自动修改材料库核对状态。
- 导出仅使用本项目选择的专用映射；核对报告记录所选分类、通用及专用材料来源。重复导出始终从原模板开始。

## 专用材料位置识别

- 选定行业时，使用完整标题、配置别名、业绩/人员/资质/授权标题规则自动识别位置；不自动选择专用材料或确认核对。优先实际投标文件格式区域，排除目录、评审条款、合同正文和普通表格单元格（显式占位符除外）。
- mapping.location_match 包含 status（matched/ambiguous/not_found/manual）、reason、candidates（locator_id/title/score/reason）。分数仅用于规则排序，不是准确率。多个相近候选或行业间位置冲突不自动落位。
- 原页预览和导出使用同一原文定位及节末锚点，专用材料附在本节表格与说明之后。
- special_locator_version 记录识别规则版本。旧项目首次读取时仅回填未确认、未跳过、未指定位置的专用项；保留已有映射 ID、已选材料、备注与人工位置。人工改选后标记 manual，当前版本不重复覆盖。


## 后台导出任务

`POST /api/projects/{id}/export-jobs` 接收 `{mode: "draft" | "reviewed"}`，返回 202 和任务；同一项目活动任务去重。`GET /api/export-jobs` 返回活动任务及最近 10 个完成/失败任务。任务包含 `status`、`progress`、`stage`、`remaining_seconds`（无法估算时为 null），完成后包含 `result` 下载信息。文档生成在单独线程执行，不持有应用数据库锁；仅任务更新和结果发布持锁。提交时冻结已验证材料记录，运行期间修改不会被结果覆盖。服务重启将未完成任务标记为失败，不宣称完成。旧同步导出接口保留兼容。

## 需求识别与原文位置（P0）

- recognition.py 分别生成材料需求与可写位置。project.requirements、mapping.requirement 含 status（required / conditional / optional / not_detected）、evidence（原文 text、source_locator、source_path、region）、required_years、year_options。识别状态属于规则判断，证据供用户展开核对；not_detected 不代表确定无需提交。
- 正文和表格单元格可提供需求证据。优先应答格式中的明确附件标签、实际表单和显式占位符；无正式位置时，同义名称或去除副本、复印件、盖章等修饰后对应的独立材料条目可直接作为位置，包括编制说明中的提交清单。评分表、合同条款、否定要求、其他主体证件不作为自动位置。at: 为按栏目区分的原始附件或材料条目，不使用可变页码。
- 附件/附表/格式编号、中文括号标题、6-5 等小节编号和并列材料清单限定写入范围。法人/代理人的独立证件表格分别定位；已有正文保持不变。清单条目直接在原段落后插入，表格内在同一单元格插入；重复清单按应答区域、匹配程度、原文顺序择一。location_match.method=entry 标记条目匹配，alternative_count 记录其他同类条目数量。多个独立专用应答栏目或跨行业共用位置仍要求核对；通用材料只在有需求且无对应条目或附件位置时生成文末区。
- 位置版本升级更新有自动匹配记录的旧位置及未定位项，并清除受影响确认；已有人工位置、跳过、手动材料及备注保留。无法证明是自动选择的旧位置保留。未来所有类别的人工位置调整均记录 location_match.status=manual。旧自动位置不再可靠或候选冲突时清空位置并提示核对。
- 商务提交的是待填充的投标文件框架。`document_toc.py` 在通用/专用栏目识别、预览和导出之前统一排除目录：Word 目录样式/链接/跨段落目录域（目录域结束标记所在段落如另有正文则保留），以及有目录标题或后续重复正文证据的手工目录。目录标题后的标题—页码表格同样排除；仅同名或短标题密集不作为删除正文的依据。两份独立正文继续保留各自定位，授权委托书正文标题支持“格式”后缀。
- 扫描返回 `excluded_locator_ids` 供旧项目升级：明确属于目录的位置不再进入选项；旧人工目录位置也取消定位并提示重新核对，材料、备注和处理方式保留。已知自动位置可迁至唯一且未被占用的正文；多个候选不猜选。导出重新识别原稿，拒绝沿用失效目录位置。未证实属于目录的普通材料清单保留原有填充能力。

## 共享分类内的材料校验

- `material_rules.py` 区分纳税和社保凭证；合并位置要求两类均覆盖。明确年月以材料名称/版本中的所属期核对，付款日期、打印日期及年份不能替代缴费月份；所属期支持“2025年1月至6月”“2025年1-6月”“2025年1至6月”等写法。月度重叠版本留给人工选择；年度、凭证类型或月份只能部分覆盖时仍自动预选唯一组合，缺少部分保留为问题。
- “近六个月”等相对期限缺少结构化投标截止日时不自动选材料，人工选定后仍提示核对所属期。
- `text_template` 在每个独立正文位置生成核对项，投标函、授权书、承诺函不可跨类型使用。文字模板仍须人工选择，不改写原有正文。
- 授权委托书标题/正文只作为文字模板位置。通用 `authorization` 图片栏目只定位到单独标注的被授权人/委托代理人身份证（护照）附件位或条目，包括授权书正文后的附件说明行；没有时记为缺项（标题“被授权人身份证附件”），不生成文末材料区。
- 小数编号（如4.1、4.2）隔开各自材料位置；共享分类新增正文位置在旧项目升级时补出核对项，已有材料、备注和人工决定保留。
- 原页预览按模板文件版本缓存指纹，按模板指纹和栏目配置缓存解析结果（各最多两份），逐卡片预览不重复解析模板；文件变化后重新校验指纹。
- `scripts/audit_matching.py --data-dir data --corpus 考题` 只读核查本地库和模板，在临时目录验证导出图片数量、目录不变及原稿指纹，结束自动清理。它不代表招标业务验收或可直接投标。
