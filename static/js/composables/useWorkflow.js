// 工作流程组合式函数：合同 → AI分析 → 回复函 → 飞书
import { postJ, uploadFile } from "api";

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
function calcExamFees(examCounts, includesExam) {
  if (includesExam) {
    return { total: 0, fees: [] };
  }
  
  const fees = {
    subject1: { name: "科目一", normal: 70, retake: 35 },
    subject2: { name: "科目二", normal: 130, retake: 65 },
    subject3: { name: "科目三", normal: 280, retake: 140 },
  };
  
  let total = 10; // 工本费
  const feeItems = [{ item: "工本费", amount: 10, reason: "固定费用" }];
  
  for (const [subject, count] of Object.entries(examCounts || {})) {
    if (count <= 0 || !fees[subject]) continue;
    
    const { name, normal, retake } = fees[subject];
    // 第一次正常费用，后续补考费
    const subjectTotal = normal + (count - 1) * retake;
    total += subjectTotal;
    feeItems.push({
      item: `${name}考试费`,
      amount: subjectTotal,
      reason: `${name}考试${count}次（第1次${normal}元${count > 1 ? `，补考${count-1}次×${retake}元` : ''}）`,
      max_amount: subjectTotal
    });
  }
  
  return { total, fees: feeItems };
}

