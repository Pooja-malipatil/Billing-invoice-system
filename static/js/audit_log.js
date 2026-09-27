async function loadAuditLog() {
  try {
    const entries = await apiFetch("/api/v1/audit-log");
    const tbody = document.getElementById("audit-tbody");
    const emptyState = document.getElementById("audit-empty");
    tbody.innerHTML = "";
    emptyState.style.display = entries.length === 0 ? "block" : "none";
    entries.forEach((e) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${e.created_at}</td>
        <td>${escapeHtml(e.username)}</td>
        <td><span class="badge badge-Pending">${e.action.replace(/_/g, ' ')}</span></td>
        <td>${escapeHtml(e.details || '')}</td>`;
      tbody.appendChild(tr);
    });
  } catch (err) { showToast(err.message, true); }
}
loadAuditLog();