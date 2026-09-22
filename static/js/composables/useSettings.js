// 系统设置组合式函数
import { getJ, putJ, postJ, delJ } from "api";

export const USER_ROLES = [
  { value: "admin",   label: "管理员" },
  { value: "handler", label: "投诉专员" },
  { value: "viewer",  label: "只读审计" },
];

export function roleLabel(role) {
  return (USER_ROLES.find(r => r.value === role) || {}).label || role;
}

// 主流大模型服务商预设：选择后自动填入 API 地址、模型与该模型的最大输出 tokens
// （max 为各官方文档公布的单次最大输出，2026-08 核对；null 表示官方未公布，不自动填充）
export const LLM_PROVIDERS = [
  { key: "custom", name: "自定义", url: "", models: [] },
  { key: "qwen", name: "通义千问（阿里云百炼）", url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    models: [
      { id: "qwen3.8-max", max: 131072 },
      { id: "qwen3.7-plus", max: 131072 },
      { id: "qwen3.7-flash", max: 131072 },
      { id: "qwen-plus", max: null },
    ] },
  { key: "deepseek", name: "DeepSeek 深度求索", url: "https://api.deepseek.com/v1",
    models: [
      { id: "deepseek-v4-pro", max: 384000 },
      { id: "deepseek-v4-flash", max: 384000 },
      { id: "deepseek-v4-flash-vision-exp", max: 384000 },
    ] },
  { key: "zhipu", name: "智谱 GLM", url: "https://open.bigmodel.cn/api/paas/v4",
    models: [
      { id: "glm-5.3", max: 131072 },
      { id: "glm-5.2", max: 131072 },
      { id: "glm-4.7-flash", max: 131072 },
    ] },
  { key: "kimi", name: "Kimi 月之暗面", url: "https://api.moonshot.cn/v1",
    models: [
      { id: "kimi-k3", max: 1048576 },
      { id: "kimi-k2.7-code", max: null },
      { id: "kimi-k2.6", max: null },
    ] },
  { key: "doubao", name: "豆包（火山方舟）", url: "https://ark.cn-beijing.volces.com/api/v3",
    models: [
      { id: "doubao-seed-2-1-pro-260628", max: 262144 },
      { id: "doubao-seed-2-1-turbo-260628", max: 262144 },
      { id: "doubao-seed-evolving-latest-version", max: 262144 },
    ] },
  { key: "openai", name: "OpenAI", url: "https://api.openai.com/v1",
    models: [
      { id: "gpt-5.6-sol", max: 131072 },
      { id: "gpt-5.6-terra", max: 131072 },
      { id: "gpt-5.6-luna", max: 131072 },
    ] },
  // OpenRouter 是聚合网关（400+ 模型、换代极快），这里的 models 只是「离线兜底清单」：
  // 选中后会先从官方 GET /api/v1/models 拉全量（后端代理优先），拉取失败才回退到下面这几条
  { key: "openrouter", name: "OpenRouter（聚合网关）", url: "https://openrouter.ai/api/v1",
    models: [
      { id: "google/gemini-3.8-flash", max: 65536 },
      { id: "deepseek/deepseek-v4.1-flash", max: 384000 },
      { id: "qwen/qwen3.8-flash", max: 131072 },
      { id: "z-ai/glm-5.3-flash", max: 131072 },
      { id: "bytedance-seed/seed-2-1-turbo", max: 235929 },
    ] },
];

function createDefaultConfig() {
  const llm = () => ({ api_url: "", api_key: "", model: "", max_tokens: 2048 });
  return {
    internal_system: { base_url: "", username: "", password: "" },
    third_system: { base_url: "", username: "", password: "" },
    driving_system: { base_url: "", username: "", password: "" },
    llm: llm(),
    llm_intake: llm(),
    llm_contract_text: llm(),
    paths: { reply_dir: "", contract_dir: "", upload_dir: "" },
    // 云 OCR 多云调度（Phase 2）：providers 结构由后端 cloud_config() 归一后下发
    cloud_ocr: { enabled: true, order: [], providers: {} },
  };
}

function mergeConfig(incoming) {
  const defaults = createDefaultConfig();
  if (!incoming || incoming.error) return defaults;
  for (const [section, defaultValue] of Object.entries(defaults)) {
    const value = incoming[section];
    defaults[section] = value && typeof value === "object"
      ? { ...defaultValue, ...value }
      : defaultValue;
  }
  return defaults;
}

