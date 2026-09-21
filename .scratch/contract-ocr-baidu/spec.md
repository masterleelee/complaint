# 合同 OCR 接入百度云 OCR · 落地方案（spec）

> 状态：**已审批（v3）**（2026-09-22 用户确认：A选方案1 / B签名改dict / C·D·E照改 / §12 按默认）
> 日期：2026-09-21（v3 修订 09-22）
> 关联：本文**替代** `.scratch/ocr-strategy.md` 中「Vision API 优先」的结论；另见 `.scratch/contract-vision-failure-20260914.md`
> 版本：v3（v2 修复 v1 前后矛盾；v3 修复复审发现的 A/B/C/D/E 五项，见 §13 前的修订记录）

---

## 0. 一句话结论

用 **百度云 OCR 作合同图片 OCR 主路**（`accurate_basic` 主文本 + `handwriting` 按需补手写），**删除 AI 视觉 OCR 路径**；本地兜底改用**已有的 macOS 原生 Vision**。

**实测依据**：百度 OCR 是**唯一**能读出本合同手写金额（3580）的引擎；而当前"本地 OCR"实为 macOS Vision，读不出手写金额、还给出错值（`total_fee=3.0`）；EasyOCR/PaddleOCR 在本机**从未真正执行**（死代码）。

---

## 1. 现状（2026-09-21 实测澄清，非推测）

### 1.1 代码链路
入口 `app.py:3273 → analyze_contract_from_file()`（`contract_service.py:1805`）→ `extract_contract_text_from_file()`（`:191`）：

1. PDF 文本层（`pdfplumber` `:201`）/ DOCX（`:214`）
2. 无文本层 → **图像 OCR**（`:226-260`）：
   - `extract_contract_text_ocr()`（`:92`）→ 名义上 PaddleOCR，PaddleOCR 不可用则 `_extract_contract_text_easyocr()`（`:76`）
   - 仍无有效文本 → `extract_contract_text_vision()`（`:618`，AI 视觉 LLM）
3. 手写金额二次补提 `_vision_amount_rescue()`（`:359`）
4. `source` → `text_confidence_of()`（`:938`）映射置信度

> 文本层（与 OCR 无关）：本地规则 `_extract_standard_contract_data()`（`:733`）；失败才 `analyze_contract()`（`:1634`，`llm_contract_text` 语义分析）。

### 1.2 ⚠️ 现状真相（实测颠覆原认知）
- **`paddle` 未安装**（`No module named 'paddle'`）→ **PaddleOCR 是死代码**。
- `extract_contract_text_ocr()` 实际落到 `_extract_contract_text_easyocr()` → 内部 `file_parser.extract_text()` → `_extract_image()` → **macOS Vision 优先**。实测 **3.08s 完成**；若真走 EasyOCR 需 100s+ → **EasyOCR 未触发**。
- **结论：当前生产合同 OCR 实际 = macOS 原生 Vision**（函数命名 `_extract_contract_text_easyocr` 具误导性）。
- macOS Vision 虽快（3s），但**认不出手写 3580**，`extract_contract_fees` 给出**错值 `total_fee=3.0`**。

### 1.3 痛点
手写金额读不出（macOS Vision 输出错误值）→ 依赖 AI 视觉 LLM 补提 → **免费池晚高峰 429（1 主 3 备全败）** → 合同分析失败（`contract-vision-failure-20260914.md`）。

---

## 2. 实测数据（2026-09-21）

### 2.1 合同图片 OCR 三方对比（郑智林 1.jpg + 2.jpg，各 3072×4096）
| 引擎 | 耗时 | 字数 | 认出 3580 | `total_fee` | 说明 |
|---|---|---|---|---|---|
| **macOS Vision** | **3.08 s** | 3871 | ❌ | **3.0（错值）** | **当前生产真实路径** |
| PaddleOCR | — | — | — | — | **未安装 = 死代码** |
| EasyOCR | **≈110 s** | 1684 | ❌ | 0 | Vision 成功即不触发 = **死代码** |
| **百度 OCR** | **≈4 s**（2 页 `accurate_basic`）/ 8 s（4 次调用） | 7985 | ✅ | **3580 ✅** | 拟新增主路 |

