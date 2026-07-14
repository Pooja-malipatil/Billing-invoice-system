async function loadDashboard() {
  try {
    const stats = await apiFetch("/api/dashboard");
    document.getElementById("stat-revenue").textContent = formatCurrency(stats.total_revenue);
    document.getElementById("stat-pending").textContent = formatCurrency(stats.total_pending);
    document.getElementById("stat-overdue-count").textContent = stats.overdue_count;
    document.getElementById("stat-overdue-amount").textContent = formatCurrency(stats.overdue_amount);
    document.getElementById("stat-total-invoices").textContent = stats.total_invoices;
  } catch (err) {
    showToast(err.message, true);
  }
}

loadDashboard();
