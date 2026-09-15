// 工作流程组合式函数：合同 → 退费分析 → 沟通记录 → 结果归档/文档
import { getJ, postJ } from "api";

/** 计算考试费
 * examCounts: { subject1: 2, subject2: 1, subject3: 0 }
 * includesExam: 合同是否包含考试费
 * 
 * 费用标准：
 * - 科目一：70元/次，补考35元
 * - 科目二：130元/次，补考65元
 * - 科目三：280元/次，补考140元
 * - 工本费：10元（固定）
 */
function examCount(examCounts, key, label) {
  return Number(examCounts?.[key] ?? examCounts?.[label] ?? 0) || 0;
}

function calcExamFees(examCounts, includesExam, includesMakeup = true) {
  if (!includesExam) {
    return { total: 0, fees: [] };
  }
  
  const fees = {
    subject1: { name: "科目一", normal: 70, retake: 35 },
    subject2: { name: "科目二", normal: 130, retake: 65 },
    subject3: { name: "科目三", normal: 280, retake: 140 },
  };
  
  let total = 0;
  const feeItems = [];

  for (const [subject, fee] of Object.entries(fees)) {
    const count = examCount(examCounts, subject, fee.name);
    if (count <= 0) continue;

    const { name, normal, retake } = fees[subject];
    total += normal;
    feeItems.push({
      item: `${name}考试费`,
      amount: normal,
      reason: `三系统显示${name}考试${count}次，合同考试费${normal}元`,
      generated_progress_fee: true,
    });
    if (includesMakeup && count > 1) {
      const makeupTotal = (count - 1) * retake;
      total += makeupTotal;
      feeItems.push({
        item: `${name}补考费`,
        amount: makeupTotal,
        reason: `三系统显示${name}补考${count - 1}次，合同补考费${retake}元/次`,
        generated_progress_fee: true,
      });
    }
  }
  
  return { total, fees: feeItems };
}

