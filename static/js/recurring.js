const tbody = document.getElementById("recurring-tbody");
const emptyState = document.getElementById("recurring-empty");
const modal = document.getElementById("rule-modal");
const form = document.getElementById("rule-form");
const customerSelect = document.getElementById("rule-customer");

async function loadRules() {
  try {
    const rules = await apiFetch("/api/v1/recurring");
    renderRules(rules);
  } catch (err) { showToast(err.message, true); }
}

function renderRules(rules) {
  tbody.innerHTML = "";
  emptyState.style.display = rules.length === 0 ? "block" : "none";
  rules.forEach((r) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(r.customer_name)}</td>
      <td>${escapeHtml(r.item_name)}</td>
      <td>${formatCurrency(r.quantity * r.price)}</td>
      <td>${r.frequency}</td>
      <td>${r.next_invoice_date}</td>
      <td><button class="btn btn-danger btn-small" data-delete="${r.id}">Delete</button></td>`;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("[data-delete]").forEach((btn) => btn.addEventListener("click", () => deleteRule(btn.dataset.delete)));
}

async function deleteRule(id) {
  if (!confirm("Delete this recurring rule?")) return;
  try { await apiFetch(`/api/v1/recurring/${id}`, { method: "DELETE" }); showToast("Rule deleted"); loadRules(); }
  catch (err) { showToast(err.message, true); }
}

async function loadCustomersIntoSelect() {
  const customers = await apiFetch("/api/v1/customers");
  customerSelect.innerHTML = customers.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
}

function openModal() {
  form.reset();
  document.getElementById("rule-date").value = new Date().toISOString().split("T")[0];
  modal.style.display = "flex";
}
function closeModal() { modal.style.display = "none"; }

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = {
    customer_id: parseInt(customerSelect.value),
    item_name: document.getElementById("rule-item").value.trim(),
    quantity: parseFloat(document.getElementById("rule-qty").value),
    price: parseFloat(document.getElementById("rule-price").value),
    tax_percent: parseFloat(document.getElementById("rule-tax").value) || 0,
    frequency: document.getElementById("rule-frequency").value,
    next_invoice_date: document.getElementById("rule-date").value,
  };
  try {
    await apiFetch("/api/v1/recurring", { method: "POST", body: JSON.stringify(payload) });
    showToast("Recurring rule created"); closeModal(); loadRules();
  } catch (err) { showToast(err.message, true); }
});

document.getElementById("btn-new-rule").addEventListener("click", async () => { await loadCustomersIntoSelect(); openModal(); });
document.getElementById("btn-cancel-rule").addEventListener("click", closeModal);
document.getElementById("btn-run-recurring").addEventListener("click", async () => {
  try {
    const result = await apiFetch("/api/v1/recurring/run", { method: "POST" });
    showToast(`${result.count} invoice(s) generated`);
    loadRules();
  } catch (err) { showToast(err.message, true); }
});

loadRules();