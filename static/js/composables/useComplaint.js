// 投诉工单受理 + 三系统查询 组合式函数
import { getJ, postJ, uploadFile, checkError } from "api";
import { todayStr } from "helpers";

export function useComplaint() {
  // refs
  const fileInputRef = Vue.ref(null);
  const intakeTextareaRef = Vue.ref(null);

  // 拖拽状态
  const isDragOver = Vue.ref(false);

  // 受理表单
  const form = Vue.reactive({
    id_card: "",
    phone: "",
    source_channel: "交通部门",
    other_channel: "",
    complaint_date: todayStr(),
    complaint_type: "A",
  });

  // 受理文件上传
  const intakeFile = Vue.ref(null);
  const intakeLoading = Vue.ref(false);
  const intakeResult = Vue.ref(null);
  const intakeErr = Vue.ref("");

  // 三系统查询
  const querying = Vue.ref(false);
  const qErr = Vue.ref("");
  const qr = Vue.ref(null);

  // 查询进度 + 计时
  const queryProgress = Vue.reactive({
    show: false,
    percent: 0,
    done: false,
    hasError: false,
    message: "",
    elapsed: 0,
    _timer: null,
    items: {
      internal: { label: "内部系统", class: "source-pending", icon: "bi-circle" },
      third: { label: "第三系统", class: "source-pending", icon: "bi-circle" },
      driving: { label: "东莞驾培", class: "source-pending", icon: "bi-circle" },
    },
  });

  // 当前工单ID
  const currentTicketId = Vue.ref("");

  function resetQueryProgress() {
    queryProgress.show = true;
    queryProgress.percent = 0;
    queryProgress.done = false;
    queryProgress.hasError = false;
    queryProgress.elapsed = 0;
    queryProgress.message = "正在连接各系统...";
    // 启动计时器
    if (queryProgress._timer) clearInterval(queryProgress._timer);
    queryProgress._timer = setInterval(() => {
      if (!queryProgress.done) {
        queryProgress.elapsed++;
      }
    }, 1000);
    ["internal", "third", "driving"].forEach((k) => {
      queryProgress.items[k] = {
        label: queryProgress.items[k].label,
        class: "source-pending",
        icon: "bi-circle",
      };
    });
  }

  function updateQueryProgress(sources) {
    let completed = 0;
    let hasError = false;
    const statusTips = {
      pending: "等待查询",
      running: "正在查询...",
      success: "查询成功，有学员记录",
      not_found: "查询成功，但该系统无此学员记录",
      error: "查询失败（登录或网络异常）",
    };
    const statusMap = {
      pending: { class: "source-pending", icon: "bi-circle" },
      running: { class: "source-running", icon: "bi-arrow-repeat spin" },
      success: { class: "source-success", icon: "bi-check-circle-fill" },
      not_found: { class: "source-info", icon: "bi-info-circle-fill" },
      error: { class: "source-error", icon: "bi-x-circle-fill" },
    };
    for (const k in sources) {
      const s = sources[k];
      const st = statusMap[s] || statusMap.pending;
      queryProgress.items[k].class = st.class;
      queryProgress.items[k].icon = st.icon;
      queryProgress.items[k].tip = statusTips[s] || "";
      if (s === "success" || s === "not_found" || s === "error") completed++;
      if (s === "error") hasError = true;
    }
    queryProgress.percent = Math.round((completed / 3) * 100);
    queryProgress.hasError = hasError;
    if (completed === 3) {
      queryProgress.done = true;
      if (queryProgress._timer) clearInterval(queryProgress._timer);
      queryProgress.message = hasError ? "查询完成，部分系统访问失败" : "查询完成";
    }
  }

  // ── 文件上传处理 ──

  function triggerFileInput() {
    if (fileInputRef.value) {
      fileInputRef.value.click();
    }
  }

  // 拖拽事件
  function onDragOver(e) {
    e.preventDefault();
    e.stopPropagation();
    isDragOver.value = true;
  }
  function onDragEnter(e) {
    e.preventDefault();
    e.stopPropagation();
    isDragOver.value = true;
  }
  function onDragLeave(e) {
    e.preventDefault();
    e.stopPropagation();
    isDragOver.value = false;
  }
  function onDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    isDragOver.value = false;
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      handleIntakeFileInner(files[0]);
    }
  }

  // 从 input change 事件接收文件
  function onFileSelected(e) {
    const files = e.target.files;
    if (files && files.length > 0) {
      handleIntakeFileInner(files[0]);
    }
    // 重置 input 以便重复选择同一文件
    e.target.value = "";
  }

  async function handleIntakeFileInner(file) {
    // 校验文件类型
    const ext = file.name.split(".").pop().toLowerCase();
    const allowed = ["pdf", "png", "jpg", "jpeg", "docx", "xlsx", "txt"];
    if (!allowed.includes(ext)) {
      intakeErr.value = `不支持的文件格式: .${ext}，支持: ${allowed.join(", ")}`;
      return;
    }

    intakeFile.value = file;
    intakeLoading.value = true;
    intakeErr.value = "";
    intakeResult.value = null;

    try {
      const d = await uploadFile("/api/intake/parse", file);
      if (d.success && d.data) {
        intakeResult.value = d.data;
        // 自动填充表单
        if (d.data.id_card) form.id_card = d.data.id_card;
        if (d.data.source_channel) form.source_channel = d.data.source_channel;
        if (d.data.complaint_date) form.complaint_date = d.data.complaint_date;
        if (d.data.phone) form.phone = d.data.phone;

        // 如果身份证号已提取，自动触发查询
        if (d.data.id_card && d.data.id_card.length >= 7) {
          setTimeout(() => queryAll(), 500);
        }
      } else {
        intakeErr.value = d.error || "提取失败";
      }
    } catch (e) {
      intakeErr.value = e.message;
    } finally {
      intakeLoading.value = false;
    }
  }

  // 外部兼容接口
  async function handleIntakeFile(file) {
    handleIntakeFileInner(file);
  }

  // ── 直接文本输入提取 ──
  const intakeText = Vue.ref("");

  async function handleIntakeText() {
    const text = intakeText.value.trim();
    if (!text || text.length < 10) {
      intakeErr.value = "请输入有效的投诉内容（至少10个字符）";
      return;
    }

    // 将文本包装为 File 对象，复用已有的上传 + LLM 提取流程
    const file = new File([text], "paste.txt", { type: "text/plain" });
    await handleIntakeFileInner(file);
  }

  // ── 粘贴图片处理 ──
  function onIntakePaste(e) {
    const items = e.clipboardData.items;
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) {
          // 给文件一个合理的名字和扩展名
          const ext = file.type.split("/")[1] || "png";
          const renamed = new File([file], `paste_image.${ext}`, { type: file.type });
          handleIntakeFileInner(renamed);
        }
        return;
      }
    }
    // 不是图片，让默认粘贴行为继续（文本粘贴）
  }

  function onSourceChange() {
    if (form.source_channel !== '其他途径') {
      form.other_channel = '';
    }
  }

  function autoResizeTextarea(e) {
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }

  // ── 三系统查询 ──

  // ── 证件号校验 ──

  function validateIdCard(id) {
    /**
     * 只验证18位大陆身份证（GB 11643-1999 校验码算法）
     * 其他证件（居留证、港澳台等）不验证，直接放行交给系统判断
     */
    id = (id || "").trim().toUpperCase();
    if (!id) return { valid: false, msg: "证件号不能为空" };
    
    // 18位大陆身份证 → 校验真伪
    if (id.length === 18 && /^\d{17}[\dX]$/.test(id)) {
      const weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2];
      const checkCodes = ['1', '0', 'X', '9', '8', '7', '6', '5', '4', '3', '2'];
      let sum = 0;
      for (let i = 0; i < 17; i++) {
        sum += parseInt(id[i]) * weights[i];
      }
      const expected = checkCodes[sum % 11];
      if (id[17] !== expected) {
        return { valid: false, msg: "身份证号校验失败，请确认是否正确" };
      }
      return { valid: true };
    }
    
    // 其他所有证件 → 不验格式，直接放行
    if (id.length >= 7) {
      return { valid: true };
    }
    
    return { valid: false, msg: "证件号至少7位" };
  }

  async function queryAll() {
    const rawId = (form.id_card || "").trim();
    const rawPhone = (form.phone || "").trim().replace(/\D/g, "");

    let idToQuery = "";
    let methodLabel = "";

    if (rawId) {
      // 先校验证件号
      const check = validateIdCard(rawId);
      if (!check.valid) {
        qErr.value = check.msg;
        return;
      }
      idToQuery = rawId;
      methodLabel = "身份证号";
      form.id_card = rawId;
    } else if (rawPhone.length === 11) {
      idToQuery = rawPhone;
      methodLabel = "手机号";
      form.phone = rawPhone;
    } else if (!rawId && !rawPhone) {
      qErr.value = "请输入身份证号或手机号";
      return;
    } else {
      qErr.value = "手机号需11位";
      return;
    }

    querying.value = true;
    qErr.value = "";
    qr.value = null;
    resetQueryProgress();

    try {
      const payload = {
        ticket_id: currentTicketId.value,
        complaint_date: form.complaint_date,
        complaint_type: form.complaint_type,
        source_channel: form.source_channel,
      };
      if (methodLabel === "身份证号") payload.id_card = idToQuery;
      else payload.phone = idToQuery;

      const d = await postJ("/api/query", payload);

      if (d.error) {
        qErr.value = d.error;
        queryProgress.message = "查询失败: " + d.error;
        queryProgress.hasError = true;
      } else {
        qr.value = d;
        if (d.sources) updateQueryProgress(d.sources);
        queryProgress.done = true;
        if (queryProgress._timer) clearInterval(queryProgress._timer);
        queryProgress.percent = 100;
        // 检查是否查到学员
        const hasName = d.name && d.name.trim();
        if (!hasName) {
          qErr.value = `该${methodLabel}在三系统均未查到学员信息，请确认是否正确`;
          queryProgress.message = "未查到学员信息";
        } else {
          queryProgress.message = "查询完成";
        }
      }
    } catch (e) {
      qErr.value = "查询失败: " + e.message;
      queryProgress.message = "查询失败: " + e.message;
      queryProgress.hasError = true;
    } finally {
      querying.value = false;
      setTimeout(() => {
        queryProgress.show = false;
      }, 5000);
    }
  }

  function reset() {
    form.id_card = "";
    form.phone = "";
    intakeFile.value = null;
    intakeResult.value = null;
    intakeErr.value = "";
    intakeText.value = "";
    isDragOver.value = false;
    qr.value = null;
    qErr.value = "";
    currentTicketId.value = "";
  }

  return {
    form,
    fileInputRef,
    isDragOver,
    intakeFile,
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
    handleIntakeFile,
    queryAll,
    reset,
  };
}
