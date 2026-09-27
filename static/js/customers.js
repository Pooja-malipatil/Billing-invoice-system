const tbody = document.getElementById("customers-tbody");
const emptyState = document.getElementById("customers-empty");
const modal = document.getElementById("customer-modal");
const form = document.getElementById("customer-form");
const modalTitle = document.getElementById("customer-modal-title");

async function loadCustomers() {
  try {
    const customers = await apiFetch("/api/v1/customers");
    renderCustomers(customers);
  } catch (err) { showToast(err.message, true); }
}

function renderCustomers(customers) {
  tbody.innerHTML = "";
  emptyState.style.display = customers.length === 0 ? "block" : "none";
  customers.forEach((c) => {
    const tr = document.createElement("tr");
    tr.id = `customer-row-${c.id}`;
    tr.innerHTML = `<td>${escapeHtml(c.name)}</td><td>${escapeHtml(c.email)}</td><td>${escapeHtml(c.phone)}</td><td>${escapeHtml(c.address)}</td>
      <td class="risk-cell" data-risk-for="${c.id}">…</td>
      <td><button class="btn btn-secondary btn-small" data-edit="${c.id}">Edit</button><button class="btn btn-danger btn-small" data-delete="${c.id}">Delete</button></td>`;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("[data-edit]").forEach((btn) => btn.addEventListener("click", () => openEditModal(customers.find(c => c.id == btn.dataset.edit))));
  tbody.querySelectorAll("[data-delete]").forEach((btn) => btn.addEventListener("click", () => deleteCustomer(btn.dataset.delete)));
  customers.forEach((c) => loadRiskScore(c.id));
}

async function loadRiskScore(customerId) {
  const cell = document.querySelector(`[data-risk-for="${customerId}"]`);
  if (!cell) return;
  try {
    const risk = await apiFetch(`/api/v1/customers/${customerId}/risk-score`);
    cell.innerHTML = `<span class="risk-${risk.risk_label}" title="${escapeHtml(risk.reasons.join('; '))}">${risk.risk_label}</span>`;
  } catch (err) {
    cell.textContent = "—";
  }
}

function openAddModal() { form.reset(); document.getElementById("customer-id").value = ""; modalTitle.textContent = "Add Customer"; modal.style.display = "flex"; }
function openEditModal(customer) {
  document.getElementById("customer-id").value = customer.id;
  document.getElementById("customer-name").value = customer.name;
  document.getElementById("customer-email").value = customer.email || "";
  document.getElementById("customer-phone").value = customer.phone || "";
  document.getElementById("customer-address").value = customer.address || "";
  modalTitle.textContent = "Edit Customer"; modal.style.display = "flex";
}
function closeModal() { modal.style.display = "none"; }
async function deleteCustomer(id) {
  if (!confirm("Delete this customer? This cannot be undone.")) return;
  try { await apiFetch(`/api/v1/customers/${id}`, { method: "DELETE" }); showToast("Customer deleted"); loadCustomers(); }
  catch (err) { showToast(err.message, true); }
}
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("customer-id").value;
  const payload = {
    name: document.getElementById("customer-name").value.trim(),
    email: document.getElementById("customer-email").value.trim(),
    phone: document.getElementById("customer-phone").value.trim(),
    address: document.getElementById("customer-address").value.trim(),
  };
  try {
    if (id) { await apiFetch(`/api/v1/customers/${id}`, { method: "PUT", body: JSON.stringify(payload) }); showToast("Customer updated"); }
    else { await apiFetch("/api/v1/customers", { method: "POST", body: JSON.stringify(payload) }); showToast("Customer added"); }
    closeModal(); loadCustomers();
  } catch (err) { showToast(err.message, true); }
});
document.getElementById("btn-new-customer").addEventListener("click", openAddModal);
document.getElementById("btn-cancel-customer").addEventListener("click", closeModal);
loadCustomers();