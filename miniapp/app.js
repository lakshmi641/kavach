const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const sampleMessages = {
  arrest: "This is a CBI officer. You are under digital arrest. Do not tell anyone. Pay Rs 50,000 immediately to avoid arrest. Your OTP is 482913 and UPI is fraud.help@okaxis.",
  "new-number": "Mom, it's Ravi, I changed my number. Please send money urgently, don't tell anyone.",
  ordinary: "Reached home safely. See you tomorrow for lunch."
};
let config = null;
let activeCase = null;
const tg = window.Telegram?.WebApp;

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options,
    headers: { ...(options.body instanceof FormData || options.body instanceof ArrayBuffer ? {} : {"Content-Type":"application/json"}), ...(options.headers || {}) } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}
function toast(message) {
  const el = $("#toast"); el.textContent = message; el.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { el.hidden = true; }, 3500);
}
function navigate(view) {
  $$(".view").forEach(el => el.classList.toggle("active", el.id === `${view}-view`));
  $$(".nav-item").forEach(el => el.classList.toggle("selected", el.dataset.nav === view));
  if (view === "history") loadHistory();
  if (view === "setup") loadSetup();
  window.scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
}
function outcomeLabel(outcome) {
  return outcome === "VERIFIED" ? "Identity route verified" : outcome === "LIKELY_SCAM" ? "Likely scam" : "Pause and verify";
}
function renderResult(data) {
  activeCase = data;
  const panel = $("#result"); panel.hidden = false; panel.dataset.outcome = data.outcome;
  const signals = Object.entries(data.signals || {}).filter(([key, value]) => key !== "claimed_family_name" && value).map(([key]) => key.replaceAll("_", " "));
  const citation = (data.evidence || []).map(item => `<a class="source" href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.title)} · ${escapeHtml(item.published_date)}<br><span>${escapeHtml(item.domain)}</span><br>${escapeHtml(item.quote || "")}</a>`).join("");
  const traceRows = (data.traces || []).map(t => `<li>${escapeHtml(t.step)} · ${Math.round(t.latency_ms || 0)} ms · ${t.cost_rupees == null || !data.cost_reporting_configured ? "cost not configured" : `₹${Number(t.cost_rupees).toFixed(3)}`}</li>`).join("");
  const reasons = (data.reasons || []).map(reason => `<li>${escapeHtml(reason)}</li>`).join("");
  panel.innerHTML = `<div class="result-top"><span class="status-dot"></span><h2>${outcomeLabel(data.outcome)}</h2></div>
    <p>${data.outcome === "VERIFIED" ? "A pre-enrolled family member answered from their bound Telegram account. This does not make a payment request safe." : data.outcome === "LIKELY_SCAM" ? "Stop. Do not pay or share a code. Contact your family using a saved number." : "The identity is not confirmed yet. Do not send money or share a code."}</p>
    ${data.media_note ? `<p class="outcome-note">${escapeHtml(data.media_note)}</p>` : ""}
    ${data.pending ? `<p class="outcome-note">A verifier has been asked to confirm. This request expires in 10 minutes.</p>` : ""}
    ${data.pending && config?.demo ? `<div class="result-actions"><button class="small-button" id="demo-confirm">Demo: verifier confirms</button><button class="small-button" id="demo-reject">Demo: verifier rejects</button></div><p class="small-note">Synthetic simulation only. This control is disabled when live Telegram is connected.</p>` : ""}
    <p class="section-kicker">Protected view sent for analysis</p><div class="redaction-box">${escapeHtml(data.redacted_text || "")}</div>
    <details><summary>Why Kavach paused</summary><ul>${reasons}${signals.map(s => `<li>Signal found: ${escapeHtml(s)}</li>`).join("")}</ul><p>${escapeHtml(data.explanation || "Identity verification is still required.")}</p>${citation || "<p class='muted'>No applicable dated official advisory found. Identity verification is still required.</p>"}</details>
    <details><summary>Steps, timing and cost</summary><p>Rule: ${escapeHtml(data.rule_fired || "policy pause")}</p><p>Model cost estimate: ${data.cost_reporting_configured ? `₹${Number(data.cost_rupees || 0).toFixed(3)}` : "Not configured. Add model token rates to the service settings."}</p><ul>${traceRows}</ul></details>
    <div class="result-actions">${data.circuit_breaker_opened ? `<button class="small-button" id="open-pause">Open Circuit-Breaker</button>` : ""}${data.outcome !== "VERIFIED" ? `<button class="small-button" id="refresh-evidence">Refresh official evidence</button>` : ""}</div>`;
  $("#open-pause")?.addEventListener("click", () => showPause(data));
  $("#demo-confirm")?.addEventListener("click", () => simulateConfirmation(data, true));
  $("#demo-reject")?.addEventListener("click", () => simulateConfirmation(data, false));
  $("#refresh-evidence")?.addEventListener("click", async () => {
    try { const fresh = await api(`/api/cases/${encodeURIComponent(data.case_id)}/refresh-evidence`, {method:"POST"}); data.evidence = fresh.evidence; renderResult(data); toast(fresh.message); }
    catch (err) { toast(err.message); }
  });
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}
async function simulateConfirmation(data, confirmed) {
  try { renderResult(await api(`/api/demo/cases/${encodeURIComponent(data.case_id)}/confirmation`, {method:"POST", body:JSON.stringify({confirmed})})); }
  catch (err) { toast(err.message); }
}
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch])); }
async function runCheck(text, kind = "text") {
  if (!text.trim()) { toast("Add the message first."); return; }
  const button = $("#check-button"); button.disabled = true; button.textContent = "Checking…";
  try { renderResult(await api("/api/check", {method:"POST", body:JSON.stringify({text, kind})})); }
  catch (err) { toast(err.message); }
  finally { button.disabled = false; button.textContent = "Check this message"; }
}
async function showPause(data) {
  navigate("pause");
  $("#pause-title").textContent = data?.circuit_breaker_text || "Pause. Do not pay.";
  const call = $("#call-link");
  call.hidden = true;
  if (data?.case_id) {
    try { const callback = await api(`/api/cases/${encodeURIComponent(data.case_id)}/callback`); call.href = callback.tel; call.hidden = false; }
    catch { /* No contact-shared phone is configured. */ }
  }
  const recovery = await api("/api/recovery").catch(() => ({steps:["Call your bank using its official number.","Keep screenshots and transaction IDs.","Do not send more money."]}));
  $("#recovery-steps").innerHTML = recovery.steps.map(step => `<li>${escapeHtml(step)}</li>`).join("");
  const recording = $("#recording"); recording.hidden = !config?.family?.recording_available || !data?.circuit_breaker_opened;
  if (!recording.hidden) recording.src = "/api/recording";
  if (data?.circuit_breaker_opened) $("#pause-title").focus?.();
}
async function loadSetup() {
  try {
    config = await api("/api/config");
    const form = $("#setup-form");
    form.elements.parent_name.value = config.family.parent_name;
    form.elements.language.value = config.family.language;
    form.elements.escalation_rule.value = config.family.escalation_rule || "high_risk";
    form.elements.consent.checked = config.family.consented;
    $("#mode-badge").textContent = config.demo ? "Synthetic demo" : "Protected session";
    $("#guardian-select").innerHTML = config.contacts.map(c => `<option value="${escapeHtml(c.contact_id)}">${escapeHtml(c.name)}</option>`).join("");
    form.elements.guardian_contact_id.value = config.family.guardian_contact_id;
    $("#contact-list").innerHTML = config.contacts.map(c => `<div class="contact-row"><div><div class="person-name">${escapeHtml(c.name)}${c.is_guardian ? " · guardian" : ""}</div><div class="person-meta">${escapeHtml(c.relationship)}${c.phone_available ? " · callback number shared" : ""}</div></div><span class="bound-status ${c.bound ? "" : "unbound"}">${c.bound ? "Telegram bound" : "Invite needed"}</span>${!c.bound ? `<button class="small-button" data-invite="${escapeHtml(c.contact_id)}">Invite</button>` : ""}</div>`).join("") || `<p class="empty-state">No verifiers have been added.</p>`;
    $$('[data-invite]').forEach(button => button.addEventListener("click", async () => {
      try { const item = await api(`/api/contacts/${button.dataset.invite}/invite`, {method:"POST"}); await navigator.clipboard?.writeText(item.instruction); toast(item.instruction); }
      catch (err) { toast(err.message); }
    }));
  } catch (err) { toast(err.message); }
}
async function loadHistory() {
  try {
    const {cases} = await api("/api/history");
    $("#history-list").innerHTML = cases.length ? cases.map(c => `<button class="history-row" data-case="${escapeHtml(c.case_id)}"><span><span class="person-name">${outcomeLabel(c.outcome)}</span><span class="person-meta">${new Date(c.created_at).toLocaleString()}</span></span><span class="history-status ${c.outcome === "LIKELY_SCAM" ? "scam" : c.outcome === "VERIFIED" ? "verified" : ""}">${c.pending ? "Waiting" : c.outcome}</span></button>`).join("") : `<p class="empty-state">Checks will appear here. Nothing from your family has been checked yet.</p>`;
    $$('[data-case]').forEach(row => row.addEventListener("click", async () => { try { renderResult(await api(`/api/cases/${row.dataset.case}`)); navigate("check"); } catch (err) { toast(err.message); } }));
    const audit = await api("/api/audit");
    $("#audit-list").innerHTML = audit.entries.length ? audit.entries.slice(0,16).map(e => `<div class="audit-item"><span>${escapeHtml(e.event.replaceAll("_"," "))}</span><time>${new Date(e.at).toLocaleTimeString()}</time></div>`).join("") : `<p class="empty-state">Audit events will appear when a check runs.</p>`;
  } catch (err) { toast(err.message); }
}
async function initialize() {
  if (tg?.initData) {
    tg.ready(); tg.expand();
    await api("/api/session", {method:"POST", body:JSON.stringify({initData:tg.initData})}).catch(err => toast(err.message));
  } else {
    await api("/api/demo/session", {method:"POST", body:"{}"}).catch(() => {});
    document.body.classList.add("browser-demo");
  }
  try { config = await api("/api/config"); $("#mode-badge").textContent = config.demo ? "Synthetic demo" : "Protected session"; }
  catch { $("#mode-badge").textContent = "Setup needed"; }
  const caseId = new URLSearchParams(location.search).get("case");
  if (location.pathname === "/pause" && caseId) {
    try { const data = await api(`/api/cases/${encodeURIComponent(caseId)}`); await showPause(data); }
    catch (err) { toast(err.message); navigate("check"); }
  }
}

