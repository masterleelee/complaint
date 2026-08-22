// 驾校投诉处理系统 - Vue 3 App
import { useToast } from "useToast";
import { useComplaint } from "useComplaint";
import { useWorkflow } from "useWorkflow";
import { useSettings } from "useSettings";
import { useHistory } from "useHistory";
import { stBadge, getTrainingTime, getEventType, getDrivingFeeBreakdown } from "helpers";
import { getJ } from "api";

// 从 reason 中提取违约金计算公式（如 "3180*20%=636"）
function extractFormula(reason) {
  if (!reason) return "";
  // 匹配类似 "3180×20%=636" 或 "3180*20%=636" 的公式
  const match = reason.match(/(\d+(?:\.\d+)?)\s*[×*]\s*(\d+(?:\.\d+)?)%\s*=\s*(\d+(?:\.\d+)?)/);
  if (match) {
    return `${match[1]}×${match[2]}%=${match[3]}`;
  }
  return "";
}

const { createApp, ref, computed, watch, onMounted } = Vue;

try { createApp({
  delimiters: ["[[", "]]"],
  setup() {
    // ── Toast ──
    const { toasts, toast } = useToast();

    // ── 侧边栏 ──
    const sidebarCollapsed = ref(false);

    // ── 当前日期 ──
    const currentDate = Vue.computed(() => {
      const d = new Date();
      return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    });

    // ── 页面标题 ──
    // P1 重构：拆分"工单受理"为"工单查询 / 工单处理"
    const pageTitle = Vue.computed(() => {
      const titles = {
        query: "工单查询",
        process: "工单处理",
        history: "处理历史",
        settings: "系统设置",
      };
      return titles[page.value] || "";
    });

    // ── 页面导航 ──
    // P1 重构：默认进入查询页（page==='query'）
    const page = ref("query");

    // P1 新增：面包屑摘要（处理页顶部「来自查询：身份证 xxx」）
    const lastQuerySummary = ref(null);

    function goPage(p) {
      page.value = p;
      if (p === "settings" && !cfg.value) loadCfg();
      if (p === "history") {
        loadHist();
        loadStats();
        loadDurationStats();
      }
    }

    // P1 新增：跳到处理页（查完即跳 / 历史恢复入口）
    // summary: { id_card, name, source_channel } 用于面包屑
    function goProcess(summary) {
      if (summary && typeof summary === "object") {
        lastQuerySummary.value = summary;
      }
      page.value = "process";
    }

    // P1 新增：返回查询页（保留 qr + workflow，仅清查询条件）
    // P2：处理页顶部"返回查询"按钮调用；只清查询表单元数据，不动 qr/workflow
    function goQuery() {
      form.id_card = "";
      form.phone = "";
      form.source_channel = "交通部门";
      form.other_channel = "";
      form.complaint_date = currentDate.value;
      form.complaint_type = "A";
      form.handler_name = "";
      form.complaint_summary = "";
      intakeResult.value = null;
      intakeErr.value = "";
      intakeText.value = "";
      page.value = "query";
    }

    // P2 新增：查询卡"查询三系统"按钮调用
    // 先跑查询，查到学员即跳处理页；失败/未查到留查询页修条件
    async function queryAllAndGo() {
      await queryAll();
      goProcessIfFound();
    }

    // 查到学员即跳处理页（查询卡按钮 / 受理自动查询共用）
    function goProcessIfFound() {
      if (qr.value && qr.value.name && !qErr.value) {
        goProcess({
          id_card: form.id_card || qr.value.id_card,
          name: qr.value.name,
          source_channel: form.source_channel,
        });
      }
    }

    // ── 投诉受理 + 查询 ──
    const {
      form,
      fileInputRef,
      isDragOver,
      intakeLoading,
      intakeResult,
      intakeErr,
      intakeText,
      handleIntakeText,
      intakeTextareaRef,
      autoResizeTextarea,
      onSourceChange,
      onIntakePaste,
      querying,
      qErr,
      qr,
      queryProgress,
      sourceStatusText,
      sourceStatusColor,
      sourceStatusIcon,
      phaseDetail,
      drivingContractTag,
      currentTicketId,
      triggerFileInput,
      onDragOver,
      onDragEnter,
      onDragLeave,
      onDrop,
      onFileSelected,
      queryAll,
      restore: restoreComplaint,
      reset: resetComplaint,
    } = useComplaint(goProcessIfFound);

    // ── 工作流程 ──
    const {
      workflowStep,
      workflowStatusText,
      cSrc,
      cPath,
      cName,
      cLoading,
      uploadedFiles,
      aLoading,
      analysisProgress,
      analysisElapsed,
      deductionSum,
      deductionMismatch,
      ar,
      aErr,
      manualContract,
      canProceedToAnalysis, threeSystemReady,
      rpLoading,
      rpResult,
      rpErr,
      fsLoading,
      fsResult,
      dlContract,
      ulContract,
      confirmContract,
      startManualEdit,
      doAnalyze,
      confirmAnalysis,
      recalc,
      addDeduction,
      removeDeduction,
      updatePenaltyRate,
      genReply,
	      submitFeishu,
	      OUTCOME_OPTIONS, COOPERATION_OPTIONS, NEGOTIATION_OPTIONS,
	      feeConfirmed, feeConfirming, feeConfirmedAt, feePlanVersion,
	      communications, communicationsLoading, commForm,
	      finalOutcome, negotiationOutcome, withdrawStatus, withdrawUpdatedAt,
	      syncOutcomeFromNegotiation, updateWithdrawStatus,
	      otherOutcome, selectOutcomeCard, onOtherOutcomeChange,
	      remarkOpen, commFormOpen,
	      branchCooperation, branchCooperationNote, archiveSaving,
	      archivedCase, feeReopenReason, feeReopening, reopenFeePlan,
	      formLoading, formResult,
	      genRegistrationForm,
      reset: resetWorkflow,
      restore: restoreWorkflow,
      // 新增：合同预览
      previewVisible, previewUrl, previewFilename, previewIsPdf,
      openPreview, closePreview,
      // 新增：保存+确认
      handleSaveAndConfirm,
      // 新增：沟通记录
	      fromHistoryLabel,
	      loadSavedAnalysis, saveAnalysis,
	      loadCommunications, addCommunication, saveCaseOutcome,
    } = useWorkflow(toast, () => qr.value, () => currentTicketId.value);

    // 触发文件上传对话框
    // ── 设置 ──
    const { cfg, cfgSaving, cfgMsg, cfgOk, loadCfg, saveCfg } = useSettings(toast);

    // ── 历史 ──
    const {
      hList, hTotal, hSearch, hLimit, hPage, hTotalPages, hLoading,
      statusFilter, schoolOptions,
      stats, chartDateStart, chartDateEnd, setChartPeriod, periodStat,
      durationStats, loadDurationStats,
      groupedHistory, toggleGroup,
      debSearch, loadHist, loadStats, loadFromHist, exportTickets, updateTicketStatus,
      loadSchoolCodes,
      statScope, setStatScope,
      drill, drillCode, onDrillSelect, onDateChange, boardComplaintRate,
      drillInto, closeDrill,
      vehicleItems, vehicleLoading, vehicleSaving,
      vehicleModalOpen,
      loadVehicleCounts, saveVehicleCounts,
      addVehicleRow, removeVehicleRow,
	      detailModal, detailTicket, detailDeductions, detailVisitRecords, detailCommunicationRecords, detailDocuments, detailLoading,
      showTicketDetail,
      initCharts, updateCharts, disposeCharts,
    } = useHistory();

    // ── 学员信息标签页 ──
    const tab = ref("basic");
    const tabs = {
      basic: "基本信息",
      exam: "考试情况",
      training: "培训时长",
      fees: "收费记录",
      timeline: "时间轴",
    };

    // ── 从历史加载 ──
    async function loadFromHistAndGo(item) {
      resetComplaint();
      resetWorkflow();
      try {
        const d = await getJ(`/api/tickets/${item.id}/detail`);
        if (!d.success) throw new Error(d.error || "工单详情加载失败");
        const detail = d.data;
        restoreComplaint(detail.ticket);
        await Vue.nextTick();
        restoreWorkflow(detail);
        // P1：历史→处理，跳处理页（不经过查询页）
        goProcess({
          id_card: detail.ticket.id_card,
          name: detail.ticket.student_name,
          source_channel: detail.ticket.source_channel,
          from: "history",
        });
        toast("已恢复案件", detail.ticket.student_name || detail.ticket.id_card, "info");
      } catch (e) {
        toast("恢复失败", e.message, "danger");
      }
    }

    // ── 从历史一键直达飞书 ──
    function goFeishu(item) {
      loadFromHist(item, form);
      // 触发查询填充学员信息
      queryAll();
      // P1：直达飞书 = 进入处理页
      goProcess({
        id_card: item.id_card,
        name: item.student_name,
        source_channel: item.source_channel,
        from: "history",
      });
      toast("已加载", item.student_name + "，可提交飞书", "info");
    }

    // ── 当查询结果返回时 ──
    Vue.watch(qr, (val) => {
      if (val && (val.contract_available || val.contract_check_deferred)) {
        cSrc.value = "download";
      } else if (val) {
        cSrc.value = "upload";
      }
      
      // 每次查询新学员，重置工作流状态（清空旧的合同路径、分析结果等）
      if (val && val.name) {
        resetWorkflow();
      }
    });

    // ── 初始化加载代号列表 ──
    loadSchoolCodes();

    // ── 图表初始化和更新 ──
    const trendChartRef = Vue.ref(null);
    const schoolChartRef = Vue.ref(null);
    const typeChartRef = Vue.ref(null);
    const statusChartRef = Vue.ref(null);

    Vue.watch(() => page.value === 'history', (isHistory) => {
      if (isHistory) {
        // 进入历史页面时初始化图表
        setTimeout(() => {
          initCharts({
            trendChartRef: trendChartRef.value,
            schoolChartRef: schoolChartRef.value,
            typeChartRef: typeChartRef.value,
            statusChartRef: statusChartRef.value
          });
          updateCharts();
        }, 100);
      }
    });

    // 统计数据变化时更新图表
    Vue.watch(stats, () => {
      if (page.value === 'history') {
        updateCharts();
      }
    }, { deep: true });

    return {
      // P1：页面路由（query / process / history / settings）
      page, goPage, goProcess, goQuery, lastQuerySummary, pageTitle, currentDate, sidebarCollapsed,
      // P2：查询卡行为钩子
      queryAllAndGo,
      toasts, toast,
      // 受理
      form, fileInputRef, isDragOver,
      intakeLoading, intakeResult, intakeErr,
      intakeText, handleIntakeText,
      intakeTextareaRef, autoResizeTextarea, onSourceChange, onIntakePaste,
      querying, qErr, qr, queryProgress,
      sourceStatusText, sourceStatusColor, sourceStatusIcon, phaseDetail, drivingContractTag,
      currentTicketId,
      triggerFileInput,
      onDragOver, onDragEnter, onDragLeave, onDrop, onFileSelected,
      queryAll,
      // 工作流
      workflowStep, workflowStatusText,
      cSrc, cPath, cName, cLoading, uploadedFiles,
      aLoading, analysisProgress, analysisElapsed, deductionSum, deductionMismatch, ar, aErr, manualContract, canProceedToAnalysis, threeSystemReady,
      rpLoading, rpResult, rpErr,
      fsLoading, fsResult,
      dlContract, ulContract,
      confirmContract, startManualEdit, doAnalyze, confirmAnalysis,
      recalc, addDeduction, removeDeduction, updatePenaltyRate,
      genReply, submitFeishu, goFeishu,
      // 合同预览
      previewVisible, previewUrl, previewFilename, previewIsPdf,
      openPreview, closePreview,
      // 保存+确认
      handleSaveAndConfirm,
      // 沟通记录+登记表
	      fromHistoryLabel,
	      OUTCOME_OPTIONS, COOPERATION_OPTIONS, NEGOTIATION_OPTIONS,
	      feeConfirmed, feeConfirming, feeConfirmedAt, feePlanVersion,
	      communications, communicationsLoading, commForm,
	      finalOutcome, negotiationOutcome, withdrawStatus, withdrawUpdatedAt,
	      syncOutcomeFromNegotiation, updateWithdrawStatus,
	      otherOutcome, selectOutcomeCard, onOtherOutcomeChange,
	      remarkOpen, commFormOpen,
	      branchCooperation, branchCooperationNote, archiveSaving,
	      archivedCase, feeReopenReason, feeReopening, reopenFeePlan,
	      formLoading, formResult,
	      genRegistrationForm,
	      loadCommunications, addCommunication, saveCaseOutcome,
      // 设置
      cfg, cfgSaving, cfgMsg, cfgOk, loadCfg, saveCfg,
      // 历史
      hList, hTotal, hSearch, hLimit, hPage, hTotalPages, hLoading,
      statusFilter, schoolOptions,
      stats, chartDateStart, chartDateEnd, setChartPeriod, periodStat,
      groupedHistory, toggleGroup,
      debSearch, loadHist, loadStats, loadFromHist: loadFromHistAndGo, exportTickets, updateTicketStatus,
      loadSchoolCodes,
      statScope, setStatScope,
      drill, drillCode, onDrillSelect, onDateChange, boardComplaintRate,
      drillInto, closeDrill,
      vehicleItems, vehicleLoading, vehicleSaving,
      vehicleModalOpen,
      loadVehicleCounts, saveVehicleCounts,
      addVehicleRow, removeVehicleRow,
	      detailTicket, detailDeductions, detailCommunicationRecords, detailDocuments, detailLoading,
      showTicketDetail,
      // 图表
      trendChartRef, schoolChartRef, typeChartRef, statusChartRef,
      // 标签
      tab, tabs,
      // 工具
      stBadge, getTrainingTime, getEventType, extractFormula, getDrivingFeeBreakdown,
    };
  },
}).mount("#app");
} catch(e) {
  console.error("Vue mount failed:", e);
  document.getElementById('app').innerHTML = '<div style="padding:20px;color:red">系统加载失败: ' + e.message + '</div>';
}
