// 工作台（四视图核心）组合式函数
// 承载：选中工单、工单分组列表（在途/已归档/已撤诉）、
// 处理情况(handling_notes)编辑态、配合度(branch_cooperation)、
// 明细解锁态(fee-unlock)、在途案件列表，以及新增的
// fee-unlock / withdraw / archive 真实接口接入。
import { getJ, postJ, putJ, delJ } from "api";
import { todayStr } from "helpers";

export function useWorkbench(toast, restoreComplaint, restoreWorkflow, getQr, onStatsRefresh = null) {
  // 统计联动：撤诉/归档/解锁费用改变统计口径后，由 app.js 注入的 loadStats 刷新看板
  function _refreshStats() {
    if (typeof onStatsRefresh === "function") onStatsRefresh();
  }
  // 工单分组列表
  const allTickets = Vue.ref([]);
  const ticketsOpen = Vue.ref([]);      // 在途（待处理 + 处理中，未撤诉未归档）
  const ticketsArchived = Vue.ref([]);  // 已归档
  const ticketsWithdrawn = Vue.ref([]); // 已撤诉
  const ticketsLoading = Vue.ref(false);

  // 当前选中工单
  const selectedTicketId = Vue.ref("");
  const selectedTicket = Vue.ref(null);   // 完整 detail（来自 /api/tickets/<id>/detail）
  const selectedLoading = Vue.ref(false);

  // 处理情况 + 配合度（工作台右/中栏编辑态）
  const handlingNotes = Vue.ref("");
  const branchCooperation = Vue.ref("");  // 好 / 中 / 差
  const coopOptions = ["好", "中", "差"];
  const feeUnlocked = Vue.ref(false);     // 当前工单明细是否被解锁（可编辑）

  // 学员姓名手动补录（三系统未命中时工作台内直接编辑）· ISS-UJ-01
  const studentNameEdit = Vue.ref("");

  // 登记表预览弹窗（归档前确认内容，文档在归档时才生成）
  const previewFormOpen = Vue.ref(false);
  const previewFormData = Vue.ref(null);
  const previewFormLoading = Vue.ref(false);

  async function previewRegistrationForm() {
    const id = selectedTicketId.value;
    if (!id) return;
    previewFormLoading.value = true;
    try {
      const d = await postJ(`/api/tickets/${id}/register-form/preview`, {
        handling_notes: handlingNotes.value,
        student_name: studentNameEdit.value.trim(),
      });
      if (!d.success) throw new Error(d.error || "预览失败");
      previewFormData.value = d.data;
      previewFormOpen.value = true;
    } catch (e) {
      toast("登记表预览失败", e.message, "danger");
    } finally {
      previewFormLoading.value = false;
    }
  }

  // 归档根目录（可编辑，默认沿用上次归档路径，持久化于 config.archive_root）
  const archiveRoot = Vue.ref("");
  getJ("/api/config").then((d) => {
    if (d && typeof d.archive_root === "string" && d.archive_root) archiveRoot.value = d.archive_root;
  }).catch(() => {});

  // 归档根目录选择弹框（服务端列目录 + 前端浏览，替代手填路径）
  const folderModalOpen = Vue.ref(false);
  const fbPath = Vue.ref("");      // 当前浏览的目录（绝对路径）
  const fbParent = Vue.ref("");    // 上一级目录（空表示已到根）
  const fbDirs = Vue.ref([]);      // 子文件夹名列表
  const fbLoading = Vue.ref(false);
  const fbErr = Vue.ref("");

  async function fbLoad(p) {
    fbLoading.value = true;
    fbErr.value = "";
    try {
      const d = await getJ(`/api/fs/browse?path=${encodeURIComponent(p || "")}`);
      if (d.success === false) throw new Error(d.error || "读取目录失败");
      fbPath.value = d.data.path;
      fbParent.value = d.data.parent || "";
      fbDirs.value = d.data.dirs || [];
    } catch (e) {
      fbErr.value = e.message;
    } finally {
      fbLoading.value = false;
    }
  }

  async function openFolderPicker() {
    folderModalOpen.value = true;
    // 起始目录：当前归档根目录为绝对路径则从它开始，否则从用户主目录开始（path 留空由后端默认）
    const cur = (archiveRoot.value || "").trim();
    const start = cur && /^[/~]/.test(cur) ? cur : "";
    await fbLoad(start);
  }

  function fbEnter(name) {
    const base = fbPath.value.replace(/[\\/]+$/, "");
    return fbLoad(`${base}/${name}`);
  }

  async function fbUp() {
    if (fbParent.value) await fbLoad(fbParent.value);
  }

  function fbConfirm() {
    if (!fbPath.value) return;
    archiveRoot.value = fbPath.value;
    folderModalOpen.value = false;
  }

  // 列表筛选
  const listFilter = Vue.ref("all");

  // 右栏折叠：学员档案 / 办理时间线（默认展开）、在途案件（默认折叠）
  const railOpen = Vue.ref(false);
  const profileOpen = Vue.ref(true);
  const timelineOpen = Vue.ref(true);

  // 合同预览（前端硬编码常见条款，高亮当前扣费依据条款）
  const contractModalOpen = Vue.ref(false);

  function classify(list) {
    const open = [], archived = [], withdrawn = [];
    for (const t of list) {
      if (t.withdraw_status === "已撤诉") withdrawn.push(t);
      else if (t.archive_status === "已归档") archived.push(t);
      else open.push(t);
    }
    return { open, archived, withdrawn };
  }

  async function loadTickets() {
    ticketsLoading.value = true;
    try {
      const d = await getJ("/api/tickets?limit=200");
      const records = d.data?.records || [];
      allTickets.value = records;
      const { open, archived, withdrawn } = classify(records);
      ticketsOpen.value = open;
      ticketsArchived.value = archived;
      ticketsWithdrawn.value = withdrawn;
    } catch (e) {
      toast("工单列表加载失败", e.message, "danger");
    } finally {
      ticketsLoading.value = false;
    }
  }

  // ═══════════════════════════════════════════════
  //  投诉列表页重设计：筛选 / 排序 / 勾选 / 批量操作
  // ═══════════════════════════════════════════════
  const OVERDUE_DAYS = 7;

  const clKw = Vue.ref("");
  const clType = Vue.ref("");
  const clChannel = Vue.ref("");
  const clHandler = Vue.ref("");
  const clFee = Vue.ref("");          // confirmed / pending / none
  const clDays = Vue.ref("all");      // all / 7 / 30
  const clDateFrom = Vue.ref("");
  const clDateTo = Vue.ref("");
  const clOnlyOverdue = Vue.ref(false);
  const clOnlyManual = Vue.ref(false);   // 仅看三系统无信息（人工建案）工单
  const clGroup = Vue.ref("all");     // KPI 过滤：all / open / archived / withdrawn
  const clSort = Vue.ref({ key: "", dir: -1 });
  const clCollapsed = Vue.ref({ withdrawn: true });
  const clSelectedIds = Vue.ref([]);

  const TYPE_LABELS = { A: "退费纠纷", B: "教学服务", C: "考试安排", D: "合同争议", E: "其他" };
  const FEE_LABELS = { confirmed: "费用已确认", pending: "费用待确认" };

  function normDate(s) {
    return String(s || "").trim().replace(/\//g, "-");
  }
  function daysOpen(t) {
    const d = normDate(t.complaint_date);
    if (!d) return 0;
    const ms = Date.now() - new Date(d + "T00:00:00").getTime();
    return isNaN(ms) ? 0 : Math.max(0, Math.floor(ms / 86400000));
  }
  function isOverdue(t) {
    return ["待处理", "处理中"].includes(t.handle_status) && t.withdraw_status !== "已撤诉" && t.archive_status !== "已归档"
      && daysOpen(t) > OVERDUE_DAYS;
  }
  function feeState(t) {
    if (t.fee_plan_status === "confirmed") return "confirmed";
    if (Number(t.refund_fee) > 0 || t.fee_plan_status === "pending") return "pending";
    return "none";
  }
  function maskPhone(p) {
    const s = String(p || "");
    return s.length === 11 ? s.slice(0, 3) + "****" + s.slice(-4) : (s || "—");
  }

  const handlerOptions = Vue.computed(() => {
    const set = new Set();
    for (const t of allTickets.value) {
      const h = String(t.handler_name || "").trim();
      if (h) set.add(h);
    }
    return [...set].sort();
  });
  const channelOptions = Vue.computed(() => {
    const set = new Set();
    for (const t of allTickets.value) {
      const c = String(t.source_channel || "").trim();
      if (c) set.add(c);
    }
    return [...set];
  });

  function _clMatch(t) {
    if (clGroup.value === "open" && !(["待处理", "处理中"].includes(t.handle_status) && t.withdraw_status !== "已撤诉" && t.archive_status !== "已归档")) return false;
    if (clGroup.value === "archived" && t.archive_status !== "已归档") return false;
    if (clGroup.value === "withdrawn" && t.withdraw_status !== "已撤诉") return false;
    if (clType.value && t.complaint_type !== clType.value) return false;
    if (clChannel.value && (t.source_channel || "") !== clChannel.value) return false;
    if (clHandler.value === "__unassigned" && String(t.handler_name || "").trim()) return false;
    if (clHandler.value && clHandler.value !== "__unassigned" && t.handler_name !== clHandler.value) return false;
    if (clFee.value && feeState(t) !== clFee.value) return false;
    if (clOnlyOverdue.value && !isOverdue(t)) return false;
    if (clOnlyManual.value && !String(t.intake_type || "").trim()) return false;
    const d = normDate(t.complaint_date);
    if (clDateFrom.value && (!d || d < clDateFrom.value)) return false;
    if (clDateTo.value && (!d || d > clDateTo.value)) return false;
    if (clDays.value !== "all") {
      const d = normDate(t.complaint_date);
      if (!d || d < _fmtDaysAgo(Number(clDays.value))) return false;
    }
    if (clKw.value) {
      const k = clKw.value.toLowerCase();
      const hay = [t.student_name, t.ticket_no, t.id, t.phone, t.id_card].map(x => String(x || "").toLowerCase());
      if (!hay.some(h => h.includes(k))) return false;
    }
    return true;
  }
  function _fmtDaysAgo(n) {
    const dt = new Date(Date.now() - n * 86400000);
    return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
  }

  function _cmp(a, b, key) {
    let va, vb;
    if (key === "days") { va = daysOpen(a); vb = daysOpen(b); }
    else if (key === "refund") { va = a.refund_fee == null ? -1 : Number(a.refund_fee); vb = b.refund_fee == null ? -1 : Number(b.refund_fee); }
    else { va = normDate(a.complaint_date); vb = normDate(b.complaint_date); }
    return va < vb ? -1 : va > vb ? 1 : 0;
  }

  const listGroups = Vue.computed(() => {
    const matched = allTickets.value.filter(_clMatch);
    const mk = (key, label, items) => ({ key, label, overdueCount: items.filter(isOverdue).length, items });
    const open = [], archived = [], withdrawn = [];
    for (const t of matched) {
      if (t.withdraw_status === "已撤诉") withdrawn.push(t);
      else if (t.archive_status === "已归档") archived.push(t);
      else open.push(t);
    }
    const sortKey = clSort.value.key, dir = clSort.value.dir;
    const sortFn = sortKey ? (a, b) => _cmp(a, b, sortKey) * dir : null;
    for (const arr of [open, archived, withdrawn]) {
      if (sortFn) arr.sort(sortFn);
      else arr.sort((a, b) => _cmp(a, b, "date"));   // 默认按受理日期倒序
    }
    return [
      mk("open", "在途", open),
      mk("archived", "已归档", archived),
      mk("withdrawn", "已撤诉", withdrawn),
    ];
  });
  const clResultCount = Vue.computed(() => listGroups.value.reduce((s, g) => s + g.items.length, 0));
  const clOverdueTotal = Vue.computed(() => allTickets.value.filter(isOverdue).length);
  const clSerialMap = Vue.computed(() => {
    const map = {};
    let n = 0;
    for (const g of listGroups.value) for (const t of g.items) map[t.id] = ++n;
    return map;
  });

  // 分页：每页条数用户可自定义（默认 10），各分组独立翻页
  const clPageSize = Vue.ref(10);
  const clPage = Vue.ref({ open: 1, archived: 1, withdrawn: 1 });
  Vue.watch(
    [clKw, clType, clChannel, clHandler, clFee, clDays, clDateFrom, clDateTo,
     clOnlyOverdue, clOnlyManual, clGroup, () => clSort.value.key, () => clSort.value.dir, clPageSize],
    () => { clPage.value = { open: 1, archived: 1, withdrawn: 1 }; }
  );
  const pagedGroups = Vue.computed(() => {
    const size = Math.max(1, Math.floor(Number(clPageSize.value) || 10));
    return listGroups.value.map((g) => {
      const pages = Math.max(1, Math.ceil(g.items.length / size));
      const page = Math.min(Math.max(1, clPage.value[g.key] || 1), pages);
      return { ...g, totalItems: g.items.length, items: g.items.slice((page - 1) * size, page * size), page, pages };
    });
  });
  function clSetPage(key, n) { clPage.value[key] = Math.max(1, n); }

  function clToggleRow(id) {
    const i = clSelectedIds.value.indexOf(id);
    i >= 0 ? clSelectedIds.value.splice(i, 1) : clSelectedIds.value.push(id);
  }
  function clToggleGroupSelect(g) {
    const ids = g.items.map(t => t.id);
    const allIn = ids.length && ids.every(id => clSelectedIds.value.includes(id));
    for (const id of ids) {
      const i = clSelectedIds.value.indexOf(id);
      if (allIn) { if (i >= 0) clSelectedIds.value.splice(i, 1); }
      else if (i < 0) clSelectedIds.value.push(id);
    }
  }
  function clSelectAllShown() {
    for (const g of listGroups.value) for (const t of g.items) {
      if (!clSelectedIds.value.includes(t.id)) clSelectedIds.value.push(t.id);
    }
  }
  function clClearSelection() { clSelectedIds.value = []; }
  function clSetSort(key) {
    if (clSort.value.key === key) clSort.value = { key, dir: -clSort.value.dir };
    else clSort.value = { key, dir: -1 };
  }
  function clClearFilters() {
    clKw.value = ""; clType.value = ""; clChannel.value = ""; clHandler.value = "";
    clFee.value = ""; clDays.value = "all"; clDateFrom.value = ""; clDateTo.value = "";
    clOnlyOverdue.value = false; clOnlyManual.value = false;
  }

  async function batchExportSelected() {
    const ids = [...clSelectedIds.value];
    if (!ids.length) return;
    try {
      const resp = await fetch("/api/tickets/export?ids=" + encodeURIComponent(ids.join(",")));
      if (!resp.ok) throw new Error("导出失败: " + resp.statusText);
      const blob = await resp.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `投诉工单导出_${ids.length}条_${todayStr()}.xlsx`;
      document.body.appendChild(a); a.click(); a.remove();
      window.URL.revokeObjectURL(url);
      toast(`已导出 ${ids.length} 条案件`, "", "success");
    } catch (e) {
      toast("导出失败", e.message, "danger");
    }
  }

  // 批量转办弹窗
  const transferModalOpen = Vue.ref(false);
  const transferTarget = Vue.ref("");
  const transferSaving = Vue.ref(false);
  function askBatchTransfer() {
    if (!clSelectedIds.value.length) return;
    transferTarget.value = "";
    transferModalOpen.value = true;
  }
  async function confirmBatchTransfer() {
    const target = String(transferTarget.value).trim();
    if (!target) { toast("请填写接收人", "", "warning"); return; }
    transferSaving.value = true;
    let ok = 0, fail = 0, firstErr = "";
    try {
      for (const id of [...clSelectedIds.value]) {
        try {
          const d = await putJ(`/api/tickets/${id}`, { handler_name: target });
          if (d.success) ok++;
          else { fail++; if (!firstErr) firstErr = d.error || "未知错误"; }
        } catch { fail++; if (!firstErr) firstErr = "网络请求失败"; }
      }
      transferModalOpen.value = false;
      clClearSelection();
      await loadTickets();
      if (!fail) toast(`已转办 ${ok} 条给 ${target}`, "", "success");
      else if (!ok) toast("转办失败", firstErr, "danger");
      else toast("部分转办成功", `成功 ${ok} 条，失败 ${fail} 条：${firstErr}`, "warning");
    } finally {
      transferSaving.value = false;
    }
  }


  // 批量删除弹窗
  const deleteModalOpen = Vue.ref(false);
  const deleteSaving = Vue.ref(false);
  function askDeleteSelected() {
    if (!clSelectedIds.value.length) return;
    deleteModalOpen.value = true;
  }
  async function confirmDeleteSelected() {
    deleteSaving.value = true;
    let ok = 0, fail = 0, firstErr = "";
    try {
      for (const id of [...clSelectedIds.value]) {
        try {
          const d = await delJ(`/api/tickets/${id}`);
          if (d.success) ok++;
          else { fail++; if (!firstErr) firstErr = d.error || "未知错误"; }
        } catch { fail++; if (!firstErr) firstErr = "网络请求失败"; }
      }
      deleteModalOpen.value = false;
      clClearSelection();
      await loadTickets();
      _refreshStats();
      if (!fail) toast(`已删除 ${ok} 条案件`, "", "success");
      else if (!ok) toast("删除失败", firstErr, "danger");
      else toast("部分删除成功", `成功 ${ok} 条，失败 ${fail} 条：${firstErr}`, "warning");
    } finally {
      deleteSaving.value = false;
    }
  }


  async function openTicket(id) {
    if (!id) return;
    selectedTicketId.value = id;
    selectedLoading.value = true;
    try {
      const d = await getJ(`/api/tickets/${id}/detail`);
      if (!d.success) throw new Error(d.error || "工单详情加载失败");
      const detail = d.data;
      selectedTicket.value = detail.ticket;
      restoreComplaint(detail.ticket);
      await Vue.nextTick();
      restoreWorkflow(detail);
      // 绑定工作台自有字段
      handlingNotes.value = detail.ticket.handling_notes || "";
      branchCooperation.value = detail.ticket.branch_cooperation || "";
      studentNameEdit.value = detail.ticket.student_name || "";
      feeUnlocked.value = false;
    } catch (e) {
      toast("打开工单失败", e.message, "danger");
    } finally {
      selectedLoading.value = false;
    }
  }

  // 受理成功后刷新列表 + 统计（供 app.js 调用）
  async function refreshAfterIntake(loadStats) {
    await loadTickets();
    if (loadStats) await loadStats();
  }

  // 保存处理情况 + 配合度 + 学员姓名（PUT /api/tickets/<id>）
  async function saveProgress() {
    const id = selectedTicketId.value;
    if (!id) return;
    try {
      const payload = {
        handling_notes: handlingNotes.value,
        branch_cooperation: branchCooperation.value,
      };
      const name = studentNameEdit.value.trim();
      if (name && name !== (selectedTicket.value?.student_name || "")) {
        payload.student_name = name;
      }
      const d = await putJ(`/api/tickets/${id}`, payload);
      if (!d.success) throw new Error(d.error || "保存失败");
      if (selectedTicket.value && payload.student_name) {
        selectedTicket.value.student_name = payload.student_name;
        await loadTickets();
      }
      toast("处理进度已保存", "", "success");
    } catch (e) {
      toast("保存失败", e.message, "danger");
    }
  }

  // 费用解锁：POST /api/tickets/<id>/fee-unlock（无 body）→ 明细改回可编辑
  async function unlockFee() {
    const id = selectedTicketId.value;
    if (!id) return;
    try {
      const d = await postJ(`/api/tickets/${id}/fee-unlock`, {});
      if (!d.success) throw new Error(d.error || "解锁失败");
      feeUnlocked.value = true;
      if (selectedTicket.value) selectedTicket.value.fee_plan_status = "draft";
      _refreshStats();
      toast("已解锁费用明细", "可重新编辑并确认", "info");
    } catch (e) {
      toast("解锁失败", e.message, "danger");
    }
  }

  // 撤诉：PUT /api/tickets/<id>/withdraw（body 可选 {reason}），常驻按钮
  async function doWithdraw(reason) {
    const id = selectedTicketId.value;
    if (!id) return;
    try {
      const d = await putJ(`/api/tickets/${id}/withdraw`, { reason: reason || "" });
      if (!d.success) throw new Error(d.error || "撤诉失败");
      if (selectedTicket.value) selectedTicket.value.withdraw_status = "已撤诉";
      await loadTickets();
      _refreshStats();
      toast("已标记撤诉", "统计已移出有效投诉", "success");
    } catch (e) {
      toast("撤诉失败", e.message, "danger");
    }
  }

  // 取消撤诉：PUT /api/tickets/<id>/withdraw-status {withdraw_status:"未撤诉"}，
  // 恢复在途统计口径，不重写 withdrawn_at/withdraw_reason 原记录
  async function doCancelWithdraw() {
    const id = selectedTicketId.value;
    if (!id) return;
    try {
      const d = await putJ(`/api/tickets/${id}/withdraw-status`, { withdraw_status: "未撤诉" });
      if (!d.success) throw new Error(d.error || "取消撤诉失败");
      if (selectedTicket.value) {
        selectedTicket.value.withdraw_status = "未撤诉";
        selectedTicket.value.withdrawn_at = "";
      }
      await loadTickets();
      _refreshStats();
      toast("已取消撤诉", "案件恢复计入有效投诉", "success");
    } catch (e) {
      toast("取消撤诉失败", e.message, "danger");
    }
  }

  // 归档闸门计算（前端预校验，与后端 archive_gate_errors 对齐）
  const archiveGates = Vue.computed(() => {
    const t = selectedTicket.value || {};
    const wf = getQr ? getQr() : null;
    return {
      notes: Boolean((handlingNotes.value || "").trim()),
      coop: Boolean(branchCooperation.value),
      fee: t.fee_plan_status === "confirmed",
    };
  });
  const gateErrors = Vue.computed(() => {
    const g = archiveGates.value;
    const errs = [];
    if (!g.notes) errs.push("处理情况未填写");
    if (!g.coop) errs.push("配合度未评价");
    if (!g.fee) errs.push("扣费明细表未确认");
    return errs;
  });
  const canArchive = Vue.computed(() => gateErrors.value.length === 0);

  // 归档：先保存处理情况/配合度，再 POST /api/tickets/<id>/archive。
  // 返回 true=归档成功，false=失败（供调用方决定是否跳转列表聚焦）
  async function doArchive() {
    const id = selectedTicketId.value;
    if (!id) return false;
    if (!canArchive.value) {
      toast("归档未满足", gateErrors.value.join("；"), "warning");
      return false;
    }
    try {
      // 1) 落盘处理情况 + 配合度（含未保存的姓名补录，避免归档夹沿用旧名）
      const savePayload = {
        handling_notes: handlingNotes.value,
        branch_cooperation: branchCooperation.value,
      };
      const name = studentNameEdit.value.trim();
      if (name && name !== (selectedTicket.value?.student_name || "")) {
        savePayload.student_name = name;
      }
      const s = await putJ(`/api/tickets/${id}`, savePayload);
      if (!s.success) throw new Error(s.error || "保存处理情况失败");
      // 2) 三闸门校验 + 归档两件套（携带用户填写的归档根目录）
      const a = await postJ(`/api/tickets/${id}/archive`, { archive_root: (archiveRoot.value || "").trim() });
      if (!a.success) throw new Error((a.errors && a.errors.join("；")) || a.error || "归档失败");
      if (selectedTicket.value && savePayload.student_name) selectedTicket.value.student_name = savePayload.student_name;
      if (selectedTicket.value) selectedTicket.value.archive_status = "已归档";
      await loadTickets();
      _refreshStats();
      const n = (a.data?.files || []).length;
      toast("归档成功", `共 ${n} 份资料：${a.data?.dir || ""}`, "success");
      if (a.data?.warning) toast("归档提示", a.data.warning, "warning");
      return true;
    } catch (e) {
      toast("归档失败", e.message, "danger");
      return false;
    }
  }

  // 投诉列表行聚焦：清空筛选 → 切到已归档组 → 翻到所在页 → 滚动定位并闪烁高亮
  const clFocusId = Vue.ref("");
  let clFocusTimer = null;

  async function focusArchivedTicket(id) {
    if (!id) return;
    clClearFilters();
    clGroup.value = "archived";
    await Vue.nextTick();               // 等筛选 watcher 把各组分页重置为第 1 页
    const idx = ticketsArchived.value.findIndex(t => t.id === id);
    if (idx >= 0) {
      const size = Math.max(1, Math.floor(Number(clPageSize.value) || 10));
      clSetPage("archived", Math.floor(idx / size) + 1);
    }
    await Vue.nextTick();               // 等分页后的行渲染出来
    clFocusId.value = id;
    const el = document.getElementById(`cl-row-${id}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    if (clFocusTimer) clearTimeout(clFocusTimer);
    clFocusTimer = setTimeout(() => { clFocusId.value = ""; }, 2600);
  }

  return {
    allTickets, ticketsOpen, ticketsArchived, ticketsWithdrawn, ticketsLoading,
    selectedTicketId, selectedTicket, selectedLoading,
    handlingNotes, branchCooperation, coopOptions, feeUnlocked,
    studentNameEdit,
    previewFormOpen, previewFormData, previewFormLoading, previewRegistrationForm,
    listFilter, railOpen, profileOpen, timelineOpen, contractModalOpen,
    loadTickets, openTicket, refreshAfterIntake, saveProgress,
    unlockFee, doWithdraw, doCancelWithdraw, doArchive,
    archiveGates, gateErrors, canArchive, archiveRoot,
    clFocusId, focusArchivedTicket,
    folderModalOpen, fbPath, fbParent, fbDirs, fbLoading, fbErr,
    openFolderPicker, fbLoad, fbEnter, fbUp, fbConfirm,
    // 投诉列表页重设计
    OVERDUE_DAYS, TYPE_LABELS, FEE_LABELS,
    clKw, clType, clChannel, clHandler, clFee, clDays, clDateFrom, clDateTo,
    clOnlyOverdue, clOnlyManual, clGroup, clSort, clCollapsed, clSelectedIds,
    handlerOptions, channelOptions, listGroups, clResultCount, clOverdueTotal, clSerialMap,
    clPageSize, pagedGroups, clSetPage,
    daysOpen, isOverdue, feeState, maskPhone,
    clToggleRow, clToggleGroupSelect, clSelectAllShown, clClearSelection, clSetSort, clClearFilters,
    batchExportSelected,
    transferModalOpen, transferTarget, transferSaving, askBatchTransfer, confirmBatchTransfer,
    deleteModalOpen, deleteSaving, askDeleteSelected, confirmDeleteSelected,
  };
}
