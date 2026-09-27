const messages = document.getElementById("assistant-messages");
const input = document.getElementById("assistant-input");

function addMessage(text, sender) {
  const div = document.createElement("div");
  div.className = `assistant-msg ${sender}`;
  div.textContent = text;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

async function ask(question) {
  if (!question.trim()) return;
  addMessage(question, "user");
  input.value = "";
  try {
    const result = await apiFetch("/api/v1/assistant/ask", { method: "POST", body: JSON.stringify({ question }) });
    addMessage(result.answer, "bot");
  } catch (err) {
    addMessage("Sorry, something went wrong: " + err.message, "bot");
  }
}

document.getElementById("btn-ask").addEventListener("click", () => ask(input.value));
input.addEventListener("keypress", (e) => { if (e.key === "Enter") ask(input.value); });
document.querySelectorAll(".suggestion-chip").forEach((btn) => {
  btn.addEventListener("click", () => ask(btn.dataset.q));
});

addMessage("Ask me about overdue invoices, revenue, top products, or invoices above an amount.", "bot");