# Iteration 03：8 题证据人工复核包

> 所有下列文字均来自仓库已保存 PDF 和离线检索候选。当前标签均为自动候选、**待人工确认**；本包不是 gold，也不证明结论语义正确。此对照运行前未用留出组调查询或 RRF 参数；本轮已经查看留出结果并据此撰写配置建议，后续不得再把它称为完全未见测试集。

页码分开记录：PDF 实际页序号按从 1 开始；印刷页码只用可提取页脚规则尝试识别，无法读取时标明待核对。不同方法的同页片段仅供人工判断，不因页码一致自动视为目标原文已召回。

## smic_competitive_position_en · smic · competitive_position

- 问题：What disclosed facts describe the company's competitive position in its industry?
- 指标：`industry.competitive_position`；语言：en；分组：development
- 源文件：`data/real_cases/smic/sources/annual_report.pdf`；SHA-256：`b184a848a948b236497cabddc4d3c8bf943abeeaeccd2bb4e8dd4a2785213eca`
- 原标注状态：`candidate_unconfirmed`；来源：`evaluation/retrieval_eval_cases.json` 候选标注；人工确认：**待确认**。
- 待确认原因：Known Chinese-query/English-report mismatch regression; human must confirm ranking context and exact chunk support.
- 是否需要多个片段共同支持：当前标注认为否；人工可更正

### 候选片段 1（分组 `position`）

- PDF 实际页序号：**21**；可识别的印刷页码：**19**
- 候选原文（匹配规则：NFKC/大小写折叠/移除空白；命中源页：是）：
  > SMIC ranks the second globally and the first among the enterprises in Chinese Mainland.
- 必要上下文（空白归一后的源页片段，约前后各 170 字）：
  > land, with leading manufacturing capability, manufacturing scale and comprehensive services. According to the global pure-play foundries’ latest published sales in 2024, SMIC ranks the second globally and the first among the enterprises in Chinese Mainland. 3. Development of new technologies, new industries, new sectors and new models during the reporting period as well as their future development trends In recent years, the
- 与当前解析 chunk 的关系：完整原文包含于 chunk `bf4af6886813338566036b78`。
- 机器初审关系：**支持候选 / 待人工确认**。初审理由：原句可在 PDF 页文本定位；事实范围、主体、时间、单位和是否足以支持该指标尚未人工核定。
- 人工复核关系（选一）：`[ ] 支持`　`[ ] 部分支持`　`[ ] 矛盾`　`[ ] 不足`。

- 原向量+原 query：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。
- 原向量+通用双语 query：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。
- BM25+通用双语 query：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。
- RRF+通用双语 query：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。

- 其他等价证据/矛盾证据：**未穷尽；需复核年报相邻页和其他页面。**
- 建议标签：`支持`（仅为自动初审建议，不能当作人工标注）；人工标签：`待确认`；待确认说明：核对上下文、事实主体与指标相关性，必要时增加共同支持片段。

## smic_intellectual_property_en · smic · intellectual_property

- 问题：What verifiable figures describe the company's patent or intellectual-property portfolio?
- 指标：`technology.intellectual_property`；语言：en；分组：development
- 源文件：`data/real_cases/smic/sources/annual_report.pdf`；SHA-256：`b184a848a948b236497cabddc4d3c8bf943abeeaeccd2bb4e8dd4a2785213eca`
- 原标注状态：`candidate_unconfirmed`；来源：`evaluation/retrieval_eval_cases.json` 候选标注；人工确认：**待确认**。
- 待确认原因：Same English-language source regression; confirm table headers and whether annual additions and cumulative totals are represented correctly.
- 是否需要多个片段共同支持：当前标注认为否；人工可更正

### 候选片段 1（分组 `portfolio`）

- PDF 实际页序号：**21**；可识别的印刷页码：**19**
- 候选原文（匹配规则：NFKC/大小写折叠/移除空白；命中源页：是）：
  > Invention patents 614 471 18,216 12,112
- 必要上下文（空白归一后的源页片段，约前后各 170 字）：
  > ring the reporting period: Addition during the year Accumulative number Number of Number of Number of Number of applications rights obtained applications rights obtained Invention patents 614 471 18,216 12,112 Utility model patents 51 43 1,892 1,852 Layout design rights – – 94 94 Total 665 514 20,202 14,058 3. Analysis of R&D costs in USD’000 Year ended December 31, 2024 as com
- 与当前解析 chunk 的关系：完整原文包含于 chunk `b32602425be529ef5089adf3`。
- 机器初审关系：**支持候选 / 待人工确认**。初审理由：原句可在 PDF 页文本定位；事实范围、主体、时间、单位和是否足以支持该指标尚未人工核定。
- 人工复核关系（选一）：`[ ] 支持`　`[ ] 部分支持`　`[ ] 矛盾`　`[ ] 不足`。