export function useWorkflow(toast, getQr, getTicketId, hooks = {}) {
  // hooks.afterFeeConfirm(confirmed, ticketId)：费用确认成功后通知外部同步工作台/列表状态
  // 工作流步骤：1=合同获取，2=退费分析，3=沟通记录，4=结果归档/文档
  const workflowStep = Vue.ref(1);
  const workflowStatusText = Vue.computed(() => {
    const m = { 1: "待合同获取", 2: "待退费分析", 3: "待沟通记录", 4: "待结果归档" };
    return m[workflowStep.value] || "已完成";
  });

  // 合同
  const cSrc = Vue.ref("upload");
  const cPath = Vue.ref("");
  const cName = Vue.ref("");
  const cLoading = Vue.ref(false);
  const cCached = Vue.ref(false);  // 是否使用缓存合同
  const cCachedAt = Vue.ref("");   // 缓存时间
  const contractInput = Vue.ref(null);  // 合同文件上传 input ref
  const uploadedFiles = Vue.ref([]);  // 已上传文件列表
  const contractManifest = Vue.ref({});

  // 回访管理（已弃用：统一为 communications）
  const fromHistoryLabel = Vue.ref("");
  const OUTCOME_OPTIONS = [
    "投诉撤销",
    "同意合同扣费",
    "不同意合同扣费但协商一致",
    "不同意合同扣费且协商失败",
    "无法联系",
    "继续培训/转校",
  ];
  const COOPERATION_OPTIONS = ["配合", "一般", "沟通困难", "不配合"];
  const NEGOTIATION_OPTIONS = ["协商一致", "协商失败", "其他情形"];
  const feeConfirmed = Vue.ref(false);
  const feeConfirming = Vue.ref(false);
  const feeConfirmedAt = Vue.ref("");
  const communications = Vue.ref([]);
  const communicationsLoading = Vue.ref(false);
  const commForm = Vue.reactive({
    contact_time: "",
    contact_method: "电话",
    summary: "",
    student_intention: "继续协商",
    next_follow_up: "",
  });
  const finalOutcome = Vue.ref("");
  const negotiationOutcome = Vue.ref("");      // 协商结果三类
  const withdrawStatus = Vue.ref("未撤诉");     // 撤诉状态，默认未撤诉
  const withdrawUpdatedAt = Vue.ref("");
  const branchCooperation = Vue.ref("");
  const branchCooperationNote = Vue.ref("");
  const archiveSaving = Vue.ref(false);
  const archivedCase = Vue.ref(false);
  const feeReopenReason = Vue.ref("");
  const feeReopening = Vue.ref(false);
  const feePlanVersion = Vue.ref(0);  // 当前工单费用方案版本，用于归档前沟通记录版本校验

  // 投诉登记表
  const formLoading = Vue.ref(false);
  const formResult = Vue.ref(null);

  // 合同预览
  const previewVisible = Vue.ref(false);
  const previewUrl = Vue.ref("");
  const previewFilename = Vue.ref("");
  const previewIsPdf = Vue.computed(() => {
    const name = previewFilename.value || "";
    return name.endsWith(".pdf") || name.endsWith(".PDF");
  });

  // AI分析
  const aLoading = Vue.ref(false);
  const ar = Vue.ref(null);
  const aErr = Vue.ref("");
  const analysisProgress = Vue.ref(0);
  const analysisElapsed = Vue.ref(0);
  let aTimerId = null;

  // 东城自制合同（2019_dongcheng）专属字段：培训方式 + 综合服务费
  // 后端扣费引擎按这两个字段走第六条退费分支（普通培训 / 先培后付）
  const dongchengServiceFee = Vue.ref(0);
  const dongchengTrainingMode = Vue.ref("");
  const dongchengSaving = Vue.ref(false);
  // 是否为东城自制档位（前端据此显示专属输入项）
  const isDongchengTier = Vue.computed(() => {
    const r = ar.value;
    const tid = String(r?.tier_id || r?.tier_result?.tier_id || "");
    return tid === "2019_dongcheng";
  });

  // 扣费合计校验
  const deductionSum = Vue.computed(() => {
    if (!ar.value || !ar.value.deductions) return 0;
    return ar.value.deductions.reduce((s, d) => s + (Number(d.amount) || 0), 0);
  });
  const deductionMismatch = Vue.computed(() => {
    if (!ar.value) return false;
    const td = Number(ar.value.total_deduction) || 0;
    const tf = Number(ar.value.total_fee) || 0;
    const rawSum = deductionSum.value;
    if (rawSum <= tf) return Math.abs(rawSum - td) > 0.01;
    return false; // 超过总费用时，封顶是正常行为
  });
  // 手动填写合同信息
  const manualContract = Vue.reactive({
    total_fee: 0,      // 合同总金额
    paid_amount: 0,    // 实际已缴金额
    contract_code: "", // 合同编号（可选）
    includes_exam: true, // 合同总培训费是否包含考试费
    includes_makeup: true, // 合同总培训费是否包含补考费
  });

  const canProceedToAnalysis = Vue.computed(() => {
    if (cSrc.value === "upload") return cPath.value !== "";
    if (cSrc.value === "download") return cPath.value !== "";
    // 手动填写：需要合同总金额和已缴金额
    return manualContract.total_fee > 0 && manualContract.paid_amount > 0;
  });

  // 三系统数据是否就绪：用于进入退费分析前的阻断，避免陈旧/缺失数据流入金额计算
  const threeSystemReady = Vue.computed(() => {
    const q = getQr ? getQr() : null;
    if (!q || !q.id_card) return false;
    const th = q.training_hours || {};
    const ec = q.exam_counts || {};
    const hasHours = Object.values(th).some(v => v && String(v).replace(/[^\d]/g, "") !== "0");
    const hasExam = Object.values(ec).some(v => Number(v) > 0);
    const hasDriving = Boolean(q.driving_fee && (q.driving_fee.contract_fee || q.driving_fee.fee_plan_status));
    return hasHours || hasExam || hasDriving;
  });

  // 回复函
  const rpLoading = Vue.ref(false);
  const rpResult = Vue.ref(null);
  const rpErr = Vue.ref("");

  // Load saved analysis (check cache + history inheritance)
  async function loadSavedAnalysis(ticketId) {
    try {
      const d = await getJ(`/api/contract/analysis/${ticketId}`);
      if (d.success && d.cached) {
        ar.value = d.data;
        if (d.from_history) {
          fromHistoryLabel.value = `已从历史工单（${d.history_ticket_id}）加载分析结果`;
        }
        return true;
      }
    } catch (e) {
      console.error("loadSavedAnalysis error:", e);
    }
    return false;
  }

  // Save analysis to ticket
  async function saveAnalysis(ticketId) {
    if (!ar.value || !ticketId) return;
    try {
      await postJ("/api/contract/save_analysis", {
        ticket_id: ticketId,
        analysis_data: ar.value,
      });
    } catch (e) {
      console.error("saveAnalysis error:", e);
    }
  }

  // ── 合同操作 ──

  async function dlContract(idCard, name, schoolShort) {
    cLoading.value = true;
    try {
      const currentQr = getQr ? getQr() : null;
      const d = await postJ("/api/contract/download", {
        id_card: idCard,
        name: name,
        school_short: schoolShort || "",
        ticket_id: getTicketId ? getTicketId() : "",
        registration_date: (currentQr && currentQr.registration_date) || "",
      });
      if (d.success) {
        cPath.value = d.data?.filepath || d.filepath;
        cName.value = d.data?.filename || d.filename;
        cCached.value = d.data?.cached || false;
        cCachedAt.value = d.data?.downloaded_at || "";
        const cacheLabel = cCached.value ? `（缓存于 ${cCachedAt.value}）` : "";
        const dlFilepath = d.data?.filepath || d.filepath;
        const dlFilename = d.data?.filename || d.filename;
        // 用下载结果重建最小 manifest，保持与上传路径一致（避免前端状态与后端脱节）
        contractManifest.value = {
          source_files: [{ filepath: dlFilepath, filename: dlFilename, page_index: 0 }],
          merged_pdf_path: dlFilepath,
          analysis_image_paths: [],
          upload_count: 1,
        };
        uploadedFiles.value = [{ filepath: dlFilepath, filename: dlFilename }];
        toast("下载成功", cName.value + cacheLabel, "success");
      } else {
        toast("下载失败", d.error, "danger");
      }
    } catch (e) {
      toast("下载失败", e.message, "danger");
    } finally {
      cLoading.value = false;
    }
  }

  async function ulContract(ev) {
    const files = ev?.target?.files;
    
    if (!files || files.length === 0) {
      return;
    }
    
    cLoading.value = true;
    try {
      const formData = new FormData();
      for (let i = 0; i < files.length; i++) {
        formData.append("file", files[i]);
      }
      
      ev.target.value = '';
      
      const currentQr = getQr ? getQr() : null;
      if (currentQr) {
        formData.append("id_card", currentQr.id_card || "");
        formData.append("name", currentQr.name || "");
        formData.append("school_short", currentQr.school_short || "");
      }
      const ticketId = getTicketId ? getTicketId() : "";
      if (ticketId) formData.append("ticket_id", ticketId);

      const resp = await fetch("/api/contract/upload", {
        method: "POST",
        body: formData,
      });
      
      const text = await resp.text();
      
      let d;
      try {
        d = JSON.parse(text);
      } catch (parseErr) {
        toast("上传失败", "服务器返回格式错误: " + text.substring(0, 100), "danger");
        return;
      }

      if (d.success) {
        cPath.value = d.data?.filepath || d.filepath;
        contractManifest.value = d.data?.manifest || {};
        const count = contractManifest.value.upload_count || d.data?.count || 1;
        cName.value = d.data?.filename || d.filename;
        uploadedFiles.value = contractManifest.value.source_files || d.data?.all_files || [{ filepath: cPath.value, filename: cName.value }];
        
        toast(`上传成功`, count > 1 ? `已保存 ${count} 个文件并生成合并PDF` : cName.value, "success");
      } else {
        toast("上传失败", d.error || "未知错误", "danger");
      }
    } catch (e) {
      toast("上传失败", e.message, "danger");
    } finally {
      cLoading.value = false;
    }
  }

  function openPreview(filepath, filename) {
    previewUrl.value = `/api/contract/preview?path=${encodeURIComponent(filepath)}`;
    previewFilename.value = filename || "";
    previewVisible.value = true;
  }
  function closePreview() {
    previewVisible.value = false;
    previewUrl.value = "";
    previewFilename.value = "";
  }

  // ── 流程控制 ──

  /** 步骤1 → 步骤2，并自动启动 AI 分析（手动填写模式除外） */
  function confirmContract() {
    if (!canProceedToAnalysis.value) {
      toast("请先获取合同信息", "", "warning");
      return;
    }

    if (cSrc.value === "manual") {
      workflowStep.value = 2;
      return;
    }

    // 非手动模式必须等三系统数据就绪，否则退费计算会用陈旧/缺失数据
    if (!threeSystemReady.value) {
      toast("三系统数据未就绪", "请先在新增投诉页完成「查询三系统」（身份证/手机号），再进入退费分析", "warning");
      return;
    }

    workflowStep.value = 2;

    const qr = getQr ? getQr() : null;
    if (!qr || !qr.id_card) {
      toast("暂无法自动分析", "三系统学员信息尚未就绪，请等待查询完成", "warning");
      return;
    }

    doAnalyze(
      qr.id_card || "",
      qr.exam_stage || "",
      qr.training_hours || {},
      getTicketId ? getTicketId() : ""
    );
  }

  /** 手动填写模式：直接进入扣费明细编辑 */
  function startManualEdit() {
    if (!manualContract.total_fee || manualContract.total_fee <= 0) {
      toast("请先输入合同总金额", "", "warning");
      return;
    }
    if (!manualContract.paid_amount || manualContract.paid_amount <= 0) {
      toast("请先输入已缴费用", "", "warning");
      return;
    }
    
    const totalFee = manualContract.total_fee || 0;
    const paidAmount = manualContract.paid_amount || 0;
    
    // 获取考试次数（从qr）
    const qr = getQr ? getQr() : null;
    const examCounts = qr?.exam_counts || {};
    
    // 计算考试费
    const examFees = calcExamFees(
      examCounts,
      manualContract.includes_exam,
      manualContract.includes_makeup,
    );
    
	    ar.value = {
	      total_fee: totalFee,
	      paid_amount: paidAmount,
	      actual_paid: paidAmount,
	      deductions: [
        { item: "综合服务费", amount: 0, reason: "" },
        { item: "建档费", amount: 0, reason: "" },
        { item: "学员IC卡费", amount: 0, reason: "" },
        { item: "理论培训费", amount: 0, reason: "" },
        { item: "科目二实操费", amount: 0, duration: qr?.training_hours?.subject2 || "", unit_price: "", reason: "" },
        { item: "科目三实操费", amount: 0, duration: qr?.training_hours?.subject3 || "", unit_price: "", reason: "" },
        ...(examFees.fees.length > 0 ? examFees.fees : []),
        { item: "违约金", amount: 0, reason: "合同约定违约金（元），人工填写" }
      ],
      total_deduction: 0,
      refund: paidAmount,
      summary: "手动填写模式，请编辑下方扣费明细",
	      contract_code: manualContract.contract_code || "",
	      penalty_amount: 0,
	      includes_exam_fee: manualContract.includes_exam,
	      includes_makeup_fee: manualContract.includes_makeup,
	      exam_fee_table: { subject1: 70, subject2: 130, subject3: 280 },
	      makeup_fee_table: { subject1: 35, subject2: 65, subject3: 140 },
      fee_plan_status: "draft",
      source: "manual",
    };

    // 进入退费分析步骤
    workflowStep.value = 2;
    recalc();
    toast("已进入扣费明细编辑", "请逐项填写或调整金额", "success");
  }

  /** 运行合同分析（由用户显式启动或重试） */
  async function doAnalyze(idCard, examStage, trainingHours, ticketId) {
    // AI 模式：必须上传/下载合同后才能分析
    if (!cPath.value) {
      aErr.value = "请先获取合同文件";
      return;
    }

    aLoading.value = true;
    aErr.value = "";
    ar.value = null;
    analysisProgress.value = 0;
    analysisElapsed.value = 0;
    if (aTimerId) clearInterval(aTimerId);
    aTimerId = setInterval(() => {
      analysisElapsed.value++;
      if (analysisElapsed.value < 10) analysisProgress.value = 0;
      else if (analysisElapsed.value < 20) analysisProgress.value = 1;
      else if (analysisElapsed.value < 30) analysisProgress.value = 2;
      else analysisProgress.value = 3;
    }, 1000);

    try {
      // 获取当前学员信息
      const currentQr = getQr ? getQr() : null;
      
      const start = await postJ("/api/contract/analyze/start", {
        filepath: cPath.value,
        image_paths: (contractManifest.value.analysis_image_paths || []).length
          ? contractManifest.value.analysis_image_paths
          : uploadedFiles.value
              .filter(f => /\.(jpe?g|png)$/i.test(f.filepath || ""))
              .map(f => f.filepath),
        exam_stage: examStage,
        training_hours: trainingHours,
        total_fee: manualContract.total_fee || 0,
        id_card: idCard,
        ticket_id: ticketId,
        exam_counts: currentQr?.exam_counts || {},  // 传入考试次数，辅助 AI 判断哪些费用已实际发生
      });
      if (!start.success) throw new Error(start.error || "启动分析失败");

      let d = null;
      for (let i = 0; i < 180; i++) {
        const status = await getJ(`/api/contract/analyze/status/${start.job_id}`);
        if (!status.success) throw new Error(status.error || "查询分析进度失败");
        if (status.status === "done") {
          d = status.result;
          break;
        }
        if (status.status === "failed" || status.status === "interrupted") {
          throw new Error(status.error || status.result?.error || "分析失败");
        }
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      if (!d) throw new Error("分析任务超时，可在当前案件重试，或改用「手动录入费用」直接建表");

      if (aTimerId) { clearInterval(aTimerId); aTimerId = null; }

      if (d.error) {
        aErr.value = d.error;
      } else {
        analysisProgress.value = 3;
        
        // 自动解析 reason 中的学时和单价，填充到表格
        // 实操项目（科目二/三）的学时必须用第三系统查到的真实数据，不能用 AI 猜的
        if (d.deductions) {
          // 构建学时映射：简化科目名 -> 真实学时字符串（如 "16时43分"）
          const hoursMap = {};
          if (trainingHours) {
            Object.entries(trainingHours).forEach(([subject, time]) => {
              if (subject.includes("二") || subject.includes("2")) hoursMap["科目二"] = time;
              if (subject.includes("三") || subject.includes("3")) hoursMap["科目三"] = time;
              if (subject.includes("一") || subject.includes("1")) hoursMap["科目一"] = time;
            });
          }

          d.deductions.forEach(ded => {
            const reason = ded.reason || "";

            // 违约金：从 reason 中提取基数和比例（如 "3180×20%=636"）
            if (ded.item === '违约金') {
              const formulaMatch = reason.match(/(\d+(?:\.\d+)?)\s*[×*]\s*(\d+(?:\.\d+)?)%/);
              if (formulaMatch) {
                ded.duration = formulaMatch[1];
                ded.unit_price = formulaMatch[2] + "%";
              }
              return;
            }

            // 实操项目：优先用第三系统真实学时覆盖 AI 的值（含小数）
            const realHours = hoursMap[ded.item];
            if (realHours) {
              ded.duration = realHours; // 如 "16时43分"
            } else {
              const durationMatch = reason.match(/(\d+时\d+分)/);
              if (durationMatch) ded.duration = durationMatch[1];
            }

            // 单价：从 reason 中提取
            const priceMatch = reason.match(/(\d+(?:\.\d+)?)\s*元\/学时/) || reason.match(/单价\s*(\d+(?:\.\d+)?)\s*元/);
            if (priceMatch) ded.unit_price = priceMatch[1];
          });
        }
        
        	        d.deductions = penaltyLast(d.deductions || []);
        	        ar.value = d;
	        if (!ar.value.actual_paid && ar.value.paid_amount) ar.value.actual_paid = ar.value.paid_amount;
	        if (!ar.value.actual_paid && manualContract.paid_amount) ar.value.actual_paid = manualContract.paid_amount;
	        // 注意：不再用三系统 contract_fee 静默兜底 actual_paid —— 学员未必 100% 已缴，
	        // 必须人工确认实际已交金额（见 step 2 UI 的 actual_paid 输入）。
	        const drivingContractFee = currentQr?.driving_fee?.contract_fee;
	        if (!ar.value.total_fee && drivingContractFee) ar.value.total_fee = drivingContractFee;
	        feeConfirmed.value = false;
        
        // 强制同步：用明细之和覆盖 AI 返回的 total_deduction，避免不一致警告
        recalc();
        
        if (ar.value.can_confirm_fee_plan === false) {
          toast("合同需补充确认", "关键字段识别不完整，暂不能确认正式费用方案", "warning");
        } else {
          toast("分析完成", "应退 " + ar.value.refund + " 元", "success");
        }
      }
    } catch (e) {
      if (aTimerId) { clearInterval(aTimerId); aTimerId = null; }
      aErr.value = "分析失败: " + e.message;
    } finally {
      aLoading.value = false;
    }
  }

  /** 确认AI分析结果 → 步骤3（沟通记录） */
  function confirmAnalysis() {
    if (!ar.value) {
      toast("请先进行AI分析", "", "warning");
      return;
    }
    workflowStep.value = 3;
	    toast("退费结果已确认", "请联系学员并记录沟通情况", "success");
	  }

  /** 东城自制合同：保存「培训方式 + 服务费」并重算退费。
   *  先持久化到工单（PUT /api/tickets/<id>，service_fee/training_mode 已进 ALLOWED_COLUMNS），
   *  再重新跑合同分析，让扣费引擎按第六条（普通培训/先培后付）分支出明细。 */
  async function saveDongchengFields(ticketId) {
    if (!ticketId) {
      toast("缺少案件ID", "", "warning");
      return;
    }
    if (!dongchengTrainingMode.value) {
      toast("请选择培训方式", "普通培训 / 先培后付决定第六条退费口径", "warning");
      return;
    }
    dongchengSaving.value = true;
    try {
      const upd = await postJ(`/api/tickets/${ticketId}`, {
        training_mode: dongchengTrainingMode.value,
        service_fee: Number(dongchengServiceFee.value) || 0,
      });
      if (!upd.success) throw new Error(upd.error || "保存失败");
      toast("已保存培训方式/服务费", "正在按第六条重算退费…", "success");
      // 重跑分析：后端从工单读 service_fee/training_mode 走东城自制分支
      const qr = getQr ? getQr() : null;
      await doAnalyze(
        qr?.id_card || "",
        qr?.exam_stage || ar.value?.stage || "已受理",
        qr?.training_hours || {},
        ticketId,
      );
    } catch (e) {
      toast("保存失败", e.message, "danger");
    } finally {
      dongchengSaving.value = false;
    }
  }

	  function buildManualContractSet() {
	    const rules = (ar.value?.deductions || [])
	      .filter(deduction => deduction.item && Number(deduction.amount) > 0)
	      .map(deduction => {
	        if (deduction.item === "违约金") {
	          return {
	            type: "fixed_penalty",
	            item: "违约金",
	            amount: Number(deduction.amount),
	            clause: deduction.reason || "人工核对合同违约金条款",
	          };
	        }
	        if (/^(科目一|科目二|科目三)(考试费|补考费)$/.test(deduction.item || "")) {
	          const m = (deduction.item || "").match(/^(科目[一二三])(考试费|补考费)$/);
	          return {
	            type: m[2] === "补考费" ? "makeup_fee" : "exam_fee",
	            item: deduction.item,
	            subject: m[1],
	            amount: Number(deduction.amount),
	            clause: deduction.reason || "人工核对合同考试费条款",
	          };
	        }
	        return {
	          type: "fixed",
	          item: deduction.item,
	          amount: Number(deduction.amount),
	          clause: deduction.reason || "人工核对合同原件后录入",
	        };
	      });
	    return {
	      contracts: [{
	        contract_id: "manual-training",
	        title: "人工录入培训合同",
	        total_fee: Number(ar.value.total_fee) || 0,
	        evidence: {
	          source: "manual",
	          contract_code: manualContract.contract_code || "",
	          note: "人工填写并核对合同原件",
	        },
	        rules,
	      }],
	    };
	  }

    function buildReviewedContractSet() {
      const fields = ar.value?.contract_fields || {};
      const valueOf = (key, fallback = 0) => fields[key]?.value ?? fallback;
      const refundClause = String(valueOf("refund_clause", "") || "").trim() || "人工核对合同退费条款";
      const rules = [];
      [
        ["service_fee", "服务费"],
        ["archive_fee", "建档费"],
        ["ic_card_fee", "学员IC卡费"],
        ["theory_fee", "理论培训费"],
      ].forEach(([key, item]) => {
        const amount = Number(valueOf(key)) || 0;
        if (amount > 0) rules.push({ type: "fixed", item, amount, clause: refundClause });
      });
      [["subject1", "科目一"], ["subject2", "科目二"], ["subject3", "科目三"]].forEach(([key, label]) => {
        const examFee = Number(valueOf(`${key}_exam_fee`)) || 0;
        const makeupFee = Number(valueOf(`${key}_makeup_fee`)) || 0;
        if (valueOf("includes_exam_fee", true) !== false && examFee > 0) {
          rules.push({ type: "exam_fee", item: `${label}考试费`, subject: label, amount: examFee, clause: refundClause });
        }
        if (valueOf("includes_makeup_fee", true) !== false && makeupFee > 0) {
          rules.push({ type: "makeup_fee", item: `${label}补考费`, subject: label, amount: makeupFee, clause: refundClause });
        }
      });
      [["subject2", "科目二"], ["subject3", "科目三"]].forEach(([key, label]) => {
        const hourlyRate = Number(valueOf(`${key}_unit_price`)) || 0;
        const maxAmount = Number(valueOf(`${key}_cap`)) || 0;
        if (hourlyRate > 0) {
          const rule = { type: "training_hour_fee", item: `${label}实操培训费`, subject: label, hourly_rate: hourlyRate, clause: refundClause };
          if (maxAmount > 0) rule.max_amount = maxAmount;
          rules.push(rule);
        }
      });
      const penaltyAmount = Number(valueOf("penalty_amount")) || 0;
      if (penaltyAmount > 0) {
        rules.push({ type: "fixed_penalty", item: "违约金", amount: penaltyAmount, clause: refundClause });
      }
      return {
        contracts: [{
          contract_id: ar.value?.contract_code || "reviewed-paper-contract",
          title: "人工核对纸质培训合同",
          total_fee: Number(valueOf("total_fee")) || 0,
          evidence: {
            source: "human_review",
            file: cPath.value,
            pages: (contractManifest.value.source_files || []).map(item => item.filepath).filter(Boolean),
          },
          rules,
        }],
      };
    }

	  async function handleSaveAndConfirm(ticketId, planStatus = "confirmed") {
    if (!ar.value) {
      toast("请先进行AI分析或手动录入费用", "", "warning");
      return;
    }
    if (ar.value.can_confirm_fee_plan === false) {
      toast("暂不能确认费用方案", "合同关键字段识别不完整，请重新上传清晰合同或人工补录", "warning");
      return;
    }
	    if (!ticketId) {
	      toast("缺少案件ID", "请先完成工单登记和学员查询", "warning");
	      return;
	    }
	    recalc();
	    const actualPaid = Number(ar.value.actual_paid || ar.value.paid_amount || manualContract.paid_amount || 0);
	    if (actualPaid <= 0) {
	      toast("请填写实际已交金额", "应退金额必须基于实际已交金额计算", "warning");
	      return;
	    }
	    feeConfirming.value = true;
	    try {
	      const d = await postJ(`/api/tickets/${ticketId}/fee-confirm`, {
	        plan_status: planStatus,
	        total_fee: Number(ar.value.total_fee) || 0,
	        actual_paid: actualPaid,
	        deductions: ar.value.deductions || [],
	        contract_fields: ar.value.contract_fields || {},
	        clauses: ar.value.clauses || [],
	        contract_code: ar.value.contract_code || manualContract.contract_code || "",
	        contract_set: cSrc.value === "manual" ? buildManualContractSet() : buildReviewedContractSet(),
	      });
	      if (!d.success) throw new Error(d.error || "确认失败");
	      const confirmed = d.data || {};
	      ar.value.actual_paid = confirmed.actual_paid;
	      ar.value.total_deduction = confirmed.total_deduction;
	      ar.value.refund = confirmed.refund;
	      ar.value.fee_plan_status = confirmed.fee_plan_status || "confirmed";
	      feePlanVersion.value = Number(confirmed.fee_plan_version) || 0;
	      feeConfirmed.value = ar.value.fee_plan_status === "confirmed";
	      feeConfirmedAt.value = new Date().toLocaleString();
	      await loadCommunications(ticketId);
	      workflowStep.value = 3;
	      toast(planStatus === "provisional" ? "阶段费用方案已确认" : "正式费用方案已确认", "已进入沟通记录", "success");
	      if (hooks.afterFeeConfirm) await hooks.afterFeeConfirm(confirmed, ticketId);
	    } catch (e) {
	      toast("保存失败", e.message, "danger");
	    } finally {
	      feeConfirming.value = false;
	    }
	  }

	  // ── 扣费明细编辑 ──

  // 查无记录·无费用明细：三系统未命中案件的费用闸门豁免（零口径确认，留痕 fee_confirm_note）
  async function confirmNoFeeBasis(ticketId) {
    if (!ticketId) {
      toast("缺少案件ID", "请先完成工单登记和学员查询", "warning");
      return;
    }
    feeConfirming.value = true;
    try {
      const d = await postJ(`/api/tickets/${ticketId}/fee-confirm`, {
        no_fee_basis: true,
        confirm_note: "三系统查无记录，无费用明细",
      });
      if (!d.success) throw new Error(d.error || "确认失败");
      const confirmed = d.data || {};
      feeConfirmed.value = (confirmed.fee_plan_status || "confirmed") === "confirmed";
      feeConfirmedAt.value = new Date().toLocaleString();
      toast("已按查无记录确认", "无费用明细（0元口径），可继续归档", "success");
      if (hooks.afterFeeConfirm) await hooks.afterFeeConfirm(confirmed, ticketId);
    } catch (e) {
      toast("确认失败", e.message, "danger");
    } finally {
      feeConfirming.value = false;
    }
  }

  // ISS-UJ-02：AI 分析失败/无结果时，初始化空表骨架供手动录入，保证流程可走通
  function startManualFeeEntry() {
    if (ar.value) return;
    ar.value = {
      deductions: [],
      total_fee: 0,
      actual_paid: 0,
      total_deduction: 0,
      refund: 0,
      contract_fields: {},
      clauses: [],
      contract_code: "",
      source: "manual",
    };
    toast("已切换手动录入", "请填写合同总额、实缴金额与扣费行后确认明细", "info");
  }

  // 违约金固定排在扣费项最后（展示顺序）
  function penaltyLast(deductions) {
    const list = Array.isArray(deductions) ? [...deductions] : [];
    const rest = list.filter(d => !/违约金/.test(d.item || ""));
    const penalty = list.filter(d => /违约金/.test(d.item || ""));
    return [...rest, ...penalty];
  }

  function recalc() {
    if (!ar.value) return;
    
    // 遍历所有扣费项，如果有时长和单价，自动重新计算金额
    ar.value.deductions.forEach(ded => {
      // 违约金为固定金额（元），人工直接填写，不做比例计算
      if (ded.item === '违约金') {
        return;
      }
      const durationStr = String(ded.duration || "").trim();
      const priceStr = String(ded.unit_price || "").trim();
      
      // 提取时长数字（支持 "16时43分" 或 "16.5"）
      let hours = 0;
      const timeMatch = durationStr.match(/(\d+(?:\.\d+)?)\s*(?:时|学时)/);
      if (timeMatch) {
        hours = parseFloat(timeMatch[1]);
        // 如果有分钟，加上分钟部分
        const minMatch = durationStr.match(/(\d+)\s*分/);
        if (minMatch) {
          hours += parseInt(minMatch[1]) / 60;
        }
      } else if (durationStr && !isNaN(parseFloat(durationStr))) {
        hours = parseFloat(durationStr);
      }
      
      // 提取单价数字（支持 "150元/学时" 或 "150"）
      let price = 0;
      const priceMatch = priceStr.match(/(\d+(?:\.\d+)?)/);
      if (priceMatch) {
        price = parseFloat(priceMatch[1]);
      }
      
      // 如果有时长和单价，自动计算金额
      if (hours > 0 && price > 0) {
        let calculated = Math.round(hours * price * 100) / 100;
        // 如果合同有上限，且计算值超过上限，则取上限值
        const maxAmt = Number(ded.max_amount);
        if (!isNaN(maxAmt) && maxAmt > 0 && calculated > maxAmt) {
          calculated = maxAmt;
        }
        ded.amount = calculated;
      }
    });
    
	    const sum = ar.value.deductions.reduce((a, d) => a + (Number(d.amount) || 0), 0);
	    const totalFee = Number(ar.value.total_fee) || 0;
	    const actualPaid = Number(ar.value.actual_paid || ar.value.paid_amount || manualContract.paid_amount || 0) || 0;
	    ar.value.total_deduction = Math.round(sum * 100) / 100;
	    ar.value.actual_paid = actualPaid;
	    const refund = actualPaid - ar.value.total_deduction;
	    ar.value.refund = refund > 0 ? Math.round(refund * 100) / 100 : 0;
	  }

  function addDeduction() {
    if (!ar.value) return;
    ar.value.deductions.push({ item: "", amount: 0, reason: "" });
    recalc();
  }

  function removeDeduction(i) {
    if (!ar.value || !ar.value.deductions) return;
    ar.value.deductions.splice(i, 1);
    recalc();
  }

  // 导出扣费明细为 Excel（前端生成 .xls，Excel/WPS 可直接打开）
  function xesc(s) {
    return String(s ?? "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  function exportDeductions() {
    if (!ar.value || !Array.isArray(ar.value.deductions)) {
      toast("暂无可导出的明细", "请先完成费用分析", "warning");
      return;
    }
    const rows = ar.value.deductions.map((d, i) =>
      `<tr><td>${i + 1}</td><td>${xesc(d.item)}</td><td>${Number(d.amount) || 0}</td><td>${xesc(d.reason)}</td></tr>`).join("");
    const actualPaid = Number(ar.value.actual_paid || ar.value.paid_amount || 0);
    const totalDeduction = Number(ar.value.total_deduction || 0);
    const refund = Math.max(actualPaid - totalDeduction, 0);
    const html = `<html xmlns:x="urn:schemas-microsoft-com:office:excel"><head><meta charset="UTF-8"></head><body>`
      + `<table border="1"><thead><tr><th>序号</th><th>扣费项目</th><th>金额（元）</th><th>扣费依据</th></tr></thead>`
      + `<tbody>${rows}</tbody>`
      + `<tfoot><tr><td colspan="2">实缴总额</td><td>${actualPaid}</td><td></td></tr>`
      + `<tr><td colspan="2">扣费合计</td><td>${totalDeduction}</td><td></td></tr>`
      + `<tr><td colspan="2">应退</td><td>${refund}</td><td></td></tr></tfoot></table></body></html>`;
    const blob = new Blob(["\ufeff" + html], { type: "application/vnd.ms-excel;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.download = `投诉扣费明细_${getQr?.()?.name || "学员"}_${new Date().toISOString().slice(0, 10)}.xls`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast("已导出扣费明细", a.download, "success");
  }

  function upsertDeductionFromField(itemName, amount, reason) {
    if (!ar.value) return;
    if (!Array.isArray(ar.value.deductions)) ar.value.deductions = [];
    const value = Number(amount) || 0;
    const existing = ar.value.deductions.find(d => d.item === itemName);
    if (existing) {
      existing.amount = value;
      existing.reason = reason || existing.reason || "";
      return;
    }
    if (value > 0) {
      ar.value.deductions.push({ item: itemName, amount: value, reason: reason || "合同字段人工核对" });
    }
  }

  function syncSubjectTrainingFee(itemName, unitPrice, cap) {
    if (!ar.value || !Array.isArray(ar.value.deductions)) return;
    const existing = ar.value.deductions.find(d => d.item === itemName);
    if (!existing) return;
    const price = Number(unitPrice) || 0;
    const maxAmount = Number(cap) || 0;
    if (price > 0) existing.unit_price = `${price}元/学时`;
    existing.max_amount = maxAmount;
  }

  function syncProgressFeeDeductions() {
    if (!ar.value || !Array.isArray(ar.value.deductions)) return;
    ar.value.deductions = ar.value.deductions.filter(
      deduction => !/^(科目一|科目二|科目三)(考试费|补考费)$/.test(deduction.item || ""),
    );
    if (ar.value.includes_exam_fee === false) return;

    const qr = getQr ? getQr() : null;
    const counts = qr?.exam_counts || {};
    const examTable = ar.value.exam_fee_table || {};
    const makeupTable = ar.value.makeup_fee_table || {};
    for (const [key, label] of [["subject1", "科目一"], ["subject2", "科目二"], ["subject3", "科目三"]]) {
      const count = examCount(counts, key, label);
      const examFee = Number(examTable[key]) || 0;
      if (count > 0 && examFee > 0) {
        ar.value.deductions.push({
          item: `${label}考试费`,
          amount: examFee,
          exam_count: count,
          reason: `三系统显示${label}考试${count}次，合同考试费${examFee}元`,
          generated_progress_fee: true,
        });
      }
      const makeupFee = Number(makeupTable[key]) || 0;
      if (ar.value.includes_makeup_fee !== false && count > 1 && makeupFee > 0) {
        ar.value.deductions.push({
          item: `${label}补考费`,
          amount: (count - 1) * makeupFee,
          exam_count: count,
          makeup_count: count - 1,
          reason: `三系统显示${label}补考${count - 1}次，合同补考费${makeupFee}元/次`,
          generated_progress_fee: true,
        });
      }
    }
  }

  function refreshContractReviewStatus() {
    if (!ar.value || !ar.value.contract_fields) return;
    const fields = ar.value.contract_fields;
    const valueOf = (key) => fields[key]?.value;
    const missing = [];
    if (!(Number(valueOf("total_fee")) > 0)) missing.push("合同总培训费");
    if (!(Number(ar.value.actual_paid) > 0)) missing.push("实际已交金额");
    if (!String(valueOf("refund_clause") || "").trim()) missing.push("退费条款");
    fields.uncertain_fields = missing;
    fields.status = missing.length ? "needs_review" : "draft";
    ar.value.unclear_fields = missing;
    ar.value.blockers = missing.length ? [`人工补录仍缺少：${missing.join("、")}`] : [];
    ar.value.can_confirm_fee_plan = missing.length === 0;
    ar.value.fee_plan_status = missing.length ? "needs_review" : "draft";
  }

  function applyContractFields() {
    if (!ar.value || !ar.value.contract_fields) return;
    const fields = ar.value.contract_fields;
    const fieldValue = (key) => Number(fields[key]?.value) || 0;

    ar.value.total_fee = fieldValue("total_fee");
    ar.value.penalty_amount = fieldValue("penalty_amount");
    ar.value.includes_exam_fee = fields.includes_exam_fee?.value !== false;
    ar.value.includes_makeup_fee = fields.includes_makeup_fee?.value !== false;
    ar.value.exam_fee_table = {
      subject1: fieldValue("subject1_exam_fee"),
      subject2: fieldValue("subject2_exam_fee"),
      subject3: fieldValue("subject3_exam_fee"),
      license: fieldValue("license_fee"),
    };
    ar.value.makeup_fee_table = {
      subject1: fieldValue("subject1_makeup_fee"),
      subject2: fieldValue("subject2_makeup_fee"),
      subject3: fieldValue("subject3_makeup_fee"),
    };
    syncProgressFeeDeductions();

    upsertDeductionFromField("综合服务费", fieldValue("service_fee"), "合同字段核对：综合服务费");
    upsertDeductionFromField("建档费", fieldValue("archive_fee"), "合同字段核对：建档费");
    upsertDeductionFromField("IC卡费", fieldValue("ic_card_fee"), "合同字段核对：IC卡费");
    upsertDeductionFromField("理论培训费", fieldValue("theory_fee"), "合同字段核对：理论培训费");
    syncSubjectTrainingFee("科目二实操费", fieldValue("subject2_unit_price"), fieldValue("subject2_cap"));
    syncSubjectTrainingFee("科目三实操费", fieldValue("subject3_unit_price"), fieldValue("subject3_cap"));

    const penaltyAmount = fieldValue("penalty_amount");
    const penaltyDed = ar.value.deductions.find(d => d.item === "违约金");
    if (penaltyDed && penaltyAmount >= 0) {
      penaltyDed.amount = Math.round(penaltyAmount * 100) / 100;
      penaltyDed.reason = `合同字段核对：违约金 ${penaltyDed.amount} 元`;
    }

    if (ar.value.includes_exam_fee === false || ar.value.includes_makeup_fee === false) {
      ar.value.fee_basis_warning = "考试费/补考费已改为不默认包含在合同总培训费内，请核对扣费明细和实际已交金额后再确认。";
    } else {
      ar.value.fee_basis_warning = "";
    }

    feeConfirmed.value = false;
    recalc();
    refreshContractReviewStatus();
  }

  // 更新违约金金额（用户手动修正，单位：元）
  function updatePenaltyRate(newAmount) {
    if (!ar.value) return;
    
    const amount = Math.round((parseFloat(newAmount) || 0) * 100) / 100;
    ar.value.penalty_amount = amount;
    
    // 找到违约金项目并更新
    const penaltyDed = ar.value.deductions.find(d => d.item === '违约金');
    if (penaltyDed) {
      penaltyDed.amount = amount;
      penaltyDed.reason = `违约金=${amount}元（手动填写）`;
    }
    
    // 重新计算总扣费和应退金额
    recalc();
    
    toast("违约金已更新", `违约金: ${amount} 元`, "success");
  }

  // ── 回复函 ──

	  async function genReply(idCard, qr, ticketId, templateId, documentType = "formal") {
	    const feeStatus = ar.value?.fee_plan_status || "";
	    const canUseFeePlan = documentType === "progress"
	      ? ["provisional", "confirmed"].includes(feeStatus)
	      : feeStatus === "confirmed";
	    if (!canUseFeePlan) {
	      toast("请先确认费用方案", documentType === "progress" ? "进展回复可使用阶段或正式费用方案" : "正式回复函必须使用正式确认后的扣费方案", "warning");
	      return;
	    }
	    rpLoading.value = true;
    rpErr.value = "";
    rpResult.value = null;

    try {
      const body = {
        document_type: documentType,
        ticket_id: ticketId,
        name: qr.name || "",
        id_card: idCard,
        school_short: qr.school_short || "",
        school_name: qr.school_name || "",
        registration_date: qr.registration_date || "",
        license_type: qr.license_type || "",
        exam_stage: qr.exam_stage || "",
	        total_fee: ar.value?.total_fee || 0,
	        actual_paid: ar.value?.actual_paid || ar.value?.paid_amount || 0,
	        deductions: ar.value?.deductions || [],
	        total_deduction: ar.value?.total_deduction || 0,
	        refund: ar.value?.refund || 0,
	        contract_code: ar.value?.contract_code || "",
	        training_hours: qr.training_hours || {},
	        final_outcome: finalOutcome.value || "",
	        branch_cooperation: branchCooperation.value || "",
	        template_id: templateId || "",
	      };
      const d = await postJ("/api/reply/generate", body);
      if (d.success) {
        rpResult.value = d;
        toast("回复函已生成", d.filename, "success");
      } else {
        rpErr.value = d.error;
      }
    } catch (e) {
      rpErr.value = e.message;
    } finally {
      rpLoading.value = false;
    }
  }

  async function loadCommunications(ticketId) {
    if (!ticketId) return;
    communicationsLoading.value = true;
    try {
      const d = await getJ(`/api/tickets/${ticketId}/communications`);
      if (d.success) {
        communications.value = d.data || [];
      }
    } catch (e) {
      toast("沟通记录加载失败", e.message, "danger");
    } finally {
      communicationsLoading.value = false;
    }
  }

  async function addCommunication(ticketId) {
    if (!ticketId) {
      toast("缺少案件ID", "", "warning");
      return;
    }
    if (!commForm.summary.trim()) {
      toast("请填写沟通摘要", "", "warning");
      return;
    }
    communicationsLoading.value = true;
    try {
      const d = await postJ(`/api/tickets/${ticketId}/communications`, {
        contact_time: commForm.contact_time || "",
        contact_method: commForm.contact_method || "",
        summary: commForm.summary.trim(),
        student_intention: commForm.student_intention || "",
        next_follow_up: commForm.next_follow_up || "",
      });
      if (!d.success) throw new Error(d.error || "保存沟通记录失败");
      commForm.contact_time = "";
      commForm.summary = "";
      commForm.next_follow_up = "";
      await loadCommunications(ticketId);
      toast("沟通记录已保存", "", "success");
    } catch (e) {
      toast("保存失败", e.message, "danger");
    } finally {
      communicationsLoading.value = false;
    }
  }

  /** 协商结果三类 + 撤诉状态 → 六类最终结果映射 */
  function mapNegotiationToOutcome(negotiation, withdraw) {
    const w = withdraw || "未撤诉";
    if (negotiation === "协商失败" && w !== "已撤诉") return "不同意合同扣费且协商失败";
    if (negotiation === "其他情形") return "继续培训/转校";
    // 协商一致（或协商失败但学员已撤诉）→ 撤诉即投诉撤销，否则同意合同扣费
    if (w === "已撤诉") return "投诉撤销";
    return "同意合同扣费";
  }

  /** 协商结果变化时自动映射最终结果（用户仍可在归档卡片手动改） */
  function syncOutcomeFromNegotiation() {
    if (!negotiationOutcome.value) return;
    finalOutcome.value = mapNegotiationToOutcome(negotiationOutcome.value, withdrawStatus.value);
  }

  // ── 归档卡片：高频结果卡片 + 其他情况下拉（2026-08-19 重构） ──
  const otherOutcome = Vue.ref("");  // 低频结果：无法联系 / 继续培训/转校
  const remarkOpen = Vue.ref(false);      // 配合度备注折叠
  const commFormOpen = Vue.ref(false);    // 新增沟通表单折叠

  /** 点击高频结果卡片：直接设最终结果；投诉撤销自动切已撤诉 */
  function selectOutcomeCard(outcome) {
    finalOutcome.value = outcome;
    otherOutcome.value = "";
    if (outcome === "投诉撤销") withdrawStatus.value = "已撤诉";
  }

  /** 低频结果下拉变化：设最终结果，清空卡片选中态 */
  function onOtherOutcomeChange() {
    finalOutcome.value = otherOutcome.value || "";
  }

  async function saveCaseOutcome(ticketId) {
    if (!ticketId) {
      toast("缺少案件ID", "", "warning");
      return;
    }
    if (!finalOutcome.value) {
      toast("请选择最终结果", "", "warning");
      return;
    }
    // 投诉撤销必须联动"已撤诉"，与 updateWithdrawStatus 路径保持一致，避免矛盾态
    if (finalOutcome.value === "投诉撤销" && withdrawStatus.value !== "已撤诉") {
      toast("请先更新撤诉状态", "投诉撤销必须对应学员已撤诉（withdraw_status=已撤诉）", "warning");
      return;
    }
    if (finalOutcome.value !== "投诉撤销") {
      if (ar.value?.fee_plan_status !== "confirmed") {
        toast("请先确认正式费用方案", "非撤销案件归档必须基于正式确认后的费用方案", "warning");
        return;
      }
      // 与后端 _completion_gate 对齐：至少 1 条沟通 + 沟通记录版本含当前方案版本
      if (!communications.value.length) {
        toast("请至少登记一次沟通记录", "再完结归档案件", "warning");
        return;
      }
      const cur = feePlanVersion.value || 0;
      const versions = communications.value.map(r => Number(r.fee_plan_version) || 0);
      const hasCurrent = cur === 0 ? versions.includes(0)
        : cur === 1 ? versions.some(v => v === 0 || v === 1)
        : versions.includes(cur);
      if (!hasCurrent) {
        toast("缺少当前费用方案版本的沟通记录", `请先登记 v${cur} 方案的沟通记录再归档`, "warning");
        return;
      }
    }
    archiveSaving.value = true;
    try {
      const d = await fetch(`/api/tickets/${ticketId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          final_outcome: finalOutcome.value,
          negotiation_outcome: negotiationOutcome.value || "",
          withdraw_status: withdrawStatus.value || "未撤诉",
          branch_cooperation: branchCooperation.value || "",
          branch_cooperation_note: branchCooperationNote.value || "",
          archive_status: "已归档",
          handle_status: "已完结",
        }),
      }).then(r => r.json());
      if (!d.success) throw new Error(d.error || "归档失败");
      archivedCase.value = true;
      workflowStep.value = 4;
      toast("案件结果已归档", "可按需生成登记表或回复函", "success");
    } catch (e) {
      toast("归档失败", e.message, "danger");
    } finally {
      archiveSaving.value = false;
    }
  }

  /** 独立更新撤诉状态（归档后仍可用，不重新打开案件） */
  async function updateWithdrawStatus(ticketId) {
    if (!ticketId) return;
    try {
      const d = await fetch(`/api/tickets/${ticketId}/withdraw-status`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ withdraw_status: withdrawStatus.value || "未撤诉" }),
      }).then(r => r.json());
      if (!d.success) throw new Error(d.error || "更新撤诉状态失败");
      withdrawUpdatedAt.value = d.data.withdraw_updated_at || "";
      syncOutcomeFromNegotiation();
      toast("撤诉状态已更新", `当前: ${withdrawStatus.value}`, "success");
    } catch (e) {
      toast("更新失败", e.message, "danger");
    }
  }

  async function reopenFeePlan(ticketId) {
    const reason = feeReopenReason.value.trim();
    if (!reason) {
      toast("请填写修改原因", "费用更正必须保留可审计原因", "warning");
      return;
    }
    feeReopening.value = true;
    try {
      const d = await postJ(`/api/tickets/${ticketId}/fee-reopen`, { reason });
      if (!d.success) throw new Error(d.error || "重新打开失败");
      archivedCase.value = false;
      feeConfirmed.value = false;
      if (ar.value) {
        ar.value.fee_plan_status = "draft";
        ar.value.source = "manual"; // 重开即编辑态：合计字段须可输入（与 restore 兜底一致）
      }
      feeReopenReason.value = "";
      workflowStep.value = 2;
      toast("费用方案已重新打开", "请复核费用并重新确认、沟通和归档", "success");
    } catch (e) {
      toast("重新打开失败", e.message, "danger");
    } finally {
      feeReopening.value = false;
    }
  }

  // ── 投诉登记表 ──

  // pageEdits：{ handling_notes, student_name, overrides }
  // overrides 为预览纸面上的编辑覆盖值（键：标签 / 标签:行号 / __title__ / __no_line__）
  async function genRegistrationForm(ticketId, pageEdits = {}) {
    formLoading.value = true;
    formResult.value = null;
    try {
      const body = {};
      if (pageEdits.handling_notes) body.handling_notes = pageEdits.handling_notes;
      if (pageEdits.student_name) body.student_name = pageEdits.student_name;
      if (pageEdits.overrides && Object.keys(pageEdits.overrides).length) {
        body.overrides = pageEdits.overrides;
      }
      const d = await postJ(`/api/tickets/${ticketId}/register-form`, body);
      if (d.success) {
        formResult.value = d.data;
        toast("登记表已生成", d.data.filename || "", "success");
      } else {
        toast("生成失败", d.error, "danger");
      }
    } catch (e) {
      toast("生成失败", e.message, "danger");
    } finally {
      formLoading.value = false;
    }
  }

  function reset() {
    workflowStep.value = 1;
    cSrc.value = "upload";
    cPath.value = "";
    cName.value = "";
    contractManifest.value = {};
    uploadedFiles.value = [];
    ar.value = null;
    aErr.value = "";
    analysisProgress.value = 0;
    analysisElapsed.value = 0;
    if (aTimerId) { clearInterval(aTimerId); aTimerId = null; }
    rpResult.value = null;
    rpErr.value = "";
	    feeConfirmed.value = false;
	    feeConfirmedAt.value = "";
	    communications.value = [];
	    commForm.contact_time = "";
	    commForm.contact_method = "电话";
	    commForm.summary = "";
	    commForm.student_intention = "继续协商";
	    commForm.next_follow_up = "";
	    finalOutcome.value = "";
	    branchCooperation.value = "";
	    branchCooperationNote.value = "";
	    archivedCase.value = false;
	    feeReopenReason.value = "";
	    formResult.value = null;
	  }

  // 重置工作流状态（查询新学员时调用）
  function resetWorkflow() {
    workflowStep.value = 1;
    cSrc.value = "download";
    cPath.value = "";
    cName.value = "";
    cCached.value = false;
    cCachedAt.value = "";
    contractManifest.value = {};
    uploadedFiles.value = [];  // 清空已上传文件列表
	    ar.value = null;
	    feeConfirmed.value = false;
	    feeConfirmedAt.value = "";
	    communications.value = [];
	    finalOutcome.value = "";
	    branchCooperation.value = "";
	    branchCooperationNote.value = "";
	    archivedCase.value = false;
	    feeReopenReason.value = "";
	    rpLoading.value = false;
    rpErr.value = "";
  }

  function restore(detail) {
    const ticket = detail.ticket || {};
    const deductions = detail.deductions || [];
    const manifest = ticket.contract_manifest || {};
    contractManifest.value = manifest;
    const sourceFiles = manifest.source_files || [];
    cPath.value = manifest.merged_pdf_path || ticket.contract_path || sourceFiles[0]?.filepath || "";
    cName.value = cPath.value ? cPath.value.split(/[\\/]/).pop() : "";
    uploadedFiles.value = sourceFiles.length
      ? sourceFiles
      : (cPath.value ? [{ filepath: cPath.value, filename: cName.value }] : []);
    cSrc.value = cPath.value ? "upload" : "upload";
    dongchengServiceFee.value = Number(ticket.service_fee) || 0;
    dongchengTrainingMode.value = String(ticket.training_mode || "");
    // 还原 source（bug：工单重开后 合同总额/实缴 只读，无法确认明细）。
    // 模板以 ar.source==='manual' 决定这两个字段是否为输入框；restore 原先丢字段。
    // source 的三个丢失点均在此兜底：
    //   ① save_analysis 整包字典 → 取字典内 source；
    //   ② fee-confirm 写库只存明细列表（无字典）→ draft/needs_review（编辑态）兜底 'manual'；
    //   ③ 零口径确认→解锁（detail='[]'、draft）→ 同上兜底。
    // confirmed 维持只读守门（须先解锁），AI 提取来源在确认前不放开合计编辑。
    const statusConfirmed = (ticket.fee_plan_status || "") === "confirmed";
    let restoredSource;
    try {
      if (typeof ticket.deduction_detail === "string" && ticket.deduction_detail.trim().startsWith("{")) {
        const parsed = JSON.parse(ticket.deduction_detail);
        if (parsed && typeof parsed === "object") restoredSource = parsed.source;
      }
    } catch (e) { /* 字典解析失败时走下方兜底 */ }
    if (!statusConfirmed) restoredSource = restoredSource || "manual";
    ar.value = deductions.length || ticket.fee_plan_status ? {
      total_fee: Number(ticket.total_fee) || 0,
      actual_paid: Number(ticket.actual_paid) || 0,
      total_deduction: Number(ticket.deduction_fee) || 0,
      refund: Number(ticket.refund_fee) || 0,
      deductions: penaltyLast(deductions),
      contract_code: ticket.contract_code || "",
      fee_plan_status: ticket.fee_plan_status || "draft",
      source: restoredSource,
    } : null;
    feeConfirmed.value = ticket.fee_plan_status === "confirmed";
    feePlanVersion.value = Number(ticket.fee_plan_version) || 0;
    feeConfirmedAt.value = ticket.fee_confirmed_at || "";
    communications.value = detail.communication_records || [];
    finalOutcome.value = ticket.final_outcome || "";
    otherOutcome.value = (ticket.final_outcome === "无法联系" || ticket.final_outcome === "继续培训/转校") ? ticket.final_outcome : "";
    branchCooperation.value = ticket.branch_cooperation || "";
    branchCooperationNote.value = ticket.branch_cooperation_note || "";
    negotiationOutcome.value = ticket.negotiation_outcome || "";
    withdrawStatus.value = ticket.withdraw_status || "未撤诉";
    withdrawUpdatedAt.value = ticket.withdraw_updated_at || "";
    archivedCase.value = ticket.archive_status === "已归档";
    rpResult.value = ticket.reply_path
      ? { filepath: ticket.reply_path, filename: ticket.reply_path.split(/[\\/]/).pop(), outdated: Boolean(ticket.reply_outdated) }
      : null;
    formResult.value = ticket.registration_form_path
      ? { filepath: ticket.registration_form_path, filename: ticket.registration_form_path.split(/[\\/]/).pop() }
      : null;
    if (ticket.archive_status === "已归档" || ticket.handle_status === "已完结" || ticket.final_outcome) {
      workflowStep.value = 4;
    } else if (feeConfirmed.value || communications.value.length) {
      workflowStep.value = 3;
    } else if (cPath.value || ar.value) {
      workflowStep.value = 2;
    } else {
      workflowStep.value = 1;
    }
  }

  // 解锁费用后由 useWorkbench 调用：把「来源丢失（undefined）」的存量明细置为人工可编辑，
  // 使 合同总额/实缴 输入框出现；不动 AI 提取（有 source 的）记录的只读守门。
  function markFeeEditable() {
    if (ar.value && !ar.value.source) ar.value.source = "manual";
  }

  return {
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
    threeSystemReady,
    rpLoading,
    rpResult,
    rpErr,
    contractInput,
    uploadedFiles,
    contractManifest,
	    dlContract,
	    ulContract,
	    confirmContract,
	    startManualEdit,
	    doAnalyze,
    confirmAnalysis,
    previewVisible, previewUrl, previewFilename, previewIsPdf,
    openPreview, closePreview,
    handleSaveAndConfirm,
    confirmNoFeeBasis,
    recalc,
    startManualFeeEntry,
    applyContractFields,
    addDeduction,
    removeDeduction,
    exportDeductions,
    updatePenaltyRate,  // 导出违约金比例修正方法
    genReply,
    dongchengServiceFee, dongchengTrainingMode, dongchengSaving, isDongchengTier,
    saveDongchengFields,
	    // 沟通/归档
	    fromHistoryLabel,
	    OUTCOME_OPTIONS, COOPERATION_OPTIONS, NEGOTIATION_OPTIONS,
	    feeConfirmed, feeConfirming, feeConfirmedAt,
	    communications, communicationsLoading, commForm,
	    finalOutcome, negotiationOutcome, withdrawStatus, withdrawUpdatedAt,
	    syncOutcomeFromNegotiation, updateWithdrawStatus,
	    otherOutcome, selectOutcomeCard, onOtherOutcomeChange,
	    remarkOpen, commFormOpen,
	    branchCooperation, branchCooperationNote, archiveSaving,
	    archivedCase, feeReopenReason, feeReopening, feePlanVersion,
	    formLoading, formResult,
	    loadSavedAnalysis, saveAnalysis,
    markFeeEditable,
	    loadCommunications, addCommunication, saveCaseOutcome, reopenFeePlan,
    genRegistrationForm,
    reset,
    resetWorkflow,  // 导出重置方法，用于查询新学员时清空旧状态
    restore,
  };
}
