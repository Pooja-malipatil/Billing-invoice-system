const tbody = document.getElementById("invoices-tbody");
const emptyState = document.getElementById("invoices-empty");
const searchInput = document.getElementById("filter-search");
const statusSelect = document.getElementById("filter-status");
let debounceTimer;

async function loadInvoices() {
  const params = new URLSearchParams();
  if (searchInput.value.trim()) params.set("search", searchInput.value.trim());
  if (statusSelect.value) params.set("status", statusSelect.value);
  try {
    const data = await apiFetch(`/api/invoices?${params.toString()}`);
    renderInvoices(data.invoices);  // API now returns {invoices, page, total_pages, ...} for pagination
  } catch (err) { showToast(err.message, true); }
}
function renderInvoices(invoices) {
  tbody.innerHTML = "";
  emptyState.style.display = invoices.length === 0 ? "block" : "none";
  invoices.forEach((inv) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${inv.invoice_number || '#' + inv.id}</td><td>${escapeHtml(inv.customer_name)}</td><td>${inv.invoice_date}</td><td>${inv.due_date}</td>
      <td>${formatCurrency(inv.total)}</td><td>${formatCurrency(inv.amount_due)}</td><td><span class="badge badge-${inv.status}">${inv.status}</span></td>
      <td><a class="btn btn-secondary btn-small" href="/invoices/${inv.id}/edit">Edit</a>
      ${inv.status !== "Paid" ? `<button class="btn btn-secondary btn-small" data-mark-paid="${inv.id}">Mark Paid</button>` : ""}
      <button class="btn btn-danger btn-small" data-delete="${inv.id}">Delete</button></td>`;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("[data-mark-paid]").forEach((btn) => btn.addEventListener("click", () => markPaid(btn.dataset.markPaid)));
  tbody.querySelectorAll("[data-delete]").forEach((btn) => btn.addEventListener("click", () => deleteInvoice(btn.dataset.delete)));
}
async function markPaid(id) {
  try { await apiFetch(`/api/invoices/${id}/status`, { method: "PATCH", body: JSON.stringify({ status: "Paid" }) }); showToast("Invoice marked as paid"); loadInvoices(); }
  catch (err) { showToast(err.message, true); }
}
async function deleteInvoice(id) {
  if (!confirm("Delete this invoice? This cannot be undone.")) return;
  try { await apiFetch(`/api/invoices/${id}`, { method: "DELETE" }); showToast("Invoice deleted"); loadInvoices(); }
  catch (err) { showToast(err.message, true); }
}
searchInput.addEventListener("input", () => { clearTimeout(debounceTimer); debounceTimer = setTimeout(loadInvoices, 300); });
statusSelect.addEventListener("change", loadInvoices);
document.getElementById("btn-export-csv").addEventListener("click", () => {
  const params = new URLSearchParams();
  if (searchInput.value.trim()) params.set("search", searchInput.value.trim());
  if (statusSelect.value) params.set("status", statusSelect.value);
  window.location.href = `/api/invoices/export?${params.toString()}`;
});
loadInvoices();
