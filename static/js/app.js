// 驾校投诉处理系统 - Vue 3 App
import { useToast } from "useToast";
import { useComplaint } from "useComplaint";
import { useWorkflow } from "useWorkflow";
import { useSettings } from "useSettings";
import { useHistory } from "useHistory";
import { stBadge, getTrainingTime, getEventType } from "helpers";

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

    // ── 页面导航 ──
    const page = ref("main");
    function goPage(p) {
      page.value = p;
      if (p === "settings" && !cfg.value) loadCfg();
      if (p === "history") {
        loadHist();
        loadStats();
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
      currentTicketId,
      triggerFileInput,
      onDragOver,
      onDragEnter,
      onDragLeave,
      onDrop,
      onFileSelected,
      queryAll,
      reset: resetComplaint,
    } = useComplaint();

    // ── 工作流程 ──
    const {
      workflowStep,
      workflowStatusText,
      cSrc,
      cPath,
      cName,
      cLoading,
      aLoading,
      analysisProgress,
      analysisElapsed,
      deductionSum,
      deductionMismatch,
      ar,
      aErr,
      manualContract,
      canProceedToAnalysis,
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
      visitStatus, visitRemark, visitLoading,
      formLoading, formResult,
      submitVisit, genRegistrationForm,
      reset: resetWorkflow,
    } = useWorkflow(toast, () => qr.value);

    // 触发文件上传对话框
    // ── 设置 ──
    const { cfg, cfgSaving, cfgMsg, cfgOk, loadCfg, saveCfg } = useSettings(toast);

    // ── 历史 ──
    const {
      hList, hTotal, hSearch, hLimit, hPage, hTotalPages, hLoading,
      statusFilter, schoolFilter, schoolCodes, dateStart, dateEnd,
      stats, chartDateStart, chartDateEnd, setChartPeriod, periodStat,
      groupedHistory, toggleGroup,
      debSearch, loadHist, loadStats, loadFromHist, exportTickets, updateTicketStatus,
      loadSchoolCodes,
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
    function loadFromHistAndGo(item) {
      loadFromHist(item, form);
      goPage("main");
      toast("已加载", item.student_name + " (" + item.id_card + ")", "info");
    }

    // ── 从历史一键直达飞书 ──
    function goFeishu(item) {
      loadFromHist(item, form);
      // 触发查询填充学员信息
      queryAll();
      goPage("main");
      toast("已加载", item.student_name + "，可提交飞书", "info");
    }

    // ── 当查询结果返回时 ──
    Vue.watch(qr, (val) => {
      if (val && val.contract_available) {
        cSrc.value = "download";
      } else if (val) {
        cSrc.value = "upload";
      }
      
      // 每次查询新学员，重置工作流状态（清空旧的合同路径、分析结果等）
      if (val && val.name) {
        resetWorkflow();
      }
    });

    // ── 进入AI分析步骤自动触发 ──
    Vue.watch(workflowStep, (val) => {
      if (val === 2 && qr.value && qr.value.name) {
        setTimeout(() => {
          doAnalyze(qr.value.id_card, qr.value.exam_stage, qr.value.training_hours, currentTicketId.value);
          // 自动→处理中
          if (currentTicketId.value) {
            updateTicketStatus(currentTicketId.value, "处理中");
          }
        }, 200);
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
      page, goPage,
      toasts, toast,
      // 受理
      form, fileInputRef, isDragOver,
      intakeLoading, intakeResult, intakeErr,
      intakeText, handleIntakeText,
      intakeTextareaRef, autoResizeTextarea, onSourceChange, onIntakePaste,
      querying, qErr, qr, queryProgress, currentTicketId,
      triggerFileInput,
      onDragOver, onDragEnter, onDragLeave, onDrop, onFileSelected,
      queryAll,
      // 工作流
      workflowStep, workflowStatusText,
      cSrc, cPath, cName, cLoading,
      aLoading, analysisProgress, analysisElapsed, deductionSum, deductionMismatch, ar, aErr, manualContract, canProceedToAnalysis,
      rpLoading, rpResult, rpErr,
      fsLoading, fsResult,
      dlContract, ulContract,
      confirmContract, startManualEdit, doAnalyze, confirmAnalysis,
      recalc, addDeduction, removeDeduction, updatePenaltyRate,
      genReply, submitFeishu, goFeishu,
      // 回访+登记表
      visitStatus, visitRemark, visitLoading,
      formLoading, formResult,
      submitVisit, genRegistrationForm,
      // 设置
      cfg, cfgSaving, cfgMsg, cfgOk, loadCfg, saveCfg,
      // 历史
      hList, hTotal, hSearch, hLimit, hPage, hTotalPages, hLoading,
      statusFilter, schoolFilter, schoolCodes, dateStart, dateEnd,
      stats, chartDateStart, chartDateEnd, setChartPeriod, periodStat,
      groupedHistory, toggleGroup,
      debSearch, loadHist, loadStats, loadFromHist: loadFromHistAndGo, exportTickets, updateTicketStatus,
      loadSchoolCodes,
      // 图表
      trendChartRef, schoolChartRef, typeChartRef, statusChartRef,
      // 标签
      tab, tabs,
      // 工具
      stBadge, getTrainingTime, getEventType, extractFormula,
    };
  },
}).mount("#app");
} catch(e) {
  console.error("Vue mount failed:", e);
  document.getElementById('app').innerHTML = '<div style="padding:20px;color:red">系统加载失败: ' + e.message + '</div>';
}

