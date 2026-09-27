const bell = document.getElementById("notif-bell");
const badge = document.getElementById("notif-badge");
const dropdown = document.getElementById("notif-dropdown");

async function loadNotifications() {
  try {
    const data = await apiFetch("/api/v1/notifications");
    badge.textContent = data.unread_count;
    badge.style.display = data.unread_count > 0 ? "inline-block" : "none";
    if (data.notifications.length === 0) {
      dropdown.innerHTML = `<p class="empty-state">No notifications yet.</p>`;
      return;
    }
    dropdown.innerHTML = data.notifications.map(n => `
      <div class="notif-item ${n.is_read ? '' : 'unread'}" data-id="${n.id}">
        <span class="notif-type">${n.type.replace('_', ' ')}</span>
        <p>${escapeHtml(n.message)}</p>
        <span class="notif-time">${n.created_at}</span>
      </div>
    `).join("");
    dropdown.querySelectorAll(".notif-item.unread").forEach(el => {
      el.addEventListener("click", async () => {
        await apiFetch(`/api/v1/notifications/${el.dataset.id}/read`, { method: "PATCH" });
        el.classList.remove("unread");
        loadNotifications();
      });
    });
  } catch (err) { /* silent fail - notifications are not critical path */ }
}

bell.addEventListener("click", () => {
  dropdown.style.display = dropdown.style.display === "none" ? "block" : "none";
});
document.addEventListener("click", (e) => {
  if (!bell.contains(e.target) && !dropdown.contains(e.target)) {
    dropdown.style.display = "none";
  }
});

loadNotifications();
setInterval(loadNotifications, 30000);