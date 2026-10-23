/* LocalPilot web UI — plain JS, no build step, works offline. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const stream = $("stream"), messagesEl = $("messages"), input = $("input");
  const ICON = {
    web_search: "🔍", fetch_url: "🌐", browse: "🧭", open_in_browser: "↗️", list_dir: "📁", read_file: "📄",
    write_file: "✏️", make_dir: "📁", move_path: "📦", copy_path: "📋", delete_path: "🗑️", find_files: "🔎",
    run_shell: "⌨️", git: "🌿", install_package: "⬇️", system_info: "🖥️", schedule_task: "⏰",
    list_schedules: "⏰", cancel_schedule: "⏰",
  };
  const STATE_TXT = { running: "running", done: "✓ done", failed: "✗ failed", denied: "denied", blocked: "blocked", skipped: "skipped" };
  let ws, sessionId, busy = false, retry = 0, lastRole = null, status = {};

  try { sessionId = localStorage.getItem("lp_session"); } catch (_) {}
  if (!sessionId) { sessionId = Math.random().toString(36).slice(2) + Date.now().toString(36); }
  try { localStorage.setItem("lp_session", sessionId); } catch (_) {}

  /* ---------------- markdown (tiny, safe) ---------------- */
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  }
  function md(src) {
    const parts = esc(src).split(/```[\w-]*\n?([\s\S]*?)```/g);
    let html = "";
    parts.forEach((part, i) => {
      if (i % 2) { html += `<pre><code>${part.replace(/\n$/, "")}</code></pre>`; return; }
      let list = null;
      for (const raw of part.split("\n")) {
        const line = raw.trimEnd();
        const ul = line.match(/^\s*[-*•]\s+(.*)/), ol = line.match(/^\s*\d+[.)]\s+(.*)/), h = line.match(/^(#{1,3})\s+(.*)/);
        if (ul || ol) {
          const tag = ul ? "ul" : "ol";
          if (list !== tag) { if (list) html += `</${list}>`; html += `<${tag}>`; list = tag; }
          html += `<li>${inline((ul || ol)[1])}</li>`;
          continue;
        }
        if (list) { html += `</${list}>`; list = null; }
        if (h) html += `<h3>${inline(h[2])}</h3>`;
        else if (line.trim()) html += `<p>${inline(line)}</p>`;
      }
      if (list) html += `</${list}>`;
    });
    return html;
  }

  /* ---------------- rendering ---------------- */
  const scrollDown = () => requestAnimationFrame(() => { messagesEl.scrollTop = messagesEl.scrollHeight; });
  const nearBottom = () => messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 160;

  function bubble(cls, role, html, ts, opts = {}) {
    $("welcome").hidden = true;
    const row = document.createElement("div");
    row.className = `msg ${cls}`;
    if (lastRole !== role) row.classList.add("gap");
    const tail = lastRole !== role ? (role === "out" ? " tail-out" : " tail-in") : "";
    lastRole = role;
    const label = opts.label ? `<span class="label">⏰ ${esc(opts.label)}</span>` : "";
    const ticks = role === "out" ? '<span class="ticks">✓✓</span>' : "";
    row.innerHTML = `<div class="bubble${tail}">${label}${html}<span class="meta">${esc(ts || now())}${ticks}</span></div>`;
    if (opts.id) row.dataset.id = opts.id;
    const stick = nearBottom();
    stream.appendChild(row);
    if (stick || role === "out") scrollDown();
    return row;
  }
  const now = () => new Date().toTimeString().slice(0, 5);

  function shortArgs(name, a) {
    a = a || {};
    const v = a.query || a.url || a.command || (a.args !== undefined ? "git " + a.args : "") || a.path || a.src || a.pattern || a.name || "";
    return String(v).replace(/\s+/g, " ").slice(0, 120);
  }

  function renderTool(e) {
    let row = stream.querySelector(`.tool[data-id="${e.id}"]`);
    const open = row && row.classList.contains("open");
    const html = `
      <div class="tool-row">
        <span class="ico">${ICON[e.name] || "🛠️"}</span>
        <span class="tname">${esc(e.name)}</span>
        <span class="targ">${esc(shortArgs(e.name, e.args))}</span>
        ${e.state === "running" ? '<span class="spin"></span>' : `<span class="state ${e.state}">${STATE_TXT[e.state] || e.state}</span>`}
      </div>
      <div class="details">
        <div class="small muted"><span class="badge ${e.risk}">${e.risk}</span> ${esc(e.reason || "")}${e.ms ? ` · ${e.ms} ms` : ""}${
          e.laya ? ` · 🛡 LAYA ${e.laya.injection !== undefined ? "injection " + Math.round(e.laya.injection * 100) + "%" : "checked"}` : ""}</div>
        ${e.preview ? `<pre>${esc(e.preview)}</pre>` : ""}
        ${e.output ? `<pre>${esc(e.output)}</pre>` : ""}
      </div>`;
    if (row) {
      row.querySelector(".bubble").innerHTML = html + `<span class="meta">${esc(e.ts || now())}</span>`;
    } else {
      row = bubble("tool in", "in", html, e.ts, { id: e.id, label: e.label });
      row.classList.add("tool");
    }
    if (open || e.state === "failed") row.classList.add("open");
    row.querySelector(".tool-row").onclick = () => row.classList.toggle("open");
    if (nearBottom()) scrollDown();
  }

  function renderApproval(e) {
    if (stream.querySelector(`.approval[data-id="${e.id}"]`)) return;
    const always = e.risk === "WRITE"
      ? `<button class="btn" data-d="always">Always allow ${esc(e.name)}</button>` : "";
    const row = bubble(`approval in ${e.risk}`, "in", `
      <div class="title"><span class="badge ${e.risk}">${e.risk === "HIGH" ? "⚠ RISKY" : "CHANGE"}</span>
        ${ICON[e.name] || "🛠️"} Allow <code>${esc(e.name)}</code>?</div>
      <div class="why">${esc(e.reason || "")}</div>
      ${e.laya && e.laya.destructive !== undefined ? `<div class="why">🛡 LAYA: ${Math.round(e.laya.destructive * 100)}% chance this is destructive</div>` : ""}
      <pre>${esc(e.preview || JSON.stringify(e.args, null, 1))}</pre>
      <div class="btns">
        <button class="btn primary" data-d="allow">Allow once</button>
        ${always}
        <button class="btn danger" data-d="deny">Deny</button>
      </div>`, e.ts, { id: e.id, label: e.label });
    row.querySelectorAll("button[data-d]").forEach((b) => (b.onclick = () => {
      send({ type: "approval", id: e.id, decision: b.dataset.d });
      markResolved(e.id, b.dataset.d);
    }));
    scrollDown();
    notify(`Approval needed: ${e.name}`, e.preview || "");
  }

  function renderClarify(e) {
    if (stream.querySelector(`.clarify[data-id="${e.id}"]`)) return;
    const opts = (e.options || []).map((o, k) =>
      `<button class="chip" data-k="${k}">${esc(o)}</button>`).join("");
    const keep = e.source === "typo" && e.original
      ? `<button class="chip" data-orig="1">↩ Send as I typed: “${esc(e.original)}”</button>` : "";
    const row = bubble("clarify in", "in", `
      <div class="title">❓ ${esc(e.question)}</div>
      <div class="opts">${opts}${keep}</div>
      <div class="other"><input placeholder="Something else… type and press Enter" aria-label="Your answer">
        <button class="btn primary sm">Send</button></div>`, e.ts, { id: e.id, label: e.label });
    const reply = (text) => {
      if (!text.trim()) return;
      send({ type: "answer", id: e.id, text });
      markClarified(e.id, text);
    };
    row.querySelectorAll(".opts .chip").forEach((b) => (b.onclick = () =>
      reply(b.dataset.orig ? e.original : e.options[+b.dataset.k])));
    const inp = row.querySelector(".other input");
    inp.onkeydown = (ev) => { if (ev.key === "Enter") reply(inp.value); };
    row.querySelector(".other .btn").onclick = () => reply(inp.value);
    scrollDown();
    notify("Question from " + (status.name || "LocalPilot"), e.question);
  }

  function markClarified(id, answer) {
    const row = stream.querySelector(`.clarify[data-id="${id}"]`);
    if (!row || row.querySelector(".resolved")) return;
    row.querySelectorAll(".opts, .other").forEach((x) => x.remove());
    row.querySelector(".title").insertAdjacentHTML("afterend",
      `<div class="resolved">${answer ? "✓ " + esc(answer) : "No answer"}</div>`);
  }

  function markResolved(id, decision) {
    const row = stream.querySelector(`.approval[data-id="${id}"]`);
    if (!row) return;
    const btns = row.querySelector(".btns");
    if (btns) btns.outerHTML = `<div class="resolved ${decision}">${
      decision === "deny" ? "✗ Denied" : decision === "always" ? "✓ Allowed (always this chat)" : "✓ Allowed"}</div>`;
  }

  function render(e) {
    switch (e.type) {
      case "user": bubble("out", "out", md(e.text), e.ts, { label: e.label }); break;
      case "assistant": bubble("in", "in", md(e.text), e.ts, { label: e.label }); break;
      case "note": bubble("note in", "in", md(e.text), e.ts, { label: e.label }); break;
      case "error": bubble("error in", "in", "⚠️ " + md(e.text), e.ts, { label: e.label }); break;
      case "tool": renderTool(e); break;
      case "approval_request": renderApproval(e); break;
      case "approval_resolved": markResolved(e.id, e.decision); break;
      case "clarify": renderClarify(e); break;
      case "clarify_resolved": markClarified(e.id, e.answer); break;
      case "fixed": {
        const row = document.createElement("div");
        row.className = "fixed-row";
        row.innerHTML = `<div class="fixed-note">✏️ Understood as: <b>${esc(e.text)}</b></div>`;
        stream.appendChild(row);
        if (nearBottom()) scrollDown();
        break;
      }
    }
  }

  /* ---------------- state ---------------- */
  function setBusy(b) {
    busy = b;
    $("typing").hidden = !b;
    $("stopBtn").hidden = !b;
    $("sendBtn").hidden = b;
    const p = $("presence");
    p.classList.toggle("is-typing", b);
    p.textContent = b ? "typing…" : status.llm_online ? `online · ${status.model}` : "model offline — start Ollama";
    if (b) scrollDown();
  }

  function applyStatus(s) {
    status = s;
    $("botName").textContent = $("chatName").textContent = s.name || "LocalPilot";
    $("modelLine").textContent = `${s.model} · ${s.llm_online ? "online" : "offline"}`;
    $("llmStatus").innerHTML = `<span class="dot" style="background:${s.llm_online ? "var(--accent)" : "var(--high)"}"></span>${s.llm_online ? "Online" : "Offline"} (${esc(s.provider)})`;
    $("activeModel").textContent = s.model + (s.native_tools ? "" : " (text tool mode)");
    if (s.recommended) {
      $("recModel").textContent = s.recommended.model;
      $("recWhy").textContent = `${s.recommended.why} · runs on ${s.recommended.runs_on}`;
      $("pullName").value ||= s.recommended.model;
    }
    $("modelNote").textContent = s.note || "";
    const sel = $("installedSel");
    sel.innerHTML = (s.installed_models || []).map((m) => `<option ${m === s.model ? "selected" : ""}>${esc(m)}</option>`).join("")
      || "<option disabled>no models installed</option>";
    const h = s.hardware || {};
    $("hwBox").innerHTML = [
      ["OS", h.os], ["CPU", `${h.cpu_cores} cores / ${h.cpu_threads} threads`], ["RAM", `${h.ram_gb} GB`],
      ...(h.gpus || []).map((g) => ["GPU", `${g.name} (${g.vram_gb} GB)`]),
    ].map(([k, v]) => `<div class="kv"><span>${k}</span><b>${esc(v)}</b></div>`).join("");
    $("wsPath").textContent = s.workspace;
    $("autofix").checked = s.autofix_typos !== false;
    const L = s.laya || {};
    const lc = { ready: "var(--accent)", loading: "var(--write)", failed: "var(--high)" }[L.state] || "var(--muted)";
    $("layaStatus").innerHTML = `<span class="dot" style="background:${lc}"></span>${esc(L.state || "not installed")}`;
    $("layaErr").textContent = L.error || "";
    setMode(s.mode);
    setBusy(busy);
  }

  function setMode(m) {
    $("modeSel").value = m;
    const pill = $("modePill");
    pill.textContent = m;
    pill.className = "mode-pill " + m;
  }

  /* ---------------- tasks / log ---------------- */
  function renderTasks(items) {
    $("taskList").innerHTML = (items || []).map((j) => `
      <div class="task ${j.enabled ? "" : "off"}" data-id="${j.id}">
        <div class="task-head"><b>${esc(j.name)}</b><span class="small muted">${esc(j.trigger_type)}: ${esc(j.trigger_value)}</span></div>
        <div class="small">${esc(j.prompt)}</div>
        <div class="small muted">Next: ${esc(j.next_run || "—")} · Last: ${esc(j.last_run || "never")} ${j.last_status ? "(" + esc(j.last_status) + ")" : ""}</div>
        ${j.last_result ? `<pre>${esc(j.last_result)}</pre>` : ""}
        <div class="actions">
          <button class="btn sm" data-a="run">▶ Run now</button>
          <button class="btn sm" data-a="toggle">${j.enabled ? "⏸ Pause" : "▶ Resume"}</button>
          <button class="btn sm danger" data-a="del">Delete</button>
        </div>
      </div>`).join("") || '<div class="small muted">No tasks yet. Add one above, or just ask in chat: “every day at 8am …”.</div>';
    $("taskList").querySelectorAll(".task").forEach((el) => el.querySelectorAll("button").forEach((b) => (b.onclick = async () => {
      const id = el.dataset.id, a = b.dataset.a;
      if (a === "del" && !confirm("Delete this task?")) return;
      const url = a === "del" ? `/api/schedules/${id}` : `/api/schedules/${id}/${a}`;
      await api(url, { method: a === "del" ? "DELETE" : "POST" });
      if (a !== "run") loadTasks();
    })));
  }
  const loadTasks = async () => renderTasks(await api("/api/schedules"));

  async function loadLog() {
    const items = await api("/api/audit?limit=150");
    $("logList").innerHTML = items.map((x) => `
      <div class="log-item"><span class="badge ${x.risk}">${x.risk}</span> <b>${esc(x.tool)}</b>
        <span class="muted">· ${esc(x.decision)}${x.ok === false ? " · failed" : ""} · ${esc(x.ts)}</span>
        <div class="mono muted">${esc(JSON.stringify(x.args)).slice(0, 200)}</div></div>`).join("")
      || '<div class="small muted">Nothing yet.</div>';
  }

  const TRIG = {
    cron: ["0 9 * * 1-5", "Cron: minute hour day month weekday — “0 9 * * 1-5” = weekdays 9:00"],
    interval: ["60", "Minutes between runs (minimum 1)"],
    once: [new Date(Date.now() + 3600e3).toISOString().slice(0, 16), "Local date & time, e.g. 2026-11-01T18:30"],
  };
  $("trigType").onchange = (ev) => { const [p, h] = TRIG[ev.target.value]; $("trigVal").placeholder = p; $("trigVal").value = ""; $("trigHint").textContent = h; };
  $("taskForm").onsubmit = async (ev) => {
    ev.preventDefault();
    $("taskErr").textContent = "";
    const body = Object.fromEntries(new FormData(ev.target));
    try {
      await api("/api/schedules", { method: "POST", body: JSON.stringify(body) });
      ev.target.reset(); $("trigType").dispatchEvent(new Event("change")); loadTasks();
    } catch (err) { $("taskErr").textContent = err.message; }
  };

  /* ---------------- API & socket ---------------- */
  async function api(url, opts = {}) {
    const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || data.error || r.statusText);
    return data;
  }
  const send = (obj) => ws && ws.readyState === 1 && ws.send(JSON.stringify(obj));

  function connect() {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => { retry = 0; send({ type: "hello", session: sessionId }); };
    ws.onclose = () => {
      $("presence").textContent = "reconnecting…";
      setTimeout(connect, Math.min(1000 * 2 ** retry++, 10000));
    };
    ws.onmessage = (m) => {
      const e = JSON.parse(m.data);
      if (e.type === "hello") {
        stream.innerHTML = ""; lastRole = null;
        $("welcome").hidden = !!e.transcript.length;
        e.transcript.forEach(render);
        e.pending.forEach(render);
        applyStatus(e.status); renderTasks(e.schedules); setBusy(e.busy); scrollDown();
        return;
      }
      if (e.type === "status") return setBusy(true);
      if (e.type === "done") { if (!e.session || e.session === sessionId) setBusy(false); return; }
      if (e.type === "mode") return setMode(e.mode);
      if (e.type === "schedules") return renderTasks(e.items);
      if (e.type === "reset") { stream.innerHTML = ""; lastRole = null; $("welcome").hidden = false; setBusy(false); return; }
      if (e.type === "pull") return onPull(e);
      if (e.type === "assistant" && document.hidden) notify(status.name || "LocalPilot", e.text.slice(0, 140));
      render(e);
    };
  }

  function onPull(e) {
    $("pullBar").hidden = false;
    const pct = e.total ? Math.round((e.completed || 0) / e.total * 100) : null;
    $("pullBar").firstElementChild.style.width = (pct ?? 0) + "%";
    $("pullText").textContent = `${e.model}: ${e.status}${pct !== null ? " " + pct + "%" : ""}`;
    if (e.status === "success") { $("pullBar").hidden = true; api("/api/status").then(applyStatus); }
  }

  function notify(title, body) {
    if (!document.hidden || !("Notification" in window) || Notification.permission !== "granted") return;
    try { new Notification(title, { body }); } catch (_) {}
  }

  /* ---------------- input ---------------- */
  function submit(text) {
    text = (text ?? input.value).trim();
    if (!text || busy) return;
    send({ type: "chat", text });
    input.value = ""; autosize();
    $("suggestBar").hidden = true;
    setBusy(true);
    if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
  }
  function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; }
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); submit(); } });
  $("sendBtn").onclick = () => submit();
  $("stopBtn").onclick = () => send({ type: "stop" });
  $("newChat").onclick = () => { if (!busy || confirm("Stop the current task and start a new chat?")) send({ type: "reset" }); };
  $("suggestions").querySelectorAll(".chip").forEach((c) => (c.onclick = () => submit(c.textContent)));
  $("modeSel").onchange = async (ev) => {
    if (ev.target.value === "trusted" && !confirm("Trusted mode runs normal changes (writing files, git commits, commands) without asking.\nRisky actions (delete, install, push, admin) still ask every time.\n\nSwitch to Trusted?")) {
      return setMode(status.mode);
    }
    const r = await api("/api/mode", { method: "POST", body: JSON.stringify({ mode: ev.target.value }) });
    status.mode = r.mode; setMode(r.mode);
  };
  $("useModel").onclick = async () => applyStatus(await api("/api/model", { method: "POST", body: JSON.stringify({ model: $("installedSel").value }) }));
  $("pullBtn").onclick = async () => {
    const m = $("pullName").value.trim(); if (!m) return;
    $("pullText").textContent = "starting download…";
    try { await api("/api/models/pull", { method: "POST", body: JSON.stringify({ model: m }) }); }
    catch (err) { $("pullText").textContent = err.message; }
  };
  $("refreshLog").onclick = loadLog;
  $("autofix").onchange = (ev) => api("/api/settings", { method: "POST", body: JSON.stringify({ autofix_typos: ev.target.checked }) });
  $("fixBtn").onclick = async () => {
    const text = input.value.trim();
    if (!text) return;
    const btn = $("fixBtn");
    btn.classList.add("working"); btn.disabled = true;
    try {
      const r = await api("/api/fix_text", { method: "POST", body: JSON.stringify({ text }) });
      const bar = $("suggestBar");
      if (r.options && r.options.length > 1) {
        bar.innerHTML = '<span class="q">❓ Did you mean:</span>' +
          r.options.map((o, k) => `<button class="chip" data-k="${k}">${esc(o)}</button>`).join("") +
          '<button class="chip" data-k="x">✕ keep mine</button>';
        bar.hidden = false;
        bar.querySelectorAll(".chip").forEach((b) => (b.onclick = () => {
          if (b.dataset.k !== "x") { input.value = r.options[+b.dataset.k]; autosize(); }
          bar.hidden = true; input.focus();
        }));
      } else if (r.changed) { input.value = r.text; autosize(); }
      input.classList.add("flash"); setTimeout(() => input.classList.remove("flash"), 600);
      input.title = r.changed ? "Typos fixed — check and press Enter" : "No typos found";
    } catch (_) { /* model offline: leave text as is */ }
    btn.classList.remove("working"); btn.disabled = false; input.focus();
  };
  document.querySelectorAll(".tab").forEach((t) => (t.onclick = () => {
    document.querySelectorAll(".tab, .pane").forEach((x) => x.classList.remove("active"));
    t.classList.add("active"); $("pane-" + t.dataset.tab).classList.add("active");
    if (t.dataset.tab === "log") loadLog();
    if (t.dataset.tab === "system") api("/api/status").then(applyStatus);
  }));
  $("openSide").onclick = () => $("sidebar").classList.add("show");
  $("closeSide").onclick = () => $("sidebar").classList.remove("show");

  connect();
})();
