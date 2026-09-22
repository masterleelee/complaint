"""设置页静态护栏：令牌完整性 / 折叠交互 / 无障碍绑定 / 敏感字段。

## 为什么需要它

2026-09-22 对「系统设置」页做了一轮可用性改造（设计与验收见
`.scratch/settings-redesign/`）。下面四类东西**改坏了也不会被任何既有检查抓到**
——pytest 全绿、`node --check` 通过、浏览器 console 干净、页面照常显示：

1. **消费了未定义的 CSS 自定义属性**。`var(--x)` 在 `--x` 未定义时**不是**回落到
   某个默认色，而是让**整条简写声明失效**：`border:1px solid var(--color-border-tertiary)`
   实测 `border-width: 0px` —— 云 OCR 四行厂商的边框**完全不渲染**，页面不报任何错。
   2026-09-22 实测正是如此（3 个令牌被消费却从未定义）。这类问题只有真实浏览器的
   `getComputedStyle` 看得见，回归里必须静态兜住。

2. **折叠头不是按钮 / 缺 aria**。折叠是本次改造的核心交互。若有人把
   `<button class="ch-toggle">` 改回 `<div>`，键盘用户与读屏用户会**彻底失去入口**，
   而页面上鼠标仍点得动，看起来完全正常。

3. **折叠键拼错**。模板写 `layout.isCollapsed('ocr2')` 但注册表里没有这个键 →
   `isCollapsed()` 恒假 → 该卡片**永远折叠不起来**，不报错、不白屏，纯静默失效。

4. **敏感字段明文**。API Key / OCR Secret 若从动态 `:type` 切回静态 `type="text"`，
   页面照常工作，但**密钥在屏幕上明文常驻**（本项目已发生同类问题：用户表格手机号明文）。

## 断言

1. 全站消费的每个 `var(--token)` 都在模板或样式表里定义过（扫描前剥离 CSS 注释，
   否则注释里举例用的 `var(--color-*)` 会误报）；
2. 每张可折叠卡片都有且仅有一个 `ch-toggle` 按钮，带 `aria-expanded` + `aria-controls`
   且 `aria-controls` 指向的 id 真实存在；按钮内不得嵌套按钮；
3. 模板用到的折叠键与 `useSettings.js` 的 `SETTINGS_CARDS` 注册表完全一致，
   且每个键都有对应的 `set-body-<key>` 面板；`SETTINGS_DEFAULT_COLLAPSED` 不多不少；
4. 设置页不用 `disabled` 输入框承担只读展示（改用 `.ro-val` 只读文本）；
5. 设置页每个带静态 `id` 的表单控件都有 `label` 绑定；敏感字段不得用静态 `type="text"`；
6. 布局与自动保存（Round 2，2026-09-22）：左分区导航 + 右双列网格不得退回单列；
   每个会写进配置的控件都必须自带 `mark(...)` 标记 —— 本轮移除了「保存配置」按钮，
   漏标即等于「这个字段不能改」（改完切走/刷新无声丢失）；离开设置页必须
   flush 未到期改动；页头保存状态的四个符号必须独立成行地出现在 `app.js` 的
   setup 返回值里。

护栏自身的有效性由 `tmp/verify_settings_v6_mutations.py` 做**变异验证**兜住：
往源码注入 9 类缺陷，逐一确认对应护栏 FAIL。只跑「全绿」不能证明护栏不是恒真
（一条 `assert True` 也是绿的）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "templates" / "index.html"
STYLE_CSS = ROOT / "static" / "css" / "style.css"
USE_SETTINGS_JS = ROOT / "static" / "js" / "composables" / "useSettings.js"
APP_JS = ROOT / "static" / "js" / "app.js"

# CSS 自定义属性名：`--seg(-seg)*`，每段至少一个字母数字。
# 刻意不允许结尾是 `-`，以免把注释里的 `var(--color-*)` 当成一个令牌名。
TOKEN_NAME = r"--[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"

# 本次改造后设置页的标题层级：h1（页面）× 1 → h2（分区）× 3 → h3（卡片）× 6
EXPECTED_SETTINGS_HEADINGS = {"h1": 1, "h2": 3, "h3": 6}


def _strip_css_comments(text: str) -> str:
    """剥离 /* ... */ ——注释里举例写的 var(--color-*) 不是真实消费。"""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


@pytest.fixture(scope="module")
def html_source() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def settings_section(html_source: str) -> str:
    """只取 `v-if="view==='settings'"` 那一个 <section>，避免其他视图干扰。"""
    m = re.search(r"<section[^>]*v-if=\"view==='settings'\"[^>]*>", html_source)
    assert m, "找不到设置页 section（v-if=\"view==='settings'\"）——结构被改动了？"
    end = html_source.index("</section>", m.start())
    return html_source[m.start():end]


@pytest.fixture(scope="module")
def use_settings_source() -> str:
    return USE_SETTINGS_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js_source() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
#  1. 令牌完整性：防「消费了未定义的 var() → 整条简写失效」
# ─────────────────────────────────────────────────────────────
def test_every_consumed_css_token_is_defined(html_source):
    """核心断言：`var(--x)` 的 `--x` 必须有定义，否则整条 CSS 简写静默失效。

    `border:1px solid var(--undefined)` 不会退化成默认色，而是
    `border-width: 0px` —— 边框彻底不渲染。2026-09-22 实测：
    `--color-border-tertiary` / `--color-background-primary` / `--color-text-primary`
    被消费却从未定义，云 OCR 四行厂商容器全部无边框。
    """
    css = STYLE_CSS.read_text(encoding="utf-8") if STYLE_CSS.exists() else ""
    blob = _strip_css_comments(html_source) + "\n" + _strip_css_comments(css)

    defined = set(re.findall(rf"({TOKEN_NAME})\s*:", blob))
    consumed = set(re.findall(rf"var\(\s*({TOKEN_NAME})", blob))
    missing = sorted(consumed - defined)

    assert not missing, (
        "以下 CSS 自定义属性被 var() 消费却从未定义 —— 消费它们的那条 CSS 简写会"
        f"整条失效（如边框宽度归零），且页面不报任何错：{missing}"
    )
    # 顺带保证断言本身没有空转（正则被改坏时会失败）
    assert len(consumed) > 40, f"只扫到 {len(consumed)} 个令牌，正则或文件结构疑似被改动"


# ─────────────────────────────────────────────────────────────
#  2. 折叠头：必须是可聚焦按钮 + aria-expanded/aria-controls
# ─────────────────────────────────────────────────────────────
def _foldable_headers(section: str) -> list[str]:
    """返回每个 `ch-toggle` 按钮的完整片段（开标签 + 内容 + 闭标签）。"""
    blocks: list[str] = []
    for m in re.finditer(r'<button[^>]*class="ch-toggle"[^>]*>', section):
        close = section.index("</button>", m.end())
        blocks.append(section[m.start(): close + len("</button>")])
    return blocks


def _layout_return_members(use_settings_source: str) -> set[str]:
    """取 `useSettingsLayout()` 那条 `return { ... }` 的成员名。

    注意函数体内还有嵌套 `_read()` 的 `return { ...DEFAULT }`，故取**最后一个**
    `return {`（useSettingsLayout 是本文件末尾的函数）。
    """
    start = use_settings_source.index("export function useSettingsLayout")
    returns = re.findall(r"^\s*return\s*\{([^}]*)\}", use_settings_source[start:], re.M)
    assert returns, "useSettingsLayout 里找不到 return { ... }"
    return set(re.findall(r"[A-Za-z_$][\w$]*", returns[-1]))


def test_settings_layout_sub_composable_is_registered(html_source, app_js_source,
                                                      use_settings_source):
    """折叠交互依赖 useSettingsLayout()，模板用的每个 layout.* 都必须能从 setup 拿到。

    `layout` 在 `app.js` 里注册。三处任一漏掉都会**静默失效**：
    - 漏 import → 模块解析阶段 ReferenceError（Vue 未挂载，整页白屏，残留 `[[ ]]`）；
    - 漏 `useSettingsLayout()` → layout 为 undefined，点击折叠头抛错；
    - 漏进 setup return → 模板里 layout 解析不到，`isCollapsed()` 报错。
    """
    assert re.search(r"const layout = useSettingsLayout\(\)", app_js_source), (
        "app.js 未注册 const layout = useSettingsLayout()"
    )
    assert re.search(r"import\s*\{[^}]*\buseSettingsLayout\b[^}]*\}\s*from\s*[\"']useSettings[\"']",
                     app_js_source), "app.js 未从 useSettings 导入 useSettingsLayout"
    assert re.search(r"^\s*layout,\s*$", app_js_source, re.M), (
        "app.js 的 setup 返回值里没有 layout —— 模板存取不到，折叠交互全部失效"
    )
    assert "layout.toggleCard(" in html_source, "模板用法变了：找不到 layout.toggleCard(...)"

    # 模板调用的每个 layout.* 成员都要在 useSettingsLayout 的 return 里
    called = set(re.findall(r"layout\.(\w+)", html_source))
    members = _layout_return_members(use_settings_source)
    missing = sorted(called - members)
    assert not missing, (
        f"模板调用了 useSettingsLayout 未返回的成员 {missing}（已返回：{sorted(members)}）"
        " —— 运行时必抛错"
    )


def test_every_collapsible_card_has_accessible_toggle(html_source, settings_section):
    """折叠头必须是 <button type="button">，带 aria-expanded + aria-controls。

    - 不是 `<button>` → 键盘 / 读屏用户没有入口（鼠标仍点得动，故肉眼看不出来）；
    - 缺 `type="button"` → 落在 form 里会变成 submit，回车即意外提交；
    - `aria-controls` 指向不存在的 id → 读屏用户被引到空处。
    """
    headers = _foldable_headers(settings_section)
    card_count = len(re.findall(r'class="card-header foldable"', settings_section))

    assert card_count >= 6, f"可折叠卡片数从 6 掉到 {card_count} —— 改造被回退了？"
    assert len(headers) == card_count, (
        f"可折叠卡片 {card_count} 张，但带 class=\"ch-toggle\" 的按钮只有 "
        f"{len(headers)} 个 —— 有卡片失去了折叠入口"
    )

    body_ids = set(re.findall(r'id="(set-body-[^"]+)"', html_source))
    for idx, block in enumerate(headers, 1):
        opened = block[: block.index(">") + 1]
        assert 'type="button"' in opened, f"第 {idx} 个折叠头缺 type=\"button\"：{opened}"
        assert ":aria-expanded=" in opened, f"第 {idx} 个折叠头缺 aria-expanded：{opened}"
        m = re.search(r'(?<![:\w-])aria-controls="([^"]+)"', opened)
        # aria-controls 允许是静态 id；动态绑定按「:aria-controls」处理
        if m:
            assert m.group(1) in body_ids, (
                f"第 {idx} 个折叠头 aria-controls=\"{m.group(1)}\" 指向不存在的 id"
            )
        else:
            assert ":aria-controls=" in opened, f"第 {idx} 个折叠头既无静态也无动态 aria-controls"
        inner = block[block.index(">") + 1: -len("</button>")]
        assert "<button" not in inner, f"第 {idx} 个折叠头内部嵌套了 <button>（非法嵌套）"


# ─────────────────────────────────────────────────────────────
#  3. 折叠键一致性：模板 ↔ useSettings.js 注册表
# ─────────────────────────────────────────────────────────────
def _js_object_entries(source: str, const_name: str) -> dict[str, str]:
    """取 `const NAME = { k: v, ... }` 的「键 → 原始值」映射。"""
    m = re.search(rf"const {const_name}\s*=\s*\{{(.*?)\}}\s*;", source, re.S)
    assert m, f"useSettings.js 里找不到 {const_name} 的定义"
    return dict(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*([^,]+)", m.group(1)))


def _js_object_keys(source: str, const_name: str) -> set[str]:
    """取 `const NAME = { ... }` 里的顶层键。"""
    return set(_js_object_entries(source, const_name))


def _cards_registry(use_settings_source: str) -> set[str]:
    """SETTINGS_CARDS（导出数组，元素形如 `{ key: "llm", label: "..." }`）的键集合。

    抽成 helper：折叠键一致性、默认折叠表两处都要用它，避免同一段正则抄两遍。
    """
    m = re.search(r"export const SETTINGS_CARDS\s*=\s*\[(.*?)\];", use_settings_source, re.S)
    assert m, "useSettings.js 里找不到 export const SETTINGS_CARDS"
    return set(re.findall(r'key:\s*"([^"]+)"', m.group(1)))


def test_fold_keys_match_layout_registry(html_source, settings_section, use_settings_source):
    """模板用的折叠键必须都在 SETTINGS_CARDS 里，且每张卡都有对应的 body id。

    拼错键（如 'ocr2'）不会报错，只会让该卡片**永远折叠不起来** —— 静默失效。
    """
    registered = _cards_registry(use_settings_source)

    used = set(re.findall(r"layout\.isCollapsed\('([^']+)'\)", settings_section))
    toggled = set(re.findall(r"layout\.toggleCard\('([^']+)'\)", settings_section))

    assert used == toggled, (
        f"折叠态判断与切换的键不一致 —— 判断了却点不动（或反之）："
        f"仅判断 {sorted(used - toggled)} / 仅切换 {sorted(toggled - used)}"
    )
    unknown = sorted(used - registered)
    assert not unknown, (
        f"模板用了注册表里没有的折叠键 {unknown}；已知键 = {sorted(registered)}。"
        "拼错的键会让该卡片永远折叠不起来，且不报任何错。"
    )
    assert used == registered, (
        f"注册了 {sorted(registered)} 但模板只用到 {sorted(used)} —— 有卡片漏了折叠入口"
    )

    body_ids = {i[len("set-body-"):] for i in re.findall(r'id="(set-body-[^"]+)"', settings_section)}
    assert body_ids == registered, (
        f"折叠面板 id 与注册表不匹配：模板 {sorted(body_ids)} vs 注册表 {sorted(registered)}"
    )


def test_default_collapsed_covers_exactly_the_registry(use_settings_source):
    """SETTINGS_DEFAULT_COLLAPSED 的键集合必须与 SETTINGS_CARDS 完全一致。

    多一个键（拼错）会让某卡片「按默认应折叠」却展开；
    少一个键会让 `collapsed.value[key]` 恒为 undefined → 该卡永远展开。
    """
    cards = _cards_registry(use_settings_source)
    entries = _js_object_entries(use_settings_source, "SETTINGS_DEFAULT_COLLAPSED")
    defaults = set(entries)
    assert defaults == cards, (
        f"默认折叠表与卡片注册表不一致：多 {sorted(defaults - cards)} / 少 {sorted(cards - defaults)}"
    )

    # 默认态必须「至少留一张展开、且不全展开」：全折叠会让页面看起来空，
    # 全展开又回到改前的 2.35 屏（spec §4 要求 ≤ 1.4 屏）
    opened = sorted(k for k, v in entries.items() if v.strip() == "false")
    assert 0 < len(opened) < len(cards), (
        f"默认展开的卡片数 = {len(opened)}（{opened}）—— 要么全折叠（页面看着空），"
        f"要么全展开（丢掉本次改造的紧凑收益）"
    )


def test_collapsed_preference_is_persisted(use_settings_source):
    """折叠偏好必须**真的**读写 localStorage —— 否则刷新即复位，默认折叠形同虚设。

    断言收得比较实：读 / 写都必须以同一个键常量调用 localStorage，
    而不是仅存在一个「名字像键」的常量（改名或删掉调用都要能被抓到）。
    """
    key_m = re.search(r"const\s+(SETTINGS_LS_KEY)\s*=\s*\"([^\"]+)\"", use_settings_source)
    assert key_m, "缺少存储键常量 const SETTINGS_LS_KEY = \"...\""
    key_name = key_m.group(1)

    assert re.search(rf"localStorage\.getItem\(\s*{key_name}\s*\)", use_settings_source), (
        f"没有读取折叠偏好的调用 localStorage.getItem({key_name}) —— 刷新后不会恢复"
    )
    assert re.search(rf"localStorage\.setItem\(\s*{key_name}\s*,", use_settings_source), (
        f"没有写入折叠偏好的调用 localStorage.setItem({key_name}, ...) —— 折叠状态不落盘"
    )
    # 读写都要有兜底：隐私模式 / 值被改坏时不得抛错中断渲染
    assert len(re.findall(r"catch\s*\(e\)", use_settings_source)) >= 2, (
        "折叠偏好的读与写都应各自 try/catch 兜底（隐私模式 localStorage 会抛错）"
    )


# ─────────────────────────────────────────────────────────────
#  3b. 标题层级 / 模板插值 / 静态版本号
# ─────────────────────────────────────────────────────────────
def test_settings_heading_hierarchy(settings_section):
    """设置页标题层级：h1（页面）× 1 → h2（分区）× 3 → h3（卡片）× 6，且不得跳级。

    改造前整页 `<h1>~<h6>` 数为 **0** —— 读屏用户无法按标题在页面里跳转。
    分区标题若退回 `<div>`，层级就断了（卡片 h3 的父级没有标题），
    所以这里同时钉住数量与嵌套关系（h2 必须出现在 h3 之前）。
    """
    counts = {tag: len(re.findall(rf"<{tag}\b", settings_section)) for tag in ("h1", "h2", "h3", "h4")}
    for tag, want in EXPECTED_SETTINGS_HEADINGS.items():
        assert counts[tag] == want, (
            f"设置页 <{tag}> 数 = {counts[tag]}，应为 {want}（实测：{counts}）"
        )
    assert counts["h4"] == 0, "设置页出现了 <h4> —— 卡片内不应再开新层级"

    first_h2 = settings_section.index("<h2")
    first_h3 = settings_section.index("<h3")
    assert first_h2 < first_h3, "分级顺序反了：卡片 <h3> 出现在分区 <h2> 之前"


def test_settings_section_uses_vue_interpolation(settings_section):
    """Vue 插值必须写 `[[ ]]`。

    本模板由 Jinja 渲染，`{{ }}` 会被 Jinja **先**解析：未被定义的变量渲染成空串、
    或直接抛 TemplateSyntaxError。写错时要么页面缺内容、要么整页 500，
    而且报错位置指向 Flask 而非那一行模板，很难定位。
    """
    bad = re.findall(r"\{\{[^}]*\}\}", settings_section)
    assert not bad, (
        f"设置页出现 `{{{{ }}}}` 插值（会被 Jinja 抢解析，应用 `[[ ]]`）：{bad[:3]}"
    )


def test_js_modules_carry_static_version_query(html_source):
    """importmap 里每个模块与 app.js 必须带 `?v=<数字>` 版本号。

    版本号是浏览器缓存的唯一破口：漏掉 → 用户端 JS 永久走旧缓存，
    表现为「改了代码但界面没变」，且刷新也未必生效（需强刷）。
    ⚠️ 本测试只能保证「版本号存在且为数字」，**无法**判断「这次改动该不该 bump」——
    该不该 bump 仍靠人工（AGENTS.md 明文约定）+ 交付时提醒强刷。
    """
    entries = dict(re.findall(r'"([\w.-]+)":\s*"([^"]+)"', html_source))
    assert entries, "index.html 里找不到 importmap 的 imports 映射"
    unversioned = sorted(k for k, v in entries.items() if not re.search(r"\?v=\d+$", v))
    assert not unversioned, f"这些 importmap 模块没有 ?v= 版本号：{unversioned}"

    m = re.search(r'<script\s+type="module"\s+src="([^"]+)"', html_source)
    assert m, "找不到入口 <script type=\"module\" src=\"...\">"
    assert re.search(r"\?v=\d+$", m.group(1)), f"app.js 入口缺 ?v= 版本号：{m.group(1)}"

    css = re.search(r'<link rel="stylesheet" href="(/static/[^"]+)"', html_source)
    if css:
        assert re.search(r"\?v=\d+$", css.group(1)), f"本地 style.css 缺 ?v= 版本号：{css.group(1)}"


def test_settings_page_has_no_hardcoded_colors(html_source, settings_section):
    """设置页不得出现硬编码 `#RRGGBB` 色值（DESIGN.md §21 / §222 / §272 明令）。

    > 所有颜色必须用语义变量引用，**禁止**在 HTML 写死 HEX。
    > 缺 Token 先补到 `:root`，不临时写 HEX。

    2026-09-22 code-review 抓到 `.chip-on{color:#15803D}` 就是这种写法 ——
    它「看着没问题」（对比度达标、颜色也对），所以只有静态断言拦得住。
    已改为 `var(--color-success-strong)`。

    说明：`rgba(...)` 光晕**不在**本断言范围内 —— 项目既有 house pattern 就这么写
    （见 `.status-dot.on{box-shadow:0 0 0 3px rgba(22,163,74,.15)}`），
    新增的 `.status-dot.danger` 与之保持一致。
    """
    start_marker = "/* ===== 系统设置页"
    end_marker = "/* 新增投诉 · 悬浮操作栏"
    assert start_marker in html_source, f"设置页样式块起点锚丢失：{start_marker}"
    assert end_marker in html_source, f"设置页样式块终点锚丢失：{end_marker}"
    css_block = html_source[html_source.index(start_marker): html_source.index(end_marker)]

    hex_in_css = sorted(set(re.findall(r"#[0-9A-Fa-f]{3,8}\b", css_block)))
    assert not hex_in_css, (
        f"设置页样式块里出现硬编码 HEX（应改用语义令牌，缺令牌先补到 :root）：{hex_in_css}"
    )
    hex_in_html = sorted(set(re.findall(r"#[0-9A-Fa-f]{3,8}\b", settings_section)))
    assert not hex_in_html, f"设置页模板内联样式里出现硬编码 HEX：{hex_in_html}"


# ─────────────────────────────────────────────────────────────
#  4. 只读展示：不用 disabled 输入框
# ─────────────────────────────────────────────────────────────
def test_settings_page_has_no_disabled_form_controls(settings_section):
    """`disabled` 输入框会被读成「现在不能改，等会儿能改」，而其实永远改不了。

    设置页的「我的资料」曾用 3 个 disabled input 展示用户名/姓名/角色，
    现改为 `.ro-val` 纯文本。禁用态按钮（loading / 权限 gate）不受此约束。
    """
    controls = re.findall(r"<(?:input|select|textarea)\b[^>]*>", settings_section)
    disabled = [c for c in controls if re.search(r'(?<![:\w-])disabled\b', c)]
    assert not disabled, (
        "设置页仍有 disabled 输入框承担只读展示（应改用 .ro-val 文本）："
        f"{disabled}"
    )


# ─────────────────────────────────────────────────────────────
#  5. 标签绑定 + 敏感字段不得明文
# ─────────────────────────────────────────────────────────────
def test_every_settings_control_with_id_has_a_label(settings_section):
    """每个表单控件都要有标签：静态 id 配 `label[for]`，动态 id 配同表达式的 `:for`。

    注意正则要用 `(?<![:\\w-])id="` —— 否则会把 Vue 的 `:id="..."` 当成静态 id
    （`:` 不是词字符，裸 `\\bid=` 照样命中 `:id=`），从而产生假失败。
    """
    # 静态 id：必须被某个 for="X"（或 :for 里的字符串字面量）引用
    # 控件集合含 textarea：只扫 input|select 会漏掉多行输入框的标签绑定
    control_ids = {
        m.group(1)
        for m in re.finditer(
            r"<(?:input|select|textarea)\b[^>]*(?<![:\w-])id=\"([^\"]+)\"", settings_section)
    }
    labelled: set[str] = set()
    for m in re.finditer(r'(?<![:\w-])for="([^"]+)"', settings_section):
        labelled.add(m.group(1))
    for m in re.finditer(r':for="([^"]+)"', settings_section):
        labelled.update(re.findall(r"'([^']+)'", m.group(1)))

    unbound = sorted(control_ids - labelled)
    assert not unbound, (
        f"设置页这些表单控件没有 label[for] 绑定（读屏用户听不到字段名）：{unbound}"
    )

    # 动态 id（v-for 生成的密钥框）：必须存在与之完全同表达式的 :for
    dyn_ids = {
        m.group(1).strip()
        for m in re.finditer(r"<(?:input|select)\b[^>]*:id=\"([^\"]+)\"", settings_section)
    }
    dyn_fors = {
        m.group(1).strip()
        for m in re.finditer(r':for="([^"]+)"', settings_section)
    }
    unpaired = sorted(dyn_ids - dyn_fors)
    assert not unpaired, (
        f"这些动态 id 的控件没有同表达式的 :for（v-for 里 id/for 表达式必须一致，"
        f"否则读屏与点标签聚焦都会断）：{unpaired}"
    )


def test_secret_fields_are_not_plaintext(settings_section, use_settings_source):
    """API Key / Secret 不得静态 `type="text"` 明文常驻。

    - LLM 的 `#s_key` 必须是动态 `:type`，且**非显示态分支是 password**；
    - 云 OCR 密钥框由 `f.secret` 驱动（后端 PROVIDER_META 里 secret=True）；
    - 设置页不允许出现静态 `type="text"` 的 input（本次改造后为 0 个）。
    """
    static_text = re.findall(r"<input[^>]*\btype=\"text\"[^>]*>", settings_section)
    assert not static_text, f"设置页出现静态 type=\"text\" 的 input（敏感字段会明文）：{static_text}"

    m = re.search(r"<input[^>]*\bid=\"s_key\"[^>]*>", settings_section)
    assert m, "找不到 #s_key（LLM API Key 输入框）—— 被改名或删除了？"
    tag = m.group(0)
    assert ':type="' in tag, "#s_key 必须是动态 :type（否则无法打码）"
    dyn = re.search(r':type="([^"]+)"', tag).group(1)
    assert "'password'" in dyn, f"#s_key 的动态 type 里没有 password 分支：{dyn}"
    assert re.search(r":\s*'password'\s*$", dyn.strip()), (
        f"#s_key 的默认（非显示）分支应为 password，实际：{dyn}"
    )

    # 云 OCR 密钥框：类型由 f.secret 决定，且注册表里必须有 secret 标记的字段
    assert re.search(r":type=\"f\.secret \? 'password' : 'text'\"", settings_section), (
        "云 OCR 密钥框不再是 f.secret 驱动的动态 type —— 密钥可能明文常驻"
    )
    meta_src = (ROOT / "services" / "cloud_ocr" / "__init__.py").read_text(encoding="utf-8")
    assert '"secret": True' in meta_src, (
        "PROVIDER_META 里没有任何 \"secret\": True 字段 —— 密钥框会全部退化为明文"
    )


# ─────────────────────────────────────────────────────────────
#  6. Round 2（2026-09-22）：吸顶页头 + 左分区导航 + 双列网格 + 自动保存
# ─────────────────────────────────────────────────────────────
def _settings_css_block(html_source: str) -> str:
    """取设置页样式块（两个锚点之间），供下面几条断言共用。"""
    start = html_source.index("/* ===== 系统设置页")
    end = html_source.index("/* 新增投诉 · 悬浮操作栏")
    assert start < end, "设置页样式块的两个锚点顺序反了"
    return html_source[start:end]


def test_settings_layout_is_nav_plus_two_columns(html_source, settings_section):
    """布局 = 左分区导航 + 右双列网格；旧的单列容器与常驻保存条不得残留。

    静默失效路径（都不报错、页面照常显示）：
    - `.set-grid` 被改回单列 → 宽表格卡压成半栏，表格挤成一团；
    - `.span2{grid-column:span 2}` 丢失 → 用户在宽屏右半边看到大片空白；
    - 旧保存条 `.set-save` 回归 → 又变成「一个按钮管两张卡」的语义。
    """
    css = _settings_css_block(html_source)
    assert ".set-wrap{display:grid;grid-template-columns:212px minmax(0,1fr)" in css, (
        "左导航 + 内容的整体栅格定义变了（期望 212px 导航 + 自适应内容）"
    )
    assert ".set-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))" in css, (
        "内容区不再是双列网格 —— 右半边会空出来"
    )
    assert ".set-grid .span2{grid-column:span 2}" in css, (
        "缺少跨列规则（.span2）—— 宽表格卡会被压进半栏"
    )
    assert ".sn-item.on{" in css, "左导航缺少当前项高亮样式"
    for dead in (".set-bar", ".set-save"):
        assert dead not in css, f"{dead} 是 Round 2 已删除的旧结构，不应回归"

    assert 'class="set-nav"' in settings_section, "设置页没有左分区导航"
    assert 'class="set-head"' in settings_section, "设置页没有吸顶页头"
    assert settings_section.count('class="set-grid"') == 3, (
        f"应有 3 个分区网格（AI 能力/存储与归档/账号与权限），实际 "
        f"{settings_section.count('class=\"set-grid\"')} 个"
    )
    assert settings_section.count('class="card panel span2"') == 4, (
        "跨列卡数量变了（共享盘 / 我的资料 / 用户管理 / 历史映射 应各占整行）"
    )
    # 卡片锚点是「点导航定位」与「滚动高亮」的唯一依据
    assert len(re.findall(r'id="card-[a-z]+"', settings_section)) == 6, (
        "每张卡都要有 id=\"card-<key>\" 锚点（导航跳转与滚动联动靠它）"
    )


def test_save_pill_is_registered_and_readable(html_source, settings_section):
    """`.save-pill` 是 DESIGN.md 登记的「第二个合法按钮例外」，四条硬约束逐条断言。

    静默失效路径（胶囊照常显示、页面照常工作，只有用户看得出来）：
    - 只读态丢掉 `opacity:1` → 继承 `.btn:disabled` 的 `.5`，状态文字灰到读不清，
      而胶囊 **99% 的时间就是只读态**；
    - `err` 态丢掉 `cursor:pointer` → 保存失败后没有任何重试入口；
    - 状态色改用硬编码 HEX → 违反 DESIGN.md「颜色必须走语义变量」；
    - 组件未在 DESIGN.md 登记 → 就是绕过「所有按钮 = `.btn` + 变体」这条强制规则。

    最后一条是**双向绑定**：删掉 DESIGN.md 的登记段落，这个测试就会红。
    """
    css = _settings_css_block(html_source)
    assert ".save-pill{" in css, "页头保存状态胶囊的样式丢失"

    assert re.search(r"\.save-pill:disabled\{opacity:1\}", css), (
        "只读态没有覆盖 :disabled 的透明度 —— 状态文字会灰到读不清（胶囊常态即只读）"
    )

    err = re.search(r"\.save-pill\.err\{([^}]*)\}", css)
    assert err, "缺少 .save-pill.err 样式 —— 保存失败态没有视觉表达"
    assert "cursor:pointer" in err.group(1), ".err 态不是可点态 —— 保存失败后没有重试入口"

    for token in ("--color-warning-50", "--color-success-50", "--color-danger-50"):
        assert token in css, f"保存胶囊的状态色没有用语义令牌 {token}"
    for body in re.findall(r"\.save-pill[^{]*\{([^}]*)\}", css):
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body), (
            f"保存胶囊写了硬编码 HEX：{body.strip()[:80]} —— 违反 DESIGN.md「颜色走语义变量」"
        )

    btn = re.search(r'<button[^>]*class="save-pill"[^>]*>', settings_section)
    assert btn, "页头找不到 .save-pill 按钮"
    tag = btn.group(0)
    assert 'type="button"' in tag, '保存胶囊缺少 type="button"（在表单里会误触发提交）'
    assert ":disabled=" in tag, "保存胶囊没有绑定 :disabled —— 非失败态会变成可点"

    design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    # 查「登记标题」而非「字符串出现过」：只查字符串的话，把登记段落删掉、
    # 而下方示例 CSS 块仍留着 `.save-pill{`，护栏就抓不到（变异验证 M13 发现的弱化点）。
    assert "### Save Pill" in design, (
        ".save-pill 在 DESIGN.md 里没有登记段落 —— 「所有按钮 = .btn + 变体」是强制规则，"
        "例外必须显式登记（照 .ch-toggle 的先例），不得靠「没人发现」存在"
    )
    assert ".save-pill{" in design, "DESIGN.md 登记了 .save-pill 却没附示例样式"


# 「这次改动要落盘」在模板里的唯一声明式入口。
# 事件名放宽到 change|input|click：文本/数字输入框用 @input（击键即标记 ——
# 只挂 @change 时，「填了密钥直接刷新/关标签页」这条路径上 blur 不保证发生，
# change 也就不触发，改动静默丢失，正是本轮要修的老毛病）；
# <select> 与 <input type=checkbox> 用 @change；按钮类用 @click。
MARK_EVENT = r'@(?:change|input|click)="[^"]*\bmark\('

# 设置段里绑定目标**不是** `cfg.*`、但**确实会写进配置**的控件。
# 通用扫描按 `cfg.` 前缀过滤会把它们漏掉，所以必须额外点名。
SETTINGS_INDIRECT_CONFIG_WRITERS = {
    "providerSel",   # #s_provider：applyProvider() 改写 cfg.llm.model / api_url / max_tokens
    "modelSel",      # #s_model_sel：写 cfg.llm.model / max_tokens
}

# 设置段里**不写配置**的绑定目标（纯界面状态 / 搜索词）。
# 新增控件想豁免，必须显式登记在这里 —— 这是「新增字段漏标」的唯一出口。
SETTINGS_NON_CONFIG_BINDINGS = {
    "orQuery",       # #s_or_q：只筛选 OpenRouter 模型清单的显示，不进 cfg
}


def test_every_config_control_marks_autosave(settings_section):
    """设置页里每个「会写进配置」的控件都必须自带 mark(...) —— 否则改了不落盘。

    静默失效路径（页面上完全看不出来）：新增一个配置字段却忘了加
    `@input="mark('llm')"` → 用户改完、切走视图或刷新时改动无声消失。
    旧版至少还有「保存配置」按钮兜底；本轮把按钮换成自动保存，这层兜底没有了
    —— 漏标即等于「这个字段不能改」。

    ⚠️ 本断言**不是**「数够 N 个就放过」。历史教训：初版用
    `v-model="cfg.*"` 前缀 + `len >= 8` 过滤，而段内真正写配置的控件是
    6 个 `cfg.*` + 2 个间接写入者（providerSel / modelSel）—— 数字凑对了、
    口径却漏了一半：删掉一个 `cfg.*` 控件再补个间接写入者，数量照样是 8，
    护栏完全空转（实测确实如此）。

    故改为**逐个控件按绑定目标分类**，四类：
      - `cfg.*`                   → 写配置，必须带 mark(...)
      - INDIRECT_CONFIG_WRITERS   → 写配置（间接），必须带 mark(...)
      - NON_CONFIG_BINDINGS       → 显式登记为「不写配置」，豁免
      - 其余                      → 未分类，直接失败，逼作者当场决定
    """
    # 逐个取控件标签。`[^>]*` 本身就跨行匹配（re.S 在此不起作用），
    # 云 OCR 动态字段 input 的属性是多行写的，靠这条才扫得到。
    controls = re.findall(r"<(?:input|select|textarea)\b[^>]*>", settings_section)

    # 底线：扫到的控件数不得塌陷。正则失配 / section 边界被改 → 下面集体空转。
    assert len(controls) >= 10, (
        f"设置段只扫到 {len(controls)} 个控件（当前结构为 10）—— "
        "section 边界或控件正则被改动了，本断言及其后几条会空转"
    )

    writers, unmarked, unclassified = [], [], []
    for tag in controls:
        vm = re.search(r'v-model(?:\.\w+)?="([^"]+)"', tag)
        if not vm:
            continue                      # 无 v-model 的控件不在本断言范围
        target = vm.group(1)
        if target.startswith("cfg.") or target in SETTINGS_INDIRECT_CONFIG_WRITERS:
            writers.append(target)
            if not re.search(MARK_EVENT, tag):
                unmarked.append(target)
        elif target not in SETTINGS_NON_CONFIG_BINDINGS:
            unclassified.append(target)

    assert len(writers) >= 8, (
        f"只认出 {len(writers)} 个会写配置的控件（当前为 8）—— "
        "分类口径被改动，或有人删掉了配置控件；护栏不能空转"
    )
    assert not unmarked, (
        "下列控件会写进配置却没有自动保存标记（改完不会落盘，页面毫无提示）：\n  "
        + "\n  ".join(unmarked)
        + "\n修法：文本/数字输入框加 @input=\"mark('llm')\"；下拉/勾选加 @change=\"mark('ocr')\""
    )
    assert not unclassified, (
        "下列控件的 v-model 目标既不是 cfg.*、也没登记为「不写配置」：\n  "
        + "\n  ".join(unclassified)
        + "\n若它确实不写配置，加进 SETTINGS_NON_CONFIG_BINDINGS；"
        "若它写配置，补上 mark(...) 标记"
    )

    # 间接写入者逐个点名 —— 通用扫描无法凭空知道它们写配置。
    for sel_id, why in (
        ("s_provider", "厂商下拉：applyProvider() 改写 cfg.llm.model / api_url / max_tokens"),
        ("s_model_sel", "模型下拉：写 cfg.llm.model / max_tokens"),
    ):
        m = re.search(r'<select[^>]*\bid="' + sel_id + r'"[^>]*>', settings_section)
        assert m, f"找不到 #{sel_id}（{why}）—— 被改名或删除了？"
        assert re.search(MARK_EVENT, m.group(0)), (
            f"#{sel_id} 未标记自动保存 —— {why}，改完不落盘"
        )

    # OCR 厂商上移/下移按钮同样改写配置（providers 数组顺序），且不带 v-model。
    movers = re.findall(r'<button[^>]*@click="[^"]*cloudOcr\.move\([^"]*"', settings_section)
    assert len(movers) == 2, (
        f"OCR 厂商上移/下移按钮应为 2 个，实际 {len(movers)} 个 —— 顺序调整入口被改动了"
    )
    assert all(re.search(MARK_EVENT, b) for b in movers), (
        "OCR 厂商上移/下移未标记自动保存 —— 调完优先级不会落盘"
    )


def test_text_inputs_mark_on_input_not_blur(settings_section):
    """写配置的**文本/数字输入框**必须用 `@input` 触发自动保存，不能只挂 `@change`。

    与上一条的分工：上一条只要求「改动会被标记」(`change|input|click` 皆可)；
    这一条额外要求文本输入框**在击键时就标记**。

    理由是一个真实丢数据的路径：`@change` 只在**失焦**时触发，而
    「填完 API Key → 直接 Cmd+R 刷新 / 关标签页」这条路径上 blur 不保证发生，
    change 也就可能不触发 → 自动保存从未排队 → 改动静默丢失。这正是本轮要修的
    原始症状（旧版：填了密钥刷新即丢）。`mark()` 自带 900ms 防抖，所以
    `@input` 不会把每次击键变成一个 PUT。

    `<select>` 与 `<input type=checkbox>` 不在此列：它们的 change 本身就是
    即时语义（点选即触发），不存在「未失焦」的空窗。
    """
    bad = []
    for tag in re.findall(r"<input\b[^>]*>", settings_section):
        if not re.search(r'v-model(?:\.\w+)?="cfg\.', tag):
            continue
        if re.search(r'type="(?:checkbox|radio)"', tag):
            continue                       # 勾选/单选：change 即点选瞬间
        if "@input=" not in tag:
            bad.append(" ".join(tag.split())[:120])
    assert not bad, (
        "下列写配置的文本/数字输入框没有用 @input 触发自动保存"
        "（只挂 @change 时，「填完直接刷新/关标签页」改动会丢）：\n  "
        + "\n  ".join(bad)
    )

    # 反向兜底：至少确认扫到了这几个输入框，否则上面是空转
    marks = re.findall(r'<input\b[^>]*@input="[^"]*\bmark\(', settings_section)
    assert len(marks) >= 5, (
        f"只扫到 {len(marks)} 个带 @input 自动保存标记的输入框（预期 5："
        "模型名 / API 地址 / API Key / 最大 Tokens + OCR 动态字段）—— 扫描口径失效"
    )


def test_nav_is_rendered_from_registry(settings_section, use_settings_source):
    """左导航由注册表渲染，且注册表里每张卡都带导航所需的 navLabel / icon / kw。

    缺 navLabel → 导航项空白；缺 kw → 搜不到该卡；分组写错 → 导航里整组消失。
    这些都不会报错，只会让「这里能改什么」重新变得看不出来。
    """
    assert re.search(r'v-for="g in layout\.groups"', settings_section), (
        "左导航没有按分区渲染（layout.groups）"
    )
    assert re.search(r'v-for="c in layout\.cardsOf\(g\.key\)"', settings_section), (
        "左导航没有按注册表渲染（layout.cardsOf）"
    )
    assert 'class="sn-item"' in settings_section, "左导航项样式类变了"
    assert "layout.matches(c.key)" in settings_section, "导航项没有跟随搜索过滤"
    assert "layout.jumpTo(c.key)" in settings_section, "导航项失去定位能力"
    assert "setNavBadge(c.key)" in settings_section, "导航项没有状态徽章"

    m = re.search(r"export const SETTINGS_CARDS\s*=\s*\[(.*?)\];", use_settings_source, re.S)
    assert m, "useSettings.js 里找不到 export const SETTINGS_CARDS"
    body = m.group(1)
    n_cards = len(re.findall(r'key:\s*"', body))
    for field in ("navLabel:", "icon:", "kw:", "group:", "label:"):
        assert body.count(field) == n_cards, (
            f"SETTINGS_CARDS 里 {field} 出现 {body.count(field)} 次，卡片数 {n_cards} —— "
            "有卡片缺这项，导航/搜索会静默失效"
        )

    g = re.search(r"export const SETTINGS_GROUPS\s*=\s*\[(.*?)\];", use_settings_source, re.S)
    assert g, "useSettings.js 里找不到 export const SETTINGS_GROUPS"
    declared = set(re.findall(r'key:\s*"([^"]+)"', g.group(1)))
    used = set(re.findall(r'group:\s*"([^"]+)"', body))
    assert used == declared, (
        f"卡片声明的分组与 SETTINGS_GROUPS 不一致：多 {sorted(used - declared)} / "
        f"少 {sorted(declared - used)}"
    )


def test_autosave_is_wired_to_single_put(use_settings_source, app_js_source, html_source):
    """自动保存链路完整：防抖 → 单次 PUT 整份 cfg → 成功清脏 / 失败可重试。

    静默失效路径：
    - 只有 mark() 没有 setTimeout → 改了永远不提交；
    - 只有定时器不清 dirtyKeys → 卡片头与导航一直显示「未保存」；
    - 少了失败态 → 保存失败后没有任何提示，用户以为存上了（旧版此处正是「填了密钥刷新即丢」）。
    """
    assert re.search(r"function mark\(key\)", use_settings_source), "缺少自动保存入口 mark()"
    assert re.search(r"setTimeout\(\(\) => \{[^}]*saveCfg\(\)", use_settings_source, re.S), (
        "mark() 没有起防抖定时器 —— 改了不会自动提交"
    )
    assert re.search(r'putJ\("/api/config", cfg\.value\)', use_settings_source), (
        "自动保存必须复用同一次 PUT /api/config 提交整份 cfg（语义不得另起一套）"
    )
    assert re.search(r"dirtyKeys\.value = \{\}", use_settings_source), (
        "保存成功后没有清脏 —— 会一直提示「未保存」"
    )
    assert re.search(r'if \(saveState\.value !== "error"\) return;', use_settings_source), (
        "缺少失败重试的守卫（只有失败态才允许点）"
    )

    # 模板里必须**不带 `.value`**：`saveText` 是 setup 顶层 ref，模板会自动解包，
    # 写成 `saveText.value` 求值为 undefined（旧版本此处把 bug 当成规范钉住了，
    # 见 test_template_never_dot_values_a_top_level_setup_symbol）。
    for sym in ("saveText", "saveCls", "saveIcon", "retrySave()"):
        assert sym in html_source, f"页头保存状态没有接上 {sym}"
    assert "save-pill" in html_source, "页头缺少保存状态胶囊"

    for sym in ("dirtyKeys", "mark", "retrySave", "saveText", "saveCls", "saveIcon",
                "saveRetryable", "layout", "setNavBadge"):
        assert re.search(rf"^\s*{sym},\s*$", app_js_source, re.M), (
            f"app.js 的 setup 返回值里没有独立成行的 {sym} —— 模板取不到，该功能静默失效"
        )


def test_save_success_only_clears_keys_the_request_covered(use_settings_source):
    """保存成功时只能清「已被本次请求覆盖」的脏键，不得无条件清空。

    静默数据丢失路径（无报错，页头甚至显示「已保存」）：
      t=0    用户改 A 字段 → mark('llm')，900ms 后发出 PUT；
      t=950  用户在 PUT 途中又改 B 字段 → mark('llm')（此时 saveState==='saving'，
             mark 不改状态，只重置定时器）；
      t=1000 PUT 成功 → 旧实现 `dirtyKeys.value = {}` 把 B 的脏标记一并清掉 →
             页头显示「已保存」、`.savedot` 消失；
      随后用户切走再进入设置页 → `loadCfg()` 用服务端返回值整体覆盖 `cfg`
             → B 的改动**永久丢失**。

    正确做法：发请求前快照改动序号，成功后只清序号 ≤ 快照的键。
    """
    assert re.search(r"dirtyRev\[key\] = \+\+_rev", use_settings_source), (
        "mark() 没有记录该键的改动序号 —— 无法区分「本次已提交」与「请求期间新产生」"
    )
    assert re.search(r"const myRev = _rev;", use_settings_source), (
        "saveCfg() 发请求前没有快照改动序号"
    )
    assert re.search(r"if \(r > myRev\)", use_settings_source), (
        "保存成功没有按序号过滤 —— 请求期间的新改动会被误清脏（切走再进来即丢）"
    )
    assert not re.search(
        r'saveState\.value = "saved";\s*\n\s*dirtyKeys\.value = \{\};', use_settings_source
    ), "保存成功处退回了「先置 saved 再无脑清脏」的旧写法"
    assert re.search(r"function _clearDirty\(\)", use_settings_source), (
        "缺少统一的清脏入口 _clearDirty()"
    )
    assert use_settings_source.count("dirtyKeys.value = {}") == 1, (
        "清脏点应只有 _clearDirty() 一处 —— 出现多处说明有人绕过序号过滤直接清空"
    )


def test_template_never_dot_values_a_top_level_setup_symbol(html_source, app_js_source):
    """模板里不得对 setup **顶层**返回的符号写 `.value`。

    静默失效路径（2026-09-22 实测踩中：点「系统设置」后内容区直接消失，页面无任何提示）：
    setup 返回的顶层 ref / computed 在模板中会被 Vue **自动解包**，于是
    `dirtyKeys.value` 求值为 `undefined`，紧接着读 `.llm` 抛
    `TypeError: Cannot read properties of undefined (reading 'llm')`
    —— 该 `<section>` 的 render 整体失败。pytest 全绿、`node --check` 通过、
    静态正则也全绿（旧断言甚至把 `saveText.value` 当成规范钉住了），
    只有真实浏览器渲染会炸。

    判据按**嵌套位置**区分，这是唯一容易搞混的地方：
      - 顶层 ref（`dirtyKeys` / `saveCls` / `cfg` …）→ 模板直接写名字；
      - 挂在普通对象上的 ref（`smb.smbLoading.value`、`users.list.value`）→ 必须写 `.value`。
    正则只匹配「符号紧跟 `.value`」且前面不是 `.`，所以 `layout.query.value`
    这类合规写法不会误报。
    """
    start = app_js_source.index("\n    return {")
    end = app_js_source.index("\n    };", start)
    body = re.sub(r"//[^\n]*", "", app_js_source[start:end])
    syms = sorted(
        {t for t in re.split(r"[,\{\}\s]+", body) if re.fullmatch(r"[A-Za-z_$][\w$]*", t)}
        - {"return"}
    )
    assert len(syms) >= 100, (
        f"只从 app.js 的 setup 返回值里认出 {len(syms)} 个顶层符号（实测 476）—— "
        "解析口径失效，本断言会空转"
    )

    bad = []
    for sym in syms:
        for m in re.finditer(r"(?<![\w.$])" + re.escape(sym) + r"\.value\b", html_source):
            line = html_source[: m.start()].count("\n") + 1
            bad.append(f"L{line}: {sym}.value")
    assert not bad, (
        "模板对 setup 顶层符号写了 .value（顶层 ref 会自动解包，.value 恒为 undefined，"
        "会把所在 section 的渲染整段抛挂）：\n  " + "\n  ".join(sorted(bad))
        + "\n顶层 ref 直接写名字；只有挂在普通对象上的 ref 才需要 .value"
    )


def test_switching_view_flushes_pending_save(app_js_source):
    """离开设置页时必须把未到期的自动保存 flush 掉。

    `loadCfg()` 只在**进入**设置页时调用，会整体覆盖 `cfg`；若离开时不 flush，
    防抖窗口内（900ms）改动的数据就永久丢了 —— 且用户以为自己改过了。
    """
    watch_block = app_js_source[app_js_source.index("watch(view, (v, old) => {"):]
    watch_block = watch_block[: watch_block.index("});") + 3]
    assert re.search(r"Object\.keys\(dirtyKeys\.value\)\.length", watch_block), (
        "离开设置页时没有检查是否有未落盘改动"
    )
    assert re.search(r"saveCfg\(\)", watch_block), "离开设置页时没有 flush 未保存改动"
    assert "layout.bindSpy()" in watch_block, "进入设置页没有启动滚动联动"
    assert "layout.unbindSpy()" in watch_block, "离开设置页没有解绑滚动监听（会越绑越多）"
