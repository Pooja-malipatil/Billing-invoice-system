const form = document.getElementById("invoice-form");
const invoiceId = form.dataset.invoiceId || null;
const customerSelect = document.getElementById("customer-select");
const itemsTbody = document.getElementById("items-tbody");
const taxInput = document.getElementById("tax-percent");
let itemCounter = 0;
let availableProducts = [];

function addItemRow(item = { item_name: "", quantity: 1, price: 0, product_id: null }) {
  const rowId = `item-${itemCounter++}`;
  const tr = document.createElement("tr");
  tr.id = rowId;
  const productOptions = `<option value="">-- none (freeform) --</option>` +
    availableProducts.map(p => `<option value="${p.id}" ${p.id == item.product_id ? 'selected' : ''}>${escapeHtml(p.name)} (${p.stock_quantity} in stock)</option>`).join("");
  tr.innerHTML = `
    <td><select class="item-product">${productOptions}</select></td>
    <td><input type="text" class="item-name" value="${escapeHtml(item.item_name)}" placeholder="e.g. Web hosting" required></td>
    <td><input type="number" class="item-qty" value="${item.quantity}" min="0.01" step="0.01" required></td>
    <td><input type="number" class="item-price" value="${item.price}" min="0" step="0.01" required></td>
    <td class="line-total">₹0.00</td>
    <td><button type="button" class="btn btn-danger btn-small" data-remove-row>&times;</button></td>`;
  itemsTbody.appendChild(tr);
  tr.querySelectorAll("input").forEach((input) => input.addEventListener("input", recalculate));
  tr.querySelector(".item-product").addEventListener("change", (e) => {
    const product = availableProducts.find(p => p.id == e.target.value);
    if (product) {
      tr.querySelector(".item-name").value = product.name;
      tr.querySelector(".item-price").value = product.price;
      recalculate();
    }
  });
  tr.querySelector("[data-remove-row]").addEventListener("click", () => { tr.remove(); recalculate(); });
  recalculate();
}
function removeAllRows() { itemsTbody.innerHTML = ""; }
function recalculate() {
  let subtotal = 0;
  itemsTbody.querySelectorAll("tr").forEach((tr) => {
    const qty = parseFloat(tr.querySelector(".item-qty").value) || 0;
    const price = parseFloat(tr.querySelector(".item-price").value) || 0;
    const lineTotal = qty * price;
    tr.querySelector(".line-total").textContent = formatCurrency(lineTotal);
    subtotal += lineTotal;
  });
  const taxPercent = parseFloat(taxInput.value) || 0;
  const taxAmount = subtotal * taxPercent / 100;
  const total = subtotal + taxAmount;
  document.getElementById("calc-subtotal").textContent = formatCurrency(subtotal);
  document.getElementById("calc-tax").textContent = formatCurrency(taxAmount);
  document.getElementById("calc-total").textContent = formatCurrency(total);
}
taxInput.addEventListener("input", recalculate);
document.getElementById("btn-add-item").addEventListener("click", () => addItemRow());

async function loadCustomers(selectedId = null) {
  const customers = await apiFetch("/api/customers");
  customerSelect.innerHTML = customers.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
  if (selectedId) customerSelect.value = selectedId;
}
async function loadProducts() {
  try { availableProducts = await apiFetch("/api/products"); } catch (err) { availableProducts = []; }
}

async function loadExistingInvoice() {
  const invoice = await apiFetch(`/api/invoices/${invoiceId}`);
  await loadCustomers(invoice.customer_id);
  document.getElementById("invoice-date").value = invoice.invoice_date;
  document.getElementById("due-date").value = invoice.due_date;
  document.getElementById("tax-percent").value = invoice.tax_percent;
  document.getElementById("status-select").value = invoice.status;
  removeAllRows();
  invoice.items.forEach((item) => addItemRow(item));
  renderPayments(invoice.payments, invoice.total);
}
function setDefaultDates() {
  const today = new Date().toISOString().split("T")[0];
  document.getElementById("invoice-date").value = today;
  const due = new Date(); due.setDate(due.getDate() + 14);
  document.getElementById("due-date").value = due.toISOString().split("T")[0];
}
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const items = Array.from(itemsTbody.querySelectorAll("tr")).map((tr) => ({
    product_id: tr.querySelector(".item-product").value || null,
    item_name: tr.querySelector(".item-name").value.trim(),
    quantity: parseFloat(tr.querySelector(".item-qty").value),
    price: parseFloat(tr.querySelector(".item-price").value),
  }));
  if (items.length === 0) { showToast("Add at least one line item", true); return; }
  const payload = {
    customer_id: parseInt(customerSelect.value, 10),
    invoice_date: document.getElementById("invoice-date").value,
    due_date: document.getElementById("due-date").value,
    tax_percent: parseFloat(taxInput.value) || 0,
    status: document.getElementById("status-select").value,
    items,
  };
  try {
    if (invoiceId) { await apiFetch(`/api/invoices/${invoiceId}`, { method: "PUT", body: JSON.stringify(payload) }); showToast("Invoice updated"); }
    else { await apiFetch("/api/invoices", { method: "POST", body: JSON.stringify(payload) }); showToast("Invoice created"); }
    window.location.href = "/invoices";
  } catch (err) { showToast(err.message, true); }
});

function renderPayments(payments, total) {
  const tbody = document.getElementById("payments-tbody");
  const emptyState = document.getElementById("payments-empty");
  if (!tbody) return;
  tbody.innerHTML = "";
  emptyState.style.display = payments.length === 0 ? "block" : "none";
  payments.forEach((p) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${p.paid_on}</td><td>${formatCurrency(p.amount)}</td><td>${escapeHtml(p.payment_method || '')}</td><td>${escapeHtml(p.reference_id || '')}</td><td>${escapeHtml(p.note || "")}</td>
      <td><button type="button" class="btn btn-danger btn-small" data-delete-payment="${p.id}">Delete</button></td>`;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("[data-delete-payment]").forEach((btn) => btn.addEventListener("click", () => deletePayment(btn.dataset.deletePayment)));
  const amountPaid = payments.reduce((sum, p) => sum + p.amount, 0);
  document.getElementById("amount-paid").textContent = formatCurrency(amountPaid);
  document.getElementById("amount-due").textContent = formatCurrency(total - amountPaid);
}
async function deletePayment(id) {
  if (!confirm("Delete this payment record?")) return;
  try { await apiFetch(`/api/payments/${id}`, { method: "DELETE" }); showToast("Payment deleted"); await loadExistingInvoice(); }
  catch (err) { showToast(err.message, true); }
}
const paymentForm = document.getElementById("payment-form");
if (paymentForm) {
  document.getElementById("payment-date").value = new Date().toISOString().split("T")[0];
  paymentForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      amount: parseFloat(document.getElementById("payment-amount").value),
      paid_on: document.getElementById("payment-date").value,
      payment_method: document.getElementById("payment-method").value,
      reference_id: document.getElementById("payment-reference").value.trim(),
      note: document.getElementById("payment-note").value.trim(),
    };
    try {
      await apiFetch(`/api/invoices/${invoiceId}/payments`, { method: "POST", body: JSON.stringify(payload) });
      showToast("Payment recorded"); paymentForm.reset();
      document.getElementById("payment-date").value = new Date().toISOString().split("T")[0];
      await loadExistingInvoice();
    } catch (err) { showToast(err.message, true); }
  });
}
(async function init() {
  try {
    await loadProducts();
    if (invoiceId) { await loadExistingInvoice(); }
    else { await loadCustomers(); setDefaultDates(); addItemRow(); }
  } catch (err) { showToast(err.message, true); }
})();
