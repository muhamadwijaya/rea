"use strict";

/* SHARX Data Transfer — UI. Tanpa framework; data live lewat WebSocket /ws. */
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const SPECIAL_KEYS = ["OPERATOR", "TEMPLATE", "SERVER_COUNTER"];
  const PAGES = ["dashboard", "logs", "settings", "about"];

  const state = {
    status: null,
    stats: null,
    counter: null,
    records: [],
    log: [],
    feedback: [],
    serverKind: "MQTT",
    version: "1.0.0",
    settings: null,
    meta: null,
    draft: null,
    dirty: false,
    preview: null,
    page: "dashboard",
    logEntries: [],
    logMatched: 0,
    lastSeq: 0,
    lastLogId: 0,
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
  const VIEW_LIMIT = 50;

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
      case "batch":
        applyBatch(msg.data);
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
    state.status = data.status;
    state.stats = data.stats;
    state.counter = data.counter;
    state.records = (data.records || []).slice(0, VIEW_LIMIT);
    state.log = (data.log || []).slice(-VIEW_LIMIT).reverse();
    state.feedback = (data.feedback || []).slice(0, VIEW_LIMIT);
    state.lastSeq = state.records.length ? state.records[0].seq : 0;
    state.lastLogId = state.log.length ? state.log[0].id : 0;
    $("#aboutVersion").textContent = `v${data.version}`;
    $("#dataDir").textContent = data.dataDirectory;
    renderStatus();
    renderStats();
    renderCounter();
    renderAllRecords();
    renderLog();
    renderFeedback();
  }

  /** Update gabungan dari server (maks. 4x per detik). */
  function applyBatch(batch) {
    if (batch.status) {
      state.status = batch.status;
      renderStatus();
    }
    if (batch.stats) {
      state.stats = batch.stats;
      renderStats();
    }
    if (batch.counter) {
      state.counter = batch.counter;
      renderCounter();
    }
    if (batch.records?.length) addRecords(batch.records);
    if (batch.logs?.length) addLogs(batch.logs);
    if (batch.feedback?.length) addFeedback(batch.feedback);
  }

  function setSystem(online) {
    $("#offlineBanner").hidden = online;
  }

  // ------------------------------------------------------------------ dashboard

  /** Ubah teks hanya jika berbeda (menghindari layout ulang yang tidak perlu). */
  function setText(el, text) {
    if (el.textContent !== text) {
      el.textContent = text;
      return true;
    }
    return false;
  }

  function setBadge(el, text, kind) {
    setText(el, text);
    el.className = `hud-badge${kind ? ` ${kind}` : ""}`;
  }

  function serverBadge(s) {
    if (!s.running) return ["STOPPED", "", "off"];
    if (s.server === "Connected") return ["ONLINE", "ok", "on"];
    if (s.server === "Connecting") return ["CONNECTING", "warn", "wait"];
    return ["OFFLINE", "err", "off"];
  }

  function printerBadge(s) {
    switch (s.printer) {
      case "Disabled":
        return ["MONITOR", "", "off"];
      case "Connecting":
        return ["CONNECTING", "warn", "wait"];
      case "Error":
        return ["ERROR", "err", "off"];
      case "Connected":
        if (s.printState === "NotStarted" || s.waitingForPrinter)
          return ["NOT STARTED", "warn", "wait"];
        return s.printState === "Started"
          ? ["STARTED", "live", "on"]
          : ["ONLINE", "ok", "on"];
      default:
        return ["OFFLINE", "", "off"];
    }
  }

  function renderStatus() {
    const s = state.status;
    if (!s) return;
    const serverState = renderServerHud(s);
    const printerState = renderPrinterHud(s);
    renderFlowState(s, serverState, printerState);
    renderControls(s);
    renderFeedbackEmpty();
    setText(
      $("#feedbackSub"),
      s.feedbackEnabled ? "After Print \u2192 Server" : "Feedback nonaktif",
    );
  }

  /** Chip MQTT Broker / TCP Server di kanan atas. Mengembalikan on / wait / off. */
  function renderServerHud(s) {
    const [text, kind, hudState] = serverBadge(s);
    setText(
      $("#serverName"),
      s.serverProtocol === "Mqtt" ? "MQTT Broker" : "TCP Server",
    );
    setText($("#serverEndpoint"), s.serverEndpoint);
    setBadge($("#serverBadge"), text, kind);
    $("#hudServer").dataset.state = hudState;
    $("#hudServer").title = s.lastError || "";
    return hudState;
  }

  /** Chip printer di kanan atas. Mengembalikan on / wait / off. */
  function renderPrinterHud(s) {
    const [text, kind, hudState] = printerBadge(s);
    // Nama mesin (jenis TIJ / CIJ + nama) diatur di Settings → Koneksi.
    setText($("#printerName"), s.printerName);
    setText($("#printerEndpoint"), `(${s.printerEndpoint})`);
    setBadge($("#printerBadge"), text, kind);
    $("#hudPrinter").dataset.state = hudState;
    $("#hudPrinter").title = [
      `${s.printerName} · ${s.printerProtocol}`,
      s.printerMessage,
    ]
      .filter(Boolean)
      .join("\n");
    return hudState;
  }

  /** Diagram Printer → Queue → Server: lampu, animasi kabel dan label. */
  function renderFlowState(s, serverState, printerState) {
    const svg = $("#flowSvg");
    svg.dataset.server = serverState;
    svg.dataset.printer = printerState;
    svg.dataset.running = String(Boolean(s.running));
    state.serverKind = s.serverProtocol === "Mqtt" ? "MQTT" : "TCP";
    const changed = [
      setText($("#flowPrinterLabel"), `[ ${s.printerName} ]`),
      setText($("#flowQueueLabel"), `[ Queue: ${s.job?.nextCounter || "-"} ]`),
      renderServerLabel(),
    ];
    if (changed.some(Boolean)) layoutFlowArrows();
  }

  const PRINT_STATE_TEXT = {
    Started: ["Started", "ok"],
    NotStarted: ["Belum start", "wait"],
  };

  /** Tombol Control Panel dan status print di bawahnya. */
  function renderControls(s) {
    $("#incomingEmpty").innerHTML = s.running
      ? "Menunggu data dari server..."
      : "Belum ada data. Tekan <b>Start Transfer</b> untuk mulai menerima data dari server.";
    setPair($("#pairTransfer"), s.running, false);
    setPair(
      $("#pairPrint"),
      // Status printer belum terbaca dianggap belum start.
      s.printState === "Started",
      !s.canControlPrint || s.printer === "Disabled",
    );

    const [label, kind] = PRINT_STATE_TEXT[s.printState] || ["-", ""];
    const printState = $("#printState");
    printState.textContent = s.waitingForPrinter
      ? `${label} — menunggu ${s.waitingRecords || 1} data`
      : label;
    printState.className = s.waitingForPrinter ? "wait" : kind;
    setText($("#jobNext"), s.job?.nextCounter || "-");
    setText($("#jobQueue"), queueText(s));
    setText($("#printerMsg"), s.printerMessage || "");
  }

  /**
   * Toggle Start / Stop: belum jalan = Start menyala, Stop mati; sudah jalan = Start mati, Stop menyala.
   * locked = tidak bisa dikendalikan (keduanya mati).
   */
  function setPair(pair, running, locked) {
    pair.querySelector(".ctl-start").disabled = locked || running;
    pair.querySelector(".ctl-stop").disabled = locked || !running;
  }

  function queueText(s) {
    const parts = [];
    if (s.job)
      parts.push(
        s.job.armed
          ? `Antrean printer: ${s.job.pending}`
          : "Menunggu printer siap",
      );
    // Feedback normalnya terkirim dalam milidetik; tampilkan hanya jika benar-benar tertahan.
    if (s.feedbackQueued && (s.server !== "Connected" || s.feedbackQueued > 5))
      parts.push(`${s.feedbackQueued} laporan menunggu server`);
    return parts.join(" · ");
  }

  function bytesText(n) {
    const f = formatBytes(n);
    return `${f.value} ${f.unit}`;
  }

  function renderServerLabel() {
    const sent = state.stats ? bytesText(state.stats.sentBytes) : "0 B";
    return setText(
      $("#flowServerLabel"),
      `[ Server ${state.serverKind || "MQTT"} ${sent} ]`,
    );
  }

  function renderStats() {
    const s = state.stats;
    if (!s) return;
    setText($("#rxValue"), bytesText(s.receivedBytes));
    setText($("#txValue"), bytesText(s.sentBytes));
    setText($("#statPrinted"), String(s.recordsPrinted));
    setText($("#statFailed"), String(s.recordsFailed));
    if (
      setText($("#flowServerBytes"), bytesText(s.receivedBytes + s.sentBytes))
    )
      fitServerBytes();
    const values = {
      records: s.recordsReceived,
      forwarded: s.recordsForwarded,
      printed: s.recordsPrinted,
      failed: s.recordsFailed,
    };
    $$("[data-stat]").forEach((el) =>
      setText(el, String(values[el.dataset.stat])),
    );
    if (renderServerLabel()) layoutFlowArrows();
  }

  /** Ukur ulang teks diagram (setelah font termuat, resize, atau kembali ke Dashboard). */
  function layoutFlow() {
    if (state.page !== "dashboard") return;
    fitServerBytes();
    layoutFlowArrows();
  }

  /** Teks byte di ikon server dipersempit jika terlalu panjang (mis. "1023.5 MB"). */
  function fitServerBytes() {
    const el = $("#flowServerBytes");
    el.removeAttribute("textLength");
    try {
      if (el.getComputedTextLength() > 50) {
        el.setAttribute("textLength", "50");
        el.setAttribute("lengthAdjust", "spacingAndGlyphs");
      }
    } catch {
      // SVG belum dirender.
    }
  }

  /** Panah antar label diagram mengikuti lebar teks label. */
  function layoutFlowArrows() {
    if (state.page !== "dashboard") return;
    try {
      const a = $("#flowPrinterLabel").getBBox();
      const b = $("#flowQueueLabel").getBBox();
      const c = $("#flowServerLabel").getBBox();
      if (!a.width) return;
      setArrow($("#flowArrow1"), a.x + a.width + 12, b.x - 12);
      setArrow($("#flowArrow2"), b.x + b.width + 12, c.x - 12);
    } catch {
      // SVG belum dirender.
    }
  }

  function setArrow(group, x1, x2) {
    const [line, head] = group.querySelectorAll("path");
    group.style.display = x2 - x1 > 24 ? "" : "none";
    line.setAttribute("d", `M${x1.toFixed(1)} 196H${x2.toFixed(1)}`);
    head.setAttribute("d", `M${(x2 - 10).toFixed(1)} 190l10 6-10 6`);
  }

  // ---- grafik kecil (sampel tiap detik dari statistik)

  const spark = { printed: [], sent: [], rate: [], last: null };

  function pushSample(list, value, max) {
    list.push(value);
    if (list.length > max) list.shift();
  }

  function sparkPath(values, width, height, slots) {
    const n = Math.max(slots, 2);
    // Skala terhadap puncak dan rata-rata: lalu lintas stabil tampil rendah, lonjakan menonjol.
    const avg = values.reduce((a, b) => a + b, 0) / Math.max(1, values.length);
    const peak = Math.max(1e-9, Math.max(...values) * 1.15, avg * 2.6);
    const step = width / (n - 1);
    const offset = n - values.length;
    const pts = [];
    for (let i = 0; i < n; i++) {
      const v = i < offset ? 0 : values[i - offset];
      pts.push([i * step, height - 1.5 - (v / peak) * (height - 4)]);
    }
    let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
    for (let i = 1; i < pts.length; i++) {
      const [x0, y0] = pts[i - 1];
      const [x1, y1] = pts[i];
      const mx = (x0 + x1) / 2;
      d += `C${mx.toFixed(1)} ${y0.toFixed(1)} ${mx.toFixed(1)} ${y1.toFixed(1)} ${x1.toFixed(1)} ${y1.toFixed(1)}`;
    }
    return d;
  }

  function drawSpark(svg, values, slots) {
    const [, , w, h] = svg.getAttribute("viewBox").split(" ").map(Number);
    const line = sparkPath(values, w, h, slots);
    svg.querySelector(".spark-line").setAttribute("d", line);
    const area = svg.querySelector(".spark-area");
    if (area) area.setAttribute("d", `${line}L${w} ${h}L0 ${h}Z`);
  }

  /** Selisih per detik: cetakan, byte terkirim, dan byte total (terima + kirim). */
  function sampleSparks() {
    const s = state.stats;
    const now = s
      ? {
          printed: s.recordsPrinted,
          sent: s.sentBytes,
          total: s.receivedBytes + s.sentBytes,
        }
      : { printed: 0, sent: 0, total: 0 };
    const prev = spark.last || now;
    // Reset statistik (Start ulang) membuat selisih negatif: dianggap 0.
    const delta = (key) => Math.max(0, now[key] - prev[key]);
    spark.last = now;
    pushSample(spark.printed, delta("printed"), 60);
    pushSample(spark.sent, delta("sent"), 40);
    pushSample(spark.rate, delta("total"), 120);
    if (state.page !== "dashboard" || document.hidden) return;
    drawSpark($("#sparkPrinted"), spark.printed, 60);
    drawSpark($("#sparkSent"), spark.sent, 40);
    drawSpark($("#sparkRate"), spark.rate, 120);
  }

  // ---- Incoming Data

  function incomingRow(record) {
    const tr = document.createElement("tr");
    const failed =
      record.status === "Rejected" || record.status === "PrinterError";
    if (failed) tr.className = "row-error";
    if (record.status === "Waiting") tr.className = "row-wait";
    tr.title = recordTooltip(record);
    tr.innerHTML = `<td></td><td>${esc(timeText(record.time))}</td><td><span class="src-tag ${esc(record.source)}">${esc(record.source)}</span></td><td>${esc(record.data)}</td>
      <td class="cell-counter">${esc(record.counter || "-")}</td>
      <td><span class="status-tag ${esc(record.status)}">${esc(statusLabel(record.status))}</span></td>`;
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

  function statusLabel(status) {
    return (
      {
        Ok: "OK",
        Printed: "PRINTED",
        Monitor: "MONITOR",
        Rejected: "DITOLAK",
        Waiting: "MENUNGGU",
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
    body.replaceChildren(...state.records.map(incomingRow));
    renumber(body);
    $("#incomingEmpty").classList.toggle("show", state.records.length === 0);
  }

  /** Tambah data baru (urutan lama → baru), simpan hanya 50 terakhir. */
  function addRecords(records) {
    // Lewati data yang sudah tampil (mis. ikut di snapshot saat browser baru terhubung).
    const unseen = records.filter((r) => r.seq > state.lastSeq);
    if (!unseen.length) return;
    state.lastSeq = Math.max(...unseen.map((r) => r.seq));
    const fresh = unseen.slice(-VIEW_LIMIT).reverse();
    state.records = fresh.concat(state.records).slice(0, VIEW_LIMIT);

    const scroller = $("#incomingScroll");
    const auto = $("#autoScroll").checked;
    const before = scroller.scrollHeight;
    const body = $("#incomingBody");
    const fragment = document.createDocumentFragment();
    for (const record of fresh) {
      const row = incomingRow(record);
      row.classList.add("row-new");
      fragment.appendChild(row);
    }
    body.prepend(fragment);
    while (body.rows.length > VIEW_LIMIT) body.deleteRow(body.rows.length - 1);
    renumber(body);
    if (auto) {
      scroller.scrollTop = 0;
    } else {
      scroller.scrollTop += scroller.scrollHeight - before;
    }
    $("#incomingEmpty").classList.remove("show");
  }

  // ---- Transfer Log

  /** Kata kunci seperti "Printed:" dan "server:" diberi warna seperti desain. */
  function logMessageHtml(message) {
    const re = /(^|-> )([A-Za-z][A-Za-z0-9 ]{0,23}):(?= )/g;
    let html = "";
    let last = 0;
    let m;
    while ((m = re.exec(message))) {
      const start = m.index + m[1].length;
      html += `${esc(message.slice(last, start))}<span class="log-key">${esc(m[2])}</span>:`;
      last = start + m[2].length + 1;
    }
    return html + esc(message.slice(last));
  }

  function logLine(entry) {
    const div = document.createElement("div");
    div.className = "log-line";
    const status = entry.status.toUpperCase();
    div.innerHTML = `[${esc(timeText(entry.time))}] ${logMessageHtml(entry.message)}  - <span class="log-status s-${esc(entry.status.toLowerCase())}">${esc(status)}</span>`;
    div.title = entry.message;
    return div;
  }

  function renderLog() {
    $("#logBox").replaceChildren(...state.log.map(logLine));
  }

  function prependLines(box, entries) {
    const fragment = document.createDocumentFragment();
    for (const entry of entries) fragment.appendChild(logLine(entry));
    box.prepend(fragment);
    while (box.children.length > VIEW_LIMIT) box.lastChild.remove();
  }

  /** Tambah log baru (urutan lama → baru), simpan hanya 50 terakhir. */
  function addLogs(entries) {
    entries = entries.filter((e) => e.id > state.lastLogId);
    if (!entries.length) return;
    state.lastLogId = Math.max(...entries.map((e) => e.id));
    const fresh = entries.slice(-VIEW_LIMIT).reverse();
    state.log = fresh.concat(state.log).slice(0, VIEW_LIMIT);
    prependLines($("#logBox"), fresh);

    if (
      state.page === "logs" &&
      $("#logDate").value === localDate(new Date())
    ) {
      const status = $("#logStatus").value;
      const q = $("#logSearch").value.trim().toLowerCase();
      const matching = entries.filter(
        (e) =>
          (!status || e.status === status) &&
          (!q || e.message.toLowerCase().includes(q)),
      );
      if (matching.length) {
        state.logEntries = state.logEntries.concat(matching).slice(-VIEW_LIMIT);
        state.logMatched += matching.length;
        renderLogHistory();
      }
    }
  }

  // ---- Transfer balik ke server (feedback setelah sinyal after print)

  const FEEDBACK_STATE = {
    Sent: ["OK", "ok"],
    Queued: ["MENUNGGU", "queued"],
    Dropped: ["DIBUANG", "dropped"],
  };

  function feedbackLine(f) {
    const div = document.createElement("div");
    div.className = "log-line";
    div.dataset.seq = f.seq;
    const [label, kind] = FEEDBACK_STATE[f.state] || [f.state, "info"];
    div.innerHTML = `[${esc(timeText(f.time))}] <span class="log-key">Printed</span>: ${esc(f.operator)} / ${esc(f.template)} / ${esc(f.counter)} -&gt; <span class="log-key">server</span>: ${esc(f.data)}  - <span class="log-status s-${kind}">${label}</span>`;
    const sent = f.sentAt ? `\nTerkirim: ${timeText(f.sentAt)}` : "";
    div.title = `Data ke server: ${f.data} (${f.bytes} byte)${sent}`;
    return div;
  }

  function renderFeedbackEmpty() {
    const empty = $("#feedbackEmpty");
    const s = state.status;
    empty.hidden = state.feedback.length > 0;
    if (!s || empty.hidden) return;
    empty.innerHTML = s.feedbackEnabled
      ? `Belum ada data tercetak. Setiap printer mengirim sinyal selesai cetak (after print), data yang dikirim balik ke server tampil di sini.<br>Data dikirim: <code>${esc(s.feedbackFormat)}</code> &middot; ubah di <a href="#settings/mapping">Settings &rarr; Flow Mapping Data</a>.`
      : `Feedback ke server <b>nonaktif</b>. Aktifkan di <a href="#settings/mapping">Settings &rarr; Flow Mapping Data</a> (bagian 6).`;
  }

  function renderFeedback() {
    const box = $("#feedbackBox");
    const empty = $("#feedbackEmpty");
    box.replaceChildren(empty, ...state.feedback.map(feedbackLine));
    renderFeedbackEmpty();
  }

  /** Feedback baru (urutan lama → baru) atau perubahan status dengan Seq yang sama. */
  function addFeedback(items) {
    const box = $("#feedbackBox");
    for (const f of items) {
      const index = state.feedback.findIndex((x) => x.seq === f.seq);
      if (index >= 0) {
        state.feedback[index] = f;
        box
          .querySelector(`[data-seq="${f.seq}"]`)
          ?.replaceWith(feedbackLine(f));
        continue;
      }
      state.feedback.unshift(f);
      $("#feedbackEmpty").after(feedbackLine(f));
    }
    state.feedback.length = Math.min(state.feedback.length, VIEW_LIMIT);
    while (box.children.length > VIEW_LIMIT + 1) box.lastChild.remove();
    renderFeedbackEmpty();
  }

  // ------------------------------------------------------------------ counter & job

  function renderCounter() {
    const c = state.counter;
    if (!c) return;
    setText($("#jobOperator"), c.activeOperator || "-");
    setText($("#jobTemplate"), c.activeTemplate || "-");
    const big = $("#jobCounter");
    const value = c.activeValue || "---";
    setText(big, value);
    big.style.setProperty("--len", String(Math.max(3, value.length)));
    setText($("#jobDate"), c.businessDate || "");
    const body = $("#counterBody");
    const rows = (c.templates || []).map((t) => {
      const tr = document.createElement("tr");
      if (t.template === c.activeTemplate) tr.className = "highlight";
      tr.title = `Operator terakhir: ${t.lastOperator || "-"}\nUpdate: ${timeText(t.updatedAt)}`;
      tr.innerHTML = `<td>${esc(t.template)}</td><td>${esc(t.text)}</td><td>${t.records}<span class="row-actions"><button class="icon-btn" data-act="set" title="Ubah counter"><svg><use href="#i-edit"/></svg></button><button class="icon-btn" data-act="reset" title="Reset counter template ini"><svg><use href="#i-reset"/></svg></button></span></td>`;
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

  /** Log History: server hanya mengirim 50 baris terakhir yang cocok dengan filter. */
  async function loadLogDay() {
    const date = $("#logDate").value;
    const params = new URLSearchParams();
    const status = $("#logStatus").value;
    const q = $("#logSearch").value.trim();
    if (status) params.set("status", status);
    if (q) params.set("q", q);
    $("#btnLogDownload").href =
      `/api/logs/${encodeURIComponent(date)}/download`;
    const page = await api(
      "GET",
      `/api/logs/${encodeURIComponent(date)}?${params}`,
    );
    state.logEntries = page.entries;
    state.logMatched = page.matched;
    renderLogHistory();
  }

  function renderLogHistory() {
    const list = state.logEntries.slice().reverse();
    $("#logHistory").replaceChildren(...list.map(logLine));
    $("#logCount").textContent =
      state.logMatched > list.length
        ? `${list.length} baris terakhir dari ${state.logMatched} (Download untuk log lengkap)`
        : `${list.length} baris`;
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
    renderRemoteRows();
    renderFeedbackRows();
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
      renderRemoteRows();
      renderFeedbackRows();
    }
    requestPreview();
  }

  function onBindingInput(e) {
    const el = e.target.closest("[data-bind]");
    if (!el || !state.draft) return;
    const path = el.dataset.bind;
    const value = readBinding(el);
    // Pindah ke format custom: mulai dari susunan data yang sedang dipilih.
    if (
      path === "feedback.format" &&
      value === "Custom" &&
      state.draft.feedback.format !== "Custom" &&
      state.preview?.feedback?.template
    ) {
      state.draft.feedback.template = state.preview.feedback.template;
      const box = $('[data-bind="feedback.template"]');
      if (box) box.value = state.draft.feedback.template;
    }
    setPath(state.draft, path, value);
    onDraftChanged(path === "mapping.mode");
  }

  document.addEventListener("input", onBindingInput);
  document.addEventListener("change", onBindingInput);

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
    const fields = state.draft.printer.fields;
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

  const BUILT_IN_LABELS = {
    COUNTER: "Counter yang tercetak",
    DATE: "Tanggal",
    TIME: "Jam",
    PROD_DATE: "Tanggal produksi (hari counter)",
    SEQ: "Nomor urut",
    RAW: "Data asli dari server",
  };

  /** Pilihan data feedback: field Flow Mapping + data bawaan. */
  function feedbackKeyOptions(selected) {
    const options = [
      ...state.draft.mapping.fields.map((f) => [f.key, f.label || f.key]),
      ...(state.meta?.builtIns || []).map((k) => [k, BUILT_IN_LABELS[k] || k]),
    ];
    if (selected && !options.some(([k]) => k === selected))
      options.push([selected, "(tidak ada di Flow Mapping)"]);
    return options
      .map(
        ([key, label]) =>
          `<option value="${esc(key)}"${key === selected ? " selected" : ""}>${esc(key)} — ${esc(label)}</option>`,
      )
      .join("");
  }

  function renderFeedbackRows() {
    const fields = state.draft.feedback.fields;
    $("#feedbackRows").replaceChildren(
      ...fields.map((f, index) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td class="row-num">${index + 1}</td>
          <td><select class="input" data-fb="key">${feedbackKeyOptions(f.key)}</select></td>
          <td><input class="input mono" data-fb="format" value="${esc(f.format)}" placeholder="-" spellcheck="false" /></td>
          <td class="value-cell" data-feedback="${index}"></td>
          <td><div class="row-tools">
            <button class="btn btn-sm btn-icon" data-fb="up" title="Naik"${index === 0 ? " disabled" : ""}><svg><use href="#i-up"/></svg></button>
            <button class="btn btn-sm btn-icon" data-fb="down" title="Turun"${index === fields.length - 1 ? " disabled" : ""}><svg><use href="#i-down"/></svg></button>
            <button class="btn btn-sm btn-icon" data-fb="delete" title="Hapus"><svg><use href="#i-trash"/></svg></button>
          </div></td>`;
        const update = (e) => {
          const prop = e.target.dataset.fb;
          if (prop !== "key" && prop !== "format") return;
          f[prop] = e.target.value.trim();
          onDraftChanged(false);
        };
        tr.addEventListener("input", update);
        tr.addEventListener("change", update);
        tr.querySelector('[data-fb="up"]').onclick = () =>
          moveFeedbackField(index, -1);
        tr.querySelector('[data-fb="down"]').onclick = () =>
          moveFeedbackField(index, 1);
        tr.querySelector('[data-fb="delete"]').onclick = () => {
          fields.splice(index, 1);
          onDraftChanged(true);
        };
        return tr;
      }),
    );
    updateFeedbackResults();
  }

  function moveFeedbackField(index, delta) {
    const fields = state.draft.feedback.fields;
    const target = index + delta;
    if (target < 0 || target >= fields.length) return;
    [fields[index], fields[target]] = [fields[target], fields[index]];
    onDraftChanged(true);
  }

  function updateFeedbackResults() {
    const values = state.preview?.feedback?.fields || [];
    $$("#feedbackRows [data-feedback]").forEach((cell) => {
      const v = values[Number(cell.dataset.feedback)];
      cell.textContent = v ?? "";
    });
  }

  let lastTemplateBox = null;

  function renderTokenChips() {
    const generic =
      state.draft.printer.protocol === "Generic"
        ? [
            "TEMPLATE_NAME",
            "FIELD_ID",
            "FIELD_NAME",
            "FIELD_VALUE",
            ...state.draft.printer.fields.map((f) => `FIELD_${f.fieldId}`),
          ]
        : [];
    const keys = [
      ...new Set([
        ...state.draft.mapping.fields.map((f) => f.key),
        ...(state.meta?.builtIns || []),
        ...generic,
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
      "SUM",
      "XOR",
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
        : $("#remoteRows input.mono");
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
    updateFeedbackResults();
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

  // ------------------------------------------------------------------ navigation

  function route() {
    const [page, sub] = (location.hash.replace("#", "") || "dashboard").split(
      "/",
    );
    const target = PAGES.includes(page) ? page : "dashboard";
    state.page = target;
    $$(".nav-item").forEach((a) =>
      a.classList.toggle("active", a.dataset.page === target),
    );
    $$(".page").forEach((p) =>
      p.classList.toggle("active", p.id === `page-${target}`),
    );
    if (target === "dashboard") layoutFlow();
    if (target === "settings") {
      loadSettings().catch((err) => toast(err.message, true));
      if (sub) showTab(sub);
    }
    if (target === "logs")
      loadLogDates().catch((err) => toast(err.message, true));
  }

  // ------------------------------------------------------------------ jendela desktop

  /**
   * Aplikasi desktop (WebView2) tanpa title bar: tombol minimize / maximize-restore / close ada di UI,
   * bar atas dipakai untuk menggeser jendela saat tidak fullscreen. Di browser biasa tidak tampil.
   */
  function setupWindowControls() {
    const webview = window.chrome?.webview;
    if (!webview) return;
    const send = (cmd) => webview.postMessage({ cmd });
    document.body.classList.add("desktop");
    $("#winControls").hidden = false;
    $$(".win-btn").forEach((b) => (b.onclick = () => send(b.dataset.win)));
    webview.addEventListener("message", (e) => {
      const maximized = e.data?.window === "maximized";
      if (!e.data?.window) return;
      $("#winMaximize").title = maximized
        ? "Restore (F11)"
        : "Fullscreen (F11)";
      $("#winMaximize use").setAttribute(
        "href",
        maximized ? "#i-restore" : "#i-max",
      );
    });
    send("state");

    let lastDown = 0;
    const onDragArea = (e) => {
      if (e.button !== 0 || e.target.closest("a, button, input, select, label"))
        return;
      const now = Date.now();
      send(now - lastDown < 400 ? "maximize" : "drag");
      lastDown = now;
    };
    $(".topbar").addEventListener("mousedown", onDragArea);
    $(".brand").addEventListener("mousedown", onDragArea);
    document.addEventListener("keydown", (e) => {
      if (e.key === "F11") {
        e.preventDefault();
        send("maximize");
      }
    });
  }

  function bindUi() {
    window.addEventListener("hashchange", route);
    window.addEventListener("resize", debounce(layoutFlow, 150));
    document.fonts?.ready.then(layoutFlow);
    setInterval(sampleSparks, 1000);

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
    const printControl = async (action) => {
      try {
        const r = await api("POST", `/api/printer/${action}`);
        toast(r.message, !r.ok);
      } catch (err) {
        toast(err.message, true);
      }
    };
    $("#btnPrintStart").onclick = () => printControl("start");
    $("#btnPrintStop").onclick = () => printControl("stop");
    $("#btnResetAll2").onclick = () => resetCounter(null);

    $("#logDate").onchange = () =>
      loadLogDay().catch((err) => toast(err.message, true));
    $("#logStatus").onchange = () =>
      loadLogDay().catch((err) => toast(err.message, true));
    $("#logSearch").oninput = debounce(
      () => loadLogDay().catch((err) => toast(err.message, true)),
      300,
    );
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
    $("#btnAddFeedback").onclick = () => {
      const fields = state.draft.feedback.fields;
      const used = new Set(fields.map((f) => f.key));
      const candidates = [
        ...state.draft.mapping.fields.map((f) => f.key),
        ...(state.meta?.builtIns || []),
      ];
      const key = candidates.find((k) => !used.has(k)) || "COUNTER";
      fields.push({ key, format: "" });
      onDraftChanged(true);
    };
    $("#btnAddRemote").onclick = () => {
      const fields = state.draft.printer.fields;
      const next = fields.length
        ? Math.max(...fields.map((f) => f.fieldId)) + 1
        : 0;
      fields.push({ fieldId: next, name: `Field ${next}`, value: "" });
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
  setupWindowControls();
  route();
  connectWs();
})();