- 原向量+原 query 的候选片段：`b806495c9294fbecc86c050c` p21：ance differentiation. Meanwhile, the market demand is becoming more diversified. The enterprises pursue not only smaller transistor structures vertically but also the derivative platforms establishment by utilizing exist
- 原向量+通用双语 query：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。
- BM25+通用双语 query 的候选片段：`45259c3232d25ced430f4414` p21：r 8-inch and 12-inch, offering “one-stop” wafer foundry and technical services. In 2024, multiple platform projects have been developed as planned. Please refer to the following information on R&D ongoing projects for de
- RRF+通用双语 query：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。

- 其他等价证据/矛盾证据：**未穷尽；需复核年报相邻页和其他页面。**
- 建议标签：`支持`（仅为自动初审建议，不能当作人工标注）；人工标签：`待确认`；待确认说明：核对上下文、事实主体与指标相关性，必要时增加共同支持片段。

## nio_intellectual_property_en · nio · intellectual_property

- 问题：What verifiable facts describe the company's intellectual-property assets and protection measures?
- 指标：`technology.intellectual_property`；语言：en；分组：development
- 源文件：`data/real_cases/nio/sources/annual_report.pdf`；SHA-256：`2dfeeb976b50766c6555b46999519f0f70659756891700291565f8870068bbb3`
- 原标注状态：`candidate_unconfirmed`；来源：`evaluation/retrieval_eval_cases.json` 候选标注；人工确认：**待确认**。
- 待确认原因：Known Chinese-query/English-report mismatch; confirm this statement is sufficient for the chosen indicator question.
- 是否需要多个片段共同支持：当前标注认为否；人工可更正

### 候选片段 1（分组 `assets`）

- PDF 实际页序号：**67**；可识别的印刷页码：**66**
- 候选原文（匹配规则：NFKC/大小写折叠/移除空白；命中源页：是）：
  > We have invested significant resources to develop our own intellectual property.
- 必要上下文（空白归一后的源页片段，约前后各 170 字）：
  > cret protection and confidentiality agreements, and technology license agreements with our employees, business constituents and others to protect our proprietary rights. We have invested significant resources to develop our own intellectual property. Failure to maintain or protect these rights could harm our business. In addition, any unauthorized use of our intellectual property by third parties may adversely affect
- 与当前解析 chunk 的关系：完整原文包含于 chunk `a9c406ba8d113d53b2652f41`。
- 机器初审关系：**支持候选 / 待人工确认**。初审理由：原句可在 PDF 页文本定位；事实范围、主体、时间、单位和是否足以支持该指标尚未人工核定。
- 人工复核关系（选一）：`[ ] 支持`　`[ ] 部分支持`　`[ ] 矛盾`　`[ ] 不足`。

- 原向量+原 query：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。
- 原向量+通用双语 query 的候选片段：`909e2bb2ae40ef897fb87c56` p67：trademark and trade secret laws and contractual restrictions on disclosure and usage to protect our intellectual property rights. Despite our efforts to protect our proprietary rights, third parties may attempt to copy o；`e4726116e9179b471897df71` p67：trade secret protection and confidentiality agreements, and technology license agreements with our employees, business constituents and others to protect our proprietary rights. We have invested significant resources to 
- BM25+通用双语 query 的候选片段：`71ffea317ee9ac3e59dfc94d` p67：PART I We may not be able to prevent others from unauthorized use of our intellectual property, which could harm our business and competitive position. We regard our trademarks, service marks, patents, domain names, trad；`a134ba87ea1c320f48472c4e` p67：eveloping technologies that are similar or that achieve results similar to ours. The intellectual property rights of others could also bar us from licensing and exploiting any patents that issue from our pending applicat
- RRF+通用双语 query 的候选片段：`71ffea317ee9ac3e59dfc94d` p67：PART I We may not be able to prevent others from unauthorized use of our intellectual property, which could harm our business and competitive position. We regard our trademarks, service marks, patents, domain names, trad；`a134ba87ea1c320f48472c4e` p67：eveloping technologies that are similar or that achieve results similar to ours. The intellectual property rights of others could also bar us from licensing and exploiting any patents that issue from our pending applicat

- 其他等价证据/矛盾证据：**未穷尽；需复核年报相邻页和其他页面。**
- 建议标签：`支持`（仅为自动初审建议，不能当作人工标注）；人工标签：`待确认`；待确认说明：核对上下文、事实主体与指标相关性，必要时增加共同支持片段。

## estun_intellectual_property_zh · estun · intellectual_property

