# Iteration 01：六家真实企业案例诊断

日期：2026-09-28

## 范围与判读原则

检查六份年报 PDF、manifest/README、runtime/real_cases 的 metadata、technology/industry analysis 与 composite evaluation，以及旧 v0.9 Chroma。六份 PDF SHA 均符合 manifest；有可抽取文本：寒武纪239/239、中芯156/159、新松218/218、埃斯顿218/218、CATL229/229、蔚来414/414。主题词页码是定位线索，不等同指标证据。旧记录没有完整 prompt input 或逐 query 候选快照；五家使用 v0.9/v1.0 prompts，CATL 使用 v1.1。另用保存的 v0.9 Chroma 和当前 query 只读查 top-3 页码，仅作当前召回风险迹象，不冒充旧运行输入。

现有状态：寒武纪 partially_scored（82.85/65.00/75.71）；埃斯顿 partially_scored（77.69/69.00/74.21）；新松 technology=74.25、industry/overall=null；中芯、CATL v1.1、蔚来全部 null、insufficient_evidence。CATL v1.0 曾 pipeline_failed，v1.1 结构化成功后为 insufficient_evidence，两轮不可混作重复结果。

## 未评分诊断

表中指标列表内每项均为未评分。原文主题词命中只说明可定位相关内容，不自动满足指标；保持 null，不降低评分阈值。

| 企业 | 指标与状态 | 原因分类 | 支持证据与边界 | 建议修复 |
|---|---|---|---|---|
| 寒武纪 | 产业 market_potential、industry_growth、policy_environment 均 null | 不能区分未召回与模型判断不足；不能判原文缺失 | Industry 输出仅保存 E10/p25、E14/p26，均用于竞争位置。年报市场规模词 p16、市场份额 p26、政策 p12/p26；当前 Chroma market/competition top-3 含 p26、policy top-3 未含 p12。Provider 输入快照缺失 | 固定逐 query chunk ID/摘录哈希；核查 p16/p26 对各指标的事实充分性；把实体清单风险和产业政策分开 |
| 中芯国际 | 技术 technical_autonomy、innovation_capability、intellectual_property、technical_maturity 均 null | 高度怀疑中英 query 错配导致召回不足，也可能是模型拒答，无法单因归责 | 英文年报 patent/IP 词见 PDF p21、23、45、74、111；R&D p16、18、26。Technology 输出正式引用页18/28/47/91/129/155/156；当前中文“专利/知识产权” top-3 为 p129/51/126。Innovation rationale 出现孤儿 E1，但该项结构化 evidence=[]，E1 只在摘要 | 加英文/双语 query、BM25/hybrid 对照；存实际送模型的块；自由文本证据号也做校验 |
| 中芯国际 | 产业 market_potential、industry_growth、competitive_position、policy_environment 均 null | 已无结构化引用；召回和抽取未分开 | 原文 market share p19/20/24/26，也有 revenue/growth、policy/subsidies 词；Industry evidence=0。当前中文 query top 页 market 31/18/34、growth 113/143/76、competition 18/65/29、policy 65/94/96；原始模型输入缺失，不能判断内容是否达指标标准 | 人工定位份额、增长、政策适用事实并做页级回归；不满足定义就继续 null |
| 新松机器人 | 产业四项 industry 指标均 null | 原文有行业主题，输出无引用；可能是召回不足或模型未形成可用证据 | 年报市场规模 p13/14、市场份额 p23/41、政策 p9/11/18 有词；industry evidence=0。当前 Chroma market top-3 含 p14/22，competition 含 p22/41，但当时输入未存 | 固定原文页并分别跑召回/抽取；区分产量、行业增速、企业增长，不能把行业份额变化归给新松 |
| 埃斯顿 | 技术 intellectual_property=null | 较明确的召回/模型证据选择缺口；原文缺失不成立 | 年报专利主题 p15；Agent 输出 E1/p14、E3/p15、E8/p33；E3 摘录不含专利词且 supports 无 IP。当前 IP query top-3 p92/12/197，未含 p15；模型 rationale 说检索片段无专利 | p15 设固定召回回归；查表格切分和 query，核实专利与核心技术关联后再判断 |
| 宁德时代 | 技术四项均 null | 有限技术线索不足以评分；剩余区别是召回/模型未能判断 | 年报核心技术 p13、自主研发 p15/16、研发投入 p24/25、专利 p16/45 有词。Agent 8 条证据多为风险；E5/p74 只有降碳专利/技术，不能代替公司整体 IP、创新或成熟度；autonomy/maturity 无证据 | 人工核 p13–16、24–25、45、74 的具体事实和产品关系，保存未引用候选；不外部补资料冒充旧输入 |
| 宁德时代 | 产业四项均 null | Industry 输出无引用；无法区分未召回、模型拒答、输入片段不足 | industry evidence=[]，schema 成功不等于事实充分。原文销量 p11/15/17、政策 p11–13、补贴 p53/190 有主题；旧 Chroma query top-k 有相关页，不证明企业级证据充分 | 保存模型输入块；逐项检查市场、行业、份额、适用政策，记录“未召回”或“已召回但证据不足” |
| 蔚来 | 技术四项均 null | 中文查询对英文年报的召回不足是主要可检验风险，尚未完全定因 | 年报 patent/IP p65–67、R&D p27–28、technology p6/25/30 有词；Technology 输出 8 块多在 p6/200/330/367/380/410，四项结构化证据为空。当前中文 query top-3 多未覆盖上述主题页。414 页有文本 | 增加双语召回回归，检查 p65–67/p27–28 的切片和召回；把未召回与召回后拒答分开记 |
| 蔚来 | 产业四项均 null | Industry 无引用；召回/抽取不可分 | 原报告有 revenue、vehicle sales、market share、policy/subsidies 英文词；industry evidence=[]，当时模型输入块未留 | 页级标注交付量、收入增长、份额、补贴/监管；双语固定 query 回归，守住公司和行业口径 |

## 结论与后续采证

1. 年报存在且 hash 一致；多项 rationale 只代表模型收到或引用的片段范围，不能推广为整份输入材料不存在事实。
2. 中芯、蔚来是英文年报配固定中文 char-ngram query 的明显召回风险；埃斯顿专利 p15 是定位明确的候选问题。寒武纪、新松、CATL 即使主题被召回，企业级数值和适用关系仍需人工核验。
3. 下一轮按案例留存：解析页/chunk → 每条 query top-k → 实际 Provider 输入 → 结构化 E IDs → PDF 页原文对照。先离线拆分，再考虑少量模型复验；不补资料掩盖失败。

补充：SMIC PDF 有 3/159 页无可抽取文本；这几页可能为空白或图片，未做视觉复核，故不能完全排除解析遗漏。可获取的英文主题页已确认词项存在，但主题词命中不代表评分充分。另发现 evaluation/run_real_cases.py 会重写 data/real_cases/human_review_v10.csv 并清空人工评价字段；执行该付费 runner 前应先审查输出写入行为并备份标注。