export function useSettings(toast) {
  const cfg = Vue.ref(createDefaultConfig());
  const cfgSaving = Vue.ref(false);
  const cfgMsg = Vue.ref("");
  const cfgOk = Vue.ref(false);
  // 服务商预设选择 + 连接测试状态
  const providerSel = Vue.ref("custom");
  const testing = Vue.ref(false);
  const testResult = Vue.ref(null);
  // API Key 默认打码显示（与云 OCR 密钥一致），用户主动点「显示」才明文
  const keyVisible = Vue.ref(false);
  // 模型选择：预设下拉 or 手动输入（自定义模型/接入点 ID）
  const modelCustom = Vue.ref(true);
  // OpenRouter 在线全量模型清单（null=未加载成功，此时用内置兜底清单）
  const orModels = Vue.ref(null);
  const orLoading = Vue.ref(false);
  const orErr = Vue.ref("");
  const orQuery = Vue.ref("");
  const presetModels = Vue.computed(() => {
    const p = LLM_PROVIDERS.find(x => x.key === providerSel.value) || {};
    if (p.key === "openrouter" && orModels.value) {
      const q = orQuery.value.trim().toLowerCase();
      if (q) return orModels.value.filter(m => m.id.toLowerCase().includes(q)).slice(0, 200);
      // 无关键字：内置推荐置顶，其余按 id 排序接在后面
      const top = p.models || [];
      const rest = orModels.value
        .filter(m => !top.some(t => t.id === m.id))
        .sort((a, b) => a.id.localeCompare(b.id));
      return [...top, ...rest].slice(0, 300);
    }
    return p.models || [];
  });
  const modelSel = Vue.computed({
    get() {
      if (modelCustom.value) return "__custom";
      return presetModels.value.some(m => m.id === cfg.value.llm.model) ? cfg.value.llm.model : "__custom";
    },
    set(v) {
      if (v === "__custom") { modelCustom.value = true; return; }
      modelCustom.value = false;
      const m = _presetModel(v);
      cfg.value.llm.model = v;
      if (m && m.max) cfg.value.llm.max_tokens = m.max;
    },
  });

  async function loadCfg() {
    try {
      const loaded = await getJ("/api/config");
      if (loaded.error) throw new Error(loaded.error);
      cfg.value = mergeConfig(loaded);
      providerSel.value = "custom";
      modelCustom.value = true;
      // 刚加载 = 与服务器一致：清脏、撤销未到期的自动保存、回到「配置已加载」
      _cancelPendingSave();
      _clearDirty();
      saveErr.value = "";
      saveState.value = "idle";
    } catch (e) {
      toast("加载配置失败", e.message, "danger");
    }
  }

  function _presetModel(modelId) {
    const p = LLM_PROVIDERS.find(x => x.key === providerSel.value);
    if (!p || p.key === "custom") return null;
    const hit = p.models.find(m => m.id === modelId);
    if (hit) return hit;
    // OpenRouter：模型可能来自在线全量清单
    return (orModels.value || []).find(m => m.id === modelId) || null;
  }

  // OpenRouter 在线模型清单：后端代理（10 分钟缓存）→ 浏览器直连官方接口 → 内置兜底清单
  // 官方 GET /api/v1/models 无需 API Key，且返回 access-control-allow-origin: *
  async function loadOpenRouterModels(force) {
    if (providerSel.value !== "openrouter") return;
    if (!force && orModels.value) return;
    orLoading.value = true;
    orErr.value = "";
    try {
      const d = await getJ("/api/config/llm/openrouter-models" + (force ? "?refresh=1" : ""));
      if (d && !d.error && Array.isArray(d.models) && d.models.length) {
        orModels.value = d.models.map(m => ({ id: m.id, max: m.max || null, vis: !!m.vis, ctx: m.ctx || null }));
        return;
      }
    } catch (e) { /* 后端不可用时落到浏览器直连 */ }
    try {
      const r = await fetch("https://openrouter.ai/api/v1/models");
      if (!r.ok) throw new Error("HTTP " + r.status);
      const raw = (await r.json()).data || [];
      orModels.value = raw.map(m => ({
        id: m.id,
        max: (m.top_provider || {}).max_completion_tokens || null,
        vis: ((m.architecture || {}).input_modalities || []).includes("image"),
        ctx: m.context_length || null,
      }));
    } catch (e) {
      orErr.value = "在线模型清单获取失败，当前显示内置推荐清单；也可在「模型名称」里手动输入，如 openai/gpt-6-astra";
    } finally {
      orLoading.value = false;
    }
  }

  function applyProvider() {
    const p = LLM_PROVIDERS.find(x => x.key === providerSel.value);
    orQuery.value = "";
    if (!p || p.key === "custom") return;
    const m = p.models[0];
    modelCustom.value = false;
    cfg.value.llm.api_url = p.url;
    cfg.value.llm.model = m ? m.id : "";
    if (m && m.max) cfg.value.llm.max_tokens = m.max;
    testResult.value = null;
    if (p.key === "openrouter") loadOpenRouterModels();
  }

  // ── 自动保存（2026-09-22 起，取代底部那条横跨两张卡的手动保存按钮）──
  // 触发：模板里每个可编辑控件在 change / click 时调 mark('llm'|'ocr')；
  // 提交：防抖到期后 PUT 整个 cfg（与手动保存**完全同一个接口与语义**）。
  // 「谁脏了」按卡片记账，仅供页头状态与卡片头徽章显示；提交内容永远是完整 cfg，
  // 因此 v-model 与 mark() 的先后顺序不影响结果 —— 不需要做 diff。
  const AUTOSAVE_MS = 900;
  const dirtyKeys = Vue.ref({});        // { llm: true, ocr: true }
  const saveState = Vue.ref("idle");    // idle | dirty | saving | saved | error
  const savedAt = Vue.ref("");
  const saveErr = Vue.ref("");
  let _saveTimer = null;
  // 改动序号：每次 mark() 自增，dirtyRev[key] 记住该键最近一次改动时的序号。
  // 保存成功时靠它区分「已被本次请求覆盖的改动」与「请求期间又产生的新改动」——
  // 后者若被一并清脏，页头会显示「已保存」而改动其实还没落盘（切走再进来即丢）。
  let _rev = 0;
  let dirtyRev = {};

  function _nowHM() {
    const d = new Date();
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  function _cancelPendingSave() {
    if (_saveTimer) { clearTimeout(_saveTimer); _saveTimer = null; }
  }

  function _clearDirty() {
    dirtyKeys.value = {};
    dirtyRev = {};
  }

  function mark(key) {
    dirtyKeys.value = { ...dirtyKeys.value, [key]: true };
    dirtyRev[key] = ++_rev;
    saveErr.value = "";
    if (saveState.value !== "saving") saveState.value = "dirty";
    _cancelPendingSave();
    _saveTimer = setTimeout(() => { _saveTimer = null; saveCfg(); }, AUTOSAVE_MS);
  }

  async function saveCfg() {
    _cancelPendingSave();
    const myRev = _rev;                  // 本次请求「看到」的改动序号（请求前快照）
    cfgSaving.value = true;
    cfgMsg.value = "";
    saveErr.value = "";
    saveState.value = "saving";
    try {
      const d = await putJ("/api/config", cfg.value);
      if (!d || !d.success) throw new Error((d && d.error) || "保存失败");
      cfgOk.value = true;
      cfgMsg.value = "已保存";
      savedAt.value = _nowHM();
      // 只清「已被本次请求覆盖」的键：序号比快照新的键是请求期间又改的，必须保留脏标记
      // —— 否则页头显示「已保存」，而用户此刻切走再进来，loadCfg 会把那些改动覆盖掉。
      const stillRev = {};
      for (const [k, r] of Object.entries(dirtyRev)) {
        if (r > myRev) stillRev[k] = r;
      }
      dirtyRev = stillRev;
      dirtyKeys.value = Object.fromEntries(Object.keys(stillRev).map((k) => [k, true]));
      saveState.value = Object.keys(stillRev).length ? "dirty" : "saved";
      return true;
    } catch (e) {
      cfgOk.value = false;
      cfgMsg.value = e.message;
      saveErr.value = e.message || "保存失败";
      saveState.value = "error";
      toast("保存失败", cfgMsg.value, "danger");
      return false;
    } finally {
      cfgSaving.value = false;
    }
  }

  function retrySave() {
    if (saveState.value !== "error") return;
    saveCfg();
  }

  // 页头状态胶囊的四个派生：文案 / 配色 / 图标 / 是否可点（只有失败态可点 = 重试）
  const saveText = Vue.computed(() => {
    if (saveState.value === "saving") return "保存中…";
    if (saveState.value === "dirty") return "有未保存改动";
    if (saveState.value === "error") return "保存失败，点此重试";
    if (saveState.value === "saved") return `已保存 · ${savedAt.value}`;
    return "配置已加载";
  });
  const saveCls = Vue.computed(() => ({
    warn: saveState.value === "dirty",
    ok: saveState.value === "saved",
    err: saveState.value === "error",
  }));
  const saveIcon = Vue.computed(() => {
    if (saveState.value === "saving") return "bi-arrow-repeat";
    if (saveState.value === "dirty") return "bi-pencil";
    if (saveState.value === "error") return "bi-exclamation-triangle";
    if (saveState.value === "saved") return "bi-cloud-check";
    return "bi-cloud";
  });
  const saveRetryable = Vue.computed(() => saveState.value === "error");

  async function testLlm() {
    testing.value = true;
    testResult.value = null;
    try {
      const d = await postJ("/api/config/llm/test", cfg.value.llm);
      if (d.error) throw new Error(d.error);
      testResult.value = { ok: true, text: `连通正常（${d.data.latency_ms}ms）：${d.data.reply || "-"}` };
    } catch (e) {
      testResult.value = { ok: false, text: e.message };
    } finally {
      testing.value = false;
    }
  }

  return { cfg, cfgSaving, cfgMsg, cfgOk, loadCfg, saveCfg,
           LLM_PROVIDERS, providerSel, applyProvider, presetModels, modelSel, modelCustom,
           orModels, orLoading, orErr, orQuery, loadOpenRouterModels,
           testing, testResult, testLlm, keyVisible,
           dirtyKeys, saveState, savedAt, saveErr,
           mark, retrySave, saveText, saveCls, saveIcon, saveRetryable };
}


// ═══════════════════════════════════════════════════════════════
//  账号管理子 composable：admin 增删改查；非 admin 仅可改自己资料
// ═══════════════════════════════════════════════════════════════
export function useUsers({ toast, currentUser }) {
  const list = Vue.ref([]);
  const loading = Vue.ref(false);
  const errMsg = Vue.ref("");
  const isAdmin = Vue.computed(() => currentUser.value?.role === "admin");

  // 弹窗状态
  const modalOpen = Vue.ref(false);
  const modalMode = Vue.ref("create"); // "create" | "edit"
  const modalForm = Vue.ref({ id: 0, username: "", real_name: "", role: "handler", phone: "", password: "" });
  const modalSaving = Vue.ref(false);
  const modalErr = Vue.ref("");

  // 重置密码弹窗
  const resetOpen = Vue.ref(false);
  const resetTarget = Vue.ref(null);
  const resetPassword = Vue.ref("");
  const resetSaving = Vue.ref(false);

  // 改自己密码弹窗
  const myPwdOpen = Vue.ref(false);
  const myPwdForm = Vue.ref({ old_password: "", new_password: "", confirm_password: "" });
  const myPwdSaving = Vue.ref(false);
  const myPwdErr = Vue.ref("");

  async function loadList() {
    if (!isAdmin.value) return;
    loading.value = true; errMsg.value = "";
    try {
      const d = await getJ("/api/users");
      if (d.error) throw new Error(d.error);
      list.value = d.data || [];
    } catch (e) {
      errMsg.value = e.message;
      list.value = [];
    } finally {
      loading.value = false;
    }
  }

  function openCreate() {
    modalMode.value = "create";
    modalForm.value = { id: 0, username: "", real_name: "", role: "handler", phone: "", password: "" };
    modalErr.value = "";
    modalOpen.value = true;
  }

  function openEdit(u) {
    modalMode.value = "edit";
    modalForm.value = { id: u.id, username: u.username, real_name: u.real_name, role: u.role, phone: u.phone || "", password: "" };
    modalErr.value = "";
    modalOpen.value = true;
  }

  function closeModal() {
    modalOpen.value = false;
    modalErr.value = "";
  }

  async function submitModal() {
    modalErr.value = ""; modalSaving.value = true;
    try {
      const f = modalForm.value;
      if (!f.username?.trim()) throw new Error("用户名不能为空");
      if (!f.real_name?.trim()) throw new Error("真实姓名不能为空");
      if (modalMode.value === "create") {
        if (!f.password) throw new Error("初始密码不能为空");
        const d = await postJ("/api/users", {
          username: f.username.trim(),
          password: f.password,
          real_name: f.real_name.trim(),
          role: f.role,
          phone: f.phone.trim(),
        });
        if (d.error) throw new Error(d.error);
        toast("创建成功", `账号 ${d.data.username} 已创建`, "success");
      } else {
        // 编辑：username 不可改；密码在重置弹窗里
        const d = await putJ(`/api/users/${f.id}`, {
          real_name: f.real_name.trim(),
          phone: f.phone.trim(),
          role: f.role,
        });
        if (d.error) throw new Error(d.error);
        toast("已更新", `${d.data.username} 资料已保存`, "success");
      }
      await loadList();
      closeModal();
    } catch (e) {
      modalErr.value = e.message;
    } finally {
      modalSaving.value = false;
    }
  }

  async function toggleStatus(u) {
    const next = u.status === "启用" ? "停用" : "启用";
    if (!confirm(`确定要${next}账号 ${u.username} 吗？`)) return;
    const d = await putJ(`/api/users/${u.id}/status`, { status: next });
    if (d.error) { toast("操作失败", d.error, "danger"); return; }
    toast(`${next}成功`, `${u.username} 已${next}`, "success");
    await loadList();
  }

  function openReset(u) {
    resetTarget.value = u;
    resetPassword.value = "";
    resetOpen.value = true;
  }

  async function submitReset() {
    if (!resetPassword.value) { toast("请输入新密码", "", "warning"); return; }
    resetSaving.value = true;
    try {
      const d = await postJ(`/api/users/${resetTarget.value.id}/reset-password`, { new_password: resetPassword.value });
      if (d.error) throw new Error(d.error);
      toast("密码已重置", `${resetTarget.value.username} 的新密码已生效，对方需要重新登录`, "success");
      resetOpen.value = false;
    } catch (e) {
      toast("重置失败", e.message, "danger");
    } finally {
      resetSaving.value = false;
    }
  }

  // 改自己密码
  function openMyPwd() {
    myPwdForm.value = { old_password: "", new_password: "", confirm_password: "" };
    myPwdErr.value = "";
    myPwdOpen.value = true;
  }

  async function submitMyPwd() {
    myPwdErr.value = ""; myPwdSaving.value = true;
    try {
      const f = myPwdForm.value;
      if (!f.old_password) throw new Error("请输入原密码");
      if (!f.new_password) throw new Error("请输入新密码");
      if (f.new_password !== f.confirm_password) throw new Error("两次输入的新密码不一致");
      const d = await postJ("/api/users/me/password", { old_password: f.old_password, new_password: f.new_password });
      if (d.error) throw new Error(d.error);
      toast("密码已修改", "当前 session 已失效，请重新登录", "success");
      // 改密后 server bump session_version，session 立即失效
      setTimeout(() => { window.location.href = "/"; }, 1200);
    } catch (e) {
      myPwdErr.value = e.message;
    } finally {
      myPwdSaving.value = false;
    }
  }

  // 改自己真实姓名/手机号
  const profileSaving = Vue.ref(false);
  const profileErr = Vue.ref("");
  async function saveProfile(real_name, phone) {
    profileSaving.value = true; profileErr.value = "";
    try {
      const d = await putJ(`/api/users/${currentUser.value.id}`, { real_name, phone });
      if (d.error) throw new Error(d.error);
      toast("资料已更新", "刷新页面后看到新资料", "success");
      // 同步 window.__CURRENT_USER__ 让顶栏立刻更新
      if (window.__CURRENT_USER__) {
        window.__CURRENT_USER__.real_name = real_name;
        window.__CURRENT_USER__.phone = phone;
      }
    } catch (e) {
      profileErr.value = e.message;
    } finally {
      profileSaving.value = false;
    }
  }

  // ── 历史处理人 → 账号 别名映射（admin）──
  const aliases = Vue.ref([]);
  const unmapped = Vue.ref([]);
  const aliasLoading = Vue.ref(false);
  const aliasErr = Vue.ref("");

  // 新增映射弹窗
  const aliasModalOpen = Vue.ref(false);
  const aliasForm = Vue.ref({ old_name: "", user_id: null });
  const aliasSaving = Vue.ref(false);
  const aliasModalErr = Vue.ref("");

  async function loadAliases() {
    if (!isAdmin.value) return;
    aliasLoading.value = true; aliasErr.value = "";
    try {
      const d = await getJ("/api/users/aliases");
      if (d.error) throw new Error(d.error);
      aliases.value = d.data?.aliases || [];
      unmapped.value = d.data?.unmapped || [];
    } catch (e) {
      aliasErr.value = e.message;
    } finally {
      aliasLoading.value = false;
    }
  }

  function openAliasCreate(prefillName) {
    aliasForm.value = { old_name: prefillName || "", user_id: null };
    aliasModalErr.value = "";
    aliasModalOpen.value = true;
  }

  function openAliasFromUnmapped(u) {
    openAliasCreate(u.old_name);
  }

  async function submitAlias() {
    aliasModalErr.value = ""; aliasSaving.value = true;
    try {
      const f = aliasForm.value;
      if (!f.old_name?.trim()) throw new Error("历史处理人名称不能为空");
      if (!f.user_id) throw new Error("请选择系统账号");
      const d = await postJ("/api/users/aliases", { old_name: f.old_name.trim(), user_id: f.user_id });
      if (d.error) throw new Error(d.error);
      toast("映射成功", `已回写 ${d.data.rewritten} 个工单`, "success");
      aliasModalOpen.value = false;
      await loadAliases();
    } catch (e) {
      aliasModalErr.value = e.message;
    } finally {
      aliasSaving.value = false;
    }
  }

  async function deleteAliasRow(a) {
    if (!confirm(`确定要删除映射「${a.old_name} → ${a.user_real_name || ""}」吗？相关工单会回滚为未关联。`)) return;
    const d = await delJ(`/api/users/aliases/${a.id}`);
    if (d.error) { toast("删除失败", d.error, "danger"); return; }
    toast("已删除", "相关工单已回滚", "success");
    await loadAliases();
  }

  return {
    list, loading, errMsg, isAdmin,
    modalOpen, modalMode, modalForm, modalSaving, modalErr,
    openCreate, openEdit, closeModal, submitModal,
    toggleStatus,
    resetOpen, resetTarget, resetPassword, resetSaving, openReset, submitReset,
    myPwdOpen, myPwdForm, myPwdSaving, myPwdErr, openMyPwd, submitMyPwd,
    profileSaving, profileErr, saveProfile,
    loadList,
    // 别名映射
    aliases, unmapped, aliasLoading, aliasErr,
    aliasModalOpen, aliasForm, aliasSaving, aliasModalErr,
    openAliasCreate, openAliasFromUnmapped, submitAlias, deleteAliasRow,
    loadAliases,
  };
}


// ═══════════════════════════════════════════════════════════════
//  SMB 共享盘状态子 composable（ISS-SMB-01）
//  背景：/Volumes/File 这个 macOS SMB 挂载点会不定时自行消失，导致 11 处归档功能
//  同时失效。归档链路已内置前置自愈；此 composable 供系统设置页展示状态 +
//  提供「一键重挂」入口，让管理员无需登服务器即可自助修复。
// ═══════════════════════════════════════════════════════════════
const SMB_REASON_TEXT = {
  ok: "正常",
  not_mounted: "未挂载（共享盘已掉线）",
  probe_missing: "归档路径不可达",
  timeout: "探测超时（挂载点无响应）",
  disabled: "自动重挂已禁用",
};

export function useSmbShare(toast) {
  const smbStatus = Vue.ref(null);
  const smbLoading = Vue.ref(false);
  const smbRemounting = Vue.ref(false);
  const smbErr = Vue.ref("");

  async function loadSmbStatus() {
    smbLoading.value = true;
    smbErr.value = "";
    try {
      const d = await getJ("/api/smb/status");
      if (d.error) throw new Error(d.error);
      smbStatus.value = d.data || null;
    } catch (e) {
      smbErr.value = e.message;
      smbStatus.value = null;
    } finally {
      smbLoading.value = false;
    }
  }

  // ── ISS-SMB-07：人工重挂节流 ──
  // 后端 ensure_mount(force=True) 有 30s 节流（防脚本化刷屏重现「窗口风暴」），
  // 前端这里做同长度的倒计时，让按钮状态与后端一致，而不是点一次吃一次 429。
  const SMB_MANUAL_COOLDOWN = 30;
  const smbCooldown = Vue.ref(0);
  let _cooldownTimer = null;

  function _startCooldown(sec = SMB_MANUAL_COOLDOWN) {
    smbCooldown.value = sec;
    if (_cooldownTimer) clearInterval(_cooldownTimer);
    _cooldownTimer = setInterval(() => {
      smbCooldown.value -= 1;
      if (smbCooldown.value <= 0) {
        clearInterval(_cooldownTimer);
        _cooldownTimer = null;
        smbCooldown.value = 0;
      }
    }, 1000);
  }

  const smbCanRemount = Vue.computed(() =>
    !smbRemounting.value && smbCooldown.value <= 0);

  async function remountSmb() {
    if (!smbCanRemount.value) return false;
    smbRemounting.value = true;
    try {
      const resp = await fetch("/api/smb/remount", { method: "POST" });
      const d = await resp.json().catch(() => ({}));
      if (d && d.success) {
        toast("共享盘已恢复",
              (d.data && d.data.action === "remounted") ? "挂载点已重新连接" : "共享盘当前可用",
              "success");
        await loadSmbStatus();
        return true;
      }
      const msg = (d && (d.message || (d.errors && d.errors[0])))
        || `重挂失败（HTTP ${resp.status}）`;
      toast(resp.status === 429 ? "请稍候" : "重挂失败", msg, "danger");
      await loadSmbStatus();
      return false;
    } catch (e) {
      toast("重挂失败", e.message, "danger");
      return false;
    } finally {
      smbRemounting.value = false;
      _startCooldown();
    }
  }

  const smbHealthy = Vue.computed(() =>
    !!(smbStatus.value && smbStatus.value.health && smbStatus.value.health.healthy));
  const smbMounted = Vue.computed(() =>
    !!(smbStatus.value && smbStatus.value.health && smbStatus.value.health.mounted));
  const smbReasonText = Vue.computed(() => {
    const r = smbStatus.value && smbStatus.value.health && smbStatus.value.health.reason;
    return SMB_REASON_TEXT[r] || r || "未知";
  });

  // ── ISS-SMB-10：把自愈守卫状态（退避/熔断/GUI 配额）翻译成一句人话 ──
  // 没有它，运维在看板上只能看到「共享盘不可用」，分不清「正在重试」还是
  // 「已被熔断暂停、不会再自动恢复」——后者必须人工介入。
  const smbGuard = Vue.computed(() =>
    (smbStatus.value && smbStatus.value.guard) || null);

  function _fmtRemain(sec) {
    const s = Math.max(0, Math.round(Number(sec) || 0));
    return s >= 60 ? `${Math.ceil(s / 60)} 分钟` : `${s} 秒`;
  }

  const smbGuardNote = Vue.computed(() => {
    const g = smbGuard.value;
    if (!g) return "";
    if (g.suspend_remaining > 0) {
      return `自动重挂已熔断暂停（连续失败 ${g.consecutive_failures} 次），`
           + `${_fmtRemain(g.suspend_remaining)}后才会再自动尝试——期间请人工重挂或联系管理员`;
    }
    if (g.backoff_remaining > 0) {
      return `自动重挂退避中（连续失败 ${g.consecutive_failures} 次），`
           + `${_fmtRemain(g.backoff_remaining)}后再试`;
    }
    if (g.gui_hourly_limit === 0) {
      return "自动重挂已禁用图形挂载（不会自动弹连接窗口），掉载需人工点「一键重挂」";
    }
    if (g.gui_attempts_last_hour >= g.gui_hourly_limit) {
      return `本小时自动重挂额度已用完（${g.gui_attempts_last_hour}/${g.gui_hourly_limit}），`
           + "仍可人工重挂";
    }
    return "";
  });

  return {
    smbStatus, smbLoading, smbErr, smbRemounting, smbCooldown, smbCanRemount,
    loadSmbStatus, remountSmb,
    smbHealthy, smbMounted, smbReasonText,
    smbGuard, smbGuardNote,
  };
}


// ═══════════════════════════════════════════════════════════════
//  云 OCR 多云配置子 composable（spec §13 / Phase 2）
//  设置页可自行填入百度/腾讯/火山/阿里四家密钥并开关；上传合同时按 order
//  依次尝试，某家不可用或本月额度用尽自动换下一家，全部失败才回退本机识别。
//
//  ⚠️ 密钥由后端打码下发（只显尾 4 位）。后端按「值里含 • 即视为未改动」还原真值，
//     所以这里绝不能对密钥框做 trim / 清空判断，原样提交即可。
// ═══════════════════════════════════════════════════════════════
export function useCloudOcr({ toast, getConfig }) {
  const providers = Vue.ref([]);
  const usage = Vue.ref(null);
  const loading = Vue.ref(false);
  const errMsg = Vue.ref("");
  const testing = Vue.ref("");
  const testResult = Vue.ref({});
  const expanded = Vue.ref("baidu");

  // 保证表单结构与后端 meta 对齐（首次加载时 providers 可能还是空对象）
  function ensureShape(meta) {
    const c = getConfig && getConfig();
    if (!c) return;
    if (!c.providers || typeof c.providers !== "object") c.providers = {};
    meta.forEach(p => {
      if (!c.providers[p.key]) c.providers[p.key] = {};
      const slot = c.providers[p.key];
      p.fields.forEach(f => {
        if (slot[f.name] === undefined || slot[f.name] === null) slot[f.name] = "";
      });
      if (slot.enabled === undefined) slot.enabled = p.key === "baidu";
    });
    if (!Array.isArray(c.order) || !c.order.length) {
      c.order = meta.map(p => p.key);
    }
  }

  async function loadUsage() {
    loading.value = true; errMsg.value = "";
    try {
      const d = await getJ("/api/ocr/usage");
      if (d.error) throw new Error(d.error);
      const data = d.data || {};
      providers.value = data.providers || [];
      usage.value = data.usage || null;
      ensureShape(providers.value);
    } catch (e) {
      errMsg.value = e.message;
    } finally {
      loading.value = false;
    }
  }

  function usageOf(key) {
    return (usage.value && usage.value.providers && usage.value.providers[key]) || null;
  }

  function toggle(key) {
    expanded.value = expanded.value === key ? "" : key;
  }

  // 顺序调整：上移/下移按钮（不做拖拽排序，见 tickets §4.2）
  function move(key, delta) {
    const c = getConfig && getConfig();
    if (!c || !Array.isArray(c.order)) return;
    const i = c.order.indexOf(key);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= c.order.length) return;
    const next = c.order.slice();
    [next[i], next[j]] = [next[j], next[i]];
    c.order = next;
  }

  // ── 只读派生：把「调度时会怎么走」翻译成折叠态也能看懂的徽章 ──
  // 判定口径与后端 `is_configured` 一致（enabled → **必填**字段齐 → 额度未用尽），
  // 但**只用于显示**，不参与调度；后端仍以自身判定为准。
  // ⚠️ 只有「后端真正消费的字段」才算必填：标了 `optional` 的（百度 App ID、
  //    各家地域）留空不影响调度 —— 把它们算进必填会误报「缺密钥 · 将跳过」、
  //    链路预览少一家，而后端其实调得通（09-22 缺陷）。
  function providerState(key) {
    const c = getConfig && getConfig();
    const meta = providers.value.find(p => p.key === key);
    if (!c || !meta) return { code: "unknown", text: "—", cls: "chip-mute" };
    const slot = (c.providers || {})[key] || {};
    if (!slot.enabled) return { code: "off", text: "未启用", cls: "chip-off" };
    const required = (meta.fields || []).filter(f => f.optional !== true);
    const missing = required.filter(f => !String(slot[f.name] == null ? "" : slot[f.name]).trim());
    if (missing.length) return { code: "nokey", text: "缺密钥 · 将跳过", cls: "chip-warn" };
    const u = usageOf(key);
    if (u && u.exhausted) return { code: "exhausted", text: "额度用尽 · 将跳过", cls: "chip-warn" };
    return { code: "ready", text: "可用", cls: "chip-on" };
  }

  // 顺序表（配置为空时按元数据原序兜底）——三个派生共用，判定只写一处
  const orderKeys = Vue.computed(() => {
    const c = getConfig && getConfig();
    return (c && Array.isArray(c.order) && c.order.length) ? c.order : providers.value.map(p => p.key);
  });

  // 「可用」= 已启用 + 字段齐 + 额度未用尽（与 providerState 同一口径，不重复判定）
  const readyKeys = Vue.computed(() => orderKeys.value.filter(k => providerState(k).code === "ready"));

  // order 里第一个「可用」的厂商 = 系统实际会首选的那家
  const firstReadyKey = Vue.computed(() => readyKeys.value[0] || "");

  // 当前识别链路预览（只读）：可用的云厂商按顺序 → 本机识别兜底
  const chainText = Vue.computed(() => {
    const names = readyKeys.value
      .map(k => (providers.value.find(p => p.key === k) || {}).label || k);
    return names.concat(["本机识别"]).join(" → ");
  });

  // 折叠态摘要。折叠 ≠ 信息丢失：一个「可用 N/M」说不出问题在哪，
  // 所以必须把「会跳过几家」一并说出来，否则用户得展开才知道某家要跳过。
  const ocrSummary = Vue.computed(() => {
    const total = providers.value.length;
    if (!total) return "";
    const codes = providers.value.map(p => providerState(p.key).code);
    const ready = codes.filter(c => c === "ready").length;
    const skipped = codes.filter(c => c === "nokey" || c === "exhausted").length;
    if (!ready) {
      return skipped ? `${skipped} 家缺密钥/额度用尽 · 全走本机识别` : "无可用云厂商 · 全部走本机识别";
    }
    const first = providers.value.find(p => p.key === firstReadyKey.value);
    const tail = skipped ? ` · ${skipped} 家将跳过` : "";
    return `首选 ${first ? first.label : firstReadyKey.value} · 可用 ${ready}/${total} 家${tail}`;
  });

  async function testProvider(key) {
    testing.value = key;
    try {
      const d = await postJ("/api/ocr/test", {
        provider: key,
        config: getConfig ? getConfig() : null,
      });
      if (d.error) throw new Error(d.error);
      const ms = (d.data && d.data.latency_ms) || 0;
      testResult.value = { ...testResult.value, [key]: { ok: true, text: `连通正常（${ms}ms）` } };
      toast("连通正常", `该厂商可用，耗时 ${ms}ms`, "success");
      await loadUsage();
    } catch (e) {
      testResult.value = { ...testResult.value, [key]: { ok: false, text: e.message } };
    } finally {
      testing.value = "";
    }
  }

  return { providers, usage, loading, errMsg, testing, testResult, expanded,
           loadUsage, usageOf, toggle, testProvider, move,
           providerState, orderKeys, readyKeys, firstReadyKey, chainText, ocrSummary };
}