- 问题：年报披露了哪些可核验的专利或知识产权数量？
- 指标：`technology.intellectual_property`；语言：zh；分组：development
- 源文件：`data/real_cases/estun/sources/annual_report.pdf`；SHA-256：`ccebb48578e1fe9b1a28e185d7fe8d3b61b0e7019a4ba903000724ce13753f35`
- 原标注状态：`candidate_unconfirmed`；来源：`evaluation/retrieval_eval_cases.json` 候选标注；人工确认：**待确认**。
- 待确认原因：Known p15 retrieval regression; human must confirm that the page text supports the question and that parser chunks preserve the exact span.
- 是否需要多个片段共同支持：当前标注认为否；人工可更正

### 候选片段 1（分组 `portfolio`）

- PDF 实际页序号：**15**；可识别的印刷页码：**15**
- 候选原文（匹配规则：NFKC/大小写折叠/移除空白；命中源页：是）：
  > 截至2024 年12 月31 日，公司共有软件著作425 件； 授权专利590 件，其中发明专利252 件。
- 必要上下文（空白归一后的源页片段，约前后各 170 字）：
  > 一支以国际行业专家、江苏省双创领军人才、海外留 学高层次人才等为主的高层次研发团队，具有健全的研发组织管理体系，形成了自动化创新团队、高工技术团队和专家学 术团队三大人才梯队。公司与多个国内外知名大学进行研发合作，强有力的技术团队是公司能够进行自主研发、不断技术 创新的保障。 报告期内，公司共新增软件著作权95 件，新增授权专利69 件。截至2024 年12 月31 日，公司共有软件著作425 件； 授权专利590 件，其中发明专利252 件。已经申请尚未收到授权的专利有156 件。截至报告期末，公司共有员工3572 人， 其中研发及工程技术人员1032 名，占员工总数的28.89%。报告期内研发投入5.03 亿元，占收入比例为12.55%。 （五）持续提升产品质量，确保高品质产品的交付 公司坚持“以客户为中心”的核心理念，专注生产高质量产品，通过建立严苛的品控体系与全流程追溯机
- 与当前解析 chunk 的关系：完整原文包含于 chunk `b75b68d6c88d77240a01c1ff`。
- 机器初审关系：**支持候选 / 待人工确认**。初审理由：原句可在 PDF 页文本定位；事实范围、主体、时间、单位和是否足以支持该指标尚未人工核定。
- 人工复核关系（选一）：`[ ] 支持`　`[ ] 部分支持`　`[ ] 矛盾`　`[ ] 不足`。

- 原向量+原 query 的候选片段：`ae9680b0166001db6327d95e` p15：持续拓宽全球化布局。在金属加工领域，提供涵盖 激光切割、多轴联动加工等全产业链智能化方案，支持多种复杂焊接工艺。在高端重载领域，埃斯顿成功推出700kg 重负 载机器人，打破技术封锁，入选工信部《首台（套）重大技术装备推广应用指导目录》，弥补国产重载机器人空白。 （四）持续高研发投入形成的技术领先和创新优势 公司秉承“从跟随到超越”的战略目标，研发投入持续多年保持占销售收入的10%左右，通过收购整合及外引内培， 奠定了公司保持技术创新领；`a7549a20b02cdb8e45b977c3` p15：自动化创新团队、高工技术团队和专家学 术团队三大人才梯队。公司与多个国内外知名大学进行研发合作，强有力的技术团队是公司能够进行自主研发、不断技术 创新的保障。 报告期内，公司共新增软件著作权95 件，新增授权专利69 件。截至2024 年12 月31 日，公司共有软件著作425 件； 授权专利590 件，其中发明专利252 件。已经申请尚未收到授权的专利有156 件。截至报告期末，公司共有员工3572 人， 其中研发及工程技术人员103
- 原向量+通用双语 query：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。
- BM25+通用双语 query 的候选片段：`a7549a20b02cdb8e45b977c3` p15：自动化创新团队、高工技术团队和专家学 术团队三大人才梯队。公司与多个国内外知名大学进行研发合作，强有力的技术团队是公司能够进行自主研发、不断技术 创新的保障。 报告期内，公司共新增软件著作权95 件，新增授权专利69 件。截至2024 年12 月31 日，公司共有软件著作425 件； 授权专利590 件，其中发明专利252 件。已经申请尚未收到授权的专利有156 件。截至报告期末，公司共有员工3572 人， 其中研发及工程技术人员103
- RRF+通用双语 query 的候选片段：`a7549a20b02cdb8e45b977c3` p15：自动化创新团队、高工技术团队和专家学 术团队三大人才梯队。公司与多个国内外知名大学进行研发合作，强有力的技术团队是公司能够进行自主研发、不断技术 创新的保障。 报告期内，公司共新增软件著作权95 件，新增授权专利69 件。截至2024 年12 月31 日，公司共有软件著作425 件； 授权专利590 件，其中发明专利252 件。已经申请尚未收到授权的专利有156 件。截至报告期末，公司共有员工3572 人， 其中研发及工程技术人员103

