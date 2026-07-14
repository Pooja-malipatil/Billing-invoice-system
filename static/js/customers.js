const tbody = document.getElementById("customers-tbody");
const emptyState = document.getElementById("customers-empty");
const modal = document.getElementById("customer-modal");
const form = document.getElementById("customer-form");
const modalTitle = document.getElementById("customer-modal-title");

async function loadCustomers() {
  try {
    const customers = await apiFetch("/api/customers");
    renderCustomers(customers);
  } catch (err) {
    showToast(err.message, true);
  }
}

function renderCustomers(customers) {
  tbody.innerHTML = "";
  emptyState.style.display = customers.length === 0 ? "block" : "none";

  customers.forEach((c) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(c.name)}</td>
      <td>${escapeHtml(c.email)}</td>
      <td>${escapeHtml(c.phone)}</td>
      <td>${escapeHtml(c.address)}</td>
      <td>
        <button class="btn btn-secondary btn-small" data-edit="${c.id}">Edit</button>
        <button class="btn btn-danger btn-small" data-delete="${c.id}">Delete</button>
      </td>
    `;
    tbody.appendChild(tr);
  });

  // Event delegation would work too, but with few rows this direct
  // binding per-row is simple and easy to follow.
  tbody.querySelectorAll("[data-edit]").forEach((btn) => {
    btn.addEventListener("click", () => openEditModal(customers.find(c => c.id == btn.dataset.edit)));
  });
  tbody.querySelectorAll("[data-delete]").forEach((btn) => {
    btn.addEventListener("click", () => deleteCustomer(btn.dataset.delete));
  });
}

function openAddModal() {
  form.reset();
  document.getElementById("customer-id").value = "";
  modalTitle.textContent = "Add Customer";
  modal.style.display = "flex";
}

function openEditModal(customer) {
  document.getElementById("customer-id").value = customer.id;
  document.getElementById("customer-name").value = customer.name;
  document.getElementById("customer-email").value = customer.email || "";
  document.getElementById("customer-phone").value = customer.phone || "";
  document.getElementById("customer-address").value = customer.address || "";
  modalTitle.textContent = "Edit Customer";
  modal.style.display = "flex";
}

function closeModal() {
  modal.style.display = "none";
}

async function deleteCustomer(id) {
  if (!confirm("Delete this customer? This cannot be undone.")) return;
  try {
    await apiFetch(`/api/customers/${id}`, { method: "DELETE" });
    showToast("Customer deleted");
    loadCustomers();
  } catch (err) {
    showToast(err.message, true);
  }
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
    if (id) {
      await apiFetch(`/api/customers/${id}`, { method: "PUT", body: JSON.stringify(payload) });
      showToast("Customer updated");
    } else {
      await apiFetch("/api/customers", { method: "POST", body: JSON.stringify(payload) });
      showToast("Customer added");
    }
    closeModal();
    loadCustomers();
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById("btn-new-customer").addEventListener("click", openAddModal);
document.getElementById("btn-cancel-customer").addEventListener("click", closeModal);

loadCustomers();
