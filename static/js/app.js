// 驾校投诉处理系统 - Vue 3 App（v2 四视图工作台）
import { useToast } from "useToast";
import { useComplaint } from "useComplaint";
import { useWorkflow } from "useWorkflow";
import { useContractPreview } from "useContractPreview";
import { useSettings, useUsers, USER_ROLES, roleLabel } from "useSettings";
import { useHistory } from "useHistory";
import { useWorkbench } from "useWorkbench";
import { stBadge, getTrainingTime, getEventType, getDrivingFeeBreakdown, todayStr } from "helpers";
import { getJ } from "api";
import { postJ } from "api";
import { startAuthWatch } from "auth";

// 从 reason 中提取违约金计算公式（如 "3180×20%=636"）
function extractFormula(reason) {
  if (!reason) return "";
  const match = reason.match(/(\d+(?:\.\d+)?)\s*[×*]\s*(\d+(?:\.\d+)?)%\s*=\s*(\d+(?:\.\d+)?)/);
  if (match) return `${match[1]}×${match[2]}%=${match[3]}`;
  return "";
}
function money(n) {
  return Number(n || 0).toLocaleString("zh-CN");
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
}

const { createApp, ref, computed, watch, nextTick } = Vue;

// Vue 3 mount("#app") 不会把 #app 内的 HTML 当作 template（与 Vue 2 不同），
// 必须显式把模板字符串传入。模板已在 #app 节点中，直接读取其 innerHTML 即可。
const __appRoot = document.getElementById("app");
const __template = __appRoot ? __appRoot.innerHTML : "";