$("#check-form").addEventListener("submit", async event => { event.preventDefault(); const file = $("#media-file").files[0];
  if (file) {
    const button = $("#check-button"); button.disabled = true; button.textContent = "Receiving upload…";
    try { const response = await fetch("/api/check/upload", {method:"POST", body:file, headers:{"Content-Type":file.type || "application/octet-stream"}, credentials:"same-origin"}); const data = await response.json(); if (!response.ok) throw new Error(data.detail); renderResult(data); }
    catch (err) { toast(err.message); } finally { button.disabled = false; button.textContent = "Check this message"; }
  } else await runCheck($("#message-text").value);
});
$("#media-file").addEventListener("change", event => { $("#file-name").textContent = event.target.files[0]?.name || ""; });
$$('[data-sample]').forEach(button => button.addEventListener("click", () => { $("#message-text").value = sampleMessages[button.dataset.sample]; $("#media-file").value = ""; $("#file-name").textContent = ""; }));
$$('[data-nav]').forEach(button => button.addEventListener("click", () => navigate(button.dataset.nav)));
$("#setup-form").addEventListener("submit", async event => { event.preventDefault(); const form = event.currentTarget; const data = Object.fromEntries(new FormData(form)); data.consent = form.elements.consent.checked; try { await api("/api/setup", {method:"PUT", body:JSON.stringify(data)}); toast("Family settings saved."); await loadSetup(); } catch (err) { toast(err.message); } });
$("#contact-form").addEventListener("submit", async event => { event.preventDefault(); const form = event.currentTarget; const data = Object.fromEntries(new FormData(form)); data.phone_self_shared = form.elements.phone_self_shared.checked; data.is_guardian = form.elements.is_guardian.checked; try { await api("/api/contacts", {method:"POST", body:JSON.stringify(data)}); form.reset(); await loadSetup(); toast("Verifier added. Send them a binding invite."); } catch (err) { toast(err.message); } });
$("#delete-family").addEventListener("click", async () => { if (!confirm("Delete all family settings, cases and audit entries from this device? This cannot be undone.")) return; try { await api("/api/family-data", {method:"DELETE"}); toast("Family data deleted."); location.reload(); } catch (err) { toast(err.message); } });
$("#language-shortcut").addEventListener("click", () => navigate("setup"));
initialize();
