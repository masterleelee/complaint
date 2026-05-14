// Toast 通知组合式函数
export function useToast() {
  const toasts = Vue.ref([]);

  function toast(title, message, type = "info") {
    toasts.value.push({ title, message, type });
    setTimeout(() => toasts.value.shift(), 4000);
  }

  return { toasts, toast };
}