- 其他等价证据/矛盾证据：**未穷尽；需复核年报相邻页和其他页面。**
- 建议标签：`支持`（仅为自动初审建议，不能当作人工标注）；人工标签：`待确认`；待确认说明：核对上下文、事实主体与指标相关性，必要时增加共同支持片段。

## cambricon_market_potential_zh · cambricon · market_potential

- 问题：材料披露了哪些行业需求、市场规模或增长依据？
- 指标：`industry.market_potential`；语言：zh；分组：heldout
- 源文件：`data/real_cases/cambricon/sources/annual_report.pdf`；SHA-256：`87f63aad82dc43fd0613de60bb72e3c88fcacffb9e074767c947d4bd76cb4d7b`
- 原标注状态：`candidate_unconfirmed`；来源：`evaluation/retrieval_eval_cases.json` 候选标注；人工确认：**待确认**。
- 待确认原因：Heldout candidate; confirm the cited market estimate and distinguish third-party forecast from company fact.
- 是否需要多个片段共同支持：当前标注认为否；人工可更正

### 候选片段 1（分组 `market_size`）

- PDF 实际页序号：**16**；可识别的印刷页码：**未从可提取页脚文字识别；需人工核对**
- 候选原文（匹配规则：NFKC/大小写折叠/移除空白；命中源页：是）：
  > Gartner 的报告预测，2027 年，全球人工智能芯片的市场的规模预计将达到1,194 亿美元。
- 必要上下文（空白归一后的源页片段，约前后各 170 字）：
  > 算量、高并发度、访存频繁的特 点，且不同子领域所涉及的运算模式具有高度多样性，对于芯片的微架构、指令集、制造工艺甚 至配套系统软件都提出了巨大的挑战。 与CPU、GPU 等芯片相比，通用型智能芯片能够更好地匹配和支持人工智能算法中的关键运算 操作，在性能和功耗上存在显著优势。根据最新的市场研究，人工智能芯片的市场规模正处于快 速增长之中。Gartner 的报告预测，2027 年，全球人工智能芯片的市场的规模预计将达到1,194 亿美元。 （2）主要技术门槛： 集成电路设计行业属于技术密集型行业，而智能芯片作为集成电路领域新兴的方向，在集成 电路和人工智能方面有着双重技术门槛。 人工智能运算常常具有大运算量、高并发度、访存频繁的特点，且不同子领域（如视觉、语 音与自然语言处理）所涉及的运算模式具有高度多样性，对于芯片的微架构、指令集、制造工艺 以及配套系统软件都提出了巨大的
- 与当前解析 chunk 的关系：完整原文包含于 chunk `67d423c38cf41a380185f2a4`。
- 机器初审关系：**支持候选 / 待人工确认**。初审理由：原句可在 PDF 页文本定位；事实范围、主体、时间、单位和是否足以支持该指标尚未人工核定。
- 人工复核关系（选一）：`[ ] 支持`　`[ ] 部分支持`　`[ ] 矛盾`　`[ ] 不足`。

- 原向量+原 query：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。
- 原向量+通用双语 query 的候选片段：`8a4c1ae78d3b8172bccf9cec` p16：前所未有的速度增长。人工智能运算常常具有大运算量、高并发度、访存频繁的特 点，且不同子领域所涉及的运算模式具有高度多样性，对于芯片的微架构、指令集、制造工艺甚 至配套系统软件都提出了巨大的挑战。 与CPU、GPU 等芯片相比，通用型智能芯片能够更好地匹配和支持人工智能算法中的关键运算 操作，在性能和功耗上存在显著优势。根据最新的市场研究，人工智能芯片的市场规模正处于快 速增长之中。Gartner 的报告预测，2027 年，全球人工智能芯
- BM25+通用双语 query 的候选片段：`8a4c1ae78d3b8172bccf9cec` p16：前所未有的速度增长。人工智能运算常常具有大运算量、高并发度、访存频繁的特 点，且不同子领域所涉及的运算模式具有高度多样性，对于芯片的微架构、指令集、制造工艺甚 至配套系统软件都提出了巨大的挑战。 与CPU、GPU 等芯片相比，通用型智能芯片能够更好地匹配和支持人工智能算法中的关键运算 操作，在性能和功耗上存在显著优势。根据最新的市场研究，人工智能芯片的市场规模正处于快 速增长之中。Gartner 的报告预测，2027 年，全球人工智能芯
- RRF+通用双语 query 的候选片段：`8a4c1ae78d3b8172bccf9cec` p16：前所未有的速度增长。人工智能运算常常具有大运算量、高并发度、访存频繁的特 点，且不同子领域所涉及的运算模式具有高度多样性，对于芯片的微架构、指令集、制造工艺甚 至配套系统软件都提出了巨大的挑战。 与CPU、GPU 等芯片相比，通用型智能芯片能够更好地匹配和支持人工智能算法中的关键运算 操作，在性能和功耗上存在显著优势。根据最新的市场研究，人工智能芯片的市场规模正处于快 速增长之中。Gartner 的报告预测，2027 年，全球人工智能芯

