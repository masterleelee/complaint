// 处理历史组合式函数 - 仪表盘 + 列表 + 分页
import { getJ, postJ, putJ, delJ } from "api";

export function useHistory(toast) {
  // 列表
  const hList = Vue.ref([]);
  const hTotal = Vue.ref(0);
  const hSearch = Vue.ref("");
  const hLimit = Vue.ref(20);
  const hPage = Vue.ref(1);
  const hTotalPages = Vue.computed(() => Math.max(1, Math.ceil(hTotal.value / hLimit.value)));
  const hLoading = Vue.ref(false);

  // 筛选
  const statusFilter = Vue.ref("");
  const schoolOptions = Vue.ref([]);  // 代号列表 [{code, name, type}]
  // 工单列表时间统一跟随看板 chartDateStart / chartDateEnd

  // Detail modal
  const detailModal = Vue.ref(null);
	  const detailTicket = Vue.ref(null);
  const detailDeductions = Vue.ref([]);
  const detailCommunicationRecords = Vue.ref([]);
	  const detailDocuments = Vue.ref([]);
  const detailLoading = Vue.ref(false);

  // 统计
  const stats = Vue.ref({
    total: 0, pending: 0, processing: 0, completed: 0,
    refund_sum: 0, repeat_count: 0,
	    integrity_issues: { archived_not_completed: 0, completed_without_final_outcome: 0, total: 0 },
	    by_school: [], by_rate: [], by_source: [], by_type: [], by_outcome: [], by_branch_cooperation: [], daily_trend: [],
	    monthly_trend: [], monthly_compare: {}, total_vehicle_count: 0, total_complaint_rate: null,
    this_month: 0, this_quarter: 0, this_year: 0,
  });
  // 看板范围：all / branch / store
  const statScope = Vue.ref("all");
  // 代号筛选：选择代号后，数据看板与工单列表整体切换到该网点
  const drillCode = Vue.ref("");            // 当前选中的代号
  const drill = Vue.computed(() => {
    const c = drillCode.value;
    if (!c) return null;
    const opt = schoolOptions.value.find(o => o.code === c);
    return opt ? { code: opt.code, name: opt.name, type: opt.type || "" } : { code: c, name: c, type: "" };
  });
  // 车辆数维护
  const vehicleModalOpen = Vue.ref(false);
  const vehicleItems = Vue.ref([]);
  const vehicleLoading = Vue.ref(false);
  const vehicleSaving = Vue.ref(false);
  const chartDateStart = Vue.ref("");
  const chartDateEnd = Vue.ref("");
  // 看板时间周期 tab：week / month / year / all / custom
  const chartPeriod = Vue.ref("all");
  // 身份证脱敏显示（明细表默认打码，可一键切换）
  const masked = Vue.ref(true);
  // 只看重复投诉（同身份证多条记录）
  const repeatOnly = Vue.ref(false);
  // 单位排行维度：count=按数量 / rate=按每百车率
  const rankMode = Vue.ref("count");
  const periodStat = Vue.computed(() => stats.value);
  const durationStats = Vue.ref({ overdue_count: 0, avg_processing_hours: 0, completed_count: 0 });

  async function loadDurationStats() {
    try {
      const params = new URLSearchParams();
      if (chartDateStart.value) params.set("start_date", chartDateStart.value);
      if (chartDateEnd.value) params.set("end_date", chartDateEnd.value);
      if (drill.value) params.set("unit_code", drill.value.code);
      const d = await getJ("/api/statistics/duration?" + params.toString());
      if (d.success) durationStats.value = d.data;
    } catch (e) {
      console.error(e);
    }
  }

  function _fmt(d) {
    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  }
  function todayStr() { return _fmt(new Date()); }
  function getWeekRange() {
    const d = new Date(); const day = d.getDay();
    const diff = d.getDate() - day + (day === 0 ? -6 : 1);
    const mon = new Date(d.setDate(diff));
    return { start: _fmt(mon), end: todayStr() };
  }
  function getMonthRange() {
    const d = new Date();
    const first = new Date(d.getFullYear(), d.getMonth(), 1);
    return { start: _fmt(first), end: todayStr() };
  }
  function getYearRange() {
    const d = new Date();
    const first = new Date(d.getFullYear(), 0, 1);
    return { start: _fmt(first), end: todayStr() };
  }
  function setChartPeriod(period) {
    chartPeriod.value = period;
    if (period === 'week') { const r = getWeekRange(); chartDateStart.value = r.start; chartDateEnd.value = r.end; }
    else if (period === 'month') { const r = getMonthRange(); chartDateStart.value = r.start; chartDateEnd.value = r.end; }
    else if (period === 'year') { const r = getYearRange(); chartDateStart.value = r.start; chartDateEnd.value = r.end; }
    else if (period === 'custom') { /* 使用手动选择的区间，不改日期 */ }
    else { chartDateStart.value = ''; chartDateEnd.value = ''; }
    loadStats();
    loadHist();
  }

  /** 身份证显示：默认脱敏（前6后4），masked=false 时显示完整 */
  function maskId(id) {
    const s = String(id || "");
    if (!masked.value) return s;
    return s.length >= 15 ? s.slice(0, 6) + "********" + s.slice(-4) : s;
  }

  let _st = null;
  let _idCardCounts = {};

  function debSearch() {
    clearTimeout(_st);
    hPage.value = 1;
    _st = setTimeout(loadHist, 300);
  }

  // 加载代号列表（含标准字典名称）
  async function loadSchoolCodes() {
    try {
      const d = await getJ("/api/school-codes");
      schoolOptions.value = d.data || [];
    } catch (e) {
      console.error("加载代号列表失败:", e);
    }
  }

  // 单条删除（投诉信息明细）
  const hDeleteModal = Vue.ref(null);
  const hDeleting = Vue.ref(false);
  function askDeleteHist(t) {
    if (!t || !t.id) return;
    hDeleteModal.value = t;
  }
  async function confirmDeleteHist() {
    const t = hDeleteModal.value;
    if (!t || !t.id) return;
    hDeleting.value = true;
    try {
      const d = await delJ(`/api/tickets/${t.id}`);
      if (!d.success) throw new Error(d.error || "未知错误");
      hDeleteModal.value = null;
      await loadHist();
      loadStats();
      toast("已删除案件", t.student_name || t.id, "success");
    } catch (e) {
      toast("删除失败", e.message, "danger");
    } finally {
      hDeleting.value = false;
    }
  }

  async function loadHist() {
    try {
      const params = new URLSearchParams();
      if (hSearch.value) params.set("search", hSearch.value);
      if (statusFilter.value) params.set("status", statusFilter.value);
      if (repeatOnly.value) params.set("repeat_only", "1");
      if (drill.value) params.set("school", drill.value.code);
      if (chartDateStart.value) params.set("start_date", chartDateStart.value);
      if (chartDateEnd.value) params.set("end_date", chartDateEnd.value);
      params.set("limit", String(hLimit.value));
      params.set("offset", String((hPage.value - 1) * hLimit.value));

      const d = await getJ("/api/tickets?" + params.toString());
      const records = d.data?.records || [];
      hTotal.value = d.data?.total || 0;

      // 计算重复投诉标记
      const idCards = {};
      for (const r of records) {
        if (r.id_card) {
          idCards[r.id_card] = (idCards[r.id_card] || 0) + 1;
        }
      }
      // 如果有多条相同身份证在同一页，标记
      for (const r of records) {
        r._repeat = idCards[r.id_card] > 1;
      }

      hList.value = records;
    } catch (e) {
      console.error(e);
    }
  }

  async function loadStats() {
    loadPrevStats();
    try {
      const params = new URLSearchParams();
      if (chartDateStart.value) params.set("start_date", chartDateStart.value);
      if (chartDateEnd.value) params.set("end_date", chartDateEnd.value);
      if (drill.value) params.set("unit_code", drill.value.code);
      else params.set("scope", statScope.value);
      const d = await getJ("/api/ticket-statistics?" + params.toString());
      if (d.success) stats.value = d.data;
    } catch (e) {
      console.error(e);
    }
    await loadDurationStats();
  }

  // ── 环比对比卡：上期窗口 = 本期窗口等长向前平移 ──
  const prevStats = Vue.ref(null);      // 上期 ticket-statistics
  const prevDuration = Vue.ref(null);   // 上期处理时长统计

  function shiftedRange(startStr, endStr) {
    const s = new Date(startStr + "T00:00:00");
    const e = new Date(endStr + "T00:00:00");
    const len = Math.round((e - s) / 86400000) + 1;
    const p1 = new Date(s); p1.setDate(p1.getDate() - len);
    const p2 = new Date(s); p2.setDate(p2.getDate() - 1);
    return { start: _fmt(p1), end: _fmt(p2) };
  }

  async function loadPrevStats() {
    prevStats.value = null;
    prevDuration.value = null;
    if (!chartDateStart.value || !chartDateEnd.value) return; // "全部"无上期可比
    try {
      const w = shiftedRange(chartDateStart.value, chartDateEnd.value);
      const params = new URLSearchParams();
      params.set("start_date", w.start);
      params.set("end_date", w.end);
      if (drill.value) params.set("unit_code", drill.value.code);
      else params.set("scope", statScope.value);
      const [a, b] = await Promise.all([
        getJ("/api/ticket-statistics?" + params.toString()),
        getJ("/api/statistics/duration?" + params.toString()),
      ]);
      prevStats.value = a.success ? a.data : null;
      prevDuration.value = b.success ? b.data : null;
    } catch (e) {
      console.error(e);
    }
  }

  /** 投诉类指标语义：涨=坏(红)、降=好(绿)；率类用百分点差 */
  function cmpDelta(curV, preV, pp) {
    if (!preV && !curV) return { deltaText: '持平', cls: 'delta-flat', icon: 'bi-dash-lg' };
    if (!preV) return { deltaText: '新增', cls: 'delta-bad', icon: 'bi-arrow-up' };
    if (!curV) return { deltaText: '清零', cls: 'delta-good', icon: 'bi-arrow-down' };
    if (pp) {
      const d = Number((curV - preV).toFixed(1));
      if (d === 0) return { deltaText: '持平', cls: 'delta-flat', icon: 'bi-dash-lg' };
      return d > 0
        ? { deltaText: '+' + d + 'pp', cls: 'delta-bad', icon: 'bi-arrow-up' }
        : { deltaText: d + 'pp', cls: 'delta-good', icon: 'bi-arrow-down' };
    }
    const pct = Math.round((curV - preV) / preV * 100);
    if (pct === 0) return { deltaText: '持平', cls: 'delta-flat', icon: 'bi-dash-lg' };
    return pct > 0
      ? { deltaText: '+' + pct + '%', cls: 'delta-bad', icon: 'bi-arrow-up' }
      : { deltaText: '-' + Math.abs(pct) + '%', cls: 'delta-good', icon: 'bi-arrow-down' };
  }

  function cmpRow(label, unit, curD, preD, fmtFn, pp) {
    const max = Math.max(curD, preD);
    return Object.assign(
      { label, unit, cur: fmtFn(curD), prev: fmtFn(preD), wCur: max > 0 ? curD / max * 100 : 0, wPrev: max > 0 ? preD / max * 100 : 0 },
      cmpDelta(curD, preD, pp),
    );
  }

  const compare = Vue.computed(() => {
    const cur = stats.value || {};
    const pre = prevStats.value;
    if (!pre || !chartDateStart.value || !chartDateEnd.value) return { rows: [], windowText: '' };
    const curDur = durationStats.value || {};
    const preDur = prevDuration.value || {};
    const rows = [
      cmpRow('投诉总量', '件', cur.total || 0, pre.total || 0, v => String(v)),
      cmpRow('有效投诉', '件', cur.effective_total || 0, pre.effective_total || 0, v => String(v)),
      cmpRow('撤诉率', '%',
        cur.total ? (cur.withdrawn_total || 0) / cur.total * 100 : 0,
        pre.total ? (pre.withdrawn_total || 0) / pre.total * 100 : 0,
        v => Number(v).toFixed(1), true),
      cmpRow('平均处理时长', '天',
        Number(curDur.avg_processing_hours || 0) / 24,
        Number(preDur.avg_processing_hours || 0) / 24,
        v => Number(v).toFixed(1)),
    ];
    const w = shiftedRange(chartDateStart.value, chartDateEnd.value);
    return { rows, windowText: `本期 ${chartDateStart.value}~${chartDateEnd.value} · 上期 ${w.start}~${w.end}` };
  });

  // 来源渠道 TOP5（迷你条形，占比基于 TOP5 内合计）
  const topChannels = Vue.computed(() => {
    const list = (stats.value.by_source || []).slice(0, 5);
    const total = list.reduce((a, b) => a + (Number(b.count) || 0), 0) || 1;
    return list.map(x => ({ source: x.source, count: x.count, pct: Number((x.count / total * 100).toFixed(1)) }));
  });

  // 明细表分页按钮（当前页 ±2 窗口）
  const hPageButtons = Vue.computed(() => {
    const tp = hTotalPages.value, cur = hPage.value;
    const from = Math.max(1, cur - 2), to = Math.min(tp, cur + 2);
    const arr = [];
    for (let i = from; i <= to; i++) arr.push({ n: i, cur: i === cur });
    return arr;
  });

  /** 切换看板范围（全部/分校/分店） */
  function setStatScope(scope) {
    statScope.value = scope;
    drillCode.value = "";
    loadStats();
    loadHist();
  }

  /** 代号筛选：下拉选择或图表点击网点，看板与工单列表联动切换 */
  function drillInto(code, name) {
    drillCode.value = code || "";
    hPage.value = 1;
    loadStats();
    loadHist();
  }

  /** 代号下拉变化 */
  function onDrillSelect() {
    hPage.value = 1;
    loadStats();
    loadHist();
  }

  /** 看板日期变化：列表同步跟随 */
  function onDateChange() {
    hPage.value = 1;
    loadStats();
    loadHist();
  }

  /** 清除代号筛选，回到范围筛选 */
  function closeDrill() {
    drillCode.value = "";
    hPage.value = 1;
    loadStats();
    loadHist();
  }

  // 看板当前显示的投诉率：选择代号时取该网点自身投诉率（总投诉率的分母是全机构车辆数，不适用于单网点）
  const boardComplaintRate = Vue.computed(() => {
    if (drill.value) {
      const item = stats.value.by_school && stats.value.by_school[0];
      return item && item.complaint_rate != null ? item.complaint_rate : null;
    }
    return stats.value.total_complaint_rate;
  });

  // ── 车辆数维护 ──
  async function loadVehicleCounts() {
    vehicleLoading.value = true;
    try {
      const d = await getJ("/api/org-vehicle-counts");
      if (d.success) vehicleItems.value = d.data.items || [];
    } catch (e) {
      console.error("加载车辆数失败:", e);
    } finally {
      vehicleLoading.value = false;
    }
  }

  function addVehicleRow() {
    vehicleItems.value.push({ unit_code: "", unit_name: "", unit_type: "分校", vehicle_count: 0 });
  }

  function removeVehicleRow(index) {
    vehicleItems.value.splice(index, 1);
  }

  async function saveVehicleCounts() {
    const codes = new Set();
    for (const item of vehicleItems.value) {
      const code = String(item.unit_code || "").trim();
      if (!code) {
        alert("请填写所有网点的代号");
        return false;
      }
      if (codes.has(code)) {
        alert("代号重复: " + code);
        return false;
      }
      codes.add(code);
    }
    vehicleSaving.value = true;
    try {
      const d = await fetch("/api/org-vehicle-counts", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: vehicleItems.value }),
      }).then(r => r.json());
      if (!d.success) throw new Error(d.error || "保存失败");
      loadStats();
      return true;
    } catch (e) {
      console.error("保存车辆数失败:", e);
      alert("保存失败: " + e.message);
      return false;
    } finally {
      vehicleSaving.value = false;
    }
  }

  // 聚合后的历史记录（按身份证分组）
  const groupedHistory = Vue.computed(() => {
    const groups = {};
    
    for (const item of hList.value) {
      const key = item.id_card || item.student_name || item.id;
      if (!groups[key]) {
        groups[key] = {
          id_card: item.id_card,
          student_name: item.student_name,
          school_short: item.school_short,
          source_channel: item.source_channel,
          items: [],
          expanded: false,
          total_refund: 0,
          latest_date: '',
          status: '待处理',
        };
      }
      groups[key].items.push(item);
      
      // 累计应退金额
      groups[key].total_refund += item.refund_fee || 0;
      
      // 取最新日期
      if (item.complaint_date > groups[key].latest_date) {
        groups[key].latest_date = item.complaint_date;
        groups[key].status = item.handle_status;
      }
    }
    
    // 转换为数组，按最新日期排序
    return Object.values(groups).sort((a, b) => 
      (b.latest_date || '').localeCompare(a.latest_date || '')
    );
  });

  function toggleGroup(group) {
    group.expanded = !group.expanded;
  }

  async function updateTicketStatus(ticketId, newStatus) {
    try {
      // 立即更新本地数据（即时视觉反馈）
      const updatedList = [...hList.value];
      let changed = false;
      for (const item of updatedList) {
        if (item.id === ticketId) {
          item.handle_status = newStatus;
          changed = true;
          break;
        }
      }
      if (changed) {
        hList.value = updatedList;
      }

      const d = await putJ(`/api/tickets/${ticketId}`, { handle_status: newStatus });
      if (d.success) {
        await loadHist();
        await loadStats();
        updateCharts();
      }
    } catch (e) {
      console.error("更新状态失败:", e);
    }
  }

  // 导出工单数据为 Excel（跟随代号 + 看板时间筛选）
  async function exportTickets() {
    try {
      const params = new URLSearchParams();
      if (statusFilter.value) params.set("status", statusFilter.value);
      if (drill.value) params.set("school", drill.value.code);
      if (statScope.value) params.set("scope", statScope.value);
      if (chartDateStart.value) params.set("date_start", chartDateStart.value);
      if (chartDateEnd.value) params.set("date_end", chartDateEnd.value);

      const url = "/api/tickets/export?" + params.toString();
      
      // 使用 fetch 下载文件
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error("导出失败: " + response.statusText);
      }
      
      const blob = await response.blob();
      const downloadUrl = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = downloadUrl;
      a.download = `工单导出_${new Date().toISOString().slice(0,10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(downloadUrl);
      
    } catch (e) {
      console.error("导出失败:", e);
      alert("导出失败: " + e.message);
    }
  }

  async function showTicketDetail(ticketId) {
    detailLoading.value = true;
    try {
      const d = await getJ(`/api/tickets/${ticketId}/detail`);
      if (d.success) {
        detailTicket.value = d.data.ticket;
	        detailDeductions.value = d.data.deductions;
	        detailCommunicationRecords.value = d.data.communication_records || [];
	        detailDocuments.value = d.data.documents;
        if (detailModal.value) {
          detailModal.value.show();
        }
      }
    } catch (e) {
      console.error("showTicketDetail error:", e);
    } finally {
      detailLoading.value = false;
    }
  }

  function loadFromHist(item, form) {
    if (form) {
      form.id_card = item.id_card;
      form.complaint_date = item.complaint_date || form.complaint_date;
      form.complaint_type = item.complaint_type || "A";
      form.source_channel = item.source_channel || "交通局";
    }
  }

  // ═══════════════════════════════════════════════════
  //  图表相关
  // ═══════════════════════════════════════════════════
  const chartInstances = {};
  const chartRefs = {};

  function initCharts(refs) {
    // 保存 ref 引用
    Object.assign(chartRefs, refs);
    
    // 初始化图表实例
    if (refs.trendChartRef) {
      chartInstances.trend = echarts.init(refs.trendChartRef);
    }
    if (refs.schoolChartRef) {
      chartInstances.school = echarts.init(refs.schoolChartRef);
    }
    if (refs.typeChartRef) {
      chartInstances.type = echarts.init(refs.typeChartRef);
    }
    if (refs.statusChartRef) {
      chartInstances.status = echarts.init(refs.statusChartRef);
    }
  }

  function updateCharts() {
    const colors = ['#3370FF', '#34D399', '#FBBF24', '#F87171', '#A78BFA', '#60A5FA', '#2DD4BF'];
    
    // 1. 投诉趋势 - 只展示数据库中的真实案件数量
    if (chartInstances.trend) {
      const trend = stats.value.daily_trend || [];
      const days = trend.map(item => {
        const d = String(item.date || "").trim();
        if (!d || d === "null" || d === "None") return "未登记日期";
        const parts = d.split("-");
        return parts.length === 3 ? `${Number(parts[1])}/${Number(parts[2])}` : d;
      });
      const data = trend.map(item => Number(item.count) || 0);
      chartInstances.trend.setOption({
        color: colors,
        grid: { left: 40, right: 20, top: 20, bottom: 30 },
        xAxis: { type: 'category', data: days, axisLine: { lineStyle: { color: '#E5E7EB' } }, axisLabel: { color: '#6B7280', fontSize: 10 } },
        yAxis: { type: 'value', axisLine: { show: false }, splitLine: { lineStyle: { color: '#F3F4F6' } }, axisLabel: { color: '#6B7280', fontSize: 10 } },
        series: [{ data: data, type: 'line', smooth: true, symbol: 'none', areaStyle: { opacity: 0.1 } }],
        tooltip: { trigger: 'axis' }
      });
    }

    // 2. 单位排行 - 横向柱状图（按数量 / 按每百车投诉率，点击柱子下钻该网点）
    if (chartInstances.school && stats.value.by_school) {
      let items = stats.value.by_school.slice(0, 10);
      if (rankMode.value === 'rate') {
        items = items.filter(i => i.complaint_rate != null)
                     .sort((a, b) => b.complaint_rate - a.complaint_rate);
      } else {
        items.sort((a, b) => b.count - a.count);
      }
      items = items.reverse(); // 图表自下而上递增
      const vals = items.map(i => rankMode.value === 'rate' ? Number(i.complaint_rate) : Number(i.count) || 0);
      const unitLabel = rankMode.value === 'rate' ? '件/百车' : '件';
      chartInstances.school.setOption({
        grid: { left: 60, right: 44, top: 10, bottom: 20 },
        xAxis: { type: 'value', axisLine: { show: false }, splitLine: { lineStyle: { color: '#F3F4F6' } }, axisLabel: { color: '#6B7280', fontSize: 10 } },
        yAxis: { type: 'category', data: items.map(i => i.name || i.school || i.code || '未知网点'), axisLine: { lineStyle: { color: '#E5E7EB' } }, axisLabel: { color: '#374151', fontSize: 11 } },
        series: [{
          data: vals, type: 'bar', barWidth: 16,
          itemStyle: {
            borderRadius: [0, 4, 4, 0],
            color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
              { offset: 0, color: '#93C5FD' },
              { offset: 1, color: '#2563EB' }
            ])
          },
          label: {
            show: true, position: 'right', fontSize: 11, fontWeight: 'bold', color: '#374151',
            formatter: p => p.value + (rankMode.value === 'rate' ? '%' : '')
          }
        }],
        tooltip: { trigger: 'axis', formatter: ps => `${ps[0].name}<br>${unitLabel}：<b>${ps[0].value}</b>` }
      }, true);
      chartInstances.school.off('click');
      chartInstances.school.on('click', p => {
        const it = items[p.dataIndex];
        if (it && it.code && it.code !== drillCode.value) drillInto(it.code, it.name || it.school || it.code);
      });
    }

    // 3. 投诉类型 - 饼图（带图例 + 百分比）
    if (chartInstances.type && stats.value.by_type) {
      // P2-14：前端双保险归一，避免 'A' 与 'A-退费纠纷' 并存、空值显示字面标签
      const RAW_TYPE_LABELS = { "A": "退费纠纷", "B": "教学服务", "C": "考试安排", "D": "合同争议", "E": "其他", "A-退费纠纷": "退费纠纷" };
      const normType = (t) => RAW_TYPE_LABELS[String(t || "").trim()] || (t ? String(t).trim() : "未知");
      const typeAgg = {};
      for (const i of stats.value.by_type) {
        const n = normType(i.type);
        typeAgg[n] = (typeAgg[n] || 0) + (Number(i.count) || 0);
      }
      const typeData = Object.entries(typeAgg).map(([name, value]) => ({ name, value }));
      const typeTotal = typeData.reduce((s, d) => s + d.value, 0);
      chartInstances.type.setOption({
        color: colors,
        legend: { bottom: 0, textStyle: { fontSize: 10, color: '#374151' } },
        series: [{
          type: 'pie',
          radius: ['35%', '60%'],
          center: ['50%', '45%'],
          data: typeData,
          label: { show: true, formatter: '{d}%', fontSize: 11, fontWeight: 'bold', color: '#374151' },
          labelLine: { show: true },
          itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 }
        }],
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' }
      });
    }

    // 4. 处理状态 - 环形图（带图例 + 百分比 + 中心合计）
    if (chartInstances.status) {
      const statusData = [
        { name: '待处理', value: stats.value.pending || 0 },
        { name: '处理中', value: stats.value.processing || 0 },
        { name: '已完结', value: stats.value.completed || 0 }
      ].filter(i => i.value > 0);
      const statusTotal = statusData.reduce((s, d) => s + d.value, 0);
      chartInstances.status.setOption({
        color: ['#FBBF24', '#60A5FA', '#34D399'],
        legend: { bottom: 0, textStyle: { fontSize: 10, color: '#374151' } },
        graphic: [{
          type: 'text', left: 'center', top: '42%',
          style: { text: '合计', textAlign: 'center', fill: '#6B7280', fontSize: 12 }
        }, {
          type: 'text', left: 'center', top: '52%',
          style: { text: String(statusTotal), textAlign: 'center', fill: '#374151', fontSize: 18, fontWeight: 'bold' }
        }],
        series: [{
          type: 'pie', radius: ['50%', '70%'], center: ['50%', '45%'],
          data: statusData.length ? statusData : [{ name: '暂无数据', value: 1 }],
          label: { show: true, formatter: '{d}%', fontSize: 10, color: '#374151' },
          labelLine: { show: true },
          itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 }
        }],
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' }
      });
    }
  }

  function disposeCharts() {
    Object.values(chartInstances).forEach(chart => chart && chart.dispose());
  }

  return {
    hList, hTotal, hSearch, hLimit, hPage, hTotalPages, hLoading,
    statusFilter, schoolOptions,
    stats, chartDateStart, chartDateEnd, setChartPeriod, periodStat,
    durationStats, loadDurationStats,
    // 看板增强：周期tab / 脱敏 / 重复投诉过滤 / 排行维度 / 环比对比卡
    chartPeriod, masked, maskId, repeatOnly, rankMode, compare, topChannels, hPageButtons,
    groupedHistory, toggleGroup,
    debSearch, loadHist, loadStats, loadFromHist, exportTickets, updateTicketStatus,
    askDeleteHist, confirmDeleteHist, hDeleteModal, hDeleting,
    loadSchoolCodes,
    // 看板三态 + 代号筛选 + 车辆数
    statScope, setStatScope,
    drill, drillCode, onDrillSelect, onDateChange, boardComplaintRate,
    drillInto, closeDrill,
    vehicleItems, vehicleLoading, vehicleSaving,
    vehicleModalOpen,
    loadVehicleCounts, saveVehicleCounts,
    addVehicleRow, removeVehicleRow,
	    detailModal, detailTicket, detailDeductions, detailCommunicationRecords, detailDocuments, detailLoading,
    showTicketDetail,
    initCharts, updateCharts, disposeCharts,
  };
}
