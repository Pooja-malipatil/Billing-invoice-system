async function loadBalances() {
  try {
    const balances = await apiFetch("/api/v1/ledger/balances");
    document.getElementById("bal-ar").textContent = formatCurrency(balances.accounts_receivable);
    document.getElementById("bal-cash").textContent = formatCurrency(balances.cash);
    document.getElementById("bal-revenue").textContent = formatCurrency(balances.revenue);
  } catch (err) { showToast(err.message, true); }
}

async function loadVerify() {
  const box = document.getElementById("ledger-verify");
  try {
    const result = await apiFetch("/api/v1/ledger/verify");
    if (result.balanced) {
      box.innerHTML = `✅ Ledger is balanced — total debits (${formatCurrency(result.total_debits)}) exactly equal total credits (${formatCurrency(result.total_credits)}).`;
      box.className = "ledger-verify ok";
    } else {
      box.innerHTML = `⚠️ Ledger is OUT OF BALANCE by ${formatCurrency(result.difference)} — this should never happen.`;
      box.className = "ledger-verify error";
    }
  } catch (err) {
    box.textContent = "Could not verify ledger.";
  }
}

async function loadEntries() {
  try {
    const entries = await apiFetch("/api/v1/ledger");
    const tbody = document.getElementById("ledger-tbody");
    const emptyState = document.getElementById("ledger-empty");
    tbody.innerHTML = "";
    emptyState.style.display = entries.length === 0 ? "block" : "none";
    entries.forEach((e) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${e.created_at}</td>
        <td>${escapeHtml(e.invoice_number || ('#' + e.invoice_id))}</td>
        <td><span class="badge ${e.entry_type === 'debit' ? 'badge-Pending' : 'badge-Paid'}">${e.entry_type}</span></td>
        <td>${e.account.replace(/_/g, ' ')}</td>
        <td>${formatCurrency(e.amount)}</td>
        <td>${escapeHtml(e.description)}</td>`;
      tbody.appendChild(tr);
    });
  } catch (err) { showToast(err.message, true); }
}

loadBalances();
loadVerify();
loadEntries();