- 其他等价证据/矛盾证据：**未穷尽；需复核年报相邻页和其他页面。**
- 建议标签：`支持`（仅为自动初审建议，不能当作人工标注）；人工标签：`待确认`；待确认说明：核对上下文、事实主体与指标相关性，必要时增加共同支持片段。

## siasun_competitive_position_zh · siasun · competitive_position

- 问题：材料披露了哪些可核验的市场份额、竞争优势或客户应用事实？
- 指标：`industry.competitive_position`；语言：zh；分组：heldout
- 源文件：`data/real_cases/siasun/sources/annual_report.pdf`；SHA-256：`178032bdc38fe00aa2a33dc767db72d920ee49adbde752569a0ea56fa963addf`
- 原标注状态：`candidate_unconfirmed`；来源：`evaluation/retrieval_eval_cases.json` 候选标注；人工确认：**待确认**。
- 待确认原因：Heldout candidate; phrase is directional and lacks a numeric share, so human should decide whether it supports the question.
- 是否需要多个片段共同支持：当前标注认为否；人工可更正

### 候选片段 1（分组 `share`）

- PDF 实际页序号：**23**；可识别的印刷页码：**23**
- 候选原文（匹配规则：NFKC/大小写折叠/移除空白；命中源页：是）：
  > 大气机械手已全面导入市场，市场份额不断提升
- 必要上下文（空白归一后的源页片段，约前后各 170 字）：
  > 现自主研发，并完成 在设备端客户的应用验证。报告期内，公司开展了对两轴、三轴真空直驱机械手产品技术的迭代更新，持续提升、优化 产品性能。 真空传输平台系列产品，包括即真空直驱机械手、真空装载机（VPH）、真空预对准机（ALIGNER）已通过工艺设 备厂客户端验证后随其批量导入FAB 厂应用。 大气类产品方面，目前设备前端模块（EFEM）、大气机械手已全面导入市场，市场份额不断提升；自主研发的 LOADPORT（晶圆加载机）、SMIF（晶圆装载检测机）在设备客户端完成验证，已能够集成在EFEM 上开展供应销售， 并成功进入到终端FAB 厂示范应用；报告期内，定制化产品，如大气aligner、6/8 寸LOADPORT 开始模块化、标准化， 逐步为大批量国产化供应提供有力支撑。 报告期内，在产品技术的研发迭代方面，公司
- 与当前解析 chunk 的关系：完整原文包含于 chunk `dd7951dedd311ebbe400d09d`。
- 机器初审关系：**支持候选 / 待人工确认**。初审理由：原句可在 PDF 页文本定位；事实范围、主体、时间、单位和是否足以支持该指标尚未人工核定。
- 人工复核关系（选一）：`[ ] 支持`　`[ ] 部分支持`　`[ ] 矛盾`　`[ ] 不足`。

- 原向量+原 query：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。
- 原向量+通用双语 query：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。
- BM25+通用双语 query：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。
- RRF+通用双语 query 的候选片段：`1a59f8a46714f245913f93b8` p23：沈阳新松机器人自动化股份有限公司2024 年年度报告全文 自动化装配产线、焊接机器人工作站及立库等机器人产品和智能化服务。在南美洲，公司与国内某大型新能源车企在巴 西的移动机器人底盘合装、电池合装项目开展持续合作，互惠双赢，共同巩固、扩大海外市场。 近年印度成为全球增长较快的新能源汽车市场之一，带动了新能源电池的产需增长。在印度，报告期内，公司为印 度知名电池品牌厂商提供的移动机器人及配套设备成功进驻印度本土新能源市场，助力客户工厂高效

- 其他等价证据/矛盾证据：**未穷尽；需复核年报相邻页和其他页面。**
- 建议标签：`支持`（仅为自动初审建议，不能当作人工标注）；人工标签：`待确认`；待确认说明：核对上下文、事实主体与指标相关性，必要时增加共同支持片段。

