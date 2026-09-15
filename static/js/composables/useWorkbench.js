// 工作台（四视图核心）组合式函数
// 承载：选中工单、工单分组列表（在途/已归档/已撤诉）、
// 处理情况(handling_notes)编辑态、配合度(branch_cooperation)、
// 明细解锁态(fee-unlock)、在途案件列表，以及新增的
// fee-unlock / withdraw / archive 真实接口接入。
import { getJ, postJ, putJ, delJ } from "api";
import { todayStr } from "helpers";

export function useWorkbench(toast, restoreComplaint, restoreWorkflow, getQr, onStatsRefresh = null, sharedAssignableUsers = null, markFeeEditable = null) {
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

  // 投诉内容/诉求 AI 提取（④卡片）：输入描述 → LLM 提取 → 预览 → 「保存进度」落库
  const extractInput = Vue.ref("");     // 投诉描述原文（仅作提取原料，不落库）
  const extractContent = Vue.ref("");   // 投诉内容（预览值，初始=库内值，归档登记表引用）
  const extractDemands = Vue.ref("");   // 投诉诉求（预览值）
  const extracting = Vue.ref(false);
  const extractDirty = Vue.ref(false);  // 提取结果尚未保存到工单
  async function aiExtractComplaint() {
    if (extracting.value) return;
    const raw = (extractInput.value || "").trim();
    if (raw.length < 10) { toast("描述太短", "请粘贴完整的投诉描述（至少 10 个字）", "warning"); return; }
    extracting.value = true;
    try {
      const d = await postJ("/api/complaint/extract", {
        text: raw,
        complaint_type: selectedTicket.value?.complaint_type || "",
      });
      if (!d.success) throw new Error(d.error || "AI 提取失败");
      extractContent.value = d.data?.complaint_content || "";
      extractDemands.value = d.data?.complaint_demands || "";
      extractDirty.value = true;
      toast("AI 已提取", "点「保存进度」把投诉内容/诉求写入工单", "success");
    } catch (e) {
      toast("AI 提取失败", e.message, "danger");
    } finally {
      extracting.value = false;
    }
  }

  // 登记表预览弹窗（纸面 1:1 还原 docx，可直接在纸上修改，生成即为所见）
  const previewFormOpen = Vue.ref(false);
  const previewFormData = Vue.ref(null);
  const previewFormLoading = Vue.ref(false);
  // 纸面编辑覆盖值：{ "投诉日期": "...", "投诉处理:1": "...", "__title__": "..." }
  const formOverrides = Vue.ref({});

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
      formOverrides.value = {};   // 每次重新预览都从库内数据出发，不残留上次编辑
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
  let lastSavedArchiveRoot = ""; // 最近一次服务端确认持久化的值（用于失败回滚，保证 UI=实际生效）
  getJ("/api/config").then((d) => {
    if (d && typeof d.archive_root === "string" && d.archive_root) {
      lastSavedArchiveRoot = d.archive_root;
      archiveRoot.value = d.archive_root;
    }
  }).catch(() => {});

  // 归档路径改完即持久化（600ms 防抖）：保证合同下载/上传、登记表/回复函生成
  // 立即按页面所见路径落盘，不再等点「归档按钮」才生效。
  // 保存失败必须回滚输入框 + 醒目报错：否则 UI 显示用户所选路径、实际落盘仍是旧路径（所见非所得）。
  let archiveRootSaveTimer = null;
  Vue.watch(archiveRoot, (val) => {
    const cur = (val || "").trim();
    if (!cur || cur === lastSavedArchiveRoot) return;
    clearTimeout(archiveRootSaveTimer);
    archiveRootSaveTimer = setTimeout(() => {
      postJ("/api/config/archive-root", { archive_root: cur })
        .then((d) => {
          if (d && d.success === false) {
            toast("归档路径保存失败，已回滚为上次生效路径", d.error || "", "danger");
            archiveRoot.value = lastSavedArchiveRoot;
          } else if (d && d.success) {
            lastSavedArchiveRoot = cur;
            // 以服务端规范化后的绝对路径回写（自动展开 ~ / 补绝对路径），保持所见即所存
            const normalized = d.data && d.data.normalized;
            if (normalized && normalized !== cur) archiveRoot.value = normalized;
          }
        })
        .catch(() => {
          toast("归档路径保存失败（网络异常），已回滚为上次生效路径", "", "danger");
          archiveRoot.value = lastSavedArchiveRoot;
        });
    }, 600);
  });

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
    // 优先调起系统原生文件夹选择对话框（macOS），失败时回退为网页内目录浏览
    const cur = (archiveRoot.value || "").trim();
    const start = cur && /^[/~]/.test(cur) ? cur : "";
    try {
      const d = await postJ("/api/fs/native-picker", { start });
      if (d && d.cancelled) return;
      if (!d || d.success === false || !d.data || !d.data.path) {
        throw new Error((d && d.error) || "无法调起原生文件夹选择对话框");
      }
      archiveRoot.value = d.data.path;
      return;
    } catch (e) {
      toast("已回退网页内目录浏览", e.message, "info");
    }
    folderModalOpen.value = true;
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

  // 右栏手风琴：展开任一块，其余两块自动折叠（右栏限高，避免三块同时撑开后互相挤压）
  const RAIL_PANELS = { profile: profileOpen, timeline: timelineOpen, rail: railOpen };
  function toggleRailPanel(name) {
    const cur = RAIL_PANELS[name];
    if (!cur) return;
    const willOpen = !cur.value;
    for (const k of Object.keys(RAIL_PANELS)) {
      RAIL_PANELS[k].value = k === name ? willOpen : false;
    }
  }

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
  const clSchool = Vue.ref("");       // 代号（school_short）
  const clDays = Vue.ref("all");      // all / 7 / 30
  const clDateFrom = Vue.ref("");
  const clDateTo = Vue.ref("");
  const clOnlyOverdue = Vue.ref(false);
  const clOnlyManual = Vue.ref(false);   // 仅看三系统无信息（人工建案）工单
  const clGroup = Vue.ref("all");     // KPI 过滤：all / open / archived / withdrawn
  // 默认按「最近操作」倒序（最近被编辑/沟通/撤诉/归档的排前面），贴合处理员日常扫单习惯。
  // 口径：
  //   - 在途：updated_at（任意编辑都会刷；新单 updated_at=created_at）
  //   - 已归档：completed_at（归档时刻），缺失则退 updated_at
  //   - 已撤诉：withdrawn_at（撤诉时刻），缺失则退 updated_at
  const clSort = Vue.ref({ key: "action", dir: -1 });
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

  // 处理人下拉/转办：使用系统账号下拉（admin+handler，排除 viewer 和停用）
  // assignableUsers 从 useComplaint 共享传入（同一份缓存，不重复请求）
  const assignableUsers = sharedAssignableUsers || Vue.ref([]);
  const userById = Vue.computed(() => {
    const m = new Map();
    for (const u of assignableUsers.value) m.set(u.id, u);
    return m;
  });

  // 工单表渲染：处理人列。优先按 handler_user_id 查真实姓名；没 user_id 时显示原 handler_name + 灰色"未关联账号"
  function handlerLabel(t) {
    if (t.handler_user_id) {
      const u = userById.value.get(t.handler_user_id);
      if (u) return { text: u.real_name, mapped: true };
    }
    const name = String(t.handler_name || "").trim();
    if (!name) return { text: "未分配", mapped: true };
    return { text: name, mapped: false };
  }

  const handlerOptions = Vue.computed(() => {
    // 合并：assignableUsers + 工单表里出现过的"未关联"名称
    const set = new Map();
    for (const u of assignableUsers.value) set.set("user:" + u.id, { value: "user:" + u.id, label: u.real_name });
    for (const t of allTickets.value) {
      if (!t.handler_user_id) {
        const h = String(t.handler_name || "").trim();
        if (h) set.set("raw:" + h, { value: "raw:" + h, label: h + "（未关联）" });
      }
    }
    return [...set.values()];
  });

  // 把"处理人筛选"统一解析为匹配函数
  function matchHandler(t, val) {
    if (!val) return true;
    if (val === "__unassigned") return !String(t.handler_name || "").trim();
    if (val.startsWith("user:")) {
      const uid = Number(val.slice(5));
      return t.handler_user_id === uid;
    }
    if (val.startsWith("raw:")) {
      return !t.handler_user_id && String(t.handler_name || "").trim() === val.slice(4);
    }
    return false;
  }
  const channelOptions = Vue.computed(() => {
    const set = new Set();
    for (const t of allTickets.value) {
      const c = String(t.source_channel || "").trim();
      if (c) set.add(c);
    }
    return [...set];
  });

  // ── 列表页行内编辑：投诉类型 / 来源渠道 ──
  // 两字段有下游产物依赖（登记表「投诉渠道」格 + 统计分组），必须走受控保存：
  // 只提交单字段（避免整对象覆盖）、改动留痕由后端完成、已归档先确认、
  // 已生成登记表时后端置 register_form_outdated 并由前端提示重出。
  const BASE_CHANNELS = ["12345", "交通部门", "电话来访", "信访", "邮件投诉", "驾培协会"];

  function clChannelEditOptions(t) {
    const set = new Set(BASE_CHANNELS);
    for (const c of channelOptions.value) set.add(c);   // 存量出现过的渠道
    const cur = String((t && t.source_channel) || "").trim();
    if (cur) set.add(cur);                              // 保全自定义值，防止下拉吞掉
    return [...set];
  }

  async function clSaveField(t, field, ev) {
    if (!t || !ev || !ev.target) return;
    const el = ev.target;
    const oldValue = String(t[field] || "").trim();
    const newValue = String(el.value || "").trim();
    if (oldValue === newValue) return;

    const label = field === "complaint_type" ? "投诉类型" : "来源渠道";
    // 已归档允许改（业务决策），但必须明确告知登记表不会自动跟着变
    if (t.archive_status === "已归档") {
      const ok = window.confirm(
        `该工单已归档。\n\n修改「${label}」后，已生成的投诉登记表不会自动更新，需要重新生成登记表。\n\n确定修改吗？`
      );
      if (!ok) { el.value = oldValue; return; }
    }

    try {
      const d = await putJ(`/api/tickets/${t.id}`, { [field]: newValue });
      if (!d || d.success === false) throw new Error((d && d.error) || "保存失败");
      t[field] = newValue;                              // 立即反映，筛选下拉随之重算
      const outdated = !!(d.data && d.data.register_form_outdated);
      if (outdated) t.register_form_outdated = true;
      _refreshStats();                                  // 类型/来源参与统计分组 → 刷新看板
      if (outdated) {
        toast(`已修改${label}`, "登记表「投诉渠道」等已与库内不一致，请重新生成登记表", "warning");
      } else {
        toast(`已修改${label}`, newValue, "success");
      }
    } catch (e) {
      el.value = oldValue;
      toast("修改失败", e.message || "请稍后重试", "danger");
    }
  }

  function clSaveType(t, ev) { return clSaveField(t, "complaint_type", ev); }
  function clSaveChannel(t, ev) { return clSaveField(t, "source_channel", ev); }
  const clSchoolOptions = Vue.computed(() => {
    const set = new Set();
    for (const t of allTickets.value) {
      const s = String(t.school_short || "").trim();
      if (s) set.add(s);
    }
    return [...set].sort();
  });

  function _clMatch(t) {
    if (clGroup.value === "open" && !(["待处理", "处理中"].includes(t.handle_status) && t.withdraw_status !== "已撤诉" && t.archive_status !== "已归档")) return false;
    if (clGroup.value === "archived" && t.archive_status !== "已归档") return false;
    if (clGroup.value === "withdrawn" && t.withdraw_status !== "已撤诉") return false;
    if (clType.value && t.complaint_type !== clType.value) return false;
    if (clChannel.value && (t.source_channel || "") !== clChannel.value) return false;
    if (clSchool.value && (t.school_short || "") !== clSchool.value) return false;
    if (clHandler.value === "__unassigned" && String(t.handler_name || "").trim()) return false;
    if (clHandler.value && !matchHandler(t, clHandler.value)) return false;
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

  // "最近操作" 取值：按工单所属分组在途/已归档/已撤诉走不同字段
  function actionAt(t) {
    if (t.withdraw_status === "已撤诉") {
      return String(t.withdrawn_at || t.updated_at || t.created_at || "");
    }
    if (t.archive_status === "已归档") {
      return String(t.completed_at || t.updated_at || t.created_at || "");
    }
    return String(t.updated_at || t.created_at || "");
  }

  function _cmp(a, b, key) {
    let va, vb;
    if (key === "days") { va = daysOpen(a); vb = daysOpen(b); }
    else if (key === "refund") { va = a.refund_fee == null ? -1 : Number(a.refund_fee); vb = b.refund_fee == null ? -1 : Number(b.refund_fee); }
    else if (key === "action") { va = actionAt(a); vb = actionAt(b); }
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
      else arr.sort((a, b) => _cmp(a, b, "action"));   // 默认按最近操作倒序
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
    [clKw, clType, clChannel, clSchool, clHandler, clFee, clDays, clDateFrom, clDateTo,
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

  // 批量操作条显隐：仅当「选中的 id 里有至少一条落在当前筛选结果中」才显示。
  // 否则切换筛选后会出现"页面上没有任何勾选、底部却挂着『已选 N 条』"的幽灵状态。
  const batchBarVisible = Vue.computed(() => {
    const sel = clSelectedIds.value;
    if (!sel.length) return false;
    const set = new Set(sel);
    for (const g of listGroups.value) {
      for (const t of g.items) if (set.has(t.id)) return true;
    }
    return false;
  });

  // 筛选条件变化后，把已不在当前结果中的选中 id 一并清掉，
  // 让内存选中态与页面所见严格一致（避免"我以为没选，实际后台还选着"）。
  Vue.watch(listGroups, (groups) => {
    if (!clSelectedIds.value.length) return;
    const visible = new Set();
    for (const g of groups) for (const t of g.items) visible.add(t.id);
    const kept = clSelectedIds.value.filter(id => visible.has(id));
    if (kept.length !== clSelectedIds.value.length) clSelectedIds.value = kept;
  }, { flush: "post" });

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
  // 打开归档：在系统文件管理器中打开该学员的案件归档夹（后端实时探测，失败 toast 原因）
  // ── 远程打开归档（kjfolder:// 协议 + 失焦检测 + 兜底弹窗） ──────────────
  // 后端 mode=remote 时：不弹服务器 Finder，前端尝试 kjfolder://<base64url(UNC)>
  // 调起客户端「归档助手」；window 失焦 = 助手已装（资源管理器抢焦点）→ 成功提示；
  // 短暂窗口内未失焦 = 未装助手（或被拦截）→ 弹兜底弹窗：UNC 路径 + 一键复制 + 下载助手。
  const kjHelperModalOpen = Vue.ref(false);
  const kjUncPath = Vue.ref("");
  const kjServerDir = Vue.ref("");

  function _utf8ToBase64Url(str) {
    const bytes = new TextEncoder().encode(str);
    let bin = "";
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function _tryKjProtocol(unc) {
    // 触发自定义协议；浏览器若无注册处理程序通常静默失败（个别浏览器弹协议选择框，
    // 用户取消后 focus 不变，同样落入兜底弹窗，行为正确）。
    try {
      window.location.href = "kjfolder://" + _utf8ToBase64Url(unc);
    } catch (e) { /* 兜底弹窗 */ }
  }

  function _watchBlurThenResolve(unc, dir) {
    let done = false;
    const onFinish = (installed) => {
      if (done) return;
      done = true;
      window.removeEventListener("blur", onBlur);
      clearTimeout(timer);
      if (installed) toast("已在你的电脑打开归档文件夹", unc, "success");
      else { kjUncPath.value = unc; kjServerDir.value = dir; kjHelperModalOpen.value = true; }
    };
    const onBlur = () => onFinish(true);
    window.addEventListener("blur", onBlur);
    const timer = setTimeout(() => onFinish(false), 1500);
  }

  async function copyKjUnc() {
    const text = kjUncPath.value || "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (e2) { /* ignore */ }
      document.body.removeChild(ta);
    }
    toast("已复制", "已复制网络路径，可粘贴到资源管理器地址栏回车打开", "success");
  }

  // ── 归档文件面板（方案 A 主路径）──────────────────────────────────────
  // 2026-09-11 起入口语义变了：不再是「尝试在本机弹出资源管理器」，而是
  // 「在网页里把该学员的归档夹摊开」——列出文件 + 单文件下载 + 单文件预览。
  // 局域网任意客户端（含未装任何东西的 Windows）零配置可用，**点了必有反应**。
  //
  // 不提供打包（用户 2026-09-10 明确：需要哪个文件自己下载）。
  //
  // 冻结（spec.md D1）：kjfolder:// 归档助手链路（上方 kj* 系列）保留不删，
  // 但本面板已不再调用它——清理归 ISS-AP-06，需单独审批。
  const apOpen = Vue.ref(false);
  const apLoading = Vue.ref(false);
  const apFiles = Vue.ref([]);
  const apDir = Vue.ref("");
  // Windows UNC 路径（\\192.0.2.199\File\...，后端追加键 `unc`）。
  // 为什么要有它：apDir 是**服务器本机**的挂载路径（/Volumes/File/...），
  // 局域网 Windows 同事拿到它根本打不开——只有这台 Mac 认识。UNC 才是可分享的。
  const apUncPath = Vue.ref("");
  const apError = Vue.ref("");        // 人话错误信息（直接展示）
  const apErrorCode = Vue.ref("");    // 后端结构化 code，决定「该找谁」
  const apErrorDetail = Vue.ref("");  // 技术明细：默认折叠，仅排查用
  const apIsServerHost = Vue.ref(false);
  const apIconView = Vue.ref(false);
  const apLastTicketId = Vue.ref("");
  const apLastName = Vue.ref("");
  const apLastDate = Vue.ref("");

  // 面板副标题（demo 的 `.m-head .meta` 形态）：任一字段缺失就整段省略，
  // 不渲染「学员： · 投诉日期：」这种半截文本。
  const apMeta = Vue.computed(() => {
    const parts = [];
    if (apLastName.value) parts.push("学员：" + apLastName.value);
    if (apLastDate.value) parts.push("投诉日期：" + apLastDate.value);
    return parts.join(" · ");
  });

  // ── 视觉层：内联 SVG 图标 + 文件类型徽标 ────────────────────────────────
  // 字形与 demo/archive-panel-redesign-demo.html 的 icon() 完全一致（同一套 path）。
  // 为什么不用 Bootstrap Icons（`.bi`）：面板的视觉基准是那份 demo，bi 的字重/轮廓
  // 与 demo 不同；内联 SVG 还能规避图标字体加载失败时出现方框的退化。
  const AP_ICONS = {
    docx: '<path d="M4 1.5h5L12.5 5v9.5h-9z" opacity=".35"/><path d="M7 4.5h5L15.5 8v6.5h-9z"/>',
    doc: '<path d="M4 1.5h5L12.5 5v9.5h-9z"/><path d="M6 8h4.5v1H6zM6 10.2h3v1H6z" fill="#fff" opacity=".85"/>',
    pdf: '<path d="M4 1.5h8v13H4z"/><path d="M6 10.5h4v1H6zM6 8h4v1H6z" fill="#fff" opacity=".85"/>',
    img: '<path d="M2 3h12v10H2z" opacity=".3"/><path d="M2 12l3.4-4 2.4 2.8L10 8l4 4.6z"/><circle cx="5.4" cy="6" r="1.2"/>',
    txt: '<path d="M4 1.5h5L12.5 5v9.5h-9z" opacity=".35"/><path d="M6 7h4.5v1H6zM6 9.2h4.5v1H6zM6 11.4h3v1H6z"/>',
    xls: '<path d="M4 1.5h5L12.5 5v9.5h-9z" opacity=".35"/><path d="M5.8 8h4.4v4.4H5.8z" fill="none" stroke="currentColor" stroke-width="1.1"/><path d="M5.8 10.2h4.4M8 8v4.4" fill="none" stroke="currentColor" stroke-width="1.1"/>',
    folder: '<path d="M4 1.5h5L12.5 5v9.5h-9z" opacity=".35"/><path d="M7 4.5h5L15.5 8v6.5h-9z"/>',
    plug: '<path d="M6 2h1.2v4H6zM9 2h1.2v4H9z"/><path d="M4.6 6h7v2.6A3.6 3.6 0 0 1 8.1 12h-.2a3.6 3.6 0 0 1-3.5-3.6z"/><path d="M7.5 12h1v2.2h-1z"/>',
    warn: '<path d="M8 1.6 15 14H1z"/><path d="M7.3 6h1.4v4H7.3zM7.3 11h1.4v1.4H7.3z" fill="#fff"/>',
    down: '<path d="M7.3 2h1.4v6h2.6L8 11.4 4.7 8h2.6z"/><path d="M3 12.6h10V14H3z"/>',
    eye: '<path d="M8 3.5C5 3.5 2.4 5.6 1.4 8c1 2.4 3.6 4.5 6.6 4.5S13.6 10.4 14.6 8C13.6 5.6 11 3.5 8 3.5zm0 6.8A2.3 2.3 0 1 1 8 5.7a2.3 2.3 0 0 1 0 4.6z"/>',
    lock: '<path d="M4.4 7V5.4a3.6 3.6 0 0 1 7.2 0V7H13v7H3V7zm1.4 0h4.4V5.4a2.2 2.2 0 0 0-4.4 0z"/>',
    copy: '<path d="M5 2h6v2H5zM3.5 3.5h9v10h-9z" opacity=".45"/><path d="M5.5 1h5v2.5h-5z"/>',
    refresh: '<path d="M13 8a5 5 0 1 1-1.5-3.6V3h1v3.5H9v-1h1.6A4 4 0 1 0 12 8z"/>',
    close: '<path d="M4.3 3.3 8 7l3.7-3.7 1 1L9 8l3.7 3.7-1 1L8 9l-3.7 3.7-1-1L7 8 3.3 4.3z"/>',
    list: '<path d="M2 3.5h12v1.4H2zM2 7.3h12v1.4H2zM2 11.1h12v1.4H2z"/>',
    grid: '<path d="M2 2h5.2v5.2H2zM8.8 2H14v5.2H8.8zM2 8.8h5.2V14H2zM8.8 8.8H14V14H8.8z"/>',
  };
  function apIcon(kind, size) {
    const s = size || 16;
    return '<svg width="' + s + '" height="' + s + '" viewBox="0 0 16 16" fill="currentColor"'
      + ' aria-hidden="true">' + (AP_ICONS[kind] || AP_ICONS.doc) + "</svg>";
  }

  // 文件类型 → demo 的 .ftype 配色类名。apFileKind 把 doc/docx 归为 "doc"，
  // 而 demo 的配色类叫 "docx"，故这里做一次映射（不改动已冻结的 apFileKind）。
  const AP_FTYPE = { doc: "docx", pdf: "pdf", img: "img", txt: "txt", xls: "xls", other: "other" };
  function apFtypeCls(name) { return "ftype " + (AP_FTYPE[apFileKind(name)] || "other"); }
  function apFileSvg(name) {
    const k = AP_FTYPE[apFileKind(name)] || "other";
    return apIcon(k === "other" ? "doc" : k, 16);
  }

  // 可内联预览的扩展名 —— 必须与后端 app.py 的 `_ARCHIVE_PREVIEW_MIME` 白名单一致，
  // 否则会出现「按钮亮着但后端 415」。改动其一必须同步另一处。
  const AP_PREVIEW_EXT = ["pdf", "png", "jpg", "jpeg", "gif", "webp", "bmp", "txt"];

  function apExt(name) {
    const s = String(name || "");
    const i = s.lastIndexOf(".");
    return i > 0 ? s.slice(i + 1).toLowerCase() : "";
  }

  function apCanPreview(name) {
    return AP_PREVIEW_EXT.includes(apExt(name));
  }

  function apFileKind(name) {
    const ext = apExt(name);
    if (ext === "pdf") return "pdf";
    if (["png", "jpg", "jpeg", "gif", "webp", "bmp"].includes(ext)) return "img";
    if (["doc", "docx"].includes(ext)) return "doc";
    if (["xls", "xlsx"].includes(ext)) return "xls";
    if (ext === "txt") return "txt";
    return "other";
  }

  function apFileIcon(name) {
    return {
      pdf: "bi-file-earmark-pdf", img: "bi-file-earmark-image",
      doc: "bi-file-earmark-word", xls: "bi-file-earmark-excel",
      txt: "bi-file-earmark-text",
    }[apFileKind(name)] || "bi-file-earmark";
  }

  function apDownloadUrl(name) {
    return `/api/tickets/${encodeURIComponent(apLastTicketId.value)}/archive-files/download?name=${encodeURIComponent(name)}`;
  }

  function apPreviewUrl(name) {
    return `/api/tickets/${encodeURIComponent(apLastTicketId.value)}/archive-files/preview?name=${encodeURIComponent(name)}`;
  }

  function apOpenPreview(name) {
    if (!apCanPreview(name)) return;   // 不可预览的类型：按钮置灰，此处兜底
    window.open(apPreviewUrl(name), "_blank", "noopener");
  }

  function apToggleView() { apIconView.value = !apIconView.value; }

  // 「复制路径」按钮的 tooltip：把将要复制的字符串**直接亮出来**——
  // 若哪天 SMB 映射配错（比如挂载点给出的主机名解析不了），用户悬停当场就能看见，
  // 而不是贴进资源管理器打不开才发现。
  const apCopyHint = Vue.computed(() => {
    const t = apUncPath.value || apDir.value || "";
    return t ? "复制共享路径（可直接粘到资源管理器）：" + t : "复制共享路径";
  });

  async function apCopyPath() {
    // 优先 UNC（\\192.0.2.199\File\...）：局域网 Windows 同事贴进资源管理器就能打开；
    // apDir（/Volumes/File/...）只有服务器这台 Mac 认识，分享出去是死路径。
    const text = apUncPath.value || apDir.value || "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (e2) { /* ignore */ }
      document.body.removeChild(ta);
    }
    toast("已复制", "已复制归档路径", "success");
  }

  // ⑤ 归档卡片头部的「复制路径」：学员信息页平时不加载 archive-files（那是面板打开时才做的事），
  // 所以这里按需请求一次；若归档面板已为**当前工单**加载过（apLastTicketId 命中），直接复用，不打二次请求。
  // 载荷与面板按钮同源：优先 UNC（\\192.0.2.199\File\...），无 UNC 回退 dir。
  const archiveDirCopyHint = Vue.computed(() => {
    const same = apLastTicketId.value === String(selectedTicketId.value || "");
    const t = same ? (apUncPath.value || apDir.value || "") : "";
    return t ? "复制共享路径（可直接粘到资源管理器）：" + t : "复制该学员归档夹的共享路径";
  });

  async function copyArchiveDirPath() {
    const id = String(selectedTicketId.value || "");
    if (!id) return;
    const same = apLastTicketId.value === id;
    let text = same ? (apUncPath.value || apDir.value || "") : "";
    if (!text) {
      try {
        const resp = await fetch(`/api/tickets/${encodeURIComponent(id)}/archive-files`);
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.success) {
          const d = data.data || {};
          text = String(d.unc || d.dir || "");
          // 面板若恰好为同一工单缓存过，顺手同步，保证 tooltip 与面板口径一致
          if (same) { apDir.value = String(d.dir || ""); apUncPath.value = String(d.unc || ""); }
        } else {
          const code = String(data.code || "");
          if (code === "dir_missing") {
            toast("暂无归档文件夹", "该学员的归档文件夹尚未创建（生成登记表/回复函或归档后会出现）", "warning");
            return;
          }
          if (code === "root_unavailable") {
            toast("共享盘不可用", "归档根目录当前无法访问，请稍后重试或联系管理员", "danger");
            return;
          }
          toast("复制失败", (data.errors && data.errors[0]) || data.error || `读取归档路径失败（HTTP ${resp.status}）`, "danger");
          return;
        }
      } catch (e) {
        toast("复制失败", String(e.message || e), "danger");
        return;
      }
    }
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (e2) { /* ignore */ }
      document.body.removeChild(ta);
    }
    toast("已复制", "已复制归档路径", "success");
  }

  async function apLoad() {
    if (!apLastTicketId.value) return;
    apLoading.value = true;
    apFiles.value = [];
    apError.value = ""; apErrorCode.value = ""; apErrorDetail.value = "";
    // ⚠️ 这两个必须在**每次加载前**重置：若只在成功分支赋值，
    // 上一条工单读成功后的 apDir/apIsServerHost 会残留到本条工单的失败面板上
    // （表现为「复制路径」可点但复制的是别的学员的路径、「在服务器上打开」凭空出现）。
    apDir.value = "";
    apUncPath.value = "";
    apIsServerHost.value = false;
    try {
      const resp = await fetch(`/api/tickets/${encodeURIComponent(apLastTicketId.value)}/archive-files`);
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data.success) {
        const d = data.data || {};
        apFiles.value = d.files || [];
        apDir.value = String(d.dir || "");
        apUncPath.value = String(d.unc || "");
        apIsServerHost.value = !!d.is_server_host;
      } else {
        // 失败态**不关面板**：面板本身是错误信息的载体（旧实现关面板+toast，等于没提示）
        apErrorCode.value = String(data.code || "");
        apError.value = (data.errors && data.errors[0]) || data.error
          || `读取归档文件失败（HTTP ${resp.status}）`;
        apErrorDetail.value = String(data.detail || "");
      }
    } catch (e) {
      apErrorCode.value = "";
      apError.value = "读取归档文件失败：" + String(e.message || e);
    } finally {
      apLoading.value = false;
    }
  }

  function apRefresh() { return apLoad(); }

  function apOpenPanel(t) {
    if (t && t.id) {
      // 换工单必须刷新 id，否则会把上一条工单的文件列在当前学员名下
      apLastTicketId.value = String(t.id);
      apLastName.value = String(t.student_name || "");
      apLastDate.value = String(t.complaint_date || "");
    }
    // 冻结的归档助手弹窗若还开着，让位给面板（避免两层弹窗叠加）
    kjHelperModalOpen.value = false;
    apOpen.value = true;
    return apLoad();
  }

  // 本机（服务器）专用：在服务器屏幕上打开该文件夹（远程来源后端返回 403）
  async function apOpenLocalDir() {
    if (!apLastTicketId.value) return false;
    try {
      const resp = await fetch(
        `/api/tickets/${encodeURIComponent(apLastTicketId.value)}/open-archive`, { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data.success) {
        toast("已在服务器上打开文件夹", String((data.data || {}).dir || ""), "success");
        return true;
      }
      toast("打开失败", (data.errors && data.errors[0]) || data.error
        || `打开失败（HTTP ${resp.status}）`, "danger");
      return false;
    } catch (e) {
      toast("打开失败", String(e.message || e), "danger");
      return false;
    }
  }

  function fmtApSize(n) {
    if (!Number.isFinite(n)) return "";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(2) + " MB";
  }

  function apTotalSize() {
    return apFiles.value.reduce((s, f) => s + (Number(f.size) || 0), 0);
  }

  // 「该找谁」指引：与后端 code 一一对应，不要用同一句通用文案糊过去
  function apErrorAction(code) {
    if (code === "dir_missing") {
      return "这个你可以自己解决：回到该工单，先生成投诉登记表或回复函，文件落盘后再回来查看。";
    }
    if (code === "root_unavailable") {
      return "这个你解决不了：归档根目录（共享盘）可能未挂载或当前路径无权限，请联系管理员，或稍后重试。";
    }
    return "可先点「刷新」重试；若持续失败，请把下方技术详情发给管理员。";
  }

  // ── 失败态的「视觉三分」：短标题 / 圆形图标 / 「该找谁」徽标，全部按后端 code 分级 ──
  // 原实现是一律左对齐的红底提示块，「还没生成文档（用户自己能解决）」与「共享盘挂了
  // （只能找管理员）」在观感上完全一样 —— 这正是要修的问题。
  const apErrTitle = Vue.computed(() => {
    if (apErrorCode.value === "dir_missing") return "还没生成归档文件";
    if (apErrorCode.value === "root_unavailable") return "归档共享盘暂时不可用";
    return "读取归档目录失败";
  });
  const apErrIconName = Vue.computed(() => {
    if (apErrorCode.value === "dir_missing") return "doc";
    if (apErrorCode.value === "root_unavailable") return "plug";
    return "warn";
  });
  const apErrStyle = Vue.computed(() => {
    if (apErrorCode.value === "dir_missing") {
      return { background: "var(--primary-light)", color: "var(--primary-dark)" };
    }
    if (apErrorCode.value === "root_unavailable") {
      return { background: "var(--warning-light)", color: "var(--warning)" };
    }
    return { background: "var(--danger-light)", color: "var(--danger)" };
  });
  const apErrWho = Vue.computed(() => (apErrorCode.value === "dir_missing"
    ? { cls: "self", icon: "doc", text: "你可以自己解决" }
    : { cls: "admin", icon: "plug", text: "需要联系管理员" }));
  // 「重试」与「刷新」是同一个动作（重拉一次归档目录）。对 dir_missing 而言重试之前
  // 得先去生成文档，措辞用「刷新」更贴合实际。
  const apErrPrimaryText = Vue.computed(
    () => (apErrorCode.value === "dir_missing" ? "刷新" : "重试"));
  function apErrorPrimary() { return apRefresh(); }

  // 打开归档（全局入口）：直接开面板——不再请求 /open-archive，不再靠 blur 猜测
  async function openArchive(t) {
    if (!t || !t.id) return false;
    return apOpenPanel(t);
  }
  // 批量条「打开归档」：仅单选生效（多条时按钮已置灰，这里兜底拦截）
  function openArchiveSelected() {
    if (clSelectedIds.value.length !== 1) return;
    const t = allTickets.value.find(x => x.id === clSelectedIds.value[0]);
    if (t) return openArchive(t);
  }
  function clClearSelection() { clSelectedIds.value = []; }
  function clSetSort(key) {
    if (clSort.value.key === key) clSort.value = { key, dir: -clSort.value.dir };
    else clSort.value = { key, dir: -1 };
  }
  function clClearFilters() {
    clKw.value = ""; clType.value = ""; clChannel.value = ""; clSchool.value = ""; clHandler.value = "";
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

  // 批量转办弹窗（接收 handler_user_id + handler_name）
  const transferModalOpen = Vue.ref(false);
  const transferTarget = Vue.ref(null);  // user_id
  const transferSaving = Vue.ref(false);
  function askBatchTransfer() {
    if (!clSelectedIds.value.length) return;
    transferTarget.value = null;
    transferModalOpen.value = true;
  }
  // 单行转办（列表行内详情用）：复用批量转办弹窗与确认逻辑
  function askTransferRow(t) {
    if (!t || !t.id) return;
    clSelectedIds.value = [t.id];
    transferTarget.value = null;
    transferModalOpen.value = true;
  }

  // 行内详情展开状态（方案B：低频字段折叠进展开行，不记忆、不跨页）
  const clExpandedIds = Vue.ref([]);
  function clToggleExpand(id) {
    const i = clExpandedIds.value.indexOf(id);
    if (i >= 0) clExpandedIds.value.splice(i, 1);
    else clExpandedIds.value.push(id);
  }

  // 复制手机号（http 非安全上下文下回退 execCommand）
  async function copyPhone(t) {
    const p = String((t && t.phone) || "").trim();
    if (!p) { toast("该案件没有手机号", "", "warning"); return; }
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(p);
      } else {
        const ta = document.createElement("textarea");
        ta.value = p; ta.setAttribute("readonly", "");
        ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.select();
        document.execCommand("copy"); ta.remove();
      }
      toast("已复制手机号 " + p, "", "success");
    } catch (e) {
      toast("复制失败，请手动选择", p, "warning");
    }
  }
  async function confirmBatchTransfer() {
    const targetId = transferTarget.value;
    if (!targetId) { toast("请选择接收人", "", "warning"); return; }
    const u = (assignableUsers.value || []).find(x => x.id === targetId);
    const targetName = u ? u.real_name : "";
    transferSaving.value = true;
    let ok = 0, fail = 0, firstErr = "";
    try {
      for (const id of [...clSelectedIds.value]) {
        try {
          const d = await putJ(`/api/tickets/${id}`, { handler_user_id: targetId, handler_name: targetName });
          if (d.success) ok++;
          else { fail++; if (!firstErr) firstErr = d.error || "未知错误"; }
        } catch { fail++; if (!firstErr) firstErr = "网络请求失败"; }
      }
      transferModalOpen.value = false;
      clClearSelection();
      await loadTickets();
      if (!fail) toast(`已转办 ${ok} 条给 ${targetName}`, "", "success");
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
  // 单行删除：复用批量删除弹窗与确认逻辑
  function askDeleteRow(t) {
    if (!t || !t.id) return;
    clSelectedIds.value = [t.id];
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
      extractInput.value = "";
      extractContent.value = detail.ticket.complaint_content || "";
      extractDemands.value = detail.ticket.complaint_demands || "";
      extractDirty.value = false;
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
        complaint_content: (extractContent.value || "").trim(),
        complaint_demands: (extractDemands.value || "").trim(),
      };
      const name = studentNameEdit.value.trim();
      if (name && name !== (selectedTicket.value?.student_name || "")) {
        payload.student_name = name;
      }
      const d = await putJ(`/api/tickets/${id}`, payload);
      if (!d.success) throw new Error(d.error || "保存失败");
      extractDirty.value = false;
      if (selectedTicket.value && payload.student_name) {
        selectedTicket.value.student_name = payload.student_name;
        await loadTickets();
      }
      toast("处理进度已保存", "", "success");
    } catch (e) {
      toast("保存失败", e.message, "danger");
    }
  }

  // ── 人工回填证件号 → 重查三系统（批次 2 · D5/D6/D7）──
  // 场景：内部系统查无该学员，但操作员已从第三系统等途径确认了身份证号。
  // 服务端约束：只补空字段（不覆盖人工录入内容）、查无也如实落库查询状态、
  // 已归档工单直接拒绝；已生成的回复函会自动标记过期。
  const requeryIdCard = Vue.ref("");
  const requerying = Vue.ref(false);
  const requerySkipped = Vue.ref([]);   // 因库内已有值而未覆盖的字段（差异提示）
  const requerySources = Vue.ref({});
  const requeryMsg = Vue.ref("");

  async function requeryWithIdCard() {
    const id = selectedTicketId.value;
    const idCard = (requeryIdCard.value || "").trim();
    if (!id || requerying.value) return;
    if (!idCard) { toast("请填写证件号", "填入学员身份证号后再重查", "warning"); return; }

    requerying.value = true;
    requeryMsg.value = "正在重查三系统…";
    requerySkipped.value = [];
    requerySources.value = {};
    try {
      const started = await postJ(`/api/tickets/${id}/requery`, { id_card: idCard });
      if (!started.success) throw new Error(started.error || "启动重查失败");

      let done = null;
      for (let i = 0; i < 120; i++) {
        const st = await getJ(`/api/query/status/${started.job_id}`);
        if (!st.success) throw new Error(st.error || "读取重查进度失败");
        if (st.sources) requerySources.value = st.sources;
        if (st.status === "failed") {
          requeryMsg.value = "";
          toast("重查失败", st.error || "三系统查询失败", "danger");
          return;
        }
        if (st.status === "done") { done = st.result || {}; break; }
        await new Promise((r) => setTimeout(r, 500));
      }
      if (!done) { toast("重查超时", "请稍后重试", "warning"); return; }

      requerySkipped.value = done.skipped_fields || [];
      requeryMsg.value = done.name
        ? `已重查到「${done.name}」，身份与档案字段已回填`
        : "三系统均未命中，已如实记录各系统查询状态";
      requeryIdCard.value = "";
      await openTicket(id);
      await loadTickets();
      _refreshStats();
      toast(done.name ? "重查完成" : "重查完成（未命中）", requeryMsg.value,
        done.name ? "success" : "warning");
    } catch (e) {
      requeryMsg.value = "";
      toast("重查失败", e.message, "danger");
    } finally {
      requerying.value = false;
    }
  }

  // 切换工单时清空上一单的重查结果，避免跨单串味。
  // 注意：不能放在 openTicket 里清空 —— requeryWithIdCard 在设置 requeryMsg 之后
  // 还要调用 openTicket 重新拉取详情，那里清空会把本轮的结论抹掉。
  Vue.watch(selectedTicketId, () => {
    requeryIdCard.value = "";
    requeryMsg.value = "";
    requerySkipped.value = [];
    requerySources.value = {};
  });

  // 费用解锁：POST /api/tickets/<id>/fee-unlock（无 body）→ 明细改回可编辑
  async function unlockFee() {
    const id = selectedTicketId.value;
    if (!id) return;
    try {
      const d = await postJ(`/api/tickets/${id}/fee-unlock`, {});
      if (!d.success) throw new Error(d.error || "解锁失败");
      feeUnlocked.value = true;
      if (selectedTicket.value) selectedTicket.value.fee_plan_status = "draft";
      // 解锁后同会话直接补录：还原丢失的 source，使 合同总额/实缴 可输入
      if (typeof markFeeEditable === "function") markFeeEditable();
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
      // 1) 落盘处理情况 + 配合度 + AI 提取的投诉内容/诉求（含未保存的姓名补录，避免归档夹沿用旧名）
      const savePayload = {
        handling_notes: handlingNotes.value,
        branch_cooperation: branchCooperation.value,
        complaint_content: (extractContent.value || "").trim(),
        complaint_demands: (extractDemands.value || "").trim(),
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
    // 人工回填证件号 → 重查三系统
    requeryIdCard, requerying, requerySkipped, requerySources, requeryMsg, requeryWithIdCard,
    extractInput, extractContent, extractDemands, extracting, extractDirty, aiExtractComplaint,
    previewFormOpen, previewFormData, previewFormLoading, previewRegistrationForm, formOverrides,
    listFilter, railOpen, profileOpen, timelineOpen, contractModalOpen, toggleRailPanel,
    loadTickets, openTicket, refreshAfterIntake, saveProgress,
    unlockFee, doWithdraw, doCancelWithdraw, doArchive,
    archiveGates, gateErrors, canArchive, archiveRoot,
    clFocusId, focusArchivedTicket,
    folderModalOpen, fbPath, fbParent, fbDirs, fbLoading, fbErr,
    openFolderPicker, fbLoad, fbEnter, fbUp, fbConfirm,
    // 投诉列表页重设计
    OVERDUE_DAYS, TYPE_LABELS, FEE_LABELS,
    clKw, clType, clChannel, clSchool, clHandler, clFee, clDays, clDateFrom, clDateTo,
    clOnlyOverdue, clOnlyManual, clGroup, clSort, clCollapsed, clSelectedIds,
    handlerOptions, channelOptions, clChannelEditOptions, clSaveType, clSaveChannel,
    clSchoolOptions, listGroups, clResultCount, clOverdueTotal, clSerialMap,
    clPageSize, pagedGroups, clSetPage, batchBarVisible,
    daysOpen, isOverdue, feeState, maskPhone,
    actionAt,
    clToggleRow, clToggleGroupSelect, openArchive, openArchiveSelected, clClearSelection, clSetSort, clClearFilters,
    kjHelperModalOpen, kjUncPath, kjServerDir, copyKjUnc,
    // 归档文件面板（方案 A 主路径）
    apOpen, apLoading, apFiles, apDir, apError, apErrorCode, apErrorDetail,
    apIsServerHost, apIconView, apLastTicketId, apLastName, apLastDate, apMeta,
    apUncPath, apCopyHint,
    archiveDirCopyHint, copyArchiveDirPath,
    apLoad, apRefresh, apOpenPanel, apOpenLocalDir, apToggleView, apCopyPath,
    apCanPreview, apFileIcon, apFileKind, apDownloadUrl, apPreviewUrl, apOpenPreview,
    fmtApSize, apTotalSize, apErrorAction,
    // 面板视觉层（样式对齐 demo/archive-panel-redesign-demo.html）
    apIcon, apFtypeCls, apFileSvg,
    apErrTitle, apErrIconName, apErrStyle, apErrWho, apErrPrimaryText, apErrorPrimary,
    batchExportSelected,
    clExpandedIds, clToggleExpand, copyPhone,
    transferModalOpen, transferTarget, transferSaving, askBatchTransfer, askTransferRow, confirmBatchTransfer,
    deleteModalOpen, deleteSaving, askDeleteSelected, askDeleteRow, confirmDeleteSelected,
    // 账号体系：处理人下拉 + 真实姓名解析（assignableUsers 从 useComplaint 共享）
    assignableUsers, handlerLabel,
  };
}
