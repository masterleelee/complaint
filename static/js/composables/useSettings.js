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
  // 模型选择：预设下拉 or 手动输入（自定义模型/接入点 ID）
  const modelCustom = Vue.ref(true);
  const presetModels = Vue.computed(() =>
    (LLM_PROVIDERS.find(p => p.key === providerSel.value) || {}).models || []);
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
    } catch (e) {
      toast("加载配置失败", e.message, "danger");
    }
  }

  function _presetModel(modelId) {
    const p = LLM_PROVIDERS.find(x => x.key === providerSel.value);
    if (!p || p.key === "custom") return null;
    return p.models.find(m => m.id === modelId) || null;
  }

  function applyProvider() {
    const p = LLM_PROVIDERS.find(x => x.key === providerSel.value);
    if (!p || p.key === "custom") return;
    const m = p.models[0];
    modelCustom.value = false;
    cfg.value.llm.api_url = p.url;
    cfg.value.llm.model = m ? m.id : "";
    if (m && m.max) cfg.value.llm.max_tokens = m.max;
    testResult.value = null;
  }

  async function saveCfg() {
    cfgSaving.value = true;
    cfgMsg.value = "";
    try {
      const d = await putJ("/api/config", cfg.value);
      cfgOk.value = d.success;
      cfgMsg.value = d.success ? "已保存" : d.error || "失败";
      if (d.success) toast("保存成功", "配置已更新，AI 功能将使用新模型", "success");
    } catch (e) {
      cfgMsg.value = e.message;
      cfgOk.value = false;
    }
  }

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
           testing, testResult, testLlm };
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

  async function remountSmb() {
    if (smbRemounting.value) return false;
    smbRemounting.value = true;
    try {
      const d = await postJ("/api/smb/remount", {});
      if (d.success) {
        toast("共享盘已恢复", (d.data && d.data.action === "remounted") ? "挂载点已重新连接" : "共享盘当前可用", "success");
        await loadSmbStatus();
        return true;
      }
      toast("重挂失败", (d.errors && d.errors[0]) || d.error || "请检查共享盘与网络", "danger");
      await loadSmbStatus();
      return false;
    } catch (e) {
      toast("重挂失败", e.message, "danger");
      return false;
    } finally {
      smbRemounting.value = false;
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

  return {
    smbStatus, smbLoading, smbErr, smbRemounting,
    loadSmbStatus, remountSmb,
    smbHealthy, smbMounted, smbReasonText,
  };
}