// ═══════════════════════════════════════════════════════════════
//  设置页布局子 composable：卡片折叠/展开 + 分区
//  纯前端展示层状态，不进 cfg、不进接口；偏好记进 localStorage，
//  读取失败（隐私模式 / 值被改坏）一律回落默认，绝不抛错。
// ═══════════════════════════════════════════════════════════════
// 设置页分区（左导航的分组；同时也是内容的 <h2> 顺序）
export const SETTINGS_GROUPS = [
  { key: "ai",      label: "AI 能力" },
  { key: "store",   label: "存储与归档" },
  { key: "account", label: "账号与权限" },
];

// 卡片注册表：折叠键、左导航标签/图标、搜索关键词都收在这一处。
// ⚠️ 新增一张卡实际要改**三处**（勿只改这里）：
//    ① 本表 ② 模板的 <section id="card-<key>"> ③ app.js 的 setNavBadge()
//    —— 徽章要读 cfg/cloudOcr/smb/users 四个来源，塞进本表会让这个纯展示层
//    反向依赖业务 composable，故显式留在组合根。
// 护栏会核对本表与模板/分组的键集合一致，并逐个断言配置控件带自动保存标记。
export const SETTINGS_CARDS = [
  { key: "llm", group: "ai", label: "AI 大模型配置", navLabel: "大模型", icon: "bi-cpu",
    kw: "大模型 模型 服务商 api 地址 密钥 key token 最大输出 测试 连接 通义千问 deepseek 智谱 kimi 豆包 openai openrouter 视觉" },
  { key: "ocr", group: "ai", label: "云 OCR（合同图片识别）", navLabel: "云 OCR", icon: "bi-cloud-arrow-down",
    kw: "云 ocr 识别 厂商 百度 腾讯 火山 阿里 密钥 顺序 优先级 启用 额度 用量 测试 链路 手写 本机" },
  { key: "smb", group: "store", label: "共享盘（归档存储）", navLabel: "共享盘", icon: "bi-hdd-network",
    kw: "共享盘 smb 归档 存储 映射 重挂 挂载 掉载 目录 kj-server 网络盘" },
  { key: "profile", group: "account", label: "我的资料", navLabel: "我的资料", icon: "bi-person-badge",
    kw: "我的资料 账号 用户名 姓名 角色 手机号 密码 改密" },
  { key: "users", group: "account", label: "用户管理", navLabel: "用户管理", icon: "bi-people",
    kw: "用户 账号 新增 权限 角色 停用 启用 重置密码 手机号 admin handler viewer" },
  { key: "alias", group: "account", label: "历史处理人映射", navLabel: "历史映射", icon: "bi-link-45deg",
    kw: "历史 处理人 映射 旧工单 绑定 别名 关联 未关联 回写" },
];