### 2.2 百度 OCR 字段级准确性（ground truth 已放大原图核实）
| 字段 | 原图手写 | `handwriting` | `accurate_basic` | 旧 AI 视觉（`ocr-strategy.md` 记录） |
|---|---|---|---|---|
| 合同总额 | **3580**（原写 3680 划掉改为 3580） | 3580 ✅ | **3580 ✅** | 3600 ❌ |
| 首付 | 2000 | 2000 ✅ | 2000 ✅ | 2000 ✅ |
| 欠款/尾款 | 1580 | **1580 ✅** | 尾 580 ❌ | 1580 ✅ |
| 签名 | 郑智林 / 卢刚 | **郑智林 / 卢刚 ✅** | 郑智林（拆散） | — |
| 签订日期 | 2024年2月28日 | **2024年2月28日 ✅** | 拆成多行 | — |
| 违约金比例 | 20% | 2%（❌读错） | **20% ✅** | — |

> 自洽校验：2000 + 1580 = **3580** ✅；旧 AI 视觉的 3600 不自洽。

### 2.3 端到端提取（百度文本 → 项目真实逻辑 `extract_contract_fees` + `match_template`）
| 信息 | 能否提取 | 实测值 | 来源 |
|---|---|---|---|
| **合同金额** | ✅ | **3580**（`warnings=[]`，通过可疑性闸门） | 百度文本 |
| **实缴费用** | ✅ | **实缴 = 付款计划首付 = 2000**；欠款 = 1580 | OCR 手写付款计划 |
| **扣费项目** | ✅ | 基础服务服务费、建档费、学员IC卡、科目二/三实操、考试费、工本费 | **模板定义** |
| **扣费金额** | ✅ | 服务费 600、建档费 300、学员IC卡 100（模板默认）；考试费 70/130/280、工本费 10（OCR） | 模板 + OCR |
| **扣费依据** | ✅ | 「扣除基础服务+必扣项+已代收考试费+已产生实操费+违约金 20%」 | **模板退费规则** |
| **合同版本** | ✅ | **东莞驾培 2023 版分校**（`template_id=19`，`is_confident=True`） | `match_template` |

### 2.4 三系统验证码（`ddddocr`）
| 指标 | 实测值 |
|---|---|
| 单张准确率（79 张真实标注样本）| **84.8%（67/79）** |
| 单张耗时 | **4.3 ms** |
| 首次初始化 | **0.18 s** |
| 6 次重试有效成功率 | **≈99.999%** |
| 依赖 EasyOCR/PaddleOCR？ | **否**（`internal.py:131`/`third.py:129`/`driving.py:195` 全走 `ddddocr`）|

### 2.5 关键技术结论（必须落进实现）
1. ⚠️ **两个接口的原始文本严禁拼接**：合并后 `extract_contract_fees` 全 0、`match_template` 无匹配 → 必须**各自独立提取、字段层合并**。
2. `accurate_basic` **一次调用即读全**（印刷条款 + 手写 3580 + 违约金 20%）→ **应作主文本**。
3. `handwriting` **仅在必要时按需补充**（如 `accurate_basic` 的 `total_fee` 缺失/被闸门拦下时）。
4. 百度输出**通过了「手写总额可疑性闸门」**（`contract_service.py:841-858`），而旧视觉输出「¥6800-2500」会被拦下。
5. 模板 `extract_fields` 在 OCR 文本上返回空值 → 固定项依赖模板 `默认值`，`extract_contract_fees` 才是费用主链路。

---

## 3. 目标 / 非目标

### 目标
- 手写金额稳定可读，消除 429 导致的整单失败。
- 百度云 OCR 为 OCR 主路；本地兜底 = macOS Vision。
- 删除 AI 视觉 OCR 路径（低效、429 故障源）。
- 密钥不落代码（环境变量）。

### 非目标（本期不做）
- 不改模板匹配逻辑、不改退费引擎、不改前端 UI。
- 不做版面坐标/条款级定位。
- 不做多云（腾讯/火山）同时接入（见 §12.4）。

---

## 4. 接口选型与调用策略（以实测为准）

| 用途 | 百度接口 | 说明 |
|---|---|---|
| **主文本**（印刷条款 / 模板匹配 / 违约金 / 手写金额） | `accurate_basic` | 实测一次调用即可读全 |
| **按需补充**（手写金额缺失时） | `handwriting` | 仅在 §4 兜底条件触发 |

