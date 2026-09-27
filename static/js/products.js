const tbody = document.getElementById("products-tbody");
const emptyState = document.getElementById("products-empty");
const modal = document.getElementById("product-modal");
const form = document.getElementById("product-form");
const modalTitle = document.getElementById("product-modal-title");
const stockModal = document.getElementById("stock-modal");
const stockForm = document.getElementById("stock-form");

async function loadProducts() {
  try {
    const products = await apiFetch("/api/v1/products");
    renderProducts(products);
  } catch (err) { showToast(err.message, true); }
}

function renderProducts(products) {
  tbody.innerHTML = "";
  emptyState.style.display = products.length === 0 ? "block" : "none";
  products.forEach((p) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(p.sku)}</td>
      <td>${escapeHtml(p.name)}</td>
      <td>${formatCurrency(p.price)}</td>
      <td>${p.stock_quantity} ${p.is_low_stock ? '<span class="badge badge-Overdue">Low</span>' : ''}</td>
      <td><span class="badge ${p.is_active ? 'badge-Paid' : 'badge-Draft'}">${p.is_active ? 'Active' : 'Inactive'}</span></td>
      <td>
        <button class="btn btn-secondary btn-small" data-edit="${p.id}">Edit</button>
        <button class="btn btn-secondary btn-small" data-adjust="${p.id}">Adjust Stock</button>
        <button class="btn btn-danger btn-small" data-delete="${p.id}">Delete</button>
      </td>`;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("[data-edit]").forEach((btn) => btn.addEventListener("click", () => openEditModal(products.find(p => p.id == btn.dataset.edit))));
  tbody.querySelectorAll("[data-adjust]").forEach((btn) => btn.addEventListener("click", () => openStockModal(btn.dataset.adjust)));
  tbody.querySelectorAll("[data-delete]").forEach((btn) => btn.addEventListener("click", () => deleteProduct(btn.dataset.delete)));
}

function openAddModal() {
  form.reset();
  document.getElementById("product-id").value = "";
  document.getElementById("stock-field").style.display = "flex";
  modalTitle.textContent = "Add Product";
  modal.style.display = "flex";
}
function openEditModal(product) {
  document.getElementById("product-id").value = product.id;
  document.getElementById("product-name").value = product.name;
  document.getElementById("product-sku").value = product.sku;
  document.getElementById("product-description").value = product.description || "";
  document.getElementById("product-price").value = product.price;
  document.getElementById("product-tax").value = product.tax_percent;
  document.getElementById("product-threshold").value = product.low_stock_threshold;
  document.getElementById("stock-field").style.display = "none";
  modalTitle.textContent = "Edit Product";
  modal.style.display = "flex";
}
function closeModal() { modal.style.display = "none"; }

function openStockModal(productId) {
  document.getElementById("stock-product-id").value = productId;
  stockForm.reset();
  stockModal.style.display = "flex";
}
function closeStockModal() { stockModal.style.display = "none"; }

async function deleteProduct(id) {
  if (!confirm("Delete this product?")) return;
  try { await apiFetch(`/api/v1/products/${id}`, { method: "DELETE" }); showToast("Product deleted"); loadProducts(); }
  catch (err) { showToast(err.message, true); }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("product-id").value;
  const payload = {
    name: document.getElementById("product-name").value.trim(),
    sku: document.getElementById("product-sku").value.trim(),
    description: document.getElementById("product-description").value.trim(),
    price: parseFloat(document.getElementById("product-price").value),
    tax_percent: parseFloat(document.getElementById("product-tax").value) || 0,
    stock_quantity: parseInt(document.getElementById("product-stock").value) || 0,
    low_stock_threshold: parseInt(document.getElementById("product-threshold").value) || 5,
    is_active: true,
  };
  try {
    if (id) { await apiFetch(`/api/v1/products/${id}`, { method: "PUT", body: JSON.stringify(payload) }); showToast("Product updated"); }
    else { await apiFetch("/api/v1/products", { method: "POST", body: JSON.stringify(payload) }); showToast("Product added"); }
    closeModal(); loadProducts();
  } catch (err) { showToast(err.message, true); }
});

stockForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const productId = document.getElementById("stock-product-id").value;
  const payload = {
    change_amount: parseInt(document.getElementById("stock-change").value),
    reason: document.getElementById("stock-reason").value.trim() || "Manual adjustment",
  };
  try {
    await apiFetch(`/api/v1/products/${productId}/adjust-stock`, { method: "POST", body: JSON.stringify(payload) });
    showToast("Stock adjusted"); closeStockModal(); loadProducts();
  } catch (err) { showToast(err.message, true); }
});

document.getElementById("btn-new-product").addEventListener("click", openAddModal);
document.getElementById("btn-cancel-product").addEventListener("click", closeModal);
document.getElementById("btn-cancel-stock").addEventListener("click", closeStockModal);
loadProducts();