// 工具函数
export function stBadge(s) {
  if (!s) return "badge-default";
  if (s.includes("待处理")) return "badge-pending";
  if (s.includes("处理中")) return "badge-processing";
  if (s.includes("已完结") || s.includes("已归档")) return "badge-completed";
  if (s.includes("退学") || s.includes("注销")) return "bg-danger";
  if (s.includes("领证") || s.includes("结业")) return "bg-success";
  return "badge-default";
}

export function getTrainingTime(details, subject, field) {
  if (!details) return "-";
  const item = details.find((t) => t.subject === subject);
  return item && item[field] ? item[field] : "-";
}

export function getEventType(title) {
  if (!title) return "其他";
  if (title.includes("报名")) return "报名";
  if (title.includes("总校收")) return "总校收";
  if (title.includes("录高拍")) return "录高拍仪";
  if (title.includes("受理提交")) return "受理提交";
  if (title.includes("受理通过")) return "受理通过";
  if (title.includes("科一收") || title.includes("科目一")) return "科目一";
  if (title.includes("科二") || title.includes("科目二")) return "科目二";
  if (title.includes("科三") || title.includes("科目三")) return "科目三";
  if (title.includes("科四") || title.includes("科目四")) return "科目四";
  return "其他";
}

export function todayStr() {
  return new Date().toISOString().split("T")[0];
}