**调用策略**（2026-09-22 用户拍板：**坚持省调用**，触发条件只看 `total_fee`）：
1. 每页先跑 `accurate_basic` → 得到文本 → `extract_contract_fees`。
2. **仅当** `total_fee` 为 0（闸门拦下或缺失）时，追加 `handwriting` 作二次尝试（只跑 handwriting，不重跑 accurate_basic）。
3. ⚠️ **严禁拼接两份原始文本**；如需两者，**各自独立提取后在字段层合并**（`handwriting` 只覆盖金额类字段）。
4. 常规用量 = **1 次/页**；仅兜底时 2 次/页。

**欠款口径（v3 新增，方案 1，0 额外调用）**：
- `accurate_basic` 的 `total_fee` 正确时 handwriting 永不触发，而实测它把尾款拆成「尾580」→ 欠款**不依赖 handwriting**，改由本地自洽推算：
  - `首付` = 从付款计划文本解析（正则 `首付/首付款 … N 元`，accurate_basic 实测读对 2000）；
  - `欠款 = total_fee − 首付`（3580−2000=1580）；
  - **一致性闸门**：若文本里解析出的「尾款」与推算值不一致 → 以推算值为准 + `warnings` 提示人工核对（防「尾580」错值流入下游 `llm_contract_text`）；
  - 落点：`contract_service` 新增 `_extract_payment_plan(text)`，结果挂 `extraction["payment_plan"] = {down_payment, balance, source, warnings}`，供 §10 集成断言与费用面板人工确认用。

（后续可选评估：`iOCR 全场景识别` / `合同审查` 单接口出结构化 KV，本期不做。）

---

## 5. 代码接入点

### 新增
- `services/baidu_ocr.py`：
  - `get_access_token(api_key, secret_key)`：**进程内缓存**（有效期 30 天），401 自动刷新。
  - `recognize(image_path, api, token, **opts) -> str`：封装 REST 调用。
  - `extract_pages_via_baidu(image_paths, need_handwriting=False) -> dict`（v3 修订 B：**返回 dict**，不再返回合并 str）：
    - `{"printed": <accurate_basic 各页拼接文本>, "handwriting": <handwriting 文本或 None>}`；
    - 默认仅跑 `accurate_basic`；`need_handwriting=True` 时**只追加跑 handwriting**（不重跑 accurate_basic，保住省调用）；
    - **字段层合并编排放 `contract_service`**（OCR 层只负责拿回两份独立文本，绝不拼接）。

### 修改
- `services/contract_service.py`
  - 新增 `extract_contract_text_baidu(image_paths)`（薄封装）+ `_extract_payment_plan(text)`（欠款推算 + 一致性闸门，见 §4）。
  - `extract_contract_text_from_file()`（`:226-260`）改为：**先试百度 → 失败/未配置则 macOS Vision 兜底**；`source` 新增 `"baidu_ocr"`（macOS Vision 兜底沿用 `local_ocr` 低置信口径，闸门行为不变）。
  - 模板匹配 source 白名单（`:268`）加入 `"baidu_ocr"`。
  - `text_confidence_of()`（`:938`）为 `"baidu_ocr"` 映射置信度（`high`）。
  - 删除 `extract_contract_text_vision` / `_vision_amount_rescue` 及调用（§6.1）；`upload_pipeline._merge_rescued_fees` 的 `vision_rescue` 合并行同步清理。
  - 保留 `_format_llm_error` / `_llm_config`（`intake_service`/`app.py` 共用，与视觉无关）。
- `config.py`
  - `DEFAULT_CONFIG` 增 `baidu_ocr` 段：`enabled`、`api_key`、`secret_key`、`timeout`、`empty_env_fallback`。
    - `empty_env_fallback`（v3 定义 E）：环境变量 `BAIDU_OCR_*` 为空时，是否回落读本段 `api_key`/`secret_key`（默认 `True`；设 `False` 可强制只用环境变量）。
  - 密钥优先读环境变量 `BAIDU_OCR_API_KEY` / `BAIDU_OCR_SECRET_KEY`。
