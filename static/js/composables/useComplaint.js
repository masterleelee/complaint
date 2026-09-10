// 投诉工单受理 + 三系统查询 组合式函数
import { getJ, postJ, uploadFile, checkError } from "api";
import { todayStr } from "helpers";

export function useComplaint(onAutoQueryDone = null) {
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
    handler_name: "",
    handler_user_id: null,  // 当前账号体系下拉选择处理人；null=未选择
  });

  // 可指派人列表（admin + handler，排除停用/viewer）
  const assignableUsers = Vue.ref([]);
  async function loadAssignableUsers() {
    try {
      const d = await getJ("/api/users/assignable");
      console.log('[useComplaint] assignable:', d);
      if (!d.error && Array.isArray(d.data)) assignableUsers.value = d.data;
    } catch (e) { /* 静默 */ }
  }

  // 受理文件上传
  const intakeFile = Vue.ref(null);
  const intakeLoading = Vue.ref(false);
  const intakeResult = Vue.ref(null);
  const intakeErr = Vue.ref("");

  // 自动回填后的输入框高亮（脉冲动画）
  const pulseIdCard = Vue.ref(false);
  const pulsePhone = Vue.ref(false);

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

  // 绑定工单的原始证件号/手机号（用于提交前判断是否换人，防止覆盖他人工单）
  const boundIdCard = Vue.ref("");
  const boundPhone = Vue.ref("");

  // 同日同人预检命中的既有工单：受理前让用户选择「并入」还是「另建新工单」，
  // 避免默认合并静默发生导致"查过却没在列表生成新单"的困惑
  const sameDayTicket = Vue.ref(null);

  // ── 三系统无信息学员 · 人工建案 ──
  // noMatchInfo: 三系统均查无时的引导状态 {eligible: 是否可转人工(无超时/异常), sources}
  const noMatchInfo = Vue.ref(null);
  const manualOpen = Vue.ref(false);
  const manualCreating = Vue.ref(false);
  const manualErr = Vue.ref("");
  const manualForm = Vue.reactive({
    student_name: "",
    id_card: "",
    phone: "",
    organization_unit_id: "",
    registration_date: "",
    license_type: "",
    complaint_content: "",
    complaint_demands: "",
    source_channel: "电话来访",
    complaint_date: todayStr(),
  });

  // ── 统一智能查询：新增字段 ──
  const studentName = Vue.ref("");
  const schoolShort = Vue.ref("全部");
  const regStart = Vue.ref("");
  const regEnd = Vue.ref("");

  // 候选学员列表
  const candidates = Vue.ref([]);
  const candWrapRef = Vue.ref(null);
  const searching = Vue.ref(false);
  const searchElapsed = Vue.ref(0);
  const searchSource = Vue.ref("");
  const candEmpty = Vue.ref(false);
  const selectedCand = Vue.ref(null);
  // 三系统进度卡右上角「选中：姓名（报名点）」——记录当前查询对象。
  const queryTarget = Vue.ref(null);  // { name, school_short } 或 null
  const candTotal = Vue.ref(0);
  const candPage = Vue.ref(1);
  const CAND_PAGE_SIZE = 10;
  const candTotalPages = Vue.computed(() => Math.max(1, Math.ceil(candTotal.value / CAND_PAGE_SIZE)));
  const orgFallback = Vue.ref(false);

  // 报名点下拉（真实机构字典，来自 /api/organization-units）
  const orgOptions = Vue.ref([]);
  const orgUnitsFull = Vue.ref([]);
  getJ("/api/organization-units").then((d) => {
    if (d.success) {
      orgUnitsFull.value = (d.data || []).filter((u) => u.active !== false);
      orgOptions.value = orgUnitsFull.value.map((u) => u.name).filter(Boolean);
    }
  }).catch(() => {});

  // 提醒 / 成功
  const phoneMismatch = Vue.ref("");
  const residencyTip = Vue.ref("");
  const successBar = Vue.ref(false);
  // 步骤 2b 命中但姓名不一致时由后端打上 name_mismatch=true；
  // 前端以红字提示「姓名不一致，请人工核验」
  const nameMismatch = Vue.ref("");
  const phoneCandidates = Vue.ref([]);
  // queryAll 轮询竞态护栏：每次 queryAll 入口 ++queryToken，
  // 旧轮询回调检测到 token 不一致则 return，避免覆盖用户后续 queryAll/searchStudents 的结果
  const queryToken = Vue.ref(0);
  let pulseTimer = null;
  function flashPulseFields() {
    if (pulseTimer) clearTimeout(pulseTimer);
    pulseIdCard.value = true;
    pulsePhone.value = true;
    pulseTimer = setTimeout(() => {
      pulseIdCard.value = false;
      pulsePhone.value = false;
      pulseTimer = null;
    }, 2200);
  }

  function clearTransient() {
    qErr.value = "";
    phoneMismatch.value = "";
    residencyTip.value = "";
    nameMismatch.value = "";
    phoneCandidates.value = [];
    successBar.value = false;
    noMatchInfo.value = null;
    candidates.value = [];
    candEmpty.value = false;
    searchSource.value = "";
    searchElapsed.value = 0;
    sameDayTicket.value = null;
  }

  function maskLocalPhone(p) {
    const s = String(p || "");
    if (s.length >= 7) return s.slice(0, 3) + "****" + s.slice(-4);
    return s;
  }
  function examStageClass(c) {
    const s = c.exam_stage || c.student_status || "";
    if (/已报名|未培训/.test(s)) return "reg";
    if (/科目一|科一/.test(s)) return "s1";
    if (/科目二|科二/.test(s)) return "s2";
    if (/科目三|科三/.test(s)) return "s3";
    if (/已拿证|已结业|科四|毕业/.test(s)) return "done";
    return "reg";
  }

  // 路由判定：身份证 > 手机号(11位) > 姓名
  function routeMode() {
    const id = (form.id_card || "").trim();
    const ph = (form.phone || "").trim().replace(/\D/g, "");
    const nm = (studentName.value || "").trim();
    if (id) return "id";
    if (ph.length === 11) return "phone";
    if (nm) return "name";
    return "none";
  }
  const queryPrimary = Vue.computed(() => {
    const m = routeMode();
    if (m === "id") return "id";
    if (m === "phone") return "phone";
    return "";
  });
  function qFieldClass(field) {
    const p = queryPrimary.value;
    if (!p) return "";
    return p === field ? "is-primary" : "is-dim";
  }
  const mainBtnText = Vue.computed(() => {
    const m = routeMode();
    if (m === "id" || m === "phone") return "查询三系统并建案";
    if (m === "name") return "搜索学员";
    return "查询三系统并建案";
  });

  async function searchStudents(page = 1) {
    queryToken.value++;
    if (page === 1) clearTransient();
    searching.value = true;
    candEmpty.value = false;
    candidates.value = [];
    orgFallback.value = false;
    phoneMismatch.value = "";
    residencyTip.value = "";
    selectedCand.value = null;
    queryTarget.value = null;
    const t0 = Date.now();
    try {
      const d = await postJ("/api/students/search", {
        name: studentName.value.trim(),
        school_short: schoolShort.value === "全部" ? "" : schoolShort.value,
        start_date: regStart.value,
        end_date: regEnd.value,
        page,
        // 内部系统 0 命中时，回落到第三系统按姓名检索（弥补「内部无档案但第三系统有」的缺口）。
        // 服务端仅在内部系统 0 结果时才真正发请求，正常情况下不增加第三系统负载。
        include_third: true,
      });
      if (!d.success) {
        qErr.value = d.error || "搜索失败";
        return;
      }
      const list = (d.data && d.data.students) || [];
      candidates.value = list;
      candTotal.value = (d.data && d.data.total) || list.length;
      candPage.value = page;
      orgFallback.value = !!(d.data && d.data.org_fallback);
      searchElapsed.value = d.data.elapsed_ms || (Date.now() - t0);
      searchSource.value = `内部系统 · ${searchElapsed.value}ms`;
      candEmpty.value = list.length === 0;
      successBar.value = false;
      // 候选若来自第三系统（内部系统 0 命中后的回落），在来源标签上如实标注来源。
      const ti = (d.data && d.data.third) || {};
      if (ti.queried && ti.found) {
        const srcs = Array.from(new Set(list.map((s) => s.source).filter(Boolean)));
        searchSource.value = `${srcs.join(" + ")} · ${searchElapsed.value}ms`;
      } else if (ti.queried && ti.error) {
        searchSource.value = `内部系统 · 第三系统不可用（${ti.error}）`;
      }
      // 查到结果后自动跳转到结果列表，避免列表被悬浮操作栏遮挡或留在首屏之外
      if (list.length) {
        Vue.nextTick(() => {
          if (candWrapRef.value) candWrapRef.value.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      }
    } catch (e) {
      qErr.value = e.message;
      candTotal.value = 0;
      candPage.value = 1;
      searchSource.value = "";
      orgFallback.value = false;
    } finally {
      searching.value = false;
    }
  }

  function gotoCandPage(p) {
    if (searching.value || p < 1 || p > candTotalPages.value || p === candPage.value) return;
    searchStudents(p);
  }

  function chooseCandidate(i) {
    const c = candidates.value[i];
    if (!c) return;
    selectedCand.value = i;
    form.id_card = c.id_card || "";
    form.phone = c.phone || "";
    studentName.value = c.student_name || "";
    queryTarget.value = { name: c.student_name || c.name || "", school_short: c.school_short || "" };
    runExactQuery();
  }

  async function runExactQuery(forceNew = false, skipSameDayCheck = false) {
    clearTransient();
    // 居留证自动补 F 前缀（如 1249468(8) -> F1249468(8)）
    const rawId = (form.id_card || "").trim();
    if (rawId && /^\d{6,17}[（(]\d{1,4}[)）]$/.test(rawId)) {
      form.id_card = "F" + rawId;
      residencyTip.value = `已按补全后的证件号 ${form.id_card} 查询（港澳居留证自动补 F 前缀）`;
    }
    const mode = routeMode();
    candEmpty.value = false;
    await queryAll(forceNew, skipSameDayCheck);
    if (qr.value && qr.value.name) {
      successBar.value = true;
      if (mode === "phone" && qr.value.phone) {
        const reg = String(qr.value.phone).replace(/\D/g, "");
        const typed = String(form.phone).replace(/\D/g, "");
        if (reg && typed && reg !== typed) {
          phoneMismatch.value = `系统登记手机号为 ${maskLocalPhone(qr.value.phone)}，与你所填不同`;
        }
      }
      setTimeout(() => {
        if (onAutoQueryDone) onAutoQueryDone();
      }, 1500);
    }
  }

  // 同日同人预检：与 save_ticket 去重同一口径；失败不阻塞正常受理
  async function checkSameDayTicket() {
    try {
      const d = await postJ("/api/tickets/same-day-check", {
        id_card: form.id_card.trim(),
        complaint_date: form.complaint_date,
      });
      return d.success ? (d.data || null) : null;
    } catch (e) {
      return null;
    }
  }

  async function chooseMergeExisting() {
    const hit = sameDayTicket.value;
    sameDayTicket.value = null;
    // 兜底：预检横幅缺失或无工单ID时，回退到原有全量查询路径
    if (!hit || !hit.id) {
      await runExactQuery(false, true);
      return;
    }
    const concreteChannel = form.source_channel === "其他途径"
      ? form.other_channel.trim()
      : form.source_channel;
    if (!concreteChannel) {
      qErr.value = "请填写具体投诉渠道";
      return;
    }
    // 快路径：复用既有工单的三系统查询档案（同日学员信息不会变化），不重查三系统
    querying.value = true;
    qErr.value = "";
    qr.value = null;
    try {
      const payload = {
        ticket_id: hit.id,
        id_card: (form.id_card || "").trim(),
        complaint_date: form.complaint_date,
        complaint_type: form.complaint_type,
        source_channel: concreteChannel,
        handler_name: form.handler_name.trim(),
        handler_user_id: form.handler_user_id || null,
        complaint_desc: (intakeText.value || "").trim(),
        complaint_summary: (intakeResult.value && intakeResult.value.complaint_summary) || "",
        complaint_demands: (intakeResult.value && intakeResult.value.complaint_demands) || "",
        attachments: intakeResult.value?._filepath ? [{
          _filepath: intakeResult.value._filepath,
          _filename: intakeResult.value._filename || "",
        }] : [],
      };
      const d = await postJ("/api/tickets/merge-existing", payload);
      if (!d.success) throw new Error(d.error || "并入失败");
      qr.value = d.data;
      currentTicketId.value = (d.data && d.data.ticket_id) || hit.id;
      successBar.value = true;
      setTimeout(() => {
        if (onAutoQueryDone) onAutoQueryDone();
      }, 1500);
    } catch (e) {
      // 并入失败（档案缺失/校验不一致等）：回退到原有三系统全量查询路径，行为与旧版一致
      await runExactQuery(false, true);
    } finally {
      querying.value = false;
    }
  }
  async function createNewAnyway() {
    sameDayTicket.value = null;
    await runExactQuery(true);
  }
  function dismissSameDayPrompt() {
    sameDayTicket.value = null;
  }

  async function onMainClick() {
    clearTransient();
    const m = routeMode();
    if (m === "id" || m === "phone") {
      // 手动按证件号/手机号查询：记录查询对象供三系统进度卡展示「选中：姓名（报名点）」。
      queryTarget.value = {
        name: studentName.value.trim(),
        school_short: schoolShort.value === "全部" ? "" : schoolShort.value,
      };
      await runExactQuery();
    } else if (m === "name") {
      // 姓名候选检索不是三系统查询：不显示三系统进度卡，避免「候选学员」与
      // 「三系统进度」两个横条同时出现（demo 优化方案）。
      queryProgress.show = false;
      if (queryProgress._timer) { clearInterval(queryProgress._timer); queryProgress._timer = null; }
      await searchStudents();
    } else {
      qErr.value = "请填写查询条件";
    }
  }

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
        duration: "",
      };
    });
  }

  function updateQueryProgress(sources, durations = {}) {
    let completed = 0;
    let hasError = false;
    const statusTips = {
      pending: "等待查询",
      running: "正在查询...",
      success: "查询成功，有学员记录",
      not_found: "查询成功，但该系统无此学员记录",
      no_contract: "2024年3月15日前报名，东莞驾培无电子合同",
      profile: "查到学员档案，无培训学时记录",
      error: "查询失败（登录或网络异常）",
      timeout: "查询超时，可稍后重试或只查看已返回系统结果",
    };
    const statusMap = {
      pending: { class: "source-pending", icon: "bi-circle" },
      running: { class: "source-running", icon: "bi-arrow-repeat spin" },
      success: { class: "source-success", icon: "bi-check-circle-fill" },
      profile: { class: "source-warning", icon: "bi-folder-check" },
      not_found: { class: "source-info", icon: "bi-info-circle-fill" },
      no_contract: { class: "source-info", icon: "bi-dash-circle" },
      error: { class: "source-error", icon: "bi-x-circle-fill" },
      timeout: { class: "source-error", icon: "bi-clock-fill" },
    };
    for (const k in sources) {
      // 后端 sources 可能带非进度卡键（如 third_profile 档案子状态），
      // 只处理已在 resetQueryProgress 初始化的 internal/third/driving。
      if (!queryProgress.items[k]) continue;
      const s = sources[k];
      const st = statusMap[s] || statusMap.pending;
      queryProgress.items[k].class = st.class;
      queryProgress.items[k].icon = st.icon;
      const ms = durations[k] || 0;
      queryProgress.items[k].duration = ms ? `${(ms / 1000).toFixed(1)}s` : "";
      queryProgress.items[k].tip = `${statusTips[s] || ""}${ms ? `，耗时 ${(ms / 1000).toFixed(1)} 秒` : ""}`;
      if (s === "success" || s === "not_found" || s === "no_contract" || s === "profile" || s === "error" || s === "timeout") completed++;
      if (s === "error" || s === "timeout") hasError = true;
    }
    queryProgress.percent = Math.round((completed / 3) * 100);
    queryProgress.hasError = hasError;
    if (completed === 3) {
      queryProgress.done = true;
      if (queryProgress._timer) clearInterval(queryProgress._timer);
      queryProgress.message = hasError ? "查询完成，部分系统访问失败" : "查询完成";
    } else {
      const labels = { internal: "内部系统", third: "第三系统", driving: "东莞驾培" };
      const runningLabels = Object.entries(sources)
        .filter(([, status]) => status === "pending" || status === "running")
        .map(([key]) => labels[key]);
      queryProgress.message = completed
        ? `已返回 ${completed}/3，仍在查询${runningLabels.join("、")}`
        : `正在查询${runningLabels.join("、")}`;
    }
  }

  function sourceStatusText(system, status) {
    if (status === "pending") return "等待查询";
    if (status === "running") return "查询中";
    if (status === "error") return "查询失败";
    if (status === "timeout") return "查询超时";
    if (status === "no_contract") return "无合同";
    if (status === "not_found") return system === "third" ? "无学时记录" : "无学员记录";
    // 第三系统「学员申请登记」档案命中、但阶段审核无学时 → 橙
    if (status === "profile") return system === "third" ? "查到档案·无学时" : "有档案无学时";
    if (status !== "success") return "状态未知";
    if (system === "internal") return "查到数据";
    if (system === "third") return "查到档案与学时";
    return "查到学员";
  }

  // 判定东莞驾培是否确认无电子合同；返回原因文案，空串表示可尝试下载
  function noEContractReason(qr) {
    const r = qr || {};
    const driving = r.sources?.driving;
    if (driving === "not_found") return "东莞驾培查无该学员记录，无电子合同";
    if (driving === "no_contract") return "该学员2024年3月15日前报名，东莞驾培无电子合同";
    if (driving === "success" && r.contract_checked && !r.contract_available) return "东莞驾培确认该学员无电子合同";
    const reg = String(r.registration_date || "").replace(/\//g, "-");
    if (/^\d{4}-\d{2}-\d{2}/.test(reg) && reg.slice(0, 10) < "2024-03-15") return "该学员2024年3月15日前报名，东莞驾培无电子合同";
    return "";
  }

  // 驾培合同标记：初始查询已用 checkContract 快速判定，成功时显示「有合同/无合同」
  function drivingContractTag(qr) {
    const r = qr || {};
    if (r.sources?.driving !== "success") return "";
    if (!r.contract_checked) return "";
    return r.contract_available ? "有合同" : "无合同";
  }

  function sourceStatusColor(status) {
    if (status === "success") return "var(--green)";
    if (status === "profile") return "var(--color-warning)";
    if (status === "pending" || status === "running") return "var(--gray-500)";
    if (status === "not_found" || status === "no_contract") return "var(--gray-600)";
    return "#dc2626";
  }

  function sourceStatusIcon(status) {
    if (status === "success") return "bi-check-circle-fill";
    if (status === "profile") return "bi-folder-check";
    if (status === "running") return "bi-arrow-repeat spin";
    if (status === "pending") return "bi-circle";
    if (status === "timeout") return "bi-clock-fill";
    if (status === "error") return "bi-x-circle-fill";
    if (status === "no_contract") return "bi-x-circle-fill";
    if (status === "not_found") return "bi-x-circle-fill";
    return "bi-info-circle-fill";
  }

  // ① 三系统查询卡片右上角徽章：
  //   ok(绿)=查到数据 / warn(橙)=第三系统有档案无学时 / na-err(红)=其余（查无/无合同/失败/查询中）
  function sourcePillClass(status) {
    if (status === "success") return "ok";
    if (status === "profile") return "warn";
    return "na-err";
  }

  // 来源查询耗时明细：登录等待/查询/重试次数，及错误原因
  function phaseDetail(system, phases, retries, error) {
    if (!phases) return "";
    const parts = [];
    if (phases.auth_wait) parts.push(`登录 ${(phases.auth_wait / 1000).toFixed(1)}s`);
    if (phases.lookup) parts.push(`查询 ${(phases.lookup / 1000).toFixed(1)}s`);
    if (phases.student_list) parts.push(`学员 ${(phases.student_list / 1000).toFixed(1)}s`);
    if (retries > 0) parts.push(`重试${retries}次`);
    let detail = parts.join(" · ");
    if (error) detail = detail ? `${detail} · ${error}` : error;
    return detail;
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
        applyIntakeResult(d.data);
      } else {
        intakeErr.value = d.error || "提取失败";
      }
    } catch (e) {
      intakeErr.value = e.message;
    } finally {
      intakeLoading.value = false;
    }
  }

  // 提取结果统一处理：回填线索 → 有证号/手机号直接查三系统；否则用姓名自动模糊搜索
  function applyIntakeResult(data) {
    intakeResult.value = data;
    // 重新提取新材料时，清掉上一位学员的候选列表与提示，避免旧数据残留误导
    candidates.value = [];
    selectedCand.value = null;
    queryTarget.value = null;
    candTotal.value = 0;
    candPage.value = 1;
    candEmpty.value = false;
    orgFallback.value = false;
    sameDayTicket.value = null;
    clearTransient();
    if (data.id_card) { form.id_card = data.id_card; }
    if (data.phone) { form.phone = data.phone; }
    if (data.id_card || data.phone) flashPulseFields();
    if (data.student_name) studentName.value = data.student_name;

    const phoneDigits = (data.phone || "").replace(/\D/g, "");
    if ((data.id_card && data.id_card.length >= 7) || phoneDigits.length >= 7) {
      setTimeout(async () => {
        await queryAll();
        if (onAutoQueryDone) onAutoQueryDone();
      }, 500);
    } else if (data.student_name) {
      // AI 仅识别到姓名（无证件号/手机号）→ 姓名候选检索，不弹三系统进度卡。
      queryProgress.show = false;
      if (queryProgress._timer) { clearInterval(queryProgress._timer); queryProgress._timer = null; }
      searchStudents();
    } else {
      intakeErr.value = data.ai_error
        ? `AI 提取失败：${data.ai_error}，请手动填写姓名或证件号`
        : "未能自动识别证件号和手机号，请手动填写后再查询";
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

    intakeLoading.value = true;
    intakeErr.value = "";
    intakeResult.value = null;
    try {
      const d = await postJ("/api/intake/parse", { text });
      if (d.success && d.data) {
        applyIntakeResult(d.data);
      } else {
        intakeErr.value = d.error || "提取失败";
      }
    } catch (e) {
      intakeErr.value = e.message;
    } finally {
      intakeLoading.value = false;
    }
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
     * 其他证件仅放行已知格式白名单（居留证、外国人永久居留证等），与服务端口径一致
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
    
    // 非18位：与服务端白名单口径一致（前缀≤3大写字母 + 6~17位数字 + 可选括号尾注，
    // 兼容居留证 F1249468(8)、外国人永久居留证），拦截明显乱码
    if (/^[A-Z]{0,3}\d{6,17}(?:[（(]\d{1,4}[)）])?$/.test(id)) {
      return { valid: true };
    }

    return { valid: false, msg: "证件号格式不正确" };
  }

  async function queryAll(forceNew = false, skipSameDayCheck = false) {
    const rawId = (form.id_card || "").trim();
    const rawPhone = (form.phone || "").trim().replace(/\D/g, "");
    const concreteChannel = form.source_channel === "其他途径"
      ? form.other_channel.trim()
      : form.source_channel;

    const myToken = ++queryToken.value;

    if (!concreteChannel) {
      qErr.value = "请填写具体投诉渠道";
      return;
    }

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

    // 同日同人预检：已绑定工单（restore 场景，走显式更新）、用户选择另建或确认并入时跳过
    // （并入由服务端 save_ticket 按同日口径合并）；命中则暂停受理并展示选择横幅，
    // 由用户决定「并入」或「另建新工单」
    if (!skipSameDayCheck && !forceNew && !currentTicketId.value && form.id_card.trim()) {
      const hit = await checkSameDayTicket();
      if (hit) {
        sameDayTicket.value = hit;
        return;
      }
    }
    sameDayTicket.value = null;

    querying.value = true;
    qErr.value = "";
    qr.value = null;
    resetQueryProgress();

    // P1-1：若当前绑定了旧工单，但本次查询的学员与绑定工单原证件号/手机号不一致，
    // 说明用户在「新增投诉」页改成了另一名学员，必须新建工单，避免把他人数据写回旧工单。
    if (currentTicketId.value && (boundIdCard.value || boundPhone.value)) {
      const fId = (form.id_card || "").trim().toUpperCase();
      const fPhone = (form.phone || "").trim();
      const bId = (boundIdCard.value || "").trim().toUpperCase();
      const bPhone = (boundPhone.value || "").trim();
      const idMatch = fId && bId && fId === bId;
      const phoneMatch = fPhone && bPhone && fPhone === bPhone;
      if (!idMatch && !phoneMatch) {
        currentTicketId.value = "";
      }
    }

    try {
      const payload = {
        ticket_id: currentTicketId.value,
        complaint_date: form.complaint_date,
        complaint_type: form.complaint_type,
        source_channel: concreteChannel,
        handler_name: form.handler_name.trim(),
        handler_user_id: form.handler_user_id || null,
        complaint_desc: (intakeText.value || "").trim(),
        complaint_summary: (intakeResult.value && intakeResult.value.complaint_summary) || "",
        complaint_demands: (intakeResult.value && intakeResult.value.complaint_demands) || "",
        force_new: !!forceNew,
        attachments: intakeResult.value?._filepath ? [{
          _filepath: intakeResult.value._filepath,
          _filename: intakeResult.value._filename || "",
        }] : [],
      };
      // 仅在有姓名时透传给后端，后端用其做 2a 步骤的姓名一致性校验
      // （姓名不匹配仍返回结果但打 name_mismatch 标记，前端红字提示人工核验）
      const expectedName = (studentName.value || "").trim();
      if (expectedName) payload.student_name = expectedName;
      if (methodLabel === "身份证号") { payload.id_card = idToQuery; payload.phone = rawPhone; }
      else payload.phone = idToQuery;

      const started = await postJ("/api/query/start", payload);
      if (!started.success) throw new Error(started.error || "启动查询失败");

      let d = null;
      for (let i = 0; i < 120; i++) {
        if (queryToken.value !== myToken) return;
        const status = await getJ(`/api/query/status/${started.job_id}`);
        if (!status.success) throw new Error(status.error || "读取查询进度失败");
        if (status.sources) {
          updateQueryProgress(status.sources, status.query_durations_ms || {});
        }
        if (status.result) {
          qr.value = status.result;
          currentTicketId.value = status.result.ticket_id || currentTicketId.value;
        }
        if (status.status === "failed") {
          // 后端按 sources 状态给出 outcome：no_match（明确查无）/ undetermined（含失败、超时、未查询）
          // 前端唯一通道原则：明确查无只展示黄色引导条；无法判定只展示红色错误条，两者互斥。
          if (status.no_match) {
            const outcome = status.match_outcome || "no_match";
            const cleanedSources = Object.fromEntries(
              Object.entries(status.sources || {}).filter(([, v]) => v && v !== "pending")
            );
            const hasName = (studentName.value || "").trim();
            const isPhonePath = (form.phone || "").replace(/\D/g, "").length >= 11;
            // phone_not_found：后端确认「手机号这条路径查过了、确实没查到」。
            // 此时 third/driving 因缺证号被记为 not_queried，outcome 判为 undetermined，
            // 语义上是对的（不能说查无此人），但姓名降级分支此前卡在 outcome==="no_match"
            // 上永远进不去。改用这个独立信号驱动降级。
            const phoneNotFound = !!status.phone_not_found;

            // 姓名降级：手机号查无 + 有姓名 → 用姓名到内部系统 + 第三系统找候选
            const degradeToNameSearch = () => {
              noMatchInfo.value = null;
              queryProgress.show = false;
              qErr.value = "";
              queryProgress.message = "手机号未查到，已改用姓名检索内部系统与第三系统…";
              searchStudents();
            };

            if (outcome === "no_match") {
              noMatchInfo.value = {
                eligible: !!status.manual_intake_eligible,
                sources: cleanedSources,
              };
              qErr.value = "";
              queryProgress.message = status.error || "未匹配到学员档案";
              // 手机号路径 + 有姓名 + 明确查无：自动降级到姓名搜索
              if (hasName && isPhonePath) {
                degradeToNameSearch();
              }
            } else if (phoneNotFound && hasName && isPhonePath) {
              // 已确认手机号查无，但第三系统/驾培因无身份证号未查询 —— 此时不能直接判
              // 「查无此人」（测试学员甲就是典型：内部与驾培均无，仅第三系统可查），
              // 改用姓名回落到第三系统检索。
              degradeToNameSearch();
            } else {
              // undetermined / ambiguous：红色错误，不展示「转人工建案」
              noMatchInfo.value = null;
              qErr.value = status.error || "未能确定学员身份，请重新查询";
              queryProgress.message = status.error || "查询失败";
            }
            return;
          }
          throw new Error(status.error || "三系统查询失败");
        }
        if (status.status === "done") {
          d = status.result;
          break;
        }
        await new Promise(resolve => setTimeout(resolve, 500));
        if (queryToken.value !== myToken) return;
      }
      if (!d) throw new Error("三系统查询超时，请稍后重试");

      currentTicketId.value = d.ticket_id || currentTicketId.value;

      if (d.error) {
        qErr.value = d.error;
        queryProgress.message = "查询失败: " + d.error;
        queryProgress.hasError = true;
      } else {
        qr.value = d;
        // 摘要已改为查询阶段与爬虫并行生成，完成后回填受理区横幅
        if (d.complaint_summary && intakeResult.value) intakeResult.value.complaint_summary = d.complaint_summary;
        if (d.complaint_demands && intakeResult.value) intakeResult.value.complaint_demands = d.complaint_demands;
        if (d.sources) updateQueryProgress(d.sources, d.query_durations_ms || {});
        queryProgress.done = true;
        if (queryProgress._timer) clearInterval(queryProgress._timer);
        queryProgress.percent = 100;
        // 步骤 2b 后端标记的姓名不一致 → 前端红字提示人工核验
        if (d.name_mismatch) {
          nameMismatch.value = d.name_mismatch_reason || "姓名不一致，请人工核验";
        }
        // 步骤 2b 返回多个候选 → 走前端候选列表
        if (Array.isArray(d.candidates) && d.candidates.length) {
          phoneCandidates.value = d.candidates;
          qErr.value = d.error || "手机号匹配到多个学员，请人工选择";
          queryProgress.message = "已匹配多个学员，请选择";
          return;
        }
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
      noMatchInfo.value = null;
      if (queryProgress._timer) { clearInterval(queryProgress._timer); queryProgress._timer = null; }
    } finally {
      querying.value = false;
      setTimeout(() => {
        queryProgress.show = false;
      }, 5000);
    }
  }

  // ── 三系统无信息 · 人工建案 ──
  function dismissNoMatch() { noMatchInfo.value = null; }

  function openManualIntake() {
    manualErr.value = "";
    manualForm.student_name = studentName.value || "";
    manualForm.id_card = form.id_card || "";
    manualForm.phone = form.phone || "";
    manualForm.organization_unit_id = "";
    manualForm.registration_date = "";
    manualForm.license_type = "";
    if (!manualForm.complaint_content.trim()) {
      manualForm.complaint_content = (intakeResult.value && intakeResult.value.complaint_summary)
        || (intakeText.value || "").trim();
    }
    manualOpen.value = true;
  }

  function closeManualIntake() { manualOpen.value = false; }

  async function submitManualIntake() {
    if (manualCreating.value) return;
    manualErr.value = "";
    const missing = [];
    if (!manualForm.student_name.trim()) missing.push("学员姓名");
    if (!manualForm.id_card.trim()) missing.push("身份证号");
    if (!manualForm.organization_unit_id) missing.push("所属分校/分店");
    if (!manualForm.registration_date) missing.push("报名时间");
    if (missing.length) {
      manualErr.value = "缺少必填项：" + missing.join("、");
      return;
    }
    manualCreating.value = true;
    try {
      const resp = await postJ("/api/tickets/manual-create", {
        ...manualForm,
        student_name: manualForm.student_name.trim(),
        id_card: manualForm.id_card.trim(),
        phone: manualForm.phone.trim(),
        complaint_content: manualForm.complaint_content.trim(),
        complaint_demands: manualForm.complaint_demands.trim(),
        complaint_date: form.complaint_date || todayStr(),
        handler_name: form.handler_name || "",
        handler_user_id: form.handler_user_id || null,
      });
      if (!resp.success) throw new Error(resp.error || "人工建案失败");
      currentTicketId.value = resp.data.ticket_id || "";
      manualOpen.value = false;
      noMatchInfo.value = null;
      successBar.value = true;
      // 与三系统建案成功走同一收尾：刷新列表 + 打开工作台
      if (onAutoQueryDone) await onAutoQueryDone();
    } catch (e) {
      manualErr.value = e.message || "人工建案失败";
    } finally {
      manualCreating.value = false;
    }
  }

  function restore(ticket) {
    clearTransient();
    if (queryProgress._timer) { clearInterval(queryProgress._timer); queryProgress._timer = null; }
    if (pulseTimer) { clearTimeout(pulseTimer); pulseTimer = null; pulseIdCard.value = false; pulsePhone.value = false; }
    const knownChannels = ["12345", "交通部门", "电话来访", "信访", "邮件投诉", "驾培协会"];
    const savedChannel = ticket.source_channel || "交通部门";
    form.id_card = ticket.id_card || "";
    form.phone = ticket.phone || "";
    form.source_channel = knownChannels.includes(savedChannel) ? savedChannel : "其他途径";
    form.other_channel = knownChannels.includes(savedChannel) ? "" : savedChannel;
    form.complaint_date = (ticket.complaint_date || todayStr()).slice(0, 10);
    form.complaint_type = ticket.complaint_type || "A";
    form.handler_name = ticket.handler_name || "";
    form.handler_user_id = ticket.handler_user_id || null;
    studentName.value = ticket.student_name || ticket.query_result?.name || "";
    schoolShort.value = "全部";
    regStart.value = "";
    regEnd.value = "";
    candidates.value = [];
    candEmpty.value = false;
    selectedCand.value = null;
    queryTarget.value = null;
    candTotal.value = 0;
    candPage.value = 1;
    orgFallback.value = false;
    phoneMismatch.value = "";
    residencyTip.value = "";
    successBar.value = false;
    intakeResult.value = Array.isArray(ticket.attachments) && ticket.attachments.length
      ? ticket.attachments[0]
      : null;
    currentTicketId.value = ticket.id || "";
    boundIdCard.value = ticket.id_card || "";
    boundPhone.value = ticket.phone || "";
    qr.value = {
      ...(ticket.query_result || {}),
      ticket_id: ticket.id || "",
      name: ticket.student_name || ticket.query_result?.name || "",
      id_card: ticket.id_card || ticket.query_result?.id_card || "",
      phone: ticket.phone || ticket.query_result?.phone || "",
      license_type: ticket.license_type || ticket.query_result?.license_type || "",
      school_name: ticket.school_name || ticket.query_result?.school_name || "",
      school_short: ticket.school_short || ticket.query_result?.school_short || "",
      registration_date: ticket.registration_date || ticket.query_result?.registration_date || "",
      exam_stage: ticket.exam_stage || ticket.query_result?.exam_stage || "",
      student_status: ticket.student_status || ticket.query_result?.student_status || "",
      training_hours: ticket.training_hours || ticket.query_result?.training_hours || {},
    };
    qErr.value = qr.value.error || "";
  }

  function reset(currentUser) {
    clearTransient();
    if (queryProgress._timer) { clearInterval(queryProgress._timer); queryProgress._timer = null; }
    if (pulseTimer) { clearTimeout(pulseTimer); pulseTimer = null; pulseIdCard.value = false; pulsePhone.value = false; }
    form.id_card = "";
    form.phone = "";
    form.source_channel = "交通部门";
    form.other_channel = "";
    form.complaint_date = todayStr();
    form.complaint_type = "A";
    // 处理人默认当前登录用户（仅 admin/handler；viewer 不进表单）
    if (currentUser && currentUser.id && currentUser.role !== 'viewer') {
      form.handler_user_id = currentUser.id;
      form.handler_name = currentUser.real_name || currentUser.username;
    } else {
      form.handler_user_id = null;
      form.handler_name = "";
    }
    studentName.value = "";
    schoolShort.value = "全部";
    regStart.value = "";
    regEnd.value = "";
    candidates.value = [];
    candEmpty.value = false;
    selectedCand.value = null;
    queryTarget.value = null;
    candTotal.value = 0;
    candPage.value = 1;
    orgFallback.value = false;
    phoneMismatch.value = "";
    residencyTip.value = "";
    successBar.value = false;
    intakeFile.value = null;
    intakeResult.value = null;
    intakeErr.value = "";
    intakeText.value = "";
    isDragOver.value = false;
    qr.value = null;
    qErr.value = "";
    currentTicketId.value = "";
    boundIdCard.value = "";
    boundPhone.value = "";
    sameDayTicket.value = null;
    noMatchInfo.value = null;
    manualOpen.value = false;
    manualErr.value = "";
  }

  return {
    form,
    fileInputRef,
    isDragOver,
    intakeFile,
    intakeLoading,
    intakeResult,
    intakeErr,
    pulseIdCard,
    pulsePhone,
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
    resetQueryProgress,
    updateQueryProgress,
    sourceStatusText,
    sourceStatusColor,
    sourceStatusIcon,
    sourcePillClass,
    phaseDetail,
    noEContractReason,
    drivingContractTag,
    currentTicketId,
    triggerFileInput,
    onDragOver,
    onDragEnter,
    onDragLeave,
    onDrop,
    onFileSelected,
    handleIntakeFile,
    queryAll,
    restore,
    reset,
    // 处理人下拉数据
    assignableUsers,
    loadAssignableUsers,
    // 统一智能查询
    studentName,
    schoolShort,
    regStart,
    regEnd,
    candidates,
    candWrapRef,
    searching,
    candEmpty,
    searchSource,
    selectedCand,
    queryTarget,
    candTotal,
    candPage,
    candTotalPages,
    gotoCandPage,
    orgFallback,
    orgOptions,
    phoneMismatch,
    residencyTip,
    nameMismatch,
    phoneCandidates,
    successBar,
    clearTransient,
    maskLocalPhone,
    examStageClass,
    routeMode,
    queryPrimary,
    qFieldClass,
    mainBtnText,
    searchStudents,
    chooseCandidate,
    onMainClick,
    runExactQuery,
    // 同日同人预检：并入 / 另建选择
    sameDayTicket,
    chooseMergeExisting,
    createNewAnyway,
    dismissSameDayPrompt,
    // 三系统无信息 · 人工建案
    noMatchInfo,
    dismissNoMatch,
    manualOpen,
    manualCreating,
    manualErr,
    manualForm,
    orgUnitsFull,
    openManualIntake,
    closeManualIntake,
    submitManualIntake,
  };
}
