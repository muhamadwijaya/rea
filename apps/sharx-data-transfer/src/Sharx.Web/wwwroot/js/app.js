"use strict";

/* SHARX Data Transfer — UI. Tanpa framework; data live lewat WebSocket /ws. */
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const SPECIAL_KEYS = ["OPERATOR", "TEMPLATE", "SERVER_COUNTER"];
  const TRIGGERS = [
    ["Always", "Setiap data"],
    ["TemplateChanged", "Saat template berubah"],
    ["OperatorChanged", "Saat operator berubah"],
    ["JobChanged", "Saat template / operator berubah"],
  ];
  const PAGES = {
    dashboard: ["Data Transfer", "Real-Time Data Monitoring"],
    transfer: ["Data Transfer", "Job, Counter & Printer Output"],
    logs: ["Log History", "Riwayat Log Transfer Harian"],
    settings: ["Settings", "Koneksi, Flow Mapping Data & Counter"],
    about: ["About", "SHARX Data Transfer"],
  };

  const state = {
    status: null,
    stats: null,
    counter: null,
    records: [],
    log: [],
    version: "1.0.0",
    shell: "browser",
    liveRows: 200,
    settings: null,
    meta: null,
    draft: null,
    dirty: false,
    preview: null,
    page: "dashboard",
    logEntries: [],
  };

  // ------------------------------------------------------------------ helpers

  async function api(method, path, body) {
    const init = {
      method,
      headers: { "Content-Type": "application/json", "X-Sharx": "1" },
    };
    if (body !== undefined) init.body = JSON.stringify(body);
    const res = await fetch(path, init);
    let data = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    if (!res.ok) {
      const err = new Error(
        data?.errors?.join("\n") || `${res.status} ${res.statusText}`,
      );
      err.data = data;
      throw err;
    }
    return data;
  }

  function esc(value) {
    return String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  }

  const pad2 = (n) => String(n).padStart(2, "0");

  function timeText(iso) {
    const d = new Date(iso);
    return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  }

  function formatBytes(n) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = Number(n) || 0;
    let i = 0;
    while (value >= 1024 && i < units.length - 1) {
      value /= 1024;
      i++;
    }
    return {
      value: i === 0 ? String(Math.round(value)) : value.toFixed(1),
      unit: units[i],
    };
  }

  function formatRate(n) {
    const f = formatBytes(n);
    return `(${f.value} ${f.unit}/s)`;
  }

  const clone = (obj) => JSON.parse(JSON.stringify(obj));

  function debounce(fn, ms) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  function toast(message, isError = false) {
    const el = document.createElement("div");
    el.className = `toast${isError ? " err" : ""}`;
    el.textContent = message;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), isError ? 6000 : 3200);
  }

  function modal({ title, body, okText = "OK", danger = false, input = null }) {
    return new Promise((resolve) => {
      const root = $("#modal");
      $("#modalTitle").textContent = title;
      const bodyEl = $("#modalBody");
      bodyEl.innerHTML = body;
      let field = null;
      if (input) {
        field = document.createElement("input");
        field.className = "input";
        field.type = input.type || "text";
        field.value = input.value ?? "";
        bodyEl.appendChild(field);
      }
      const ok = $("#modalOk");
      ok.textContent = okText;
      ok.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
      root.hidden = false;
      (field || ok).focus();
      const close = (result) => {
        root.hidden = true;
        ok.onclick = null;
        $("#modalCancel").onclick = null;
        root.onkeydown = null;
        resolve(result);
      };
      ok.onclick = () => close(field ? field.value : true);
      $("#modalCancel").onclick = () => close(null);
      root.onkeydown = (e) => {
        if (e.key === "Escape") close(null);
        if (e.key === "Enter") close(field ? field.value : true);
      };
    });
  }

  // ------------------------------------------------------------------ websocket

  let ws = null;
  let wsRetry = 0;

  function connectWs() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => {
      wsRetry = 0;
      setSystem(true);
    };
    ws.onmessage = (ev) => {
      try {
        handleMessage(JSON.parse(ev.data));
      } catch (err) {
        console.error(err);
      }
    };
    ws.onclose = () => {
      setSystem(false);
      setTimeout(connectWs, Math.min(5000, 400 * 2 ** wsRetry++));
    };
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case "snapshot":
        applySnapshot(msg.data);
        break;
      case "status":
        state.status = msg.data;
        renderStatus();
        break;
      case "record":
        addRecord(msg.data);
        break;
      case "log":
        addLog(msg.data);
        break;
      case "stats":
        state.stats = msg.data;
        renderStats();
        break;
      case "counter":
        state.counter = msg.data;
        renderCounter();
        break;
    }
  }

  function applySnapshot(data) {
    state.version = data.version;
    state.shell = data.shell;
    state.status = data.status;
    state.stats = data.stats;
    state.counter = data.counter;
    state.records = data.records || [];
    state.liveRows = Math.max(10, state.records.length, state.liveRows);
    state.log = (data.log || []).slice().reverse();
    $("#systemVersion").textContent = `v${data.version}`;
    $("#aboutVersion").textContent = `v${data.version}`;
    $("#dataDir").textContent = data.dataDirectory;
    document.body.dataset.shell = data.shell;
    renderStatus();
    renderStats();
    renderCounter();
    renderAllRecords();
    renderLog();
  }

  function setSystem(online) {
    $("#systemBox").classList.toggle("offline", !online);
    $("#systemText").textContent = online ? "System Online" : "System Offline";
  }

  // ------------------------------------------------------------------ dashboard

  function renderStatus() {
    const s = state.status;
    if (!s) return;
    const el = $("#connState");
    let key = "stopped";
    let title = "Stopped";
    let sub = "Transfer idle";
    if (s.running) {
      if (s.server === "Connected") {
        key = "connected";
        title = "Connected";
        sub = "to Server";
      } else if (s.server === "Connecting") {
        key = "connecting";
        title = "Connecting...";
        sub = "to Server";
      } else {
        key = "disconnected";
        title = "Disconnected";
        sub = "Reconnecting...";
      }
    }
    el.dataset.state = key;
    $("#connTitle").textContent = title;
    $("#connSub").textContent = sub;
    const idx = s.serverEndpoint.lastIndexOf(":");
    $("#serverIp").textContent = s.serverEndpoint.slice(0, idx);
    $("#serverPort").textContent = s.serverEndpoint.slice(idx + 1);
    $("#btnStart").disabled = s.running;
    $("#btnStop").disabled = !s.running;

    const pill = $("#printerPill");
    const printerText = {
      Disabled: ["Disabled (monitor)", "off"],
      Idle: ["Idle", "off"],
      Connecting: ["Connecting...", "warn"],
      Connected: ["Connected", "ok"],
      Error: ["Error", "err"],
    }[s.printer] || [s.printer, "off"];
    pill.textContent = printerText[0];
    pill.className = `pill ${printerText[1]}`;
    $("#printerEndpoint").textContent =
      `${s.printerEndpoint} · ${s.printerProtocol}`;
    $("#printerMsg").textContent = s.printerMessage || "";
    const job = s.job;
    $("#jobNext").textContent = job?.nextCounter || "-";
    const queue = [];
    if (job)
      queue.push(
        job.armed ? `antrean printer ${job.pending}` : "menunggu printer siap",
      );
    if (s.feedbackQueued)
      queue.push(`${s.feedbackQueued} laporan menunggu server`);
    $("#jobQueue").textContent = queue.length ? `(${queue.join(", ")})` : "";
  }

  function renderStats() {
    const s = state.stats;
    if (!s) return;
    const rx = formatBytes(s.receivedBytes);
    const tx = formatBytes(s.sentBytes);
    $("#rxValue").textContent = rx.value;
    $("#rxUnit").textContent = rx.unit;
    $("#txValue").textContent = tx.value;
    $("#txUnit").textContent = tx.unit;
    $("#rxRate").textContent = formatRate(s.receivedRate);
    $("#txRate").textContent = formatRate(s.sentRate);
    $("#statRecords").textContent = s.recordsReceived;
    $("#statForwarded").textContent = s.recordsForwarded;
    $("#statPrinted").textContent = s.recordsPrinted;
    $("#statFailed").textContent = s.recordsFailed;
  }

  function incomingRow(record) {
    const tr = document.createElement("tr");
    const failed =
      record.status === "Rejected" || record.status === "PrinterError";
    if (failed) tr.className = "row-error";
    tr.title = recordTooltip(record);
    tr.innerHTML = `<td></td><td>${esc(timeText(record.time))}</td><td>${esc(record.source)}</td><td>${esc(record.data)}</td>`;
    return tr;
  }

  function recordTooltip(r) {
    const lines = [
      `Operator: ${r.operator ?? "-"}`,
      `Template: ${r.template ?? "-"}`,
      `Counter: ${r.counter ?? "-"}`,
      `Status: ${r.status}`,
    ];
    if (r.message) lines.push(r.message);
    if (r.counterNote) lines.push(r.counterNote);
    return lines.join("\n");
  }

  function mappedRow(record) {
    const tr = document.createElement("tr");
    const output = (record.commands || []).map((c) => c.data).join("  |  ");
    tr.title = recordTooltip(record);
    tr.innerHTML = `<td>${record.seq}</td><td>${esc(timeText(record.time))}</td><td>${esc(record.source)}</td><td>${esc(record.operator || "-")}</td>
      <td>${esc(record.template || "-")}</td><td class="mono">${esc(record.counter || "-")}</td>
      <td class="mono">${esc(output || record.message || "")}</td>
      <td><span class="status-tag ${esc(record.status)}">${esc(statusLabel(record.status))}</span></td>`;
    return tr;
  }

  function statusLabel(status) {
    return (
      {
        Ok: "OK",
        Printed: "PRINTED",
        Monitor: "MONITOR",
        Rejected: "DITOLAK",
        PrinterError: "GAGAL",
      }[status] || status
    );
  }

  function renumber(tbody) {
    const rows = tbody.rows;
    for (let i = 0; i < rows.length; i++)
      rows[i].cells[0].textContent = String(i + 1);
  }

  function renderAllRecords() {
    const body = $("#incomingBody");
    const mapped = $("#mappedBody");
    body.replaceChildren(...state.records.map(incomingRow));
    mapped.replaceChildren(...state.records.map(mappedRow));
    renumber(body);
    $("#incomingEmpty").classList.toggle("show", state.records.length === 0);
  }

  function addRecord(record) {
    state.records.unshift(record);
    const limit = state.liveRows;
    if (state.records.length > limit) state.records.length = limit;

    const scroller = $("#incomingScroll");
    const auto = $("#autoScroll").checked;
    const before = scroller.scrollHeight;
    const body = $("#incomingBody");
    const row = incomingRow(record);
    row.classList.add("row-new");
    body.prepend(row);
    while (body.rows.length > limit) body.deleteRow(body.rows.length - 1);
    renumber(body);
    if (auto) {
      scroller.scrollTop = 0;
    } else {
      scroller.scrollTop += scroller.scrollHeight - before;
    }

    const mapped = $("#mappedBody");
    mapped.prepend(mappedRow(record));
    while (mapped.rows.length > limit) mapped.deleteRow(mapped.rows.length - 1);
    $("#incomingEmpty").classList.remove("show");
  }

  function logLine(entry) {
    const div = document.createElement("div");
    div.className = "log-line";
    const status = entry.status.toUpperCase();
    div.innerHTML = `[${esc(timeText(entry.time))}]  ${esc(entry.message)}  - <span class="log-status s-${esc(entry.status.toLowerCase())}">${esc(status)}</span>`;
    div.title = entry.message;
    return div;
  }

  function renderLog() {
    $("#logBox").replaceChildren(...state.log.slice(0, 200).map(logLine));
  }

  function addLog(entry) {
    state.log.unshift(entry);
    if (state.log.length > 200) state.log.length = 200;
    const box = $("#logBox");
    box.prepend(logLine(entry));
    while (box.children.length > 200) box.lastChild.remove();
    if (
      state.page === "logs" &&
      $("#logDate").value === localDate(new Date(entry.time))
    ) {
      state.logEntries.push(entry);
      renderLogHistory();
    }
  }

  // ------------------------------------------------------------------ counter / data transfer page

  function renderCounter() {
    const c = state.counter;
    if (!c) return;
    $("#jobOperator").textContent = c.activeOperator || "-";
    $("#jobTemplate").textContent = c.activeTemplate || "-";
    $("#jobCounter").textContent = c.activeValue || "-";
    $("#jobDate").textContent = c.businessDate
      ? `Hari produksi ${c.businessDate}`
      : "-";
    const body = $("#counterBody");
    const rows = (c.templates || []).map((t) => {
      const tr = document.createElement("tr");
      if (t.template === c.activeTemplate) tr.className = "highlight";
      tr.innerHTML = `<td>${esc(t.template)}</td><td class="mono">${esc(t.text)}</td><td>${t.records}</td>
        <td>${esc(t.lastOperator || "-")}</td><td>${esc(timeText(t.updatedAt))}</td>
        <td><button class="btn btn-sm btn-icon" data-act="set" title="Ubah counter"><svg><use href="#i-edit"/></svg></button>
            <button class="btn btn-sm btn-icon" data-act="reset" title="Reset counter template ini"><svg><use href="#i-reset"/></svg></button></td>`;
      tr.querySelector('[data-act="set"]').onclick = () => setCounter(t);
      tr.querySelector('[data-act="reset"]').onclick = () =>
        resetCounter(t.template);
      return tr;
    });
    body.replaceChildren(...rows);
    $("#counterEmpty").classList.toggle("show", rows.length === 0);
    if (c.persistError)
      toast(`Counter gagal disimpan: ${c.persistError}`, true);
  }

  async function resetCounter(template) {
    const ok = await modal({
      title: "Reset Counter",
      body: template
        ? `Reset counter template <b>${esc(template)}</b>? Data berikutnya untuk template ini mulai dari nilai awal.`
        : "Reset <b>semua</b> counter template? Data berikutnya mulai dari nilai awal / counter server.",
      okText: "Reset",
      danger: true,
    });
    if (!ok) return;
    try {
      await api("POST", "/api/counter/reset", template ? { template } : {});
      toast(template ? `Counter ${template} direset` : "Semua counter direset");
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function setCounter(t) {
    const value = await modal({
      title: `Ubah counter ${t.template}`,
      body: "Nilai counter terakhir (data berikutnya = nilai ini + step).",
      input: { type: "number", value: t.value },
    });
    if (value === null || value === "") return;
    try {
      await api("POST", "/api/counter/set", {
        template: t.template,
        value: Number(value),
      });
      toast(`Counter ${t.template} diubah`);
    } catch (err) {
      toast(err.message, true);
    }
  }

  // ------------------------------------------------------------------ log history

  function localDate(d) {
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  }

  async function loadLogDates() {
    const select = $("#logDate");
    const current = select.value;
    const dates = await api("GET", "/api/logs/dates");
    const today = localDate(new Date());
    if (!dates.includes(today)) dates.unshift(today);
    select.innerHTML = dates.map((d) => `<option>${esc(d)}</option>`).join("");
    select.value = dates.includes(current) ? current : dates[0];
    await loadLogDay();
  }

  async function loadLogDay() {
    const date = $("#logDate").value;
    $("#btnLogDownload").href =
      `/api/logs/${encodeURIComponent(date)}/download`;
    state.logEntries = await api(
      "GET",
      `/api/logs/${encodeURIComponent(date)}`,
    );
    renderLogHistory();
  }

  function renderLogHistory() {
    const status = $("#logStatus").value;
    const q = $("#logSearch").value.trim().toLowerCase();
    const list = state.logEntries
      .filter(
        (e) =>
          (!status || e.status === status) &&
          (!q || e.message.toLowerCase().includes(q)),
      )
      .slice()
      .reverse();
    const shown = list.slice(0, 3000);
    $("#logHistory").replaceChildren(...shown.map(logLine));
    $("#logCount").textContent =
      `${list.length} baris${list.length > shown.length ? ` (ditampilkan ${shown.length})` : ""}`;
  }

  // ------------------------------------------------------------------ settings

  function getPath(obj, path) {
    return path
      .split(".")
      .reduce((o, k) => (o == null ? undefined : o[k]), obj);
  }

  function setPath(obj, path, value) {
    const keys = path.split(".");
    const last = keys.pop();
    const target = keys.reduce((o, k) => o[k], obj);
    target[last] = value;
  }

  async function loadSettings(force = false) {
    if (state.dirty && !force) return;
    const data = await api("GET", "/api/settings");
    state.settings = data.settings;
    state.meta = data.meta;
    state.draft = clone(data.settings);
    setDirty(false);
    $("#settingsErrors").hidden = true;
    $$("[data-options='encodings']").forEach((sel) => {
      sel.innerHTML = data.meta.encodings
        .map((e) => `<option>${esc(e)}</option>`)
        .join("");
    });
    $("#serialPorts").innerHTML = (data.meta.serialPorts || [])
      .map((p) => `<option value="${esc(p)}">`)
      .join("");
    fillBindings();
    renderFieldRows();
    renderStepRows();
    renderRemoteRows();
    renderTokenChips();
    requestPreview();
  }

  function fillBindings() {
    $$("[data-bind]").forEach((el) => {
      const value = getPath(state.draft, el.dataset.bind);
      if (el.type === "checkbox") el.checked = Boolean(value);
      else el.value = value ?? "";
    });
    applyShowRules();
  }

  function applyShowRules() {
    $$("[data-show]").forEach((el) => {
      const [path, expected] = el.dataset.show.split("=");
      const value = String(getPath(state.draft, path));
      el.classList.toggle("hidden-by-rule", value !== expected);
    });
    const fixed = state.draft?.mapping.mode === "FixedWidth";
    $$(".col-delimited").forEach((el) => (el.hidden = fixed));
  }

  function readBinding(el) {
    if (el.type === "checkbox") return el.checked;
    if (el.dataset.type === "int") {
      const n = parseInt(el.value, 10);
      return Number.isFinite(n) ? n : 0;
    }
    return el.value;
  }

  function setDirty(dirty) {
    state.dirty = dirty;
    $("#dirtyNote").hidden = !dirty;
  }

  function onDraftChanged(structural = false) {
    setDirty(true);
    applyShowRules();
    if (structural) {
      renderFieldRows();
      renderStepRows();
      renderRemoteRows();
    }
    requestPreview();
  }

  document.addEventListener("input", (e) => {
    const el = e.target.closest("[data-bind]");
    if (!el || !state.draft) return;
    setPath(state.draft, el.dataset.bind, readBinding(el));
    onDraftChanged(el.dataset.bind === "mapping.mode");
  });

  document.addEventListener("change", (e) => {
    const el = e.target.closest("[data-bind]");
    if (!el || !state.draft) return;
    setPath(state.draft, el.dataset.bind, readBinding(el));
    onDraftChanged(el.dataset.bind === "mapping.mode");
  });

  document.addEventListener("focusin", (e) => {
    if (e.target.matches("#page-settings input.mono, #page-settings textarea"))
      lastTemplateBox = e.target;
  });

  function positionOptions(selected) {
    const fields = state.preview?.fields || [];
    const count = Math.max(fields.length, selected, 3);
    let html = "";
    for (let i = 1; i <= count; i++) {
      const sample = fields[i - 1];
      const label =
        sample === undefined
          ? `${i} — (tidak ada)`
          : `${i} — ${sample === "" ? "(kosong)" : sample}`;
      html += `<option value="${i}"${i === selected ? " selected" : ""}>${esc(label)}</option>`;
    }
    return html;
  }

  function renderFieldRows() {
    const body = $("#fieldRows");
    const fixed = state.draft.mapping.mode === "FixedWidth";
    body.replaceChildren(
      ...state.draft.mapping.fields.map((f, index) => {
        const special = SPECIAL_KEYS.includes(f.key);
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${special ? `<b class="mono">${esc(f.key)}</b>` : `<input class="input mono" data-f="key" value="${esc(f.key)}" />`}</td>
          <td><input class="input" data-f="label" value="${esc(f.label)}" /></td>
          <td class="col-delimited"${fixed ? " hidden" : ""}><select class="input" data-f="position">${positionOptions(f.position)}</select></td>
          <td><input class="input" type="number" min="0" data-f="start" value="${f.start}" title="${fixed ? "Karakter awal (1 = pertama)" : "0 = seluruh field"}" /></td>
          <td><input class="input" type="number" min="0" data-f="length" value="${f.length}" title="0 = sampai akhir" /></td>
          <td><input type="checkbox" data-f="required"${f.required ? " checked" : ""} /></td>
          <td class="value-cell" data-value="${index}"></td>
          <td>${f.key === "TEMPLATE" ? "" : `<button class="btn btn-sm btn-icon" data-f="delete" title="Hapus"><svg><use href="#i-trash"/></svg></button>`}</td>`;
        tr.addEventListener("input", (e) => updateField(f, e.target));
        tr.addEventListener("change", (e) => updateField(f, e.target));
        const del = tr.querySelector('[data-f="delete"]');
        if (del) {
          del.onclick = () => {
            state.draft.mapping.fields.splice(
              state.draft.mapping.fields.indexOf(f),
              1,
            );
            onDraftChanged(true);
          };
        }
        return tr;
      }),
    );
    updateFieldResults();
  }

  function updateField(field, el) {
    const prop = el.dataset.f;
    if (!prop || prop === "delete") return;
    if (prop === "required") field.required = el.checked;
    else if (prop === "key") {
      field.key = el.value.toUpperCase().replace(/[^A-Z0-9_]/g, "");
      if (el.value !== field.key) el.value = field.key;
    } else if (prop === "label") field.label = el.value;
    else field[prop] = Math.max(0, parseInt(el.value, 10) || 0);
    onDraftChanged(false);
  }

  function updateFieldResults() {
    const mapped = state.preview?.mapped || [];
    $$("#fieldRows [data-value]").forEach((cell) => {
      const m = mapped[Number(cell.dataset.value)];
      cell.textContent = m ? (m.missing ? "(kosong)" : m.value) : "";
      cell.classList.toggle("missing", Boolean(m?.missing));
    });
    $$("#fieldRows select[data-f='position']").forEach((sel) => {
      const value = Number(sel.value);
      sel.innerHTML = positionOptions(value);
    });
  }

  function renderRemoteRows() {
    const fields = state.draft.printer.tij.fields;
    $("#remoteRows").replaceChildren(
      ...fields.map((f, index) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><input class="input" type="number" min="0" max="255" data-r="fieldId" value="${f.fieldId}" /></td>
          <td><input class="input" data-r="name" value="${esc(f.name)}" /></td>
          <td><input class="input mono" data-r="value" value="${esc(f.value)}" spellcheck="false" /></td>
          <td class="value-cell" data-remote="${index}"></td>
          <td><button class="btn btn-sm btn-icon" data-r="delete" title="Hapus"><svg><use href="#i-trash"/></svg></button></td>`;
        const update = (e) => {
          const prop = e.target.dataset.r;
          if (!prop || prop === "delete") return;
          f[prop] =
            prop === "fieldId"
              ? Math.max(0, Math.min(255, parseInt(e.target.value, 10) || 0))
              : e.target.value;
          onDraftChanged(false);
        };
        tr.addEventListener("input", update);
        tr.addEventListener("change", update);
        tr.querySelector('[data-r="delete"]').onclick = () => {
          fields.splice(fields.indexOf(f), 1);
          onDraftChanged(true);
        };
        return tr;
      }),
    );
    updateRemoteResults();
  }

  function updateRemoteResults() {
    const values = state.preview?.remoteFields || [];
    $$("#remoteRows [data-remote]").forEach((cell) => {
      const v = values[Number(cell.dataset.remote)];
      cell.textContent = v ? v.value : "";
    });
  }

  function renderStepRows() {
    const root = $("#stepRows");
    const steps = state.draft.printer.steps;
    root.replaceChildren(
      ...steps.map((step, index) => {
        const div = document.createElement("div");
        div.className = "step";
        div.innerHTML = `
          <div class="step-head">
            <span class="flow-num">${index + 1}</span>
            <input class="input step-name" data-s="name" value="${esc(step.name)}" placeholder="Nama langkah" />
            <select class="input" data-s="trigger">${TRIGGERS.map(([v, l]) => `<option value="${v}"${v === step.trigger ? " selected" : ""}>${l}</option>`).join("")}</select>
            <label class="check"><input type="checkbox" data-s="enabled"${step.enabled ? " checked" : ""} /> Aktif</label>
            <button class="btn btn-sm btn-icon" data-s="up" title="Naik"${index === 0 ? " disabled" : ""}>&uarr;</button>
            <button class="btn btn-sm btn-icon" data-s="down" title="Turun"${index === steps.length - 1 ? " disabled" : ""}>&darr;</button>
            <button class="btn btn-sm btn-icon" data-s="delete" title="Hapus"><svg><use href="#i-trash"/></svg></button>
          </div>
          <textarea class="input" rows="2" data-s="template" spellcheck="false">${esc(step.template)}</textarea>`;
        div.addEventListener("input", (e) => updateStep(step, e.target));
        div.addEventListener("change", (e) => updateStep(step, e.target));
        div.querySelector('[data-s="up"]').onclick = () => moveStep(index, -1);
        div.querySelector('[data-s="down"]').onclick = () => moveStep(index, 1);
        div.querySelector('[data-s="delete"]').onclick = () => {
          steps.splice(index, 1);
          onDraftChanged(true);
        };
        return div;
      }),
    );
  }

  function updateStep(step, el) {
    const prop = el.dataset.s;
    if (!prop || ["up", "down", "delete"].includes(prop)) return;
    step[prop] = el.type === "checkbox" ? el.checked : el.value;
    onDraftChanged(false);
  }

  function moveStep(index, delta) {
    const steps = state.draft.printer.steps;
    const [item] = steps.splice(index, 1);
    steps.splice(index + delta, 0, item);
    onDraftChanged(true);
  }

  let lastTemplateBox = null;

  function renderTokenChips() {
    const keys = [
      ...new Set([
        ...state.draft.mapping.fields.map((f) => f.key),
        ...(state.meta?.builtIns || []),
      ]),
    ];
    $("#placeholderChips").innerHTML = keys
      .map(
        (k) =>
          `<button class="chip" data-insert="{${esc(k)}}">{${esc(k)}}</button>`,
      )
      .join("");
    const tokens = [
      "CR",
      "LF",
      "STX",
      "ETX",
      "ESC",
      "TAB",
      "ACK",
      "NAK",
      "EOT",
    ];
    $("#tokenChips").innerHTML = tokens
      .map(
        (t) =>
          `<button class="chip" data-insert="<${t}>">&lt;${t}&gt;</button>`,
      )
      .join("");
  }

  document.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-insert]");
    if (!chip) return;
    e.preventDefault();
    const box =
      lastTemplateBox && document.body.contains(lastTemplateBox)
        ? lastTemplateBox
        : $("#stepRows textarea");
    if (!box) return;
    const text = chip.dataset.insert;
    const start = box.selectionStart ?? box.value.length;
    const end = box.selectionEnd ?? box.value.length;
    box.value = box.value.slice(0, start) + text + box.value.slice(end);
    box.focus();
    box.selectionStart = box.selectionEnd = start + text.length;
    box.dispatchEvent(new Event("input", { bubbles: true }));
  });

  const requestPreview = debounce(async () => {
    if (!state.draft) return;
    try {
      state.preview = await api("POST", "/api/settings/preview", {
        settings: state.draft,
        sample: state.draft.mapping.sampleData,
      });
      renderPreview();
    } catch (err) {
      $("#previewBox").innerHTML =
        `<div class="pv-err">${esc(err.message)}</div>`;
    }
  }, 250);

  function renderPreview() {
    const p = state.preview;
    if (!p) return;
    const assigned = new Map();
    state.draft.mapping.fields.forEach((f) => {
      if (state.draft.mapping.mode === "Delimited" && f.position > 0) {
        assigned.set(f.position, [...(assigned.get(f.position) || []), f.key]);
      }
    });
    $("#splitChips").innerHTML =
      state.draft.mapping.mode === "Delimited"
        ? p.fields
            .map((v, i) => {
              const keys = assigned.get(i + 1);
              return `<span class="chip${keys ? " assigned" : ""}"><b>${i + 1}</b>${esc(v === "" ? "(kosong)" : v)}${keys ? ` <small>&rarr; ${esc(keys.join(", "))}</small>` : ""}</span>`;
            })
            .join("")
        : "";
    updateFieldResults();
    updateRemoteResults();
    renderTokenChips();

    const rows = [];
    const errors = [
      ...p.parseErrors.map((e) => `Data: ${e}`),
      ...p.settingErrors,
    ];
    if (errors.length)
      rows.push(
        `<div class="pv-row"><div class="pv-key">Masalah</div><div class="pv-err">${errors.map(esc).join("<br>")}</div></div>`,
      );
    p.mapped.forEach((m) => {
      rows.push(
        `<div class="pv-row"><div class="pv-key">${esc(m.label)} <span class="muted">{${esc(m.key)}}</span></div><div class="pv-val">${m.missing ? '<span class="pv-err">(kosong)</span>' : esc(m.value)}</div></div>`,
      );
    });
    rows.push(
      `<div class="pv-row"><div class="pv-key">Counter <span class="muted">{COUNTER}</span></div><div class="pv-val">${esc(p.counter.text)} <span class="muted">— ${esc(p.counter.explanation)}</span></div></div>`,
    );
    p.commands.forEach((c) => {
      rows.push(
        `<div class="pv-row"><div class="pv-key">${esc(c.name)}</div>
         <div class="pv-val${c.send ? "" : " skip"}">${c.send ? "" : "(tidak dikirim untuk data ini) "}${esc(c.text)}<div class="pv-hex">HEX ${esc(c.hex.replace(/(..)/g, "$1 ").trim())}</div></div></div>`,
      );
    });
    if (p.feedback.enabled) {
      rows.push(
        `<div class="pv-row"><div class="pv-key">Feedback ke server<br><span class="muted">setelah printer selesai cetak</span></div>
         <div class="pv-val">${esc(p.feedback.text)}<div class="pv-hex">HEX ${esc(p.feedback.hex.replace(/(..)/g, "$1 ").trim())}</div></div></div>`,
      );
    }
    $("#previewBox").innerHTML = rows.join("");
  }

  async function saveSettings() {
    try {
      const result = await api("PUT", "/api/settings", state.draft);
      state.settings = clone(state.draft);
      setDirty(false);
      $("#settingsErrors").hidden = true;
      toast(
        result.reconnecting
          ? "Settings disimpan — koneksi server diulang"
          : "Settings disimpan",
      );
      state.liveRows = state.draft.general.liveRows;
    } catch (err) {
      const errors = err.data?.errors || [err.message];
      const box = $("#settingsErrors");
      box.innerHTML = `<b>Settings belum disimpan:</b><br>${errors.map(esc).join("<br>")}`;
      box.hidden = false;
      toast("Periksa kembali isian settings", true);
    }
  }

  function showTab(tab) {
    $$("#settingsTabs .tab").forEach((t) =>
      t.classList.toggle("active", t.dataset.tab === tab),
    );
    $$(".settings-panel").forEach((p) =>
      p.classList.toggle("active", p.dataset.panel === tab),
    );
  }

  async function runTest(button, resultEl, path, body) {
    button.disabled = true;
    resultEl.className = "test-result";
    resultEl.textContent = "Menguji...";
    try {
      const r = await api("POST", path, body);
      resultEl.className = `test-result ${r.ok ? "ok" : "err"}`;
      resultEl.textContent = `${r.message} (${r.elapsedMs} ms)`;
    } catch (err) {
      resultEl.className = "test-result err";
      resultEl.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  }

  // ------------------------------------------------------------------ navigation & window

  function route() {
    const [page, sub] = (location.hash.replace("#", "") || "dashboard").split(
      "/",
    );
    const target = PAGES[page] ? page : "dashboard";
    state.page = target;
    $$(".nav-item").forEach((a) =>
      a.classList.toggle("active", a.dataset.page === target),
    );
    $$(".page").forEach((p) =>
      p.classList.toggle("active", p.id === `page-${target}`),
    );
    $("#pageTitle").textContent = PAGES[target][0];
    $("#pageSubtitle").textContent = PAGES[target][1];
    if (target === "settings") {
      loadSettings().catch((err) => toast(err.message, true));
      if (sub) showTab(sub);
    }
    if (target === "logs")
      loadLogDates().catch((err) => toast(err.message, true));
  }

  const webview = window.chrome?.webview;

  function windowCommand(cmd) {
    if (webview) {
      webview.postMessage({ cmd });
      return true;
    }
    return false;
  }

  async function onWindowButton(action) {
    if (action === "close") {
      const running = state.status?.running;
      const ok = await modal({
        title: "Tutup aplikasi?",
        body: running
          ? "Transfer sedang berjalan. Menutup aplikasi akan <b>menghentikan transfer</b> ke printer."
          : "Aplikasi SHARX Data Transfer akan ditutup.",
        okText: "Tutup",
        danger: true,
      });
      if (!ok) return;
      if (windowCommand("close")) return;
      await api("POST", "/api/app/shutdown").catch(() => {});
      document.body.innerHTML =
        '<div style="display:grid;place-items:center;height:100vh;font-size:22px;color:#8fa3dc">Aplikasi ditutup. Tab ini boleh ditutup.</div>';
      return;
    }
    if (windowCommand(action)) return;
    if (action === "maximize") {
      if (document.fullscreenElement) document.exitFullscreen();
      else document.documentElement.requestFullscreen?.();
    } else if (action === "minimize") {
      toast("Minimize tersedia di aplikasi desktop (SharxDataTransfer.exe)");
    }
  }

  function bindUi() {
    window.addEventListener("hashchange", route);
    $$(".win-btn").forEach(
      (b) => (b.onclick = () => onWindowButton(b.dataset.win)),
    );
    // Drag jendela dari area judul; klik dua kali cepat = maximize / restore.
    let lastDown = 0;
    $("#titlebar").addEventListener("mousedown", (e) => {
      if (e.button !== 0 || e.target.closest("button")) return;
      const now = Date.now();
      windowCommand(now - lastDown < 400 ? "maximize" : "drag");
      lastDown = now;
    });

    $("#btnStart").onclick = async () => {
      try {
        await api("POST", "/api/transfer/start");
      } catch (err) {
        toast(err.message, true);
      }
    };
    $("#btnStop").onclick = async () => {
      try {
        await api("POST", "/api/transfer/stop");
      } catch (err) {
        toast(err.message, true);
      }
    };
    $("#autoScroll").onchange = (e) => {
      if (e.target.checked) $("#incomingScroll").scrollTop = 0;
    };
    $("#btnResetAll").onclick = () => resetCounter(null);
    $("#btnResetAll2").onclick = () => resetCounter(null);

    $("#logDate").onchange = () =>
      loadLogDay().catch((err) => toast(err.message, true));
    $("#logStatus").onchange = renderLogHistory;
    $("#logSearch").oninput = debounce(renderLogHistory, 150);
    $("#btnLogRefresh").onclick = () =>
      loadLogDates().catch((err) => toast(err.message, true));

    $$("#settingsTabs .tab").forEach(
      (t) => (t.onclick = () => showTab(t.dataset.tab)),
    );
    $("#btnSettingsSave").onclick = saveSettings;
    $("#btnSettingsReload").onclick = () =>
      loadSettings(true).catch((err) => toast(err.message, true));
    $("#btnAddField").onclick = () => {
      const used = new Set(state.draft.mapping.fields.map((f) => f.key));
      let n = 1;
      while (used.has(`FIELD${n}`)) n++;
      const nextPos =
        Math.max(0, ...state.draft.mapping.fields.map((f) => f.position)) + 1;
      state.draft.mapping.fields.push({
        key: `FIELD${n}`,
        label: `Field ${n}`,
        position: nextPos,
        start: 0,
        length: 0,
        required: false,
      });
      onDraftChanged(true);
    };
    $("#btnAddRemote").onclick = () => {
      const fields = state.draft.printer.tij.fields;
      const next = fields.length
        ? Math.max(...fields.map((f) => f.fieldId)) + 1
        : 0;
      fields.push({ fieldId: next, name: `Field ${next}`, value: "" });
      onDraftChanged(true);
    };
    $("#btnAddStep").onclick = () => {
      state.draft.printer.steps.push({
        name: "Langkah baru",
        enabled: true,
        trigger: "Always",
        template: "{COUNTER}<CR>",
      });
      onDraftChanged(true);
    };
    $("#btnTestServer").onclick = (e) =>
      runTest(
        e.currentTarget,
        $("#testServerResult"),
        "/api/settings/test-server",
        state.draft.server,
      );
    $("#btnTestPrinter").onclick = (e) =>
      runTest(
        e.currentTarget,
        $("#testPrinterResult"),
        "/api/settings/test-printer",
        state.draft.printer,
      );
  }

  bindUi();
  route();
  connectWs();
})();