## catl_policy_environment_zh · catl · policy_environment

- 问题：材料披露了哪些影响该产业的政策措施或监管安排？
- 指标：`industry.policy_environment`；语言：zh；分组：heldout
- 源文件：`data/real_cases/catl/sources/annual_report.pdf`；SHA-256：`b4f1713d7b821eb076c102711d177fe942ccc2bc8dd171ae5d7a95799a65b0ad`
- 原标注状态：`candidate_unconfirmed`；来源：`evaluation/retrieval_eval_cases.json` 候选标注；人工确认：**待确认**。
- 待确认原因：Heldout multi-passage candidate; confirm both policies are relevant and that the question requires their joint coverage.
- 是否需要多个片段共同支持：是

### 候选片段 1（分组 `policy_examples`）

- PDF 实际页序号：**12**；可识别的印刷页码：**12**
- 候选原文（匹配规则：NFKC/大小写折叠/移除空白；命中源页：是）：
  > 《推动大规模设备更新和消费品以旧换新行动方案》
- 必要上下文（空白归一后的源页片段，约前后各 170 字）：
  > 2024 年全球市占率为37.9%，较第二名高出20.7 个百分 点；在储能领域，公司2021-2024 年连续4 年储能电池出货量排名全球第一，2024 年全球市占率为36.5%， 较第二名高出23.3 个百分点。 4、主要法律法规及行业政策 2024 年以来行业有关的主要法律法规及政策如下表所示： 时间 颁布单位 文件名称及主要内容 《推动大规模设备更新和消费品以旧换新行动方案》，开展汽车以旧换新，加大政 策支持力度，畅通流通堵点，促进汽车梯次消费、更新消费。支持交通运输设备和 老旧农业机械更新，持续推进城市公交车电动化替代，支持老旧新能源公交车和动 2024 年3 月 国务院 力电池更新换代；加快淘汰国三及以下排放标准营运类柴油货车；加强电动、氢能 等绿色航空装备产业化能力建设；加快高耗能高排放老旧船舶报废更新，
- 与当前解析 chunk 的关系：完整原文包含于 chunk `212566fa5152714cc13a84a1`。
- 机器初审关系：**支持候选 / 待人工确认**。初审理由：原句可在 PDF 页文本定位；事实范围、主体、时间、单位和是否足以支持该指标尚未人工核定。
- 人工复核关系（选一）：`[ ] 支持`　`[ ] 部分支持`　`[ ] 矛盾`　`[ ] 不足`。

### 候选片段 2（分组 `policy_examples`）

- PDF 实际页序号：**13**；可识别的印刷页码：**13**
- 候选原文（匹配规则：NFKC/大小写折叠/移除空白；命中源页：是）：
  > 《关于推动车网互动规模化应用试点工作的通知》
- 必要上下文（空白归一后的源页片段，约前后各 170 字）：
  > 不受价格飙升的影响，加快可再生能源等清洁电力的部署，激励清洁能源转 欧洲议会及理事 2024 年7 月 型。关键举措包括：1、通过对长期购电协议（PPA）和差价合约的推广、可再生能 会 源的投资建设，间接驱动储能发展；2、非化石灵活性支持系统“可用容量付费”， 使灵活性资源充分满足清洁能源目标，或将直接增加储能机组收益，促进储能发 展。 《关于推动车网互动规模化应用试点工作的通知》，按照“创新引导、先行先试”的 原则，全面推广新能源汽车有序充电，扩大双向充放电（V2G）项目规模，丰富车 网互动应用场景，以城市为主体完善规模化、可持续的车网互动政策机制，以V2G 国家发改委、国 项目为主体探索技术先进、模式清晰、可复制推广的商业模式，力争以市场化机制 2024 年9 月 家能源局 引导车网互动规模化发展。参与试点的地区
- 与当前解析 chunk 的关系：完整原文包含于 chunk `79604a9437d6bc14b1528ae7`。
- 机器初审关系：**支持候选 / 待人工确认**。初审理由：原句可在 PDF 页文本定位；事实范围、主体、时间、单位和是否足以支持该指标尚未人工核定。
- 人工复核关系（选一）：`[ ] 支持`　`[ ] 部分支持`　`[ ] 矛盾`　`[ ] 不足`。

