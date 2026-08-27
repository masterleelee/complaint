// 系统设置组合式函数
import { getJ, putJ, postJ } from "api";

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
    llm_contract_vision: llm(),
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
