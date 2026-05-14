// 处理历史组合式函数 - 仪表盘 + 列表 + 分页
import { getJ, postJ, putJ } from "api";

export function useHistory() {
  // 列表
  const hList = Vue.ref([]);
  const hTotal = Vue.ref(0);
  const hSearch = Vue.ref("");
  const hLimit = Vue.ref(10);
  const hPage = Vue.ref(1);
  const hTotalPages = Vue.computed(() => Math.max(1, Math.ceil(hTotal.value / hLimit.value)));
  const hLoading = Vue.ref(false);

  // 筛选
  const statusFilter = Vue.ref("");
  const schoolFilter = Vue.ref("");
  const schoolCodes = Vue.ref([]);  // 代号列表
  const dateStart = Vue.ref("");
  const dateEnd = Vue.ref("");

  // 统计
  const stats = Vue.ref({
    total: 0, pending: 0, processing: 0, completed: 0,
    refund_sum: 0, repeat_count: 0,
    by_school: [], by_source: [], by_type: [],
    this_month: 0, this_quarter: 0, this_year: 0,
  });
  const chartDateStart = Vue.ref("");
  const chartDateEnd = Vue.ref("");
  const periodStat = Vue.computed(() => stats.value);

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
    if (period === 'week') { const r = getWeekRange(); chartDateStart.value = r.start; chartDateEnd.value = r.end; }
    else if (period === 'month') { const r = getMonthRange(); chartDateStart.value = r.start; chartDateEnd.value = r.end; }
    else if (period === 'year') { const r = getYearRange(); chartDateStart.value = r.start; chartDateEnd.value = r.end; }
    else { chartDateStart.value = ''; chartDateEnd.value = ''; }
    loadStats();
  }

  let _st = null;
  let _idCardCounts = {};

  function debSearch() {
    clearTimeout(_st);
    hPage.value = 1;
    _st = setTimeout(loadHist, 300);
  }

  // 加载代号列表
  async function loadSchoolCodes() {
    try {
      const d = await getJ("/api/school-codes");
      schoolCodes.value = d.data || [];
    } catch (e) {
      console.error("加载代号列表失败:", e);
    }
  }

  async function loadHist() {
    try {
      const params = new URLSearchParams();
      if (hSearch.value) params.set("search", hSearch.value);
      if (statusFilter.value) params.set("status", statusFilter.value);
      if (schoolFilter.value) params.set("school", schoolFilter.value);
      if (dateStart.value) params.set("start_date", dateStart.value);
      if (dateEnd.value) params.set("end_date", dateEnd.value);
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
    try {
      const params = new URLSearchParams();
      if (chartDateStart.value) params.set("start_date", chartDateStart.value);
      if (chartDateEnd.value) params.set("end_date", chartDateEnd.value);
      const d = await getJ("/api/ticket-statistics?" + params.toString());
      if (d.success) stats.value = d.data;
    } catch (e) {
      console.error(e);
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

  // 导出工单数据为 Excel
  async function exportTickets() {
    try {
      const params = new URLSearchParams();
      if (statusFilter.value) params.set("status", statusFilter.value);
      if (dateStart.value) params.set("date_start", dateStart.value);
      if (dateEnd.value) params.set("date_end", dateEnd.value);

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
    // 飞书风格配色
    const colors = ['#3370FF', '#34D399', '#FBBF24', '#F87171', '#A78BFA', '#60A5FA', '#2DD4BF'];
    
    // 1. 趋势图 - 使用模拟数据（后端需要添加趋势接口）
    if (chartInstances.trend) {
      const days = [];
      const data = [];
      for (let i = 29; i >= 0; i--) {
        const d = new Date();
        d.setDate(d.getDate() - i);
        days.push(`${d.getMonth()+1}/${d.getDate()}`);
        data.push(Math.floor(Math.random() * 5)); // 模拟数据
      }
      chartInstances.trend.setOption({
        color: colors,
        grid: { left: 40, right: 20, top: 20, bottom: 30 },
        xAxis: { type: 'category', data: days, axisLine: { lineStyle: { color: '#E5E7EB' } }, axisLabel: { color: '#6B7280', fontSize: 10 } },
        yAxis: { type: 'value', axisLine: { show: false }, splitLine: { lineStyle: { color: '#F3F4F6' } }, axisLabel: { color: '#6B7280', fontSize: 10 } },
        series: [{ data: data, type: 'line', smooth: true, symbol: 'none', areaStyle: { opacity: 0.1 } }],
        tooltip: { trigger: 'axis' }
      });
    }

    // 2. 驾校排行 - 横向柱状图（渐变色）
    if (chartInstances.school && stats.value.by_school) {
      const schoolData = stats.value.by_school.slice(0, 10).reverse();
      const schoolVals = schoolData.map(i => i.count);
      const maxVal = Math.max(...schoolVals, 1);
      chartInstances.school.setOption({
        grid: { left: 60, right: 30, top: 10, bottom: 20 },
        xAxis: { type: 'value', axisLine: { show: false }, splitLine: { lineStyle: { color: '#F3F4F6' } }, axisLabel: { color: '#6B7280', fontSize: 10 } },
        yAxis: { type: 'category', data: schoolData.map(i => i.school), axisLine: { lineStyle: { color: '#E5E7EB' } }, axisLabel: { color: '#374151', fontSize: 11 } },
        series: [{
          data: schoolVals, type: 'bar', barWidth: 16,
          itemStyle: {
            borderRadius: [0, 4, 4, 0],
            color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
              { offset: 0, color: '#93C5FD' },
              { offset: 1, color: '#2563EB' }
            ])
          },
          label: { show: true, position: 'right', fontSize: 11, fontWeight: 'bold', color: '#374151' }
        }],
        tooltip: { trigger: 'axis' }
      });
    }

    // 3. 投诉类型 - 饼图（带图例 + 百分比）
    if (chartInstances.type && stats.value.by_type) {
      const typeData = stats.value.by_type.map(i => ({ name: i.type, value: i.count }));
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
    statusFilter, schoolFilter, schoolCodes, dateStart, dateEnd,
    stats, chartDateStart, chartDateEnd, setChartPeriod, periodStat,
    groupedHistory, toggleGroup,
    debSearch, loadHist, loadStats, loadFromHist, exportTickets, updateTicketStatus,
    loadSchoolCodes,
    initCharts, updateCharts, disposeCharts,
  };
}
