// 回归测试：工单重开（restore）时费用明细 source 字段的还原逻辑。
//
// 背景 bug（王俊林 5dbbf56f）：零口径确认→解锁后 fee_plan_status='draft'、
// deduction_detail='[]'。restore() 重建 ar 时丢失 source → 模板中
// 合同总额/实缴（v-if="ar.source==='manual'"）退化为只读 <b> →
// 实缴恒 0 → handleSaveAndConfirm 的 actualPaid<=0 拦截 → 无法确认明细。
//
// 本测试直接加载真实的 static/js/composables/useWorkflow.js（Vue 用最小桩，
// "api" 裸导入剔除），覆盖 5 条路径。
const fs = await import("node:fs");
const path = await import("node:path");
const url = await import("node:url");

let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log("  PASS:", msg); }
  else { fail++; console.error("  FAIL:", msg); }
}

// ── Vue 最小桩：ref 可读写；computed 惰性求值 ──
globalThis.Vue = {
  ref: (v) => ({ value: v }),
  computed: (fn) => {
    let cache, done = false;
    return {
      get value() { if (!done) { cache = fn(); done = true; } return cache; },
      set value(_) {},
    };
  },
  reactive: (o) => o,
};

// ── 加载真实模块（剔除 "api" 裸导入，node 无法解析 importmap） ──
const srcPath = path.join(path.dirname(url.fileURLToPath(import.meta.url)), "..", "static", "js", "composables", "useWorkflow.js");
const code = fs.readFileSync(srcPath, "utf8").replace(/^import .* from "api";\s*$/m, "");
const mod = await import("data:text/javascript;base64," + Buffer.from(code).toString("base64"));

const toast = () => {};
const wf = mod.useWorkflow(toast, () => null, () => "");
const restore = wf.restore;

// ── 用例 1（王俊林真实数据形态）：零口径确认→解锁 → draft + detail='[]' ──
restore({
  ticket: {
    id: "5dbbf56f", student_name: "王俊林",
    fee_plan_status: "draft", deduction_detail: "[]",
    total_fee: 0, actual_paid: 0, deduction_fee: 0, refund_fee: 0,
    contract_manifest: {},
  },
  deductions: [],
});
assert(wf.ar.value && wf.ar.value.source === "manual",
  "draft+空明细：restore 后 source='manual'（合同总额/实缴可输入）");

// ── 用例 2：零口径 confirmed + detail='[]'：维持只读守门 ──
restore({
  ticket: { id: "t2", fee_plan_status: "confirmed", deduction_detail: "[]", contract_manifest: {} },
  deductions: [],
});
assert(wf.ar.value && wf.ar.value.source !== "manual",
  "confirmed+空明细：source 非 manual（合计只读，须先解锁）");

// ── 用例 3：整包字典（save_analysis 保存）里带 source → 原样还原 ──
restore({
  ticket: {
    id: "t3", fee_plan_status: "draft",
    deduction_detail: JSON.stringify({ source: "manual", total_fee: 3000, deductions: [] }),
    contract_manifest: {},
  },
  deductions: [],
});
assert(wf.ar.value && wf.ar.value.source === "manual",
  "字典明细带 source='manual'：restore 原样还原");

// ── 用例 4：fee-confirm 写库形态（明细列表、无字典）+ draft → 可编辑 ──
restore({
  ticket: { id: "t4", fee_plan_status: "draft", deduction_detail: '[{"item":"综合服务费","amount":100}]', contract_manifest: {} },
  deductions: [{ item: "综合服务费", amount: 100 }],
});
assert(wf.ar.value && wf.ar.value.source === "manual",
  "draft+列表明细（fee-confirm 写库形态）：source 兜底 'manual'");

// ── 用例 5：confirmed + 列表明细 → 只读（行为不变） ──
restore({
  ticket: { id: "t5", fee_plan_status: "confirmed", deduction_detail: '[{"item":"综合服务费","amount":100}]', contract_manifest: {} },
  deductions: [{ item: "综合服务费", amount: 100 }],
});
assert(wf.ar.value && wf.ar.value.source !== "manual",
  "confirmed+列表明细：source 非 manual（只读，行为不变）");

// ── 附：markFeeEditable（解锁后同会话补录路径） ──
if (typeof wf.markFeeEditable === "function") {
  wf.markFeeEditable();
  assert(wf.ar.value && wf.ar.value.source === "manual",
    "markFeeEditable()：解锁后可将当前明细置为可编辑");
} else {
  fail++; console.error("  FAIL: 未导出 markFeeEditable()");
}

console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
process.exit(fail ? 1 : 0);
