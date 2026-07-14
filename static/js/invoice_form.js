const form = document.getElementById("invoice-form");
const invoiceId = form.dataset.invoiceId || null;
const customerSelect = document.getElementById("customer-select");
const itemsTbody = document.getElementById("items-tbody");
const taxInput = document.getElementById("tax-percent");

let itemCounter = 0;

// ---- Line item rows -------------------------------------------------
function addItemRow(item = { item_name: "", quantity: 1, price: 0 }) {
  const rowId = `item-${itemCounter++}`;
  const tr = document.createElement("tr");
  tr.id = rowId;
  tr.innerHTML = `
    <td><input type="text" class="item-name" value="${escapeHtml(item.item_name)}" placeholder="e.g. Web hosting" required></td>
    <td><input type="number" class="item-qty" value="${item.quantity}" min="0.01" step="0.01" required></td>
    <td><input type="number" class="item-price" value="${item.price}" min="0" step="0.01" required></td>
    <td class="line-total">₹0.00</td>
    <td><button type="button" class="btn btn-danger btn-small" data-remove-row>&times;</button></td>
  `;
  itemsTbody.appendChild(tr);

  tr.querySelectorAll("input").forEach((input) => input.addEventListener("input", recalculate));
  tr.querySelector("[data-remove-row]").addEventListener("click", () => {
    tr.remove();
    recalculate();
  });
  recalculate();
}

function removeAllRows() {
  itemsTbody.innerHTML = "";
}

// ---- Live totals (mirrors the backend's recalculate_totals logic) ---
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

// ---- Load customers into the dropdown --------------------------------
async function loadCustomers(selectedId = null) {
  const customers = await apiFetch("/api/customers");
  customerSelect.innerHTML = customers
    .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
    .join("");
  if (selectedId) customerSelect.value = selectedId;
}

// ---- If editing, load the existing invoice ----------------------------
async function loadExistingInvoice() {
  const invoice = await apiFetch(`/api/invoices/${invoiceId}`);
  await loadCustomers(invoice.customer_id);
  document.getElementById("invoice-date").value = invoice.invoice_date;
  document.getElementById("due-date").value = invoice.due_date;
  document.getElementById("tax-percent").value = invoice.tax_percent;
  document.getElementById("status-select").value = invoice.status;

  removeAllRows();
  invoice.items.forEach((item) => addItemRow(item));
}

function setDefaultDates() {
  const today = new Date().toISOString().split("T")[0];
  document.getElementById("invoice-date").value = today;
  const due = new Date();
  due.setDate(due.getDate() + 14); // default: due in 14 days
  document.getElementById("due-date").value = due.toISOString().split("T")[0];
}

// ---- Submit -------------------------------------------------------------
form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const items = Array.from(itemsTbody.querySelectorAll("tr")).map((tr) => ({
    item_name: tr.querySelector(".item-name").value.trim(),
    quantity: parseFloat(tr.querySelector(".item-qty").value),
    price: parseFloat(tr.querySelector(".item-price").value),
  }));

  if (items.length === 0) {
    showToast("Add at least one line item", true);
    return;
  }

  const payload = {
    customer_id: parseInt(customerSelect.value, 10),
    invoice_date: document.getElementById("invoice-date").value,
    due_date: document.getElementById("due-date").value,
    tax_percent: parseFloat(taxInput.value) || 0,
    status: document.getElementById("status-select").value,
    items,
  };

  try {
    if (invoiceId) {
      await apiFetch(`/api/invoices/${invoiceId}`, { method: "PUT", body: JSON.stringify(payload) });
      showToast("Invoice updated");
    } else {
      await apiFetch("/api/invoices", { method: "POST", body: JSON.stringify(payload) });
      showToast("Invoice created");
    }
    window.location.href = "/invoices";
  } catch (err) {
    showToast(err.message, true);
  }
});

// ---- Init -----------------------------------------------------------------
(async function init() {
  try {
    if (invoiceId) {
      await loadExistingInvoice();
    } else {
      await loadCustomers();
      setDefaultDates();
      addItemRow(); // start with one empty row
    }
  } catch (err) {
    showToast(err.message, true);
  }
})();