// v2（2026-09-22 布局重排）：默认态从「只展开大模型」改为「展开大模型 + 云 OCR」。
// 必须 bump 键名 —— 旧值里 ocr=true 会一直压着新默认，老用户看不到这次的变化。
const SETTINGS_LS_KEY = "settings.cards.collapsed.v2";
// 智能默认：展开最常改的两张（大模型 + 云 OCR），其余折叠成「一行摘要 + 一眼状态」
const SETTINGS_DEFAULT_COLLAPSED = { llm: false, ocr: false, smb: true, profile: true, users: true, alias: true };

export function useSettingsLayout() {
  function _read() {
    try {
      const raw = window.localStorage.getItem(SETTINGS_LS_KEY);
      if (!raw) return { ...SETTINGS_DEFAULT_COLLAPSED };
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return { ...SETTINGS_DEFAULT_COLLAPSED };
      const out = { ...SETTINGS_DEFAULT_COLLAPSED };
      SETTINGS_CARDS.forEach(c => { if (typeof parsed[c.key] === "boolean") out[c.key] = parsed[c.key]; });
      return out;
    } catch (e) {
      return { ...SETTINGS_DEFAULT_COLLAPSED };
    }
  }

  const collapsed = Vue.ref(_read());

  function _write() {
    try { window.localStorage.setItem(SETTINGS_LS_KEY, JSON.stringify(collapsed.value)); } catch (e) { /* 隐私模式忽略 */ }
  }

  function isCollapsed(key) { return !!collapsed.value[key]; }

  function toggleCard(key) {
    collapsed.value = { ...collapsed.value, [key]: !isCollapsed(key) };
    _write();
  }

  function setAll(flag) {
    const next = {};
    SETTINGS_CARDS.forEach(c => { next[c.key] = !!flag; });
    collapsed.value = next;
    _write();
  }

  function expandAll() { setAll(false); }
  function collapseAll() { setAll(true); }

  const allCollapsed = Vue.computed(() => SETTINGS_CARDS.every(c => !!collapsed.value[c.key]));

  // ── 分区与导航 ──
  const groups = SETTINGS_GROUPS;
  function cardsOf(groupKey) {
    return SETTINGS_CARDS.filter(c => c.group === groupKey);
  }

  // ── 设置项搜索：只过滤卡片，不碰任何配置，也不写折叠偏好 ──
  // 命中即临时展开（isOpen），否则「搜到了却只看到一行标题」等于没搜到。
  const query = Vue.ref("");
  const hit = Vue.computed(() => {
    const q = query.value.trim().toLowerCase();
    if (!q) return null;
    return new Set(SETTINGS_CARDS
      .filter(c => `${c.label} ${c.navLabel} ${c.kw}`.toLowerCase().includes(q))
      .map(c => c.key));
  });
  function setQuery(v) { query.value = v || ""; }
  function matches(key) { return !hit.value || hit.value.has(key); }
  function isOpen(key) {
    // 命中搜索时强制展开（只影响显示，不改也不写折叠偏好）
    if (hit.value && hit.value.has(key)) return true;
    return !isCollapsed(key);
  }
  function groupVisible(groupKey) { return cardsOf(groupKey).some(c => matches(c.key)); }

  // ── 滚动联动：内容区跟随整页滚动，高亮导航里当前所在的卡片 ──
  // 被搜索隐藏的卡（offsetParent === null）必须跳过，否则高亮会停在看不见的卡上。
  const active = Vue.ref(SETTINGS_CARDS[0].key);
  let _spyFn = null;
  function _onSpy() {
    let cur = "";
    for (const c of SETTINGS_CARDS) {
      const el = document.getElementById("card-" + c.key);
      if (!el || el.offsetParent === null) continue;
      if (el.getBoundingClientRect().top <= 160) cur = c.key;
    }
    active.value = cur || (SETTINGS_CARDS.find(c => matches(c.key)) || SETTINGS_CARDS[0]).key;
  }
  function bindSpy() {
    unbindSpy();
    _onSpy();
    _spyFn = _onSpy;
    window.addEventListener("scroll", _spyFn, { passive: true });
    window.addEventListener("resize", _spyFn);
  }
  function unbindSpy() {
    if (!_spyFn) return;
    window.removeEventListener("scroll", _spyFn);
    window.removeEventListener("resize", _spyFn);
    _spyFn = null;
  }
  function jumpTo(key) {
    const el = document.getElementById("card-" + key);
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY - 96;
    window.scrollTo({ top: Math.max(top, 0), behavior: "smooth" });
    active.value = key;
  }

  return { collapsed, isCollapsed, toggleCard, expandAll, collapseAll, allCollapsed,
           groups, cardsOf, query, setQuery, matches, isOpen, groupVisible,
           active, jumpTo, bindSpy, unbindSpy };
}
