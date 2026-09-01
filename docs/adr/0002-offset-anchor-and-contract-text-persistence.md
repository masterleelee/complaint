# 扣费项以字符偏移量锚定原文，合同正文随分析落库

下载件的定位高亮靠"条款切分 + 字符串 includes 关键词匹配"（`app.js clauseHtml`），对带文本层的驾培 PDF 勉强可用；上传件走 OCR/Vision，错字与断行会让关键词匹配大量落空，"定位并高亮原文"整条链路失效（`build_contract_clauses` 只支持 pdfplumber，扫描件直接返回 `no_text_layer`）。

决定：扣费明细每条在分析时锚定到 `contract_text` 的字符区间 `[start, end)` 并**随分析结果一并落库**；前端按 offset 渲染 `<mark>`，不再运行时搜索关键词。合同正文（含 OCR 来源与置信度）随之持久化到工单（此前分析结果显式剔除 text 字段），同一工单复用文本不再重跑 OCR。

Considered Options：加厚关键词词典（拒绝：OCR 错字无解，词典永远追不上）；页码+行号定位（拒绝：重排后漂移，且 OCR 无稳定行概念）。

Consequences：锚点只对"产生它的那份文本"有意义——正文落库是本方案的硬前提；若日后重新 OCR 同一份原件，必须重算全部锚点（缓存键含文件 sha256 即为此设计）。
