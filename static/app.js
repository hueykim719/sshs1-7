// PWA Service Worker
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/service-worker.js');
}

// 완료 토글: 이벤트 델리게이션
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.check');
  if (!btn) return;
  const id = btn.dataset.taskId;
  if (!id) return;
  const r = await fetch(`/tasks/complete/${id}`, { method: 'POST' });
  if (r.ok) location.reload();
  else alert('변경 실패');
});