- 原向量+原 query 的候选片段：`6e51b0b0e132d10ecef591ed` p12：4 月 国家能源局 式、加强运行管理等措施，明确新型储能的功能定位和技术要求，持续完善新型储 能调度机制，保障新型储能合理高效利用，有力支撑新型电力系统建设。 生态环境部、发 《关于建立碳足迹管理体系的实施方案》，优先聚焦锂电池、新能源汽车、光伏和 2024 年5 月 改委、工信部等 电子电器等重点产品，制定发布核算规则标准。力争在锂电池、新能源汽车、光伏 十五部门 和电子电器等领域推动制定产品碳足迹国际标准。 《锂离子电池行业规范条件；`f9e568a1f13fcdced05eb1af` p13：应调尽调。完善调节资源参与市场机制，包括 完善峰谷电价机制，建立健全调频、备用辅助服务市场体系，加快建立市场化容量 补偿机制。 二、报告期内公司从事的主要业务 公司需遵守《深圳证券交易所上市公司自律监管指引第4 号——创业板行业信息披露》中的“锂离子 电池产业链相关业务”的披露要求。 1、主要业务 公司是全球领先的新能源创新科技公司，主要从事动力电池、储能电池的研发、生产、销售，以推 动移动式化石能源替代、固定式化石能源替代，并通过电动
- 原向量+通用双语 query 的候选片段：`eb0ecb1cdc1d31dd4ef357e7` p13：宁德时代新能源科技股份有限公司2024 年年度报告全文 Regulation (EU) 2024/1747《欧盟电力市场改革方案》，为应对天然气价格导致电价 上涨问题，欧盟电力市场改革旨在降低电价对波动的化石燃料价格的依赖，保护消 费者不受价格飙升的影响，加快可再生能源等清洁电力的部署，激励清洁能源转 欧洲议会及理事 2024 年7 月 型。关键举措包括：1、通过对长期购电协议（PPA）和差价合约的推广、可再生能 会 源的投资建设，间接
- BM25+通用双语 query 的候选片段：`a1eed8087f619696a3610e17` p12：，2024 年全球市占率为36.5%， 较第二名高出23.3 个百分点。 4、主要法律法规及行业政策 2024 年以来行业有关的主要法律法规及政策如下表所示： 时间 颁布单位 文件名称及主要内容 《推动大规模设备更新和消费品以旧换新行动方案》，开展汽车以旧换新，加大政 策支持力度，畅通流通堵点，促进汽车梯次消费、更新消费。支持交通运输设备和 老旧农业机械更新，持续推进城市公交车电动化替代，支持老旧新能源公交车和动 2024 年3 月 国；`6e51b0b0e132d10ecef591ed` p12：4 月 国家能源局 式、加强运行管理等措施，明确新型储能的功能定位和技术要求，持续完善新型储 能调度机制，保障新型储能合理高效利用，有力支撑新型电力系统建设。 生态环境部、发 《关于建立碳足迹管理体系的实施方案》，优先聚焦锂电池、新能源汽车、光伏和 2024 年5 月 改委、工信部等 电子电器等重点产品，制定发布核算规则标准。力争在锂电池、新能源汽车、光伏 十五部门 和电子电器等领域推动制定产品碳足迹国际标准。 《锂离子电池行业规范条件
- RRF+通用双语 query 的候选片段：`6e51b0b0e132d10ecef591ed` p12：4 月 国家能源局 式、加强运行管理等措施，明确新型储能的功能定位和技术要求，持续完善新型储 能调度机制，保障新型储能合理高效利用，有力支撑新型电力系统建设。 生态环境部、发 《关于建立碳足迹管理体系的实施方案》，优先聚焦锂电池、新能源汽车、光伏和 2024 年5 月 改委、工信部等 电子电器等重点产品，制定发布核算规则标准。力争在锂电池、新能源汽车、光伏 十五部门 和电子电器等领域推动制定产品碳足迹国际标准。 《锂离子电池行业规范条件；`a1eed8087f619696a3610e17` p12：，2024 年全球市占率为36.5%， 较第二名高出23.3 个百分点。 4、主要法律法规及行业政策 2024 年以来行业有关的主要法律法规及政策如下表所示： 时间 颁布单位 文件名称及主要内容 《推动大规模设备更新和消费品以旧换新行动方案》，开展汽车以旧换新，加大政 策支持力度，畅通流通堵点，促进汽车梯次消费、更新消费。支持交通运输设备和 老旧农业机械更新，持续推进城市公交车电动化替代，支持老旧新能源公交车和动 2024 年3 月 国

- 其他等价证据/矛盾证据：**未穷尽；需复核年报相邻页和其他页面。**
- 建议标签：`支持`（仅为自动初审建议，不能当作人工标注）；人工标签：`待确认`；待确认说明：核对上下文、事实主体与指标相关性，必要时增加共同支持片段。

## catl_technical_autonomy_zh · catl · technical_autonomy