- `data/config.json`：落 `baidu_ocr.enabled=true`（密钥留空，走环境变量）。

---

## 6. 目标链路（2026-09-21 决策）

```
合同图片
  └─ 1. 百度云 OCR（accurate_basic 主；必要时 handwriting 补）   ← 主路
       ↓ 不可用 / 失败 / 未配置
     2. macOS 原生 Vision（本地兜底，已在用、3s 级）              ← 本地兜底
       ↓ 得到文本
   ┌───────────────────────────────────────────────┐
   │ 文本层（与 OCR 无关，保持现状）                     │
   │  3. 本地规则 _extract_standard_contract_data      │  ← 标准东莞驾培合同，无 LLM
   │       ↓ 失败才                                     │
   │  4. llm_contract_text 语义分析                     │  ← 保留
   └───────────────────────────────────────────────┘
```

### 6.1 删除项 A：AI 视觉 OCR（用户判定低效）—— 4 项
| # | 内容 | 位置 |
|---|---|---|
| 1 | `extract_contract_text_vision()` + 调用 | `contract_service.py:618` / 调用 `:251` |
| 2 | `_vision_amount_rescue()` + 调用 | `contract_service.py:359` / 调用 `:296` |
| 3 | `llm_contract_vision` 配置 | `config.py:67-90+`（整段含 `fallback_models`/B 方案开关等，删到段尾防孤儿键）+ `data/config.json` |
| 4 | 3 个测试 | `test_vision_rescue.py`、`test_contract_vision_failover.py`、`test_contract_vision_robustness.py` |

### 6.2 删除项 B：死代码 EasyOCR / PaddleOCR（**待 §12.1 确认**）
| 引擎 | 位置 | 为何可删 |
|---|---|---|
| PaddleOCR | `contract_service.extract_contract_text_ocr`（`:92`）+ `file_parser._create_paddle_ocr/_run_paddle_ocr` | **未安装**，从不执行 |
| EasyOCR | `file_parser._extract_image_easyocr` + `contract_service._extract_contract_text_easyocr`（`:76`）+ `warmup_ocr` 加载 | Vision 优先成功 → 从不触发 |

**收益**：省 `torch` **501M** + `easyocr` 16M；`app.py:87 warmup_ocr()` 不再加载 EasyOCR → 启动更快。
⚠️ 依赖移除前需实测确认 `torch` 无他处间接引用（如 modelscope），**不可盲目 `pip uninstall`**。

### 6.3 保留项
- **`llm_contract_text`（文本语义分析）**：仅本地规则失败时兜底；**不是 OCR**。✅ 用户确认保留。
- **macOS 原生 Vision**：系统自带框架 `/System/Library/Frameworks/Vision.framework`（209M，属 macOS 本体、非项目占用）；项目仅需 `pyobjc-framework-Vision`（实测 pyobjc 合计 **≈25MB**）。离线、PII 不出网、亚秒级。
- **`ddddocr`**：三系统验证码（见 §6.4）。

### 6.4 全系统 OCR 清点（决定删除安全性）
| 引擎 | 使用位置 | 验证码依赖 | 处置 |
|---|---|---|---|
| **ddddocr** | `core/ocr_engine.py` → `crawlers/{internal,third,driving}.py` | ✅ 就是它 | **保留** |
| **macOS Vision** | `file_parser._extract_image`（图片首选）；受理(intake)共用 | ❌ | **保留**（本地兜底）|
| **EasyOCR** | `file_parser` 兜底 + `contract_service` | ❌ | **删除**（待 §12.1）|
| **PaddleOCR** | 仅 `contract_service` | ❌ | **删除**（待 §12.1）|

要点：验证码 = `ddddocr`，与合同 OCR **两套独立引擎** → 删 EasyOCR/PaddleOCR **对登录零影响**（实测 84.8% / 4.3ms）。受理(intake)图片路径**首选 macOS Vision**，删 EasyOCR 对受理影响极小。

### 6.5 失败处理
一律 **soft-fail**：写 `warnings` + `pending`，**不整任务 failed**（沿用带工单上传管线口径）。
> v3 核实：无工单路径 soft-fail **已于 2026-09-21 落地**（`app.py:3326-3334`：无工单号时 LLM/提取失败降级为警告回传部分结果，不整任务 failed）→ §12.6 已闭环，无需再改。