try {
  // Vue 3 的自定义定界符必须配置在 app.config.compilerOptions 上，
  // 直接挂在根配置里被忽略，会让 [[ ]] 被当作文本输出，遇到 .length 时报错。
  const __app = createApp({
    template: __template,
    setup() {
    const { toasts, toast } = useToast();
    const currentDate = Vue.computed(() => todayStr());

    // ── 当前账号（后端注入 window.__CURRENT_USER__）──
    const currentUser = Vue.ref(window.__CURRENT_USER__ || null);
    const isAdmin = Vue.computed(() => currentUser.value?.role === 'admin');
    const isViewer = Vue.computed(() => currentUser.value?.role === 'viewer');

    async function logout() {
      if (!confirm('确定要退出登录吗？')) return;
      await postJ('/api/session/logout', {});
      window.location.href = '/';
    }

    // ── 四视图路由（列表 / 受理 / 工作台 / 看板） ──
    const view = ref("intake");
    const viewTitle = Vue.computed(() => ({
      list: "投诉列表", intake: "新增投诉", workbench: "学员信息", kanban: "投诉统计", settings: "系统设置",
    }[view.value] || ""));

    const sidebarCollapsed = ref(localStorage.getItem("sidebarCollapsed") === "1");
    watch(sidebarCollapsed, v => { try { localStorage.setItem("sidebarCollapsed", v ? "1" : "0"); } catch (e) {} });
    function toggleSidebar() { sidebarCollapsed.value = !sidebarCollapsed.value; }

    // ── 主题切换（亮/暗，持久化到 localStorage） ──
    const isDark = ref(localStorage.getItem("theme") === "dark");
    watch(isDark, v => {
      document.documentElement.classList.toggle("dark", v);
      try { localStorage.setItem("theme", v ? "dark" : "light"); } catch (e) {}
    });
    function toggleTheme() { isDark.value = !isDark.value; }

    // ── 投诉受理 + 查询 ──
    async function intakeComplete() {
      if (currentTicketId.value) {
        await refreshAfterIntake(loadStats);
        await openTicket(currentTicketId.value);
        view.value = "workbench";
      }
    }

    const {
      form, fileInputRef, isDragOver,
      intakeLoading, intakeResult, intakeErr, pulseIdCard, pulsePhone,
      intakeText, handleIntakeText,
      intakeTextareaRef, autoResizeTextarea, onSourceChange, onIntakePaste,
      querying, qErr, qr, queryProgress,
      sourceStatusText, sourceStatusColor, sourceStatusIcon, sourcePillClass, phaseDetail, noEContractReason, drivingContractTag,
      currentTicketId, triggerFileInput,
      onDragOver, onDragEnter, onDragLeave, onDrop, onFileSelected,
      queryAll, restore: restoreComplaint, reset: resetComplaint,
      assignableUsers, loadAssignableUsers,
      studentName, schoolShort, regStart, regEnd,
      candidates, candWrapRef, searching, candEmpty, searchSource, selectedCand, queryTarget,
      candTotal, candPage, candTotalPages, gotoCandPage, orgFallback, orgOptions,
      phoneMismatch, residencyTip, nameMismatch, phoneCandidates, successBar,
      clearTransient, examStageClass,
      routeMode, queryPrimary, qFieldClass, mainBtnText,
      searchStudents, chooseCandidate, onMainClick, runExactQuery,
      sameDayTicket, chooseMergeExisting, createNewAnyway, dismissSameDayPrompt,
      noMatchInfo, dismissNoMatch, manualOpen, manualCreating, manualErr, manualForm,
      orgUnitsFull, openManualIntake, closeManualIntake, submitManualIntake,
    } = useComplaint(intakeComplete);

    async function queryAllAndGo() {
      await onMainClick();
    }

    // 新增投诉页：重置受理表单与查询状态（对应悬浮操作栏「重置」）
    function resetIntake() {
      focusZone.value = null;
      flashTarget.value = "";
      resetComplaint(currentUser.value);
    }

    // 启动时拉一次可指派人列表（同时把处理人下拉填好默认值）
    loadAssignableUsers();
    resetComplaint(currentUser.value);

    // ── 新增投诉页：聚焦式交互（非焦点区变暗 + 焦点卡光晕 + 滚动校正） ──
    const focusZone = ref(null);   // null | 'register' | 'candidates' | 'query'
    const flashTarget = ref("");   // 当前光晕闪烁的卡片 id
    const leftDim = computed(() => (focusZone.value ? "dimmed" : ""));
    const registerDim = computed(() => (focusZone.value === "query" ? "dimmed" : ""));
    const filledCount = computed(() =>
      intakeResult.value ? ["student_name", "id_card", "phone"].filter((k) => intakeResult.value[k]).length : 0
    );
    function undoDim() { if (focusZone.value) focusZone.value = null; }
    function flash(id) {
      flashTarget.value = "";
      requestAnimationFrame(() => {
        flashTarget.value = id;
        setTimeout(() => { flashTarget.value = ""; }, 1300);
      });
    }
    // 滚动定位：预留底部悬浮操作栏高度，防止进度条/成功条被遮挡
    function scrollToSec(id, block = "start") {
      nextTick(() => {
        const el = document.getElementById(id);
        if (!el) return;
        const barH = 84, margin = 16;
        if (block === "center") {
          const r = el.getBoundingClientRect();
          window.scrollTo({ top: r.top + window.scrollY - Math.max(margin, (window.innerHeight - barH - r.height) / 2 - margin), behavior: "smooth" });
          return;
        }
        el.scrollIntoView({ behavior: "smooth", block });
        setTimeout(() => {
          const r = el.getBoundingClientRect();
          const limit = window.innerHeight - barH - margin;
          if (r.bottom > limit) window.scrollBy({ top: r.bottom - limit, behavior: "smooth" });
        }, 420);
      });
    }
    // 提取线索完成 → 聚焦②登记卡
    watch(intakeResult, (v) => {
      if (v && !intakeLoading.value) { focusZone.value = "register"; flash("secRegister"); scrollToSec("secRegister", "start"); }
    });
    // 搜索出候选学员 → 聚焦候选列表
    watch(() => candidates.value.length, (n) => {
      if (n > 0) { focusZone.value = "candidates"; flash("secCand"); scrollToSec("secCand", "start"); }
    });
    // 三系统查询开始 → 聚焦进度条
    watch(() => queryProgress.show, (v) => {
      if (v) { focusZone.value = "query"; flash("secProgress"); scrollToSec("secProgress", "center"); }
    });
    // 建案成功 → 聚焦成功条
    watch(successBar, (v) => { if (v) scrollToSec("secDone", "center"); });
    // 步骤指示器状态
    function stpDone(n) {
      return n === 1 ? !!intakeResult.value
        : n === 2 ? (candidates.value.length > 0 || !!qr.value)
        : !!successBar.value;
    }
    function stpState(n) {
      const d = stpDone(n);
      return { done: d, active: !d && (n === 1 ? true : n === 2 ? !!intakeResult.value : stpDone(2)) };
    }
    function jumpTo(n) {
      undoDim();
      scrollToSec(n === 1 ? "secMaterial" : n === 2 ? "secRegister" : "secCand", "start");
    }

    // ── 工作流程（合同 → 退费分析 → 沟通 → 归档） ──
    const {
      workflowStep, workflowStatusText,
      cSrc, cPath, cName, cLoading, uploadedFiles,
      aLoading, analysisProgress, analysisElapsed, deductionSum, deductionMismatch,
      ar, aErr, manualContract, canProceedToAnalysis, threeSystemReady,
      rpLoading, rpResult, rpErr,
      dlContract, ulContract, confirmContract, startManualEdit, doAnalyze, confirmAnalysis,
      recalc, addDeduction, removeDeduction, exportDeductions, updatePenaltyRate, genReply,
      dongchengServiceFee, dongchengTrainingMode, dongchengSaving, isDongchengTier,
      saveDongchengFields,
      OUTCOME_OPTIONS, COOPERATION_OPTIONS, NEGOTIATION_OPTIONS,
      feeConfirmed, feeConfirming, feeConfirmedAt, feePlanVersion,
      communications, communicationsLoading, commForm,
      finalOutcome, negotiationOutcome, withdrawStatus, withdrawUpdatedAt,
      syncOutcomeFromNegotiation, updateWithdrawStatus,
      otherOutcome, selectOutcomeCard, onOtherOutcomeChange,
      remarkOpen, commFormOpen,
      branchCooperationNote, archiveSaving, archivedCase, feeReopenReason, feeReopening, reopenFeePlan,
      formLoading, formResult,
      genRegistrationForm, reset: resetWorkflow, restore: restoreWorkflow,
      previewVisible, previewUrl, previewFilename, previewIsPdf, openPreview, closePreview,
      handleSaveAndConfirm,
      startManualFeeEntry,
      confirmNoFeeBasis,
      fromHistoryLabel, loadSavedAnalysis, saveAnalysis,
      loadCommunications, addCommunication, saveCaseOutcome,
    } = useWorkflow(toast, () => qr.value, () => currentTicketId.value, {
      // ISS-UJ-05/06：费用确认成功后同步工作台快照与列表，免去手动刷新
      async afterFeeConfirm(confirmed) {
        const st = selectedTicket.value;
        if (st) {
          st.fee_plan_status = confirmed.fee_plan_status || "confirmed";
          st.total_fee = confirmed.total_fee;
          st.actual_paid = confirmed.actual_paid;
          st.deduction_fee = confirmed.total_deduction;
          st.refund_fee = confirmed.refund;
        }
        await loadTickets();
      },
    });

    // ── 合同三栏预览（上传合同分析完成后渲染：原文 / 原件 / 扣费明细） ──
    // cp.* 暴露给模板（cp.tierDisplayName / cp.locate / cp.toggleFolded 等）
    const cp = useContractPreview(() => ar.value, () => cPath.value);

    // ── 设置 ──
    const { cfg, cfgSaving, cfgMsg, cfgOk, loadCfg, saveCfg,
            LLM_PROVIDERS, providerSel, applyProvider, presetModels, modelSel, modelCustom,
            testing, testResult, testLlm } = useSettings(toast);

    // ── 账号管理（admin 增删改查；所有人改自己资料/密码）──
    const users = useUsers({ toast, currentUser });
    // 切到系统设置时拉一次账号列表
    watch(view, (v) => {
      if (v === 'settings' && users.isAdmin.value) {
        users.loadList();
        users.loadAliases();
      }
      // 切到投诉列表时刷新（确保别名映射后的回写能立即看到）
      if (v === 'list' && typeof loadTickets === 'function') {
        loadTickets();
      }
    });

    // ── 历史 / 看板统计 ──
    const {
      hList, hTotal, hSearch, hLimit, hPage, hTotalPages, hLoading,
      statusFilter, schoolOptions,
      stats, chartDateStart, chartDateEnd, setChartPeriod, periodStat,
      durationStats, loadDurationStats,
      chartPeriod, masked, maskId, repeatOnly, rankMode, compare, topChannels, hPageButtons,
      groupedHistory, toggleGroup,
      debSearch, loadHist, loadStats, loadFromHist, exportTickets, updateTicketStatus,
      loadSchoolCodes,
      statScope, setStatScope, drill, drillCode, onDrillSelect, onDateChange, boardComplaintRate,
      drillInto, closeDrill,
      vehicleItems, vehicleKeyword, vehicleActiveItems, vehicleInactiveItems, vehicleLoading, vehicleSaving, vehicleModalOpen,
      loadVehicleCounts, saveVehicleCounts, addVehicleRow, removeVehicleRow,
      detailTicket, detailDeductions, detailCommunicationRecords, detailDocuments, detailLoading,
      showTicketDetail,
      initCharts, updateCharts, disposeCharts,
      askDeleteHist, confirmDeleteHist, hDeleteModal, hDeleting,
    } = useHistory(toast);

    // ── 学员信息页（原工作台，四视图核心状态） ──
    const {
      allTickets, ticketsOpen, ticketsArchived, ticketsWithdrawn, ticketsLoading,
      selectedTicketId, selectedTicket, selectedLoading,
      handlingNotes, branchCooperation, coopOptions, feeUnlocked, studentNameEdit,
      requeryIdCard, requerying, requerySkipped, requerySources, requeryMsg, requeryWithIdCard,
      extractInput, extractContent, extractDemands, extracting, extractDirty, aiExtractComplaint,
      previewFormOpen, previewFormData, previewFormLoading, previewRegistrationForm, formOverrides,
      listFilter, railOpen, profileOpen, timelineOpen, contractModalOpen, toggleRailPanel,
      loadTickets, openTicket, refreshAfterIntake, saveProgress,
      unlockFee, doWithdraw, doCancelWithdraw, doArchive, archiveGates, gateErrors, canArchive, archiveRoot,
      clFocusId, focusArchivedTicket,
      folderModalOpen, fbPath, fbParent, fbDirs, fbLoading, fbErr,
      openFolderPicker, fbLoad, fbEnter, fbUp, fbConfirm,
      // 投诉列表页重设计
      OVERDUE_DAYS, TYPE_LABELS, FEE_LABELS,
      clKw, clType, clChannel, clSchool, clHandler, clFee, clDays, clDateFrom, clDateTo,
      clOnlyOverdue, clOnlyManual, clGroup, clSort, clCollapsed, clSelectedIds,
      handlerOptions, channelOptions, clSchoolOptions, listGroups, clResultCount, clOverdueTotal, clSerialMap,
      clPageSize, pagedGroups, clSetPage, batchBarVisible,
      daysOpen, isOverdue, feeState, maskPhone,
      clToggleRow, clToggleGroupSelect, openArchive, openArchiveSelected, clClearSelection, clSetSort, clClearFilters,
      kjHelperModalOpen, kjUncPath, kjServerDir, copyKjUnc,
      kjLastTicketId, kjLastName, kjFilesOpen, kjFilesLoading, kjFiles, kjFilesDir,
      openKjFiles, kjFileUrl, fmtKjSize,
      batchExportSelected,
      clExpandedIds, clToggleExpand, copyPhone,
      transferModalOpen, transferTarget, transferSaving, askBatchTransfer, askTransferRow, confirmBatchTransfer,
      deleteModalOpen, deleteSaving, askDeleteSelected, askDeleteRow, confirmDeleteSelected,
      handlerLabel,
      actionAt,
    } = useWorkbench(toast, restoreComplaint, restoreWorkflow, () => qr.value, loadStats, assignableUsers);

    // ── 学员信息标签页（左栏三系统查询） ──
    const tab = ref("basic");
    const tabs = {
      basic: "基本信息", exam: "考试情况", training: "培训学时", fees: "收费记录", timeline: "学习时间轴",
    };

    // 工单卡片点击进入工作台
    async function openCase(id) {
      await openTicket(id);
      replySaved.value = false; replyPolished.value = false; replyEdited.value = false; replyCollapsed.value = false;
      view.value = "workbench";
    }

    // 从历史恢复
    async function loadFromHistAndGo(item) {
      resetComplaint();
      resetWorkflow();
      try {
        await openTicket(item.id);
        replySaved.value = false; replyPolished.value = false; replyEdited.value = false; replyCollapsed.value = false;
        view.value = "workbench";
        toast("已恢复案件", item.student_name || item.id_card, "info");
      } catch (e) {
        toast("恢复失败", e.message, "danger");
      }
    }

    // 处理情况 AI 优化：把输入框中用户记录的内容交给 LLM 整理为正式表述
    const notesPolishing = ref(false);
    async function aiOptimizeNotes() {
      if (notesPolishing.value) return;
      const raw = (handlingNotes.value || "").trim();
      if (!raw) { toast("请先记录处理情况", "随手记几个沟通关键词后再点「AI优化」", "warning"); return; }
      notesPolishing.value = true;
      try {
        const d = await postJ("/api/notes/polish", { text: raw });
        if (!d.success) throw new Error(d.error || "AI 优化失败");
        handlingNotes.value = (d.data?.polished || "").trim() || raw;
        toast("AI 已优化", "已按你记录的内容整理为正式表述", "success");
      } catch (e) {
        toast("AI 优化失败", e.message, "danger");
      } finally {
        notesPolishing.value = false;
      }
    }

    // 投诉内容/诉求 AI 润色（④卡片）：只修正错别字/病句/口语化，忠实原文不重写
    const complaintPolishing = ref(""); // "" | "content" | "demands"：追踪正在润色的栏位
    async function aiOptimizeComplaint(field) {
      if (complaintPolishing.value) return;
      const target = field === "content" ? extractContent : extractDemands;
      const raw = (target.value || "").trim();
      const label = field === "content" ? "投诉内容" : "投诉诉求";
      if (!raw) { toast(`${label}为空`, `请先确认${label}已有文字后再「AI优化」`, "warning"); return; }
      complaintPolishing.value = field;
      try {
        const d = await postJ("/api/complaint/polish", { field, text: raw });
        if (!d.success) throw new Error(d.error || "AI 优化失败");
        target.value = (d.data?.polished || "").trim() || raw;
        toast("AI 已优化", `已修正错别字并整理为规范表述（${label}）`, "success");
      } catch (e) {
        toast("AI 优化失败", e.message, "danger");
      } finally {
        complaintPolishing.value = "";
      }
    }

    // ── 合同获取 + AI 分析（工作台一键链路）──
    const contractFileInput = Vue.ref(null);

    function runAnalysisIfReady() {
      const q = qr.value || {};
      if (!cPath.value || !q.id_card) {
        toast("未获取到合同文件", "可改用「上传合同分析」导入纸质合同", "warning");
        return;
      }
      doAnalyze(q.id_card, q.exam_stage || "", q.training_hours || {}, currentTicketId.value);
    }

    async function dlContractAnalyze() {
      const q = qr.value || {};
      if (!q.id_card) { toast("缺少身份证号", "请先完成三系统查询", "warning"); return; }
      await dlContract(q.id_card, q.name || "", q.school_short || "");
      runAnalysisIfReady();
    }

    async function ulContractAnalyze(ev) {
      await ulContract(ev);
      runAnalysisIfReady();
    }

    // qr 变化时重置工作流
    Vue.watch(qr, (val) => {
      if (val && (val.contract_available || val.contract_check_deferred)) cSrc.value = "download";
      else if (val) cSrc.value = "upload";
      if (val && val.name) resetWorkflow();
    });

    // ── 初始化 ──
    loadSchoolCodes();
    loadTickets();
    loadStats();
    loadHist();

    // ── 图表 ──
    const trendChartRef = Vue.ref(null);
    const schoolChartRef = Vue.ref(null);
    const typeChartRef = Vue.ref(null);
    const statusChartRef = Vue.ref(null);
    const byTypeChartRef = Vue.ref(null);

    Vue.watch(() => view.value === "kanban", (isK) => {
      if (isK) {
        setTimeout(() => {
          initCharts({
            trendChartRef: trendChartRef, schoolChartRef: schoolChartRef,
            typeChartRef: typeChartRef, statusChartRef: statusChartRef,
          });
          updateCharts();
          initByTypeChart();
        }, 100);
      }
    });
    Vue.watch(stats, () => { if (view.value === "kanban") { Vue.nextTick(() => { updateCharts(); updateByTypeChart(); }); } }, { deep: true });

    function initByTypeChart() {
      if (byTypeChartRef.value) {
        if (!chartInstances.byType) chartInstances.byType = echarts.init(byTypeChartRef.value);
        updateByTypeChart();
      }
    }
    const chartInstances = {};
    function updateByTypeChart() {
      if (!chartInstances.byType) return;
      // P2-14：前端归一，'A'/'A-退费纠纷' 统一为 退费纠纷，空值归为 未知
      const RAW_TYPE_LABELS = { "A": "退费纠纷", "B": "教学服务", "C": "考试安排", "D": "合同争议", "E": "其他", "A-退费纠纷": "退费纠纷" };
      const normType = (t) => RAW_TYPE_LABELS[String(t || "").trim()] || (t ? String(t).trim() : "未知");
      const agg = {};
      for (const i of (stats.value.by_complaint_type || [])) {
        const n = normType(i.complaint_type);
        agg[n] = (agg[n] || 0) + (Number(i.count) || 0);
      }
      const data = Object.entries(agg).map(([name, value]) => ({ name, value }));
      const total = data.reduce((s, d) => s + d.value, 0);
      chartInstances.byType.setOption({
        color: ["#2563EB", "#4F46E5", "#16A34A", "#D97706", "#DC2626", "#64748B"],
        tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
        legend: { bottom: 0, textStyle: { fontSize: 11, color: "#374151" } },
        series: [{
          type: "pie", radius: ["40%", "65%"], center: ["50%", "45%"],
          data: total ? data : [{ name: "暂无数据", value: 1 }],
          label: { show: true, formatter: "{d}%", fontSize: 11, fontWeight: "bold", color: "#374151" },
          itemStyle: { borderRadius: 4, borderColor: "#fff", borderWidth: 2 },
        }],
      });
    }

    // ── 回复函富文本编辑器 + AI 润色 + 生成 ──
    const replyPolished = Vue.ref(false);
    const replyEdited = Vue.ref(false);
    const replySaved = Vue.ref(false);
    const replyHtml = Vue.ref("");
    const replyCollapsed = Vue.ref(false);

    function fmt(cmd, val) {
      document.execCommand(cmd, false, val || null);
      const el = document.getElementById("re-letter");
      if (el) { replyHtml.value = el.innerHTML; replyEdited.value = true; el.focus(); }
    }
    function buildLetter(polished) {
      const t = selectedTicket.value || {};
      const rows = (ar.value?.deductions || []).filter(d => Number(d.amount) > 0);
      const items = rows.map((r, i) =>
        `<p>${i + 1}、${esc(String(r.item || "").replace(/扣除$/, ""))}扣除（${esc(r.reason || "")}）：${money(r.amount)}元</p>`).join("");
      const now = new Date();
      const ds = `${now.getFullYear()}年${now.getMonth() + 1}月${now.getDate()}日`;
      const ded = Number(ar.value?.total_deduction || 0);
      const paid = Number(ar.value?.actual_paid || ar.value?.paid_amount || t.actual_paid || 0);
      const refund = Math.max(paid - ded, 0);
      const th = t.training_hours || {};
      const hoursDesc = [["科目三", "subject3"], ["科目二", "subject2"]]
        .map(([label, key]) => ({ label, v: String(th[label] || th[key] || "").trim() }))
        .filter(x => x.v && x.v.replace(/[^\d]/g, "") !== "0")
        .map(x => `${x.label}培训${x.v}`)
        .join("、");
      const stage = String(t.exam_stage || "").trim();
      const code = String(ar.value?.contract_code || t.contract_code || "").trim();
      const codePart = code ? `（合同编码：${esc(code)}）` : "";
      const schoolName = String(t.school_name || "").trim();
      const unitType = String(t.organization_unit_type || "").trim();
      const branch = unitType && schoolName.endsWith(unitType) ? schoolName : schoolName + unitType;
      return `<p class="ltr-title">关于${esc(t.student_name || "")}投诉的回复</p>`
        + `<p class="noind">东莞市交通运输局：</p>`
        + `<p>经我驾校调查核实，投诉人${esc(t.student_name || "")}（身份证号：${esc(t.id_card || "")}），于${esc(t.registration_date || "")}在${branch}网点报名${esc(t.license_type || "C1")}驾照培训。现收到学员投诉，要求退费。</p>`
        + `<p>据了解，学员报名共交培训服务费${money(paid)}元，目前进度处于：${esc(stage || "未知")}阶段${hoursDesc ? "，" + esc(hoursDesc) : ""}。</p>`
        + `<p>按照《东莞市机动车驾驶员培训服务合同》${codePart}第九条退学退费相关约定：（一）合同有效期内，甲方因个人原因中途提出退学的应向乙方提交书面申请，按项目扣除费用，剩余款项由乙方退回。扣费如下：</p>`
        + items
        + `<p><b>总扣费：${money(ded)}元。学员实际已交费用${money(paid)}元，应退回：${money(refund)}元。</b></p>`
        + `<p>${polished ? "以上扣费严格依据双方签订的《东莞市机动车驾驶员培训服务合同》相关条款及已核实的学员培训、考试进度计算，数据真实、口径一致；我驾校愿意按案件最终处理结果继续配合办理退学退费手续，并妥善做好学员沟通解释工作。" : "以上扣费严格依据双方签订的《东莞市机动车驾驶员培训服务合同》相关条款执行，我驾校愿意按合同约定配合办理退学退费手续。"}</p>`
        + `<p class="sig">驾校名称：东莞市快捷汽车驾驶员培训有限公司（公章）<br>${ds}</p>`;
    }
    function syncLetter() {
      const el = document.getElementById("re-letter");
      replyHtml.value = buildLetter(replyPolished.value);
      if (el) el.innerHTML = replyHtml.value;
      replyEdited.value = false;
    }
    const replyPolishing = ref(false);
    async function aiPolishReply() {
      if (replyPolishing.value) return;
      const el = document.getElementById("re-letter");
      const paras = el
        ? Array.from(el.querySelectorAll("p")).map(p => ({
            cls: p.className || "",
            text: (p.innerText || "").trim(),
          }))
        : [];
      if (!ar.value && !paras.some(p => p.text)) {
        toast("请先完成费用分析或填写回复内容再润色", "", "warning");
        return;
      }
      // 只润色叙述性段落：标题/称谓/落款（样式类）与扣费明细行、合计段（固定话术+纯数字）一律跳过，AI 不碰
      const isFixed = p => (p.cls && /ltr-title|noind|sig/.test(p.cls))
        || !p.text || /^\d+、/.test(p.text) || /^总扣费/.test(p.text);
      const targets = paras.map((p, i) => ({ p, i })).filter(x => !isFixed(x.p));
      if (!targets.length) {
        toast("无需润色", "标题、明细与落款为固定格式，可润色的正文段落为空", "info");
        return;
      }
      replyPolishing.value = true;
      try {
        const d = await postJ("/api/reply/polish", { paragraphs: targets.map(x => x.p.text) });
        if (!d.success) throw new Error(d.error || "AI 润色失败");
        const raw = String(d.data?.polished || "");
        let parts = raw.split(/<PARA>/i).map(s => s.trim()).filter(Boolean);
        if (parts.length !== targets.length) {
          // 兜底：模型未按 <PARA> 分段时按行尝试；仍不吻合则缺失段回填原文，绝不破坏已有内容
          const byLine = raw.split("\n").map(s => s.trim()).filter(Boolean);
          parts = targets.map((t, i) => (parts[i] ?? byLine[i] ?? t.p.text).replace(/\s*\n+\s*/g, " "));
        }
        el.innerHTML = paras.map((p, i) => {
          const k = targets.findIndex(t => t.i === i);
          const html = esc(String(k >= 0 ? (parts[k] ?? p.text) : p.text)).replace(/\n/g, "<br>");
          return `<p${p.cls ? ` class="${p.cls}"` : ""}>${html}</p>`;
        }).join("");
        replyHtml.value = el.innerHTML;
        replyEdited.value = false;
        toast("AI 润色完成", "已基于内置大模型通读全文后优化表述", "success");
      } catch (e) {
        toast("AI 润色失败", e.message, "danger");
      } finally {
        replyPolishing.value = false;
      }
    }
    async function generateReply() {
      const id = selectedTicketId.value;
      if (!id) { toast("请先打开工单", "", "warning"); return; }
      const deductions = (ar.value?.deductions || [])
        .filter(d => Number(d.amount) > 0)
        .map(d => ({ item: d.item, amt: Number(d.amount), basis: d.reason || "" }));
      try {
        const d = await postJ("/api/reply/generate", { ticket_id: id, deductions });
        if (!d.success) throw new Error(d.error || "生成失败");
        const el = document.getElementById("re-letter");
        replyHtml.value = el ? el.innerHTML : buildLetter(replyPolished.value);
        replySaved.value = true;
        if (selectedTicket.value) selectedTicket.value.reply_path = d.data?.filepath || "generated";
        toast("回复函已生成（v2）", d.data?.filename || "", "success");
      } catch (e) {
        toast("生成失败", e.message, "danger");
      }
    }

    // ── 合同预览（左合同原文 / 右扣费项对照；无文本层时回退原件预览，无合同时明确提示，不展示任何模板内容） ──
    const contractPreviewUrl = ref("");
    const contractIsImage = ref(false);
    const cmpData = ref(null);
    const cmpLoading = ref(false);
    const cmpRefreshing = ref(false);
    async function loadComparison() {
      const id = selectedTicketId.value;
      if (!id) return;
      cmpLoading.value = true;
      cmpData.value = null;
      try {
        const d = await getJ("/api/contract/comparison/" + id);
        if (!d.success) throw new Error(d.error || "加载失败");
        cmpData.value = d.data;
      } catch (e) {
        cmpData.value = null;
      }
      cmpLoading.value = false;
    }
    async function refreshPlatform() {
      const id = selectedTicketId.value;
      if (!id) return;
      cmpRefreshing.value = true;
      try {
        const d = await postJ("/api/contract/comparison/" + id + "/refresh");
        if (!d.success) throw new Error(d.error || "刷新失败");
        cmpData.value = d.data;
        toast("平台数据已刷新", "", "success");
      } catch (e) {
        toast("刷新失败", e.message, "danger");
      }
      cmpRefreshing.value = false;
    }
    const FEE_CLAUSE_KEYWORDS = [
      ["科目二", ["第二部分", "基础和场地驾驶培训"]],
      ["科目三", ["第三部分", "道路驾驶培训"]],
      ["综合服务费", ["综合服务费"]],
      ["理论培训", ["理论培训费"]],
      ["理论费", ["理论培训费"]],
      ["违约金", ["违约金"]],
      ["合同总额", ["培训费用合计", "培训服务费合计"]],
    ];
    // 标签 → 合同原文短语：直接在条款正文里检索费用原文（如"科目二实际操作培训费人民币 1200 元"），
    // 不再假设费用一定写在"第二部分/第三部分"条款里。自上而下首个命中的标签类别生效。
    const LABEL_TEXT_PHRASES = [
      // 电子合同（东莞驾培平台）用「第二部分/第三部分」表述费用定义句，放最前优先命中；
      // 旧纸质/旧电子合同无此短语，自动落回后面的原文短语，行为不变。
      ["科目二实操培训费", ["第二部分基础和场地驾驶培训费", "科目二实际操作培训费", "科目二实操培训费"]],
      ["科目二学时单价", ["第二部分基础和场地驾驶培训费", "科目二实际操作培训费", "科目二实操培训费"]],
      ["科目二实操费", ["第二部分基础和场地驾驶培训费", "科目二实际操作培训费", "科目二实操培训费"]],
      ["科目三实操培训费", ["第三部分道路驾驶培训费", "科目三实际操作培训费", "科目三实操培训费"]],
      ["科目三学时单价", ["第三部分道路驾驶培训费", "科目三实际操作培训费", "科目三实操培训费"]],
      ["科目三实操费", ["第三部分道路驾驶培训费", "科目三实际操作培训费", "科目三实操培训费"]],
      ["科目二补训", ["科目二补训费", "科目二补训"]],
      ["科目三补训", ["科目三补训费", "科目三补训"]],
      ["平台备案", ["补训", "接送服务"]],
      ["接送", ["接送服务费用", "接送服务费", "接送"]],
    ];
    // 条款全文（标题+正文），去空白后比对，规避 PDF 提取时插入的空格
    function clauseFullText(c) {
      return String((c.title || "") + "\n" + (c.body || "")).replace(/\s+/g, "");
    }
    function clauseNosForLabel(label) {
      const clauses = (cmpData.value && cmpData.value.clauses) || [];
      if (!clauses.length) return [];
      const text = String(label || "");
      // ① 优先按标签对应的合同原文短语在条款正文中直接检索（最可靠）。
      // 短语按优先级排序，首个命中任何条款的短语生效（避免低优短语把退费条款等误带进来）
      for (const [k, phrases] of LABEL_TEXT_PHRASES) {
        if (!text.includes(k)) continue;
        for (const p of phrases) {
          const np = p.replace(/\s+/g, "");
          const nos = clauses
            .filter(c => clauseFullText(c).includes(np))
            .map(c => c.no)
            .filter(Boolean);
          if (nos.length) return nos;
        }
        break; // 该类标签所有短语均未命中原文时，再走条款标题关键词兜底
      }
      // ② 兜底：按条款标题/正文关键词（如"第二部分""基础和场地驾驶培训"）定位
      const kws = [];
      for (const [k, ws] of FEE_CLAUSE_KEYWORDS) if (text.includes(k)) kws.push(...ws);
      if (!kws.length) return [];
      return clauses
        .filter(c => kws.some(w => (c.title || "").includes(w) || (c.body || "").includes(w)))
        .map(c => c.no)
        .filter(Boolean);
    }
    const GENERAL_CLAUSE_KEYWORDS = ["付款方式", "付款", "接送", "适应性训练", "补充培训", "违约", "退学", "退费", "争议"];
    function reflowBody(body) {
      const MARK = /^\s*(?:[（(][一二三四五六七八九十百零\d]{1,4}[）)]|[一二三四五六七八九十]{1,3}[、..．]|[0-9]{1,3}[、..．]|第[一二三四五六七八九十百零\d]+条|第[一二三四五]部分|附[则件表]|甲方[:：]|乙方[:：])/;
      const lines = [];
      for (const raw of String(body || "").split("\n")) {
        const t = raw.trim();
        if (!t) continue;
        if (!lines.length || MARK.test(t)) lines.push(t);
        else lines[lines.length - 1] += t;
      }
      return lines.join("\n");
    }
    const relevantClauses = Vue.computed(() => {
      const data = cmpData.value;
      const clauses = (data && data.clauses) || [];
      if (!clauses.length) return [];
      const tagMap = new Map();
      const addTag = (no, label) => {
        if (!no || !label) return;
        if (!tagMap.has(no)) tagMap.set(no, []);
        if (!tagMap.get(no).includes(label)) tagMap.get(no).push(label);
      };
      const link = (label, reason) => {
        const ms = [...String(reason || "").matchAll(/第([一二三四五六七八九十百零\d]+)条/g)];
        if (ms.length) { ms.forEach(m => addTag(m[1], label)); return; }
        const fromReason = clauseNosForLabel(reason);
        if (fromReason.length) { fromReason.forEach(no => addTag(no, label)); return; }
        clauseNosForLabel(label).forEach(no => addTag(no, label));
      };
      (data.deductions || []).forEach(d => link(d.item, d.reason));
      (data.items || []).forEach(it => link(it.label, ""));
      (data.platform_extra || []).forEach(pe => link(pe.label, ""));
      const generalNos = new Set(
        clauses
          .filter(c => c.no && GENERAL_CLAUSE_KEYWORDS.some(k => (c.title || "").includes(k) || (c.body || "").includes(k)))
          .map(c => c.no)
      );
      const out = [];
      clauses.forEach(c => {
        if (!c.no) return;
        const labels = tagMap.get(c.no);
        if (labels && labels.length) { out.push({ ...c, body: reflowBody(c.body), labels }); return; }
        if (generalNos.has(c.no)) out.push({ ...c, body: reflowBody(c.body), labels: [] });
      });
      if (out.length) return out;
      return clauses.filter(c => c.no).map(c => ({ ...c, body: reflowBody(c.body), labels: [] }));
    });
    const cmpActive = ref({ nos: [], kws: [], locked: false });
    function feeKeywordsOf(label) {
      const t = String(label || "");
      // 优先用合同原文短语做高亮（如"科目二实际操作培训费…学时单价…"整句命中；
      // 电子合同的费用定义句是"第二部分基础和场地驾驶培训费…学时单价…"，两套命名并存覆盖）
      if (t.includes("科目二实操培训费") || t.includes("科目二学时单价")) return ["第二部分基础和场地驾驶培训费", "科目二实际操作培训费", "科目二实操培训费", "科目二"];
      if (t.includes("科目二实操费")) return ["第二部分基础和场地驾驶培训费", "科目二实际操作培训费", "科目二实操培训费", "科目二实操费"];
      if (t.includes("科目三实操培训费") || t.includes("科目三学时单价")) return ["第三部分道路驾驶培训费", "科目三实际操作培训费", "科目三实操培训费", "科目三"];
      if (t.includes("科目三实操费")) return ["第三部分道路驾驶培训费", "科目三实际操作培训费", "科目三实操培训费", "科目三实操费"];
      if (t.includes("科目二补训")) return ["科目二补训"];
      if (t.includes("科目三补训")) return ["科目三补训"];
      if (t.includes("平台备案")) return ["补训", "接送"];
      if (t.includes("接送")) return ["接送"];
      if (t.includes("科目二")) return ["第二部分", "科目二"];
      if (t.includes("科目三")) return ["第三部分", "科目三"];
      if (t.includes("综合服务费")) return ["综合服务费"];
      if (t.includes("理论")) return ["理论培训费", "理论费"];
      if (t.includes("违约金")) return ["违约金为"];
      if (t.includes("合同总额")) return ["培训费用合计", "培训服务费合计", "培训服务费总额", "总金额"];
      return [];
    }
    function clauseNosForItem(label, reason) {
      let nos = [...String(reason || "").matchAll(/第([一二三四五六七八九十百零\d]+)条/g)].map(m => m[1]);
      if (!nos.length) nos = clauseNosForLabel(reason);
      if (!nos.length) nos = clauseNosForLabel(label);
      return nos;
    }
    function escHtml(s) {
      return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }
    function clauseHtml(cl) {
      const body = String(cl.body || "");
      const kws = cmpActive.value.nos.includes(cl.no) ? cmpActive.value.kws : [];
      if (!kws.length) return escHtml(body);
      // 按完整句（。；）切分后逐句比对（比对时去空白，PDF 跨行句可整句命中）；
      // 不按 \n 切——PDF 换行会把"…折算⏎学时单价150.00元/学时"拦腰截断导致半句高亮
      const parts = body.split(/(。|；)/);
      const norm = s => String(s).replace(/\s+/g, "");
      let html = "";
      for (const p of parts) {
        html += (p.trim() && kws.some(kw => norm(p).includes(norm(kw))))
          ? `<mark class="clause-mark">${escHtml(p)}</mark>`
          : escHtml(p);
      }
      return html;
    }
    function scrollToClause(no) {
      if (!no) return;
      nextTick(() => {
        const el = document.querySelector(`.cmp-left .clause-block[data-clause="${no}"]`);
        if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
      });
    }
    function hoverClause(label, reason) {
      const nos = clauseNosForItem(label, reason);
      cmpActive.value = { nos, kws: feeKeywordsOf(label), locked: false };
      scrollToClause(nos[0]);
    }
    function leaveClause() {
      if (!cmpActive.value.locked) cmpActive.value = { nos: [], kws: [], locked: false };
    }
    function clickClause(label, reason) {
      const nos = clauseNosForItem(label, reason);
      cmpActive.value = { nos, kws: feeKeywordsOf(label), locked: true };
      scrollToClause(nos[0]);
    }
    function openContract() {
      if (!selectedTicketId.value) { toast("请先打开工单", "", "warning"); return; }
      if (cPath.value) {
        contractPreviewUrl.value = "/api/contract/preview?path=" + encodeURIComponent(cPath.value);
        contractIsImage.value = /\.(png|jpe?g|gif|bmp|webp)$/i.test(cPath.value || "");
      } else {
        contractPreviewUrl.value = "";
        contractIsImage.value = false;
      }
      contractModalOpen.value = true;
      loadComparison();
    }
    function closeContract() { contractModalOpen.value = false; contractPreviewUrl.value = ""; cmpData.value = null; }

    // ── 撤案弹窗 ──
    const withdrawModalOpen = Vue.ref(false);
    const withdrawReason = Vue.ref("");
    function askWithdraw() { withdrawReason.value = ""; withdrawModalOpen.value = true; }
    async function confirmWithdraw() {
      await doWithdraw(withdrawReason.value);
      withdrawModalOpen.value = false;
    }
    // 已撤诉案件的取消撤诉：确认后恢复在途统计口径
    async function askCancelWithdraw() {
      const t = selectedTicket.value || {};
      if (!confirm(`确认取消「${t.student_name || qr.value.name || '该学员'}」的撤诉标记？案件将重新计入有效投诉。`)) return;
      await doCancelWithdraw();
    }

    // 切换视图时若进入工作台且已有选中，刷新图表无关
    function goView(v) {
      view.value = v;
      if (v === "settings") loadCfg();
    }
    // 归档成功 → 跳转投诉列表并聚焦该笔归档记录
    async function doArchiveAndGoList() {
      const id = selectedTicketId.value;
      const ok = await doArchive();
      if (!ok || !id) return;
      view.value = "list";
      await nextTick();
      focusArchivedTicket(id);
    }

    // 确认生成登记表：带页面当前编辑（与预览同一数据源，所见即所得），成功后回写路径并关预览
    async function confirmGenerateForm() {
      const id = selectedTicketId.value;
      if (!id) { toast("请先打开工单", "", "warning"); return; }
      await genRegistrationForm(id, {
        handling_notes: handlingNotes.value,
        student_name: studentNameEdit.value.trim(),
        overrides: formOverrides.value,     // 纸面上改过的内容，生成即所见
      });
      if (formResult.value && formResult.value.filepath) {
        if (selectedTicket.value) selectedTicket.value.registration_form_path = formResult.value.filepath;
        previewFormOpen.value = false;
      }
    }

    // ── 登记表预览：A4 纸版式辅助（与 services/visit_service 的 docx 版式一一对应） ──
    // 信息区六元组行 / 分区行分别渲染
    const regInfoRows = Vue.computed(() => (previewFormData.value?.fields || []).filter(r => r.length === 6));
    const regSectionRows = Vue.computed(() => (previewFormData.value?.fields || []).filter(r => r.length !== 6));
    // 分区值按 \n 拆行：首行与加粗小标题同段，其余各自成段
    function regSecLines(v) { return String(v ?? "").split("\n"); }
    // 分区行高 = docx SECTION_HEIGHTS_CM（services/visit_service.py），cm → px，96dpi：1cm = 37.795px
    // ⚠️ 改 docx 侧定高必须同步改这里，否则预览与实际生成的 Word 版式不一致
    const REG_SEC_H_CM = { "投诉内容": 6.2, "投诉诉求": 2.2, "费用核算": 1.4, "投诉处理": 7.2, "回访记录": 2.4 };
    function regSecStyle(label) {
      const cm = REG_SEC_H_CM[label];
      return cm ? { height: Math.round(cm * 37.795) + "px" } : {};
    }
    // 纸面编辑收集：键为 标签 / 标签:行号 / __title__ / __no_line__
    function onFormValEdit(key, e) {
      // nbsp → 普通空格（contenteditable 常插入 \u00a0），再收尾空白
      const v = String(e.target.innerText || "").replace(/\u00a0/g, " ").trim();
      formOverrides.value = Object.assign({}, formOverrides.value, { [key]: v });
    }
    // 纸面整体等比缩放，保证弹窗内始终看到完整 A4 版面
    function fitRegPaper() {
      const body = document.getElementById("rfBody");
      const wrap = document.getElementById("rfWrap");
      const paper = document.getElementById("rfPaper");
      if (!wrap || !paper) return;
      const avail = body ? body.clientWidth - 4 : 794;
      const s = Math.min(1, avail / 794);
      paper.style.transform = "scale(" + s + ")";
      wrap.style.height = (paper.offsetHeight * s) + "px";
    }
    watch(previewFormOpen, (v) => { if (v) nextTick(fitRegPaper); });
    window.addEventListener("resize", fitRegPaper);
    // 切换视图即清掉聚焦状态（覆盖 goView 与 intakeComplete 的直接赋值两种路径），并回到页面顶部
    watch(view, () => { focusZone.value = null; flashTarget.value = ""; nextTick(() => window.scrollTo(0, 0)); });

    return {
      toasts, toast, currentDate, view, viewTitle, sidebarCollapsed, goView, doArchiveAndGoList, toggleSidebar, isDark, toggleTheme,
      // 当前账号
      currentUser, isAdmin, isViewer, logout,
      // 处理人字段（assignableUsers / handlerLabel）
      assignableUsers, loadAssignableUsers, handlerLabel,
      // 受理
      form, fileInputRef, isDragOver, intakeLoading, intakeResult, intakeErr, pulseIdCard, pulsePhone,
      intakeText, handleIntakeText, intakeTextareaRef, autoResizeTextarea, onSourceChange, onIntakePaste,
      querying, qErr, qr, queryProgress, sourceStatusText, sourceStatusColor, sourceStatusIcon, sourcePillClass, phaseDetail, noEContractReason, drivingContractTag,
      currentTicketId, triggerFileInput,       onDragOver, onDragEnter, onDragLeave, onDrop, onFileSelected,
      queryAll, queryAllAndGo, resetIntake,
      assignableUsers, loadAssignableUsers,
      focusZone, flashTarget, leftDim, registerDim, filledCount, undoDim, stpDone, stpState, jumpTo,
      // 统一智能查询
      studentName, schoolShort, regStart, regEnd,
      candidates, candWrapRef, searching, candEmpty, searchSource, selectedCand, queryTarget,
      candTotal, candPage, candTotalPages, gotoCandPage, orgFallback, orgOptions,
      phoneMismatch, residencyTip, nameMismatch, phoneCandidates, successBar,
      clearTransient, examStageClass,
      routeMode, queryPrimary, qFieldClass, mainBtnText,
      searchStudents, chooseCandidate, onMainClick, runExactQuery,
      sameDayTicket, chooseMergeExisting, createNewAnyway, dismissSameDayPrompt,
      noMatchInfo, dismissNoMatch, manualOpen, manualCreating, manualErr, manualForm,
      orgUnitsFull, openManualIntake, closeManualIntake, submitManualIntake,
      // 工作流（扣费明细 / 合同 / 归档）
      workflowStep, workflowStatusText, cSrc, cPath, cName, cLoading, uploadedFiles,
      contractFileInput, dlContractAnalyze, ulContractAnalyze,
      aLoading, analysisProgress, analysisElapsed, deductionSum, deductionMismatch, ar, aErr, manualContract,
      canProceedToAnalysis, threeSystemReady, rpLoading, rpResult, rpErr,
      dlContract, ulContract, confirmContract, startManualEdit, doAnalyze, confirmAnalysis,
      recalc, addDeduction, removeDeduction, exportDeductions, updatePenaltyRate, genReply, genRegistrationForm,
      formLoading, formResult, confirmGenerateForm,
      feeConfirmed, feeConfirming, feeConfirmedAt, feePlanVersion,
      handleSaveAndConfirm, startManualFeeEntry, confirmNoFeeBasis, previewVisible, previewUrl, previewFilename, previewIsPdf, openPreview, closePreview,
      fromHistoryLabel, loadCommunications, addCommunication, communications, communicationsLoading, commForm,
      branchCooperationNote, archivedCase, reopenFeePlan,
      // 设置（系统设置页：AI 大模型配置）
      cfg, cfgSaving, cfgMsg, cfgOk, loadCfg, saveCfg,
      LLM_PROVIDERS, providerSel, applyProvider, presetModels, modelSel, modelCustom,
      testing, testResult, testLlm,
      // 账号管理
      users, USER_ROLES, roleLabel,
      // 历史 / 看板
      hList, hTotal, hSearch, hLimit, hPage, hTotalPages, hLoading,
      statusFilter, schoolOptions, stats, chartDateStart, chartDateEnd, setChartPeriod, periodStat,
      durationStats, loadDurationStats, groupedHistory, toggleGroup,
      chartPeriod, masked, maskId, repeatOnly, rankMode, compare, topChannels, hPageButtons,
      debSearch, loadHist, loadStats, loadFromHist: loadFromHistAndGo, exportTickets, updateTicketStatus,
      askDeleteHist, confirmDeleteHist, hDeleteModal, hDeleting,
      loadSchoolCodes, statScope, setStatScope, drill, drillCode, onDrillSelect, onDateChange, boardComplaintRate,
      drillInto, closeDrill, vehicleItems, vehicleKeyword, vehicleActiveItems, vehicleInactiveItems, vehicleLoading, vehicleSaving, vehicleModalOpen,
      loadVehicleCounts, saveVehicleCounts, addVehicleRow, removeVehicleRow,
      detailTicket, detailDeductions, detailCommunicationRecords, detailDocuments, detailLoading, showTicketDetail,
      trendChartRef, schoolChartRef, typeChartRef, statusChartRef, byTypeChartRef, updateCharts,
      // 工作台
      allTickets, ticketsOpen, ticketsArchived, ticketsWithdrawn, ticketsLoading,
      selectedTicketId, selectedTicket, selectedLoading, openCase, aiOptimizeNotes, notesPolishing,
      aiOptimizeComplaint, complaintPolishing,
      handlingNotes, branchCooperation, coopOptions, feeUnlocked, studentNameEdit,
      requeryIdCard, requerying, requerySkipped, requerySources, requeryMsg, requeryWithIdCard,
      extractInput, extractContent, extractDemands, extracting, extractDirty, aiExtractComplaint,
      previewFormOpen, previewFormData, previewFormLoading, previewRegistrationForm, formOverrides,
      regInfoRows, regSectionRows, regSecLines, regSecStyle, onFormValEdit, fitRegPaper,
      listFilter, railOpen, profileOpen, timelineOpen, contractModalOpen, toggleRailPanel, loadTickets, openTicket, saveProgress,
      unlockFee, doWithdraw, doArchive, archiveGates, gateErrors, canArchive, archiveRoot,
      folderModalOpen, fbPath, fbParent, fbDirs, fbLoading, fbErr, openFolderPicker, fbLoad, fbEnter, fbUp, fbConfirm,
      // 投诉列表页重设计
      OVERDUE_DAYS, TYPE_LABELS, FEE_LABELS,
      clKw, clType, clChannel, clSchool, clHandler, clFee, clDays, clDateFrom, clDateTo,
      clOnlyOverdue, clOnlyManual, clGroup, clSort, clCollapsed, clSelectedIds,
      handlerOptions, channelOptions, clSchoolOptions, listGroups, clResultCount, clOverdueTotal, clSerialMap,
      clPageSize, pagedGroups, clSetPage, batchBarVisible,
      daysOpen, isOverdue, feeState, maskPhone, actionAt,
      clToggleRow, clToggleGroupSelect, openArchive, openArchiveSelected, clClearSelection, clSetSort, clClearFilters,
      kjHelperModalOpen, kjUncPath, kjServerDir, copyKjUnc,
      kjLastTicketId, kjLastName, kjFilesOpen, kjFilesLoading, kjFiles, kjFilesDir,
      openKjFiles, kjFileUrl, fmtKjSize,
      batchExportSelected,
      clExpandedIds, clToggleExpand, copyPhone,
      transferModalOpen, transferTarget, transferSaving, askBatchTransfer, askTransferRow, confirmBatchTransfer,
      deleteModalOpen, deleteSaving, askDeleteSelected, askDeleteRow, confirmDeleteSelected,
      // 回复函
      replyPolished, replyEdited, replySaved, replyHtml, replyCollapsed, replyPolishing, fmt, buildLetter, syncLetter, aiPolishReply, generateReply,
      // 合同预览（双栏对照）
      contractPreviewUrl, contractIsImage, cmpData, cmpLoading, cmpRefreshing,
      openContract, closeContract, refreshPlatform, relevantClauses,
      cmpActive, clauseHtml, hoverClause, leaveClause, clickClause,
      // 合同三栏预览（上传合同分析：原文 / 原件 / 扣费明细）
      cp, contractText: cp.contractText,
      // 撤案
      withdrawModalOpen, withdrawReason, askWithdraw, confirmWithdraw, askCancelWithdraw,
      // 标签
      tab, tabs,
      // 工具
      stBadge, getTrainingTime, getEventType, getDrivingFeeBreakdown, extractFormula, money, esc,
    };
  },
});
  __app.config.compilerOptions.delimiters = ["[[", "]]"];
  __app.mount("#app");

  // 启动跨标签页登录态同步 + 30s 心跳兜底
  startAuthWatch();
} catch (e) {
  console.error("Vue mount failed:", e);
  const appEl = document.getElementById("app");
  appEl.removeAttribute("v-cloak");
  appEl.innerHTML = '<div style="padding:20px;color:red;font-family:monospace;white-space:pre-wrap">系统加载失败: ' + (e.stack || e.message) + "</div>";
}
