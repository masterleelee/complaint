// 系统设置组合式函数
import { getJ, postJ } from "api";

export function useSettings(toast) {
  const cfg = Vue.ref(null);
  const cfgSaving = Vue.ref(false);
  const cfgMsg = Vue.ref("");
  const cfgOk = Vue.ref(false);

  async function loadCfg() {
    try {
      cfg.value = await getJ("/api/config");
    } catch (e) {
      toast("加载配置失败", e.message, "danger");
    }
  }

  async function saveCfg() {
    cfgSaving.value = true;
    cfgMsg.value = "";
    try {
      const d = await postJ("/api/config", cfg.value);
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