---

## 7. 配置与密钥
- 环境变量：`BAIDU_OCR_API_KEY`、`BAIDU_OCR_SECRET_KEY`（**不入库、不落代码**）。
- 启动方式：写入 `~/Library/LaunchAgents/com.complaint.system.plist` 的 `EnvironmentVariables`，或项目本地 `.env`（gitignore）。
- ⚠️ **密钥曾在聊天中明文出现 → 须重置后再用**。

---

## 8. 免费额度与成本（与 §4 策略一致）
| 接口 | 个人认证免费额度 | 说明 |
|---|---|---|
| `accurate_basic` | 1000 次/月 | 当月有效、不结转 |
| `handwriting` | 500 次/月 | 当月有效、不结转 |

- **常规 1 次/页**（仅 `accurate_basic`）→ **1000 页/月免费**；仅兜底时才 2 次/页。
- 本项目投诉工单量低 → 基本长期零成本。
- 超出后按量付费（手写/高精度单价高于通用，精确档位上线后再核）。

---

## 9. 风险与权衡
| 风险 | 说明 | 缓解 |
|---|---|---|
| 隐私/合规 | 合同含 PII，图片出本机到百度 | 需用户确认（§12.2）；否则退回 macOS Vision 本地兜底 |
| 网络依赖 | 百度为云端，需出网 | 失败自动降级 macOS Vision |
| token 过期 | 30 天有效期 | 进程内缓存 + 401 刷新 |
| 免额度清零 | 当月不用即失效 | 低频场景反而不受影响 |
| 手写歧义 | 划改/潦草可能读错 | 保留前端人工确认（`needs_review`）|
| 云服务不可用 | 偶发超时 | soft-fail 降级链 |

---

## 10. 测试清单
- 单测 `tests/test_baidu_ocr.py`：
  - token 缓存复用 + 401 刷新；
  - 接口异常 → 抛错被上层捕获并降级；
  - **返回 dict 结构**（`printed`/`handwriting` 独立文本，**不拼接整段文本**）；`need_handwriting=True` 时 handwriting 仅追加、不重跑 accurate_basic；
  - `source="baidu_ocr"` 正确标记、`text_confidence_of` 映射正确。
- 单测 `tests/test_contract_ocr_fallback.py`：百度不可用 → 走 **macOS Vision**（沿用 `local_ocr` 口径）→ 文本层规则。
- 单测：`_extract_payment_plan` 欠款推算 + 尾款不一致闸门（郑智林口径：首付 2000、欠款 1580、闸门拦「尾580」）。
- 单测（删除护栏）：断言 AI 视觉 OCR 入口已移除（防回归引用）。
- 集成（郑智林样本）：断言 `total_fee==3580`、`payment_plan.down_payment==2000`、`payment_plan.balance==1580`（来源=本地推算，非 OCR 尾款字段）。
- **全量回归**（v3 修订 C）：全绿即可；测试总数较 859 基线**减少 3**（删除 §6.1-4 的三个 vision 测试文件），其余数量必须守恒。
- 密钥缺失时优雅降级（不抛栈、不整单失败）。

---

## 11. 分步实施（审批后逐项落地）
| Step | 内容 | 产物 | 可回滚 |
|---|---|---|---|
| 1 | 新增 `services/baidu_ocr.py` + 单测 | 无副作用新模块 | ✅ |
| 2 | 接入 `contract_service`（主路 + `source` + 降级到 macOS Vision） | 改动 OCR 路径 | ✅ |
| 3 | 配置 + 环境变量（`config.py` / plist / `.env`） | 开关可控 | ✅ |
| 4 | 删除 AI 视觉 OCR（§6.1 四项）+ 同步清理测试/配置 | 删除清单落地 | ✅ |
| 5 | 删除 EasyOCR/PaddleOCR（§6.2，经 §12.1 确认） | 依赖瘦身 | ✅ |
| 6 | 郑智林 e2e + 全量回归 | 测试报告 | — |
| 7 | 更新 `.scratch/ocr-strategy.md` + `CONTEXT.md` | 文档 | — |

---

## 12. 待用户确认项（阻塞审批）

