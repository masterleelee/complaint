// 系统设置组合式函数
import { getJ, putJ } from "api";

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
    feishu: { app_id: "", app_secret: "", bitable_app_token: "", bitable_table_id: "" },
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

  async function loadCfg() {
    try {
      const loaded = await getJ("/api/config");
      if (loaded.error) throw new Error(loaded.error);
      cfg.value = mergeConfig(loaded);
    } catch (e) {
      toast("加载配置失败", e.message, "danger");
    }
  }

  async function saveCfg() {
    cfgSaving.value = true;
    cfgMsg.value = "";
    try {
      const d = await putJ("/api/config", cfg.value);
      cfgOk.value = d.success;
      cfgMsg.value = d.success ? "已保存" : d.error || "失败";
      if (d.success) toast("保存成功", "配置已更新", "success");
    } catch (e) {
      cfgMsg.value = e.message;
      cfgOk.value = false;
    } finally {
      cfgSaving.value = false;
    }
  }

  return { cfg, cfgSaving, cfgMsg, cfgOk, loadCfg, saveCfg };
}