export function useWorkflow(toast, getQr) {
  // 工作流步骤：1=合同，2=AI分析，3=回复函，4=飞书
  const workflowStep = Vue.ref(1);
  const workflowStatusText = Vue.computed(() => {
    const m = { 1: "待获取合同", 2: "待AI分析", 3: "待生成回复函", 4: "待提交飞书" };
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

  // 回访管理
  const visitStatus = Vue.ref("");
  const visitRemark = Vue.ref("");
  const visitLoading = Vue.ref(false);

  // 投诉登记表
  const formLoading = Vue.ref(false);
  const formResult = Vue.ref(null);

  // AI分析
  const aLoading = Vue.ref(false);
  const ar = Vue.ref(null);
  const aErr = Vue.ref("");
  const analysisProgress = Vue.ref(0);
  const analysisElapsed = Vue.ref(0);
  let aTimerId = null;

  // 扣费合计校验
  const deductionSum = Vue.computed(() => {
    if (!ar.value || !ar.value.deductions) return 0;
    return ar.value.deductions.reduce((s, d) => s + (Number(d.amount) || 0), 0);
  });
  const deductionMismatch = Vue.computed(() => {
    if (!ar.value) return false;
    const td = Number(ar.value.total_deduction) || 0;
    return Math.abs(deductionSum.value - td) > 0.01;
  });

  // 手动填写合同信息
  const manualContract = Vue.reactive({
    total_fee: 0,      // 合同总金额
    paid_amount: 0,    // 实际已缴金额
    contract_code: "", // 合同编号（可选）
    includes_exam: false, // 合同是否包含考试费
  });

  const canProceedToAnalysis = Vue.computed(() => {
    if (cSrc.value === "upload") return cPath.value !== "";
    if (cSrc.value === "download") return cPath.value !== "";
    // 手动填写：需要合同总金额和已缴金额
    return manualContract.total_fee > 0 && manualContract.paid_amount > 0;
  });

  // 回复函
  const rpLoading = Vue.ref(false);
  const rpResult = Vue.ref(null);
  const rpErr = Vue.ref("");

  // 飞书
  const fsLoading = Vue.ref(false);
  const fsResult = Vue.ref(null);

  // ── 合同操作 ──

  async function dlContract(idCard, name, schoolShort) {
    cLoading.value = true;
    try {
      const d = await postJ("/api/contract/download", {
        id_card: idCard,
        name: name,
        school_short: schoolShort || "",
      });
      if (d.success) {
        cPath.value = d.data?.filepath || d.filepath;
        cName.value = d.data?.filename || d.filename;
        cCached.value = d.data?.cached || false;
        cCachedAt.value = d.data?.downloaded_at || "";
        const cacheLabel = cCached.value ? `（缓存于 ${cCachedAt.value}）` : "";
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
    console.log("=== Upload Debug ===");
    console.log("Event type:", ev?.type);
    console.log("Event target:", ev?.target);
    console.log("Event target tagName:", ev?.target?.tagName);
    console.log("Event target files:", ev?.target?.files);
    console.log("Event target files length:", ev?.target?.files?.length);
    
    const files = ev?.target?.files;
    
    if (!files || files.length === 0) {
      console.warn("No files selected - user may have cancelled");
      // 不显示错误，可能是用户点了取消
      return;
    }
    
    console.log("Files to upload:", Array.from(files).map(f => f.name));
    
    cLoading.value = true;
    try {
      // 构建 FormData，支持多文件
      const formData = new FormData();
      for (let i = 0; i < files.length; i++) {
        formData.append("file", files[i]);
      }
      
      // 清空 input（必须在 FormData 构建完成后）
      ev.target.value = '';
      
      // 添加学员信息（用于归档）
      const currentQr = getQr ? getQr() : null;
      console.log("Current QR data:", currentQr);
      if (currentQr) {
        formData.append("id_card", currentQr.id_card || "");
        formData.append("name", currentQr.name || "");
        formData.append("school_short", currentQr.school_short || "");
      } else {
        console.warn("QR data is null or undefined!");
      }

      console.log("FormData entries:");
      for (let pair of formData.entries()) {
        console.log("  ", pair[0], ":", pair[1] instanceof File ? `File(${pair[1].name})` : pair[1]);
      }

      const resp = await fetch("/api/contract/upload", {
        method: "POST",
        body: formData,
      });
      console.log("Response status:", resp.status, "ok:", resp.ok);
      
      const text = await resp.text();
      console.log("Raw response text:", text.substring(0, 500));
      
      let d;
      try {
        d = JSON.parse(text);
      } catch (parseErr) {
        console.error("JSON parse error:", parseErr, "text:", text);
        toast("上传失败", "服务器返回格式错误: " + text.substring(0, 100), "danger");
        return;
      }
      
      console.log("Parsed response:", d);

      if (d.success) {
        cPath.value = d.data?.filepath || d.filepath;
        const count = d.data?.count || 1;
        cName.value = count > 1 ? `${count}个文件` : (d.data?.filename || d.filename);
        
        // 保存上传的文件列表，用于 AI 分析时传递所有图片路径
        uploadedFiles.value = d.data?.all_files || [{ filepath: cPath.value, filename: cName.value }];
        
        toast(`上传成功`, count > 1 ? `已保存 ${count} 个文件并生成合并PDF` : cName.value, "success");
      } else {
        toast("上传失败", d.error || "未知错误", "danger");
        console.error("Upload Error:", d);
      }
    } catch (e) {
      toast("上传失败", e.message, "danger");
      console.error("Upload Exception:", e);
    } finally {
      cLoading.value = false;
    }
  }

  // ── 流程控制 ──

  /** 步骤1 → 步骤2，自动触发AI分析 */
  function confirmContract() {
    if (!canProceedToAnalysis.value) {
      toast("请先获取合同信息", "", "warning");
      return;
    }
    workflowStep.value = 2;
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
    const examFees = calcExamFees(examCounts, manualContract.includes_exam);
    
    ar.value = {
      total_fee: totalFee,
      paid_amount: paidAmount,
      deductions: [
        { item: "报名费", amount: 0, max_amount: 1000, reason: "" },
        { item: "档案费", amount: 300, max_amount: 300, reason: "固定费用" },
        { item: "IC卡费", amount: 100, max_amount: 100, reason: "固定费用" },
        { item: "理论培训费", amount: 0, duration: "", unit_price: "", reason: "" },
        { item: "科目二", amount: 0, duration: qr?.training_hours?.subject2 || "", unit_price: "", reason: "" },
        { item: "科目三", amount: 0, duration: qr?.training_hours?.subject3 || "", unit_price: "", reason: "" },
        ...(examFees.fees.length > 0 ? examFees.fees : []),
        { item: "违约金", amount: Math.round(totalFee * 0.2 * 100) / 100, max_amount: Math.round(totalFee * 0.2 * 100) / 100, reason: `违约金=${totalFee}×20%`, penalty_rate: 0.2 }
      ],
      total_deduction: 0,
      refund: paidAmount,
      summary: "手动填写模式，请编辑下方扣费明细",
      contract_code: manualContract.contract_code || "",
    };
    
    // 直接进入步骤3（扣费明细编辑）
    workflowStep.value = 3;
    recalc(); // 确保总数同步
    toast("已进入扣费明细编辑", "请逐项填写或调整金额", "success");
  }

  /** 运行AI分析（自动触发） */
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
      
      const d = await postJ("/api/contract/analyze", {
        filepath: cPath.value,
        image_paths: uploadedFiles.value.map(f => f.filepath),
        exam_stage: examStage,
        training_hours: trainingHours,
        total_fee: manualContract.total_fee || 0,
        id_card: idCard,
        ticket_id: ticketId,
        exam_counts: currentQr?.exam_counts || {},  // 传入考试次数，辅助 AI 判断哪些费用已实际发生
      });

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
            if (priceMatch) ded.unit_price = priceMatch[1] + "元/学时";
          });
        }
        
        ar.value = d;
        
        // 强制同步：用明细之和覆盖 AI 返回的 total_deduction，避免不一致警告
        recalc();
        
        toast("分析完成", "应退 " + ar.value.refund + " 元", "success");
      }
    } catch (e) {
      if (aTimerId) { clearInterval(aTimerId); aTimerId = null; }
      aErr.value = "分析失败: " + e.message;
    } finally {
      aLoading.value = false;
    }
  }

  // 进入步骤2时自动触发AI分析（由 app.js 中的外部 watcher 负责调用 doAnalyze）
  // 本 composables 不重复注册 watcher，避免双重触发

  /** 确认AI分析结果 → 步骤3 */
  function confirmAnalysis() {
    if (!ar.value) {
      toast("请先进行AI分析", "", "warning");
      return;
    }
    workflowStep.value = 3;
    toast("退费结果已确认", "请生成回复函", "success");
  }

  // ── 扣费明细编辑 ──

  function recalc() {
    if (!ar.value) return;
    
    // 遍历所有扣费项，如果有时长和单价，自动重新计算金额
    ar.value.deductions.forEach(ded => {
      const durationStr = String(ded.duration || "").trim();
      const priceStr = String(ded.unit_price || "").trim();

      // 违约金：基数 × 比例% = 金额
      if (ded.item === '违约金') {
        const base = parseFloat(durationStr.replace(/[^\d.]/g, ""));
        const rateMatch = priceStr.match(/(\d+(?:\.\d+)?)/);
        const rate = rateMatch ? parseFloat(rateMatch[1]) : 0;
        if (base > 0 && rate > 0) {
          ded.amount = Math.round(base * rate / 100 * 100) / 100;
        }
        return;
      }
      
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
    ar.value.total_deduction = Math.round(sum * 100) / 100;
    const refund = (ar.value.total_fee || 0) - ar.value.total_deduction;
    ar.value.refund = refund > 0 ? Math.round(refund * 100) / 100 : 0;
  }

  function addDeduction() {
    if (!ar.value) return;
    ar.value.deductions.push({ item: "", amount: 0, reason: "" });
  }

  function removeDeduction(i) {
    if (!ar.value || !ar.value.deductions) return;
    ar.value.deductions.splice(i, 1);
    recalc();
  }

  // 更新违约金比例（用户手动修正）
  function updatePenaltyRate(newRate) {
    if (!ar.value) return;
    
    const rate = parseFloat(newRate);
    ar.value.penalty_rate = rate;
    
    // 找到违约金项目并更新
    const penaltyDed = ar.value.deductions.find(d => d.item === '违约金');
    if (penaltyDed) {
      const base = ar.value.total_fee || 0;
      const oldRate = (penaltyDed.reason.match(/(\d+(?:\.\d+)?)%/) || [null, '20'])[1];
      penaltyDed.amount = Math.round(base * rate * 100) / 100;
      penaltyDed.reason = `违约金=${base}元×${rate*100}%=${penaltyDed.amount}元（手动修正，原识别${oldRate}%）`;
    }
    
    // 重新计算总扣费和应退金额
    recalc();
    
    toast("违约金比例已更新", `当前比例: ${rate*100}%`, "success");
  }

  // ── 回复函 ──

  async function genReply(idCard, qr, ticketId, templateId) {
    rpLoading.value = true;
    rpErr.value = "";
    rpResult.value = null;

    try {
      const body = {
        ticket_id: ticketId,
        name: qr.name || "",
        id_card: idCard,
        school_short: qr.school_short || "",
        school_name: qr.school_name || "",
        registration_date: qr.registration_date || "",
        license_type: qr.license_type || "",
        exam_stage: qr.exam_stage || "",
        total_fee: ar.value?.total_fee || 0,
        deductions: ar.value?.deductions || [],
        total_deduction: ar.value?.total_deduction || 0,
        refund: ar.value?.refund || 0,
        contract_code: ar.value?.contract_code || "",
        training_hours: qr.training_hours || {},
        template_id: templateId || "",
      };
      const d = await postJ("/api/reply/generate", body);
      if (d.success) {
        rpResult.value = d;
        toast("回复函已生成", d.filename, "success");
        workflowStep.value = 4;
      } else {
        rpErr.value = d.error;
      }
    } catch (e) {
      rpErr.value = e.message;
    } finally {
      rpLoading.value = false;
    }
  }

  // ── 飞书 ──

  async function submitFeishu(data) {
    fsLoading.value = true;
    fsResult.value = null;

    try {
      const d = await postJ("/api/feishu/submit", data);
      if (d.success) {
        fsResult.value = d;
        toast("飞书提交成功", "编号: " + (d.handle_no || ""), "success");
      } else {
        toast("提交失败", d.error, "danger");
      }
    } catch (e) {
      toast("提交失败", e.message, "danger");
    } finally {
      fsLoading.value = false;
    }
  }

  // ── 回访管理 ──

  async function submitVisit(ticketId, doUpdateStatus) {
    if (!visitStatus.value) {
      toast("请选择回访状态", "", "warning");
      return;
    }
    if (!visitRemark.value.trim()) {
      toast("请填写回访备注", "", "warning");
      return;
    }
    visitLoading.value = true;
    try {
      const d = await postJ(`/api/tickets/${ticketId}/visit`, {
        visit_status: visitStatus.value,
        visit_remark: visitRemark.value,
      });
      if (d.success) {
        toast("回访已保存", "", "success");
        // 自动→已完结
        if (doUpdateStatus) {
          await doUpdateStatus("已完结");
        }
      } else {
        toast("保存失败", d.error, "danger");
      }
    } catch (e) {
      toast("保存失败", e.message, "danger");
    } finally {
      visitLoading.value = false;
    }
  }

  // ── 投诉登记表 ──

  async function genRegistrationForm(ticketId) {
    formLoading.value = true;
    formResult.value = null;
    try {
      const d = await postJ(`/api/tickets/${ticketId}/register-form`, {});
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
    ar.value = null;
    aErr.value = "";
    analysisProgress.value = 0;
    analysisElapsed.value = 0;
    if (aTimerId) { clearInterval(aTimerId); aTimerId = null; }
    rpResult.value = null;
    rpErr.value = "";
    fsResult.value = null;
    visitStatus.value = "";
    visitRemark.value = "";
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
    uploadedFiles.value = [];  // 清空已上传文件列表
    ar.value = null;
    rpLoading.value = false;
    rpErr.value = "";
    fsLoading.value = false;
    fsErr.value = "";
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
    rpLoading,
    rpResult,
    rpErr,
    fsLoading,
    fsResult,
    contractInput,
    uploadedFiles,
    dlContract,
    ulContract,
    confirmContract,
    doAnalyze,
    confirmAnalysis,
    recalc,
    addDeduction,
    removeDeduction,
    updatePenaltyRate,  // 导出违约金比例修正方法
    genReply,
    submitFeishu,
    // 回访
    visitStatus, visitRemark, visitLoading,
    formLoading, formResult,
    submitVisit, genRegistrationForm,
    reset,
    resetWorkflow,  // 导出重置方法，用于查询新学员时清空旧状态
  };
}