### 已决（2026-09-21 用户拍板；09-22 补充确认）
- ✅ **主路换成百度云 OCR**。
- ✅ **删除 AI 视觉 OCR**（`llm_contract_vision`，低效）。
- ✅ **保留 `llm_contract_text`（AI 文本分析）**。
- ✅ **实缴 = 合同付款计划首付**（东莞驾培系统无费用信息）。
- ✅ **验证码 `ddddocr` 不动**（与合同 OCR 独立）。
- ✅（09-22）**欠款口径 = 本地自洽推算**（`欠款 = total_fee − 首付`，方案 1，省调用）。
- ✅（09-22）**`extract_pages_via_baidu` 返回 dict**，字段层合并编排放 `contract_service`。
- ✅（09-22）§12 按默认：①删 EasyOCR/PaddleOCR（先实测 `torch` 无间接引用再卸载）②PII 出网按"可" ③删除清单显式确认 ⑤不付费、只用免费额度、超额 soft-fail 降级 ⑥无工单路径已落地（见 §6.5）。
- ✅（09-22）**新增 Phase 2**（多云兜底 + 系统设置页，见 §13），与 Phase 1 分开审批实施。

### 仍待确认（Phase 2 范围，见 §13）
4. 多云兜底与设置页的具体方案（§13 待批）。

---

## 13. Phase 2（新增需求，2026-09-22 用户提出；待细化审批后实施）

**用户原话要点**：系统设置里可让用户自己修改云 OCR 配置；预设**百度、腾讯、字节、阿里**四家；首选百度，百度免费额度用完后其他云兜底；都用各厂商每月免费额度。

### 13.1 范围
| 项 | 内容 |
|---|---|
| 设置页 | 系统设置新增「云 OCR」卡片：四厂商预设（百度/腾讯/字节/阿里），每家 `enabled` 开关 + `api_key`/`secret_key`（百度另有 App ID）输入框，保存走现有 settings API |
| 配置 | `data/config.json` 增 `cloud_ocr.providers.{baidu,tencent,byte,aliyun}`；百度迁移自 `baidu_ocr` 段（读侧兼容旧键，一次性迁移） |
| 调度 | 兜底顺序 = 配置中 `enabled` 且已填密钥的厂商按固定优先级（百度→腾讯→字节→阿里）；跳过条件 = 未配置 / 调用失败 / **月度免费额度耗尽** |
| 额度判定 | 各厂商无"剩余额度"查询 API → 本地**按月计数器**（`data/cloud_ocr_usage.json`：`{provider: {month: "2026-09", used: N}}`）+ 失败错误码兜底（如百度 6=无权限/额度类错误）；计数在成功响应后 +1 |
| 密钥安全 | 仍支持环境变量覆盖；UI 显示打码（只显尾 4 位） |

### 13.2 非目标（Phase 2 仍不做）
- 不做各厂商额度实时查询（无 API）；不做计费告警/通知。

### 13.3 待批点
1. 设置页 UI 形态（现有 settings 视图加卡片 vs 独立页）；
2. 兜底顺序是否允许用户拖拽调整（本期建议：固定优先级，不做拖拽）；
3. 字节/阿里对应的具体 OCR 产品名（建议：字节=火山引擎 OCR、阿里=读光 OCR），额度以各家当期官网为准。

---

## 附录 A · v3 修订记录（2026-09-22 复审）

| # | 修订 | 原因 |
|---|---|---|
| A | §4 增「欠款=total_fee−首付 本地推算 + 一致性闸门」 | 省调用触发条件只看 total_fee，永不覆盖「尾580」错值；欠款改本地推算，0 调用 |
| B | §5 `extract_pages_via_baidu` 返回 dict | str 无法承载字段层合并；need_handwriting=True 只跑 handwriting 不重跑 |
| C | §10 回归标准改「全绿 + 数量守恒」 | 删 3 个测试文件后总数必然下降，859 不可作硬断言 |
| D | §6.1-3 行号改 `config.py:67-90+` | 实测 `llm_contract_vision` 段至 :90+，按 68-82 删会留孤儿键 |
| E | §5 定义 `empty_env_fallback` | v2 未定义含义，实现无法落笔 |
| F | §6.5/§12.6 标记已闭环 | `app.py:3326-3334` 已实现无工单 soft-fail（09-21） |
