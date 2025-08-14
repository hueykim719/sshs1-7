// PWA SW
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/service-worker.js');
}

async function toggleComplete(id){
  const r = await fetch(`/tasks/complete/${id}`, {method:'POST'});
  if (r.ok) location.reload(); else alert('변경 실패');
}
