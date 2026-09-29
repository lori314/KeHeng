# 科衡科技企业技术价值分析报告

> 本报告由 KeHeng 根据已提供的企业技术资料、Technology Agent 结构化指标和 Evaluation Engine 确定性评分结果生成。报告仅用于辅助尽调，关键结论需由专业人员复核，不构成授信、投资或融资决策。

## 1. 企业概览

- 企业名称：{{ enterprise_name }}
- 资料范围：{{ source_documents }}
- 分析摘要：{{ summary }}

## 2. 技术能力评分

- 技术综合评分：**{{ technology_score }} / 100**
- 创新维度：{{ dimension_scores.innovation }}
- 知识产权维度：{{ dimension_scores.ip }}
- 技术成熟度维度：{{ dimension_scores.maturity }}

## 3. 核心技术优势

{{# strengths }}
### {{ index }}. {{ content }}

- 证据编号：{{ evidence_ids }}
- 来源：{{ document_name }}，第 {{ page_number }} 页
- 原文：{{ excerpt }}
{{/ strengths }}

## 4. 风险因素

{{# risks }}
### {{ index }}. {{ content }}

- 证据编号：{{ evidence_ids }}
- 依据：{{ document_name }}，第 {{ page_number }} 页
- 原文：{{ excerpt }}
{{/ risks }}

## 5. 评价依据

- 指标配置版本：{{ indicator_version }}
- 权重配置版本：{{ weight_version }}
- 评分方法：固定权重加权
- 缺失值规则：缺失指标不计零分；存在可评分指标时按可用权重重新归一化。

报告生成器只展示 Evaluation Engine 已产生的分数，不重新计算或修改评分。

## 6. 指标评分详情

| 指标 | 指标分 | 权重 | 加权得分 | 计算过程 | 证据 |
| --- | ---: | ---: | ---: | --- | --- |
| 技术自主性 | {{ technical_autonomy.score }} | {{ technical_autonomy.weight }} | {{ technical_autonomy.weighted_score }} | {{ technical_autonomy.calculation }} | {{ technical_autonomy.evidence }} |
| 创新能力 | {{ innovation_capability.score }} | {{ innovation_capability.weight }} | {{ innovation_capability.weighted_score }} | {{ innovation_capability.calculation }} | {{ innovation_capability.evidence }} |
| 知识产权能力 | {{ intellectual_property.score }} | {{ intellectual_property.weight }} | {{ intellectual_property.weighted_score }} | {{ intellectual_property.calculation }} | {{ intellectual_property.evidence }} |
| 技术成熟度 | {{ technical_maturity.score }} | {{ technical_maturity.weight }} | {{ technical_maturity.weighted_score }} | {{ technical_maturity.calculation }} | {{ technical_maturity.evidence }} |

## 证据索引

{{# references }}
- **{{ evidence_id }}**：{{ document_name }}，第 {{ page_number }} 页，片段 `{{ chunk_id }}`
{{/ references }}