- 问题：材料如何描述企业的研发体系、自主研发或外部技术依赖？
- 指标：`technology.technical_autonomy`；语言：zh；分组：heldout
- 源文件：`data/real_cases/catl/sources/annual_report.pdf`；SHA-256：`b4f1713d7b821eb076c102711d177fe942ccc2bc8dd171ae5d7a95799a65b0ad`
- 原标注状态：`candidate_unconfirmed`；来源：`evaluation/retrieval_eval_cases.json` 候选标注；人工确认：**待确认**。
- 待确认原因：Heldout candidate; human should confirm the nuance between in-house R&D and external collaboration.
- 是否需要多个片段共同支持：当前标注认为否；人工可更正

### 候选片段 1（分组 `rd_model`）

- PDF 实际页序号：**15**；可识别的印刷页码：**15**
- 候选原文（匹配规则：NFKC/大小写折叠/移除空白；命中源页：是）：
  > 形成以自主研发为主、外部合作为辅的研发模式
- 必要上下文（空白归一后的源页片段，约前后各 170 字）：
  > 的关键金属资源实现有效循环利用。 此外，为进一步保障电池生产所需的上游关键资源及材料供应，公司通过自建、参股、合资等多种 方式参与锂、镍、钴、磷等电池矿产资源及相关产品的投资、建设及运营。 3、经营模式 公司拥有独立的研发、采购、生产和销售体系，主要通过销售动力电池、储能电池和电池材料等产 品实现盈利。研发方面，公司建立了完备的研发体系，形成以自主研发为主、外部合作为辅的研发模式， 通过数字化、智能化的方式，紧紧围绕材料及材料体系、系统结构、绿色极限制造及商业模式领域开展 创新，以引领行业技术发展。采购方面，公司通过严格的评估和考核程序遴选合格供应商，并通过技术 授权、长期协议、合资合作等方式与供应商紧密合作，以保证原料、设备的技术先进性、产品可靠性以 及成本竞争力。生产销售方面，公司综合考虑市场情况以及客户需求安
- 与当前解析 chunk 的关系：完整原文包含于 chunk `1a7d9d76101c9ee726dd52c6`。
- 机器初审关系：**支持候选 / 待人工确认**。初审理由：原句可在 PDF 页文本定位；事实范围、主体、时间、单位和是否足以支持该指标尚未人工核定。
- 人工复核关系（选一）：`[ ] 支持`　`[ ] 部分支持`　`[ ] 矛盾`　`[ ] 不足`。

- 原向量+原 query：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。
- 原向量+通用双语 query 的候选片段：`41bb2025e5261354cc581173` p15：购、生产和销售体系，主要通过销售动力电池、储能电池和电池材料等产 品实现盈利。研发方面，公司建立了完备的研发体系，形成以自主研发为主、外部合作为辅的研发模式， 通过数字化、智能化的方式，紧紧围绕材料及材料体系、系统结构、绿色极限制造及商业模式领域开展 创新，以引领行业技术发展。采购方面，公司通过严格的评估和考核程序遴选合格供应商，并通过技术 授权、长期协议、合资合作等方式与供应商紧密合作，以保证原料、设备的技术先进性、产品可靠性以 及成
- BM25+通用双语 query 的候选片段：`41bb2025e5261354cc581173` p15：购、生产和销售体系，主要通过销售动力电池、储能电池和电池材料等产 品实现盈利。研发方面，公司建立了完备的研发体系，形成以自主研发为主、外部合作为辅的研发模式， 通过数字化、智能化的方式，紧紧围绕材料及材料体系、系统结构、绿色极限制造及商业模式领域开展 创新，以引领行业技术发展。采购方面，公司通过严格的评估和考核程序遴选合格供应商，并通过技术 授权、长期协议、合资合作等方式与供应商紧密合作，以保证原料、设备的技术先进性、产品可靠性以 及成
- RRF+通用双语 query 的候选片段：`41bb2025e5261354cc581173` p15：购、生产和销售体系，主要通过销售动力电池、储能电池和电池材料等产 品实现盈利。研发方面，公司建立了完备的研发体系，形成以自主研发为主、外部合作为辅的研发模式， 通过数字化、智能化的方式，紧紧围绕材料及材料体系、系统结构、绿色极限制造及商业模式领域开展 创新，以引领行业技术发展。采购方面，公司通过严格的评估和考核程序遴选合格供应商，并通过技术 授权、长期协议、合资合作等方式与供应商紧密合作，以保证原料、设备的技术先进性、产品可靠性以 及成

- 其他等价证据/矛盾证据：**未穷尽；需复核年报相邻页和其他页面。**
- 建议标签：`支持`（仅为自动初审建议，不能当作人工标注）；人工标签：`待确认`；待确认说明：核对上下文、事实主体与指标相关性，必要时增加共同支持片段。
