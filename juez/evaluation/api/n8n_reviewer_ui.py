from __future__ import annotations


def render_n8n_reviewer_html() -> str:
    return """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Juez n8n Reviewer</title>
  <style>
    :root {
      --bg: #f4efe7;
      --panel: rgba(255, 252, 247, 0.92);
      --panel-strong: #fffdf8;
      --ink: #1f2d2f;
      --muted: #5a6b6d;
      --line: rgba(31, 45, 47, 0.12);
      --accent: #d55d3f;
      --accent-soft: rgba(213, 93, 63, 0.12);
      --success: #2d6a4f;
      --warn: #b7791f;
      --danger: #a63d40;
      --shadow: 0 20px 45px rgba(43, 34, 23, 0.12);
      --radius: 22px;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Bahnschrift", "Trebuchet MS", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(213, 93, 63, 0.18), transparent 28%),
        radial-gradient(circle at right center, rgba(97, 153, 128, 0.14), transparent 26%),
        linear-gradient(160deg, #efe6d7 0%, #f9f5ee 48%, #eef4f1 100%);
    }

    .shell {
      width: min(1200px, calc(100% - 32px));
      margin: 28px auto 56px;
    }

    .hero {
      padding: 28px;
      border: 1px solid var(--line);
      border-radius: calc(var(--radius) + 4px);
      background: linear-gradient(140deg, rgba(255, 249, 240, 0.96), rgba(255, 255, 255, 0.86));
      box-shadow: var(--shadow);
      overflow: hidden;
      position: relative;
    }

    .hero::after {
      content: "";
      position: absolute;
      inset: auto -80px -60px auto;
      width: 220px;
      height: 220px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(213, 93, 63, 0.12), transparent 68%);
      pointer-events: none;
    }

    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 8px 12px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      font-weight: 700;
    }

    h1 {
      margin: 18px 0 10px;
      font-family: Georgia, "Times New Roman", serif;
      font-size: clamp(2rem, 3vw, 3.2rem);
      line-height: 0.98;
      max-width: 12ch;
    }

    .hero p {
      margin: 0;
      color: var(--muted);
      max-width: 74ch;
      font-size: 1.02rem;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(320px, 390px) minmax(0, 1fr);
      gap: 20px;
      margin-top: 22px;
      align-items: start;
    }

    .card {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }

    .form-card {
      padding: 22px;
      position: sticky;
      top: 20px;
    }

    .card-title {
      margin: 0 0 4px;
      font-size: 1.15rem;
      font-weight: 700;
    }

    .card-subtitle {
      margin: 0 0 18px;
      color: var(--muted);
      font-size: 0.94rem;
    }

    .field {
      display: grid;
      gap: 8px;
      margin-bottom: 16px;
    }

    .field label {
      font-size: 0.92rem;
      font-weight: 700;
      color: var(--ink);
    }

    .field input,
    .field select,
    .field button {
      font: inherit;
    }

    .field input[type="password"],
    .field input[type="text"],
    .field select {
      width: 100%;
      border: 1px solid rgba(31, 45, 47, 0.18);
      background: var(--panel-strong);
      color: var(--ink);
      border-radius: 14px;
      padding: 12px 14px;
      outline: none;
      transition: border-color 140ms ease, box-shadow 140ms ease;
    }

    .field input:focus,
    .field select:focus {
      border-color: rgba(213, 93, 63, 0.7);
      box-shadow: 0 0 0 4px rgba(213, 93, 63, 0.12);
    }

    .dropzone {
      display: grid;
      gap: 12px;
      justify-items: center;
      text-align: center;
      padding: 22px 16px;
      border: 1.5px dashed rgba(31, 45, 47, 0.2);
      border-radius: 18px;
      background:
        linear-gradient(145deg, rgba(255,255,255,0.7), rgba(255, 247, 238, 0.94));
      transition: border-color 140ms ease, transform 140ms ease, background 140ms ease;
      cursor: pointer;
    }

    .dropzone:hover,
    .dropzone.dragging {
      border-color: rgba(213, 93, 63, 0.7);
      background: linear-gradient(145deg, rgba(255,255,255,0.92), rgba(255, 241, 231, 1));
      transform: translateY(-1px);
    }

    .dropzone strong {
      font-size: 1rem;
    }

    .dropzone span {
      color: var(--muted);
      font-size: 0.93rem;
    }

    .dropzone input[type="file"] {
      display: none;
    }

    .toggles {
      display: grid;
      gap: 10px;
      margin-bottom: 18px;
    }

    .toggle {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 0.92rem;
      color: var(--muted);
    }

    .toggle input {
      width: 16px;
      height: 16px;
      accent-color: var(--accent);
    }

    .submit {
      width: 100%;
      border: 0;
      padding: 14px 16px;
      border-radius: 16px;
      background: linear-gradient(135deg, #c45137, #de7a4a);
      color: white;
      font-weight: 800;
      letter-spacing: 0.02em;
      cursor: pointer;
      transition: transform 140ms ease, filter 140ms ease;
    }

    .submit:hover { transform: translateY(-1px); filter: brightness(1.03); }
    .submit:disabled { opacity: 0.6; cursor: wait; transform: none; }

    .helper {
      margin-top: 12px;
      color: var(--muted);
      font-size: 0.86rem;
      line-height: 1.45;
    }

    .results {
      padding: 22px;
      min-height: 560px;
    }

    .status-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-bottom: 18px;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(31, 45, 47, 0.06);
      color: var(--ink);
      font-size: 0.9rem;
      font-weight: 700;
    }

    .pill.ok { background: rgba(45, 106, 79, 0.12); color: var(--success); }
    .pill.warning { background: rgba(183, 121, 31, 0.14); color: var(--warn); }
    .pill.fail,
    .pill.high,
    .pill.critical { background: rgba(166, 61, 64, 0.12); color: var(--danger); }
    .pill.medium { background: rgba(183, 121, 31, 0.14); color: var(--warn); }
    .pill.low,
    .pill.info { background: rgba(31, 45, 47, 0.06); color: var(--ink); }

    .empty-state {
      display: grid;
      place-items: center;
      min-height: 460px;
      text-align: center;
      color: var(--muted);
      border: 1px dashed rgba(31, 45, 47, 0.14);
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(255,255,255,0.55), rgba(255,255,255,0.28));
    }

    .result-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }

    .metric-box {
      padding: 16px;
      border-radius: 18px;
      background: var(--panel-strong);
      border: 1px solid var(--line);
    }

    .metric-box .label {
      display: block;
      color: var(--muted);
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
      font-weight: 700;
    }

    .metric-box .value {
      font-size: 1.6rem;
      font-weight: 800;
      line-height: 1;
    }

    .stack {
      display: grid;
      gap: 14px;
      margin-top: 14px;
    }

    .section {
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel-strong);
    }

    .section h2 {
      margin: 0 0 10px;
      font-size: 1rem;
      letter-spacing: 0.01em;
    }

    .section p {
      margin: 0;
      color: var(--muted);
      line-height: 1.55;
    }

    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    ul.clean {
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 10px;
    }

    ul.clean li {
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(31, 45, 47, 0.04);
      border: 1px solid rgba(31, 45, 47, 0.08);
      line-height: 1.45;
    }

    .finding-title {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-bottom: 8px;
      font-weight: 700;
      color: var(--ink);
    }

    .finding-meta {
      color: var(--muted);
      font-size: 0.88rem;
    }

    details {
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel-strong);
      overflow: hidden;
    }

    details summary {
      list-style: none;
      cursor: pointer;
      padding: 15px 18px;
      font-weight: 700;
    }

    details summary::-webkit-details-marker {
      display: none;
    }

    pre {
      margin: 0;
      padding: 0 18px 18px;
      overflow: auto;
      color: #253638;
      font-size: 0.88rem;
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .message {
      margin-bottom: 14px;
      padding: 14px 16px;
      border-radius: 16px;
      font-size: 0.92rem;
      line-height: 1.45;
      display: none;
    }

    .message.show { display: block; }
    .message.error { background: rgba(166, 61, 64, 0.1); color: var(--danger); border: 1px solid rgba(166, 61, 64, 0.16); }
    .message.info { background: rgba(31, 45, 47, 0.06); color: var(--ink); border: 1px solid rgba(31, 45, 47, 0.12); }

    @media (max-width: 980px) {
      .layout { grid-template-columns: 1fr; }
      .form-card { position: static; }
      .result-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }

    @media (max-width: 640px) {
      .shell { width: min(100% - 18px, 100%); margin-top: 12px; }
      .hero { padding: 22px 18px; }
      .results, .form-card { padding: 18px; }
      .result-grid { grid-template-columns: 1fr; }
      h1 { max-width: none; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="eyebrow">Juez · n8n reviewer</div>
      <h1>Sube un workflow y deja que el Juez lo diagnostique.</h1>
      <p>
        Esta pantalla usa las tools internas del proyecto para revisar la estructura del workflow, generar un veredicto
        autónomo y devolverte riesgos, redundancias y acciones sugeridas sin salir de la API.
      </p>
    </section>

    <section class="layout">
      <form class="card form-card" id="review-form">
        <h2 class="card-title">Evaluar workflow</h2>
        <p class="card-subtitle">Selecciona un export JSON de n8n y el modo de diagnóstico que quieres usar.</p>

        <div class="field">
          <label for="api-key">API key del Juez</label>
          <input id="api-key" name="api-key" type="password" placeholder="Ingresa la X-API-KEY del servidor">
        </div>

        <div class="field">
          <label for="diagnosis-mode">Modo de diagnóstico</label>
          <select id="diagnosis-mode" name="diagnosis-mode">
            <option value="auto">Auto: LLM si existe key, fallback si no</option>
            <option value="fallback">Fallback: totalmente interno/determinístico</option>
            <option value="llm">LLM: obliga diagnóstico con modelo</option>
          </select>
        </div>

        <div class="field">
          <label for="workflow-file">Archivo del workflow</label>
          <label class="dropzone" id="dropzone" for="workflow-file">
            <strong id="file-name">Selecciona o arrastra un JSON</strong>
            <span>n8n export completo con nodes y connections</span>
            <input id="workflow-file" name="workflow-file" type="file" accept=".json,application/json">
          </label>
        </div>

        <div class="toggles">
          <label class="toggle"><input id="include-diagnosis" type="checkbox" checked> Incluir diagnóstico narrativo del propio Juez</label>
          <label class="toggle"><input id="include-graph" type="checkbox"> Incluir grafo completo en la respuesta</label>
        </div>

        <button class="submit" type="submit" id="submit-btn">Evaluar workflow</button>
        <p class="helper">
          La pantalla sirve el UI; la evaluación real sigue pasando por <code>/v1/n8n/analyze</code>.
        </p>
      </form>

      <section class="card results">
        <div id="message" class="message info"></div>
        <div id="results-root" class="empty-state">
          <div>
            <strong>Listo para revisar.</strong>
            <p>Sube un export JSON de n8n y aquí aparecerán scorecard, diagnóstico, hallazgos y respuesta cruda.</p>
          </div>
        </div>
      </section>
    </section>
  </main>

  <script>
    const form = document.getElementById("review-form");
    const fileInput = document.getElementById("workflow-file");
    const fileName = document.getElementById("file-name");
    const apiKeyInput = document.getElementById("api-key");
    const diagnosisModeInput = document.getElementById("diagnosis-mode");
    const includeDiagnosisInput = document.getElementById("include-diagnosis");
    const includeGraphInput = document.getElementById("include-graph");
    const submitBtn = document.getElementById("submit-btn");
    const messageBox = document.getElementById("message");
    const resultsRoot = document.getElementById("results-root");
    const dropzone = document.getElementById("dropzone");

    function escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function setMessage(text, tone = "info") {
      if (!text) {
        messageBox.className = "message info";
        messageBox.textContent = "";
        return;
      }
      messageBox.className = `message ${tone} show`;
      messageBox.textContent = text;
    }

    function statusClass(value) {
      const normalized = String(value || "").toLowerCase();
      if (["ok", "warning", "fail", "critical", "high", "medium", "low", "info"].includes(normalized)) {
        return normalized;
      }
      return "";
    }

    function scorePercent(value) {
      const num = Number(value);
      if (Number.isNaN(num)) return "N/A";
      return `${Math.round(num * 100)}%`;
    }

    function renderList(items, emptyText) {
      if (!items || !items.length) {
        return `<ul class="clean"><li>${escapeHtml(emptyText)}</li></ul>`;
      }
      return `<ul class="clean">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
    }

    function renderSeverityChips(counts) {
      const entries = Object.entries(counts || {});
      if (!entries.length) {
        return '<div class="chips"><span class="pill">Sin hallazgos</span></div>';
      }
      return `<div class="chips">${entries
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([label, value]) => `<span class="pill ${statusClass(label)}">${escapeHtml(label)}: ${escapeHtml(value)}</span>`)
        .join("")}</div>`;
    }

    function renderPriorityFindings(items) {
      if (!items || !items.length) {
        return renderList([], "No hay hallazgos prioritarios.");
      }
      return `<ul class="clean">${items.map((item) => `
        <li>
          <div class="finding-title">
            <span>${escapeHtml(item.title)}</span>
            <span class="pill ${statusClass(item.severity)}">${escapeHtml(item.severity)}</span>
          </div>
          <div class="finding-meta">${escapeHtml(item.why_it_matters)}</div>
          ${item.node_names && item.node_names.length ? `<div class="finding-meta">Nodos: ${escapeHtml(item.node_names.join(", "))}</div>` : ""}
        </li>
      `).join("")}</ul>`;
    }

    function renderFindings(items) {
      if (!items || !items.length) {
        return renderList([], "No se detectaron hallazgos.");
      }
      return `<ul class="clean">${items.map((item) => `
        <li>
          <div class="finding-title">
            <span>${escapeHtml(item.title)}</span>
            <span class="pill ${statusClass(item.severity)}">${escapeHtml(item.severity)}</span>
          </div>
          <div class="finding-meta">${escapeHtml(item.message)}</div>
          ${item.node_names && item.node_names.length ? `<div class="finding-meta">Nodos: ${escapeHtml(item.node_names.join(", "))}</div>` : ""}
        </li>
      `).join("")}</ul>`;
    }

    function renderResults(payload) {
      const analysis = payload.analysis || {};
      const inventory = analysis.inventory || {};
      const scorecard = analysis.scorecard || {};
      const diagnosis = analysis.diagnosis || null;
      const apiMeta = payload.api_meta || {};

      const summaryHtml = diagnosis
        ? `
          <div class="section">
            <h2>Diagnóstico autónomo</h2>
            <div class="status-row">
              <span class="pill ${statusClass(scorecard.status)}">${escapeHtml(scorecard.status || "sin estado")}</span>
              <span class="pill ${statusClass(diagnosis.risk_level)}">Riesgo ${escapeHtml(diagnosis.risk_level)}</span>
              <span class="pill">${escapeHtml(diagnosis.verdict || "Sin veredicto")}</span>
              <span class="pill">Fuente: ${escapeHtml(diagnosis.source || "n/a")}</span>
            </div>
            <p>${escapeHtml(diagnosis.executive_summary || "Sin resumen ejecutivo.")}</p>
          </div>
        `
        : `
          <div class="section">
            <h2>Diagnóstico autónomo</h2>
            <p>El diagnóstico narrativo fue omitido en esta ejecución.</p>
          </div>
        `;

      resultsRoot.className = "";
      resultsRoot.innerHTML = `
        <div class="status-row">
          <span class="pill ${statusClass(scorecard.status)}">Estado ${escapeHtml(scorecard.status || "n/a")}</span>
          <span class="pill">Workflow: ${escapeHtml(inventory.workflow_name || "sin nombre")}</span>
          <span class="pill">Nodos: ${escapeHtml(inventory.total_nodes || 0)}</span>
          <span class="pill">Edges: ${escapeHtml(inventory.total_edges || 0)}</span>
        </div>

        <div class="result-grid">
          <div class="metric-box">
            <span class="label">Overall</span>
            <span class="value">${scorePercent(scorecard.overall)}</span>
          </div>
          <div class="metric-box">
            <span class="label">Seguridad</span>
            <span class="value">${scorePercent(scorecard.security_posture)}</span>
          </div>
          <div class="metric-box">
            <span class="label">Resiliencia</span>
            <span class="value">${scorePercent(scorecard.operational_resilience)}</span>
          </div>
          <div class="metric-box">
            <span class="label">Mantenibilidad</span>
            <span class="value">${scorePercent(scorecard.maintainability)}</span>
          </div>
        </div>

        <div class="stack">
          ${summaryHtml}

          <div class="section">
            <h2>Conteo por severidad</h2>
            ${renderSeverityChips(analysis.counts_by_severity)}
          </div>

          <div class="section">
            <h2>Acciones recomendadas</h2>
            ${renderList(diagnosis ? diagnosis.recommended_actions : [], "No hay acciones sugeridas.")}
          </div>

          <div class="section">
            <h2>Hallazgos prioritarios</h2>
            ${renderPriorityFindings(diagnosis ? diagnosis.priority_findings : [])}
          </div>

          <div class="section">
            <h2>Redundancias detectadas</h2>
            ${renderList(diagnosis ? diagnosis.redundancies : [], "No se detectaron redundancias relevantes.")}
          </div>

          <div class="section">
            <h2>Modos probables de fallo</h2>
            ${renderList(diagnosis ? diagnosis.failure_modes : [], "No hay modos de fallo resumidos.")}
          </div>

          <div class="section">
            <h2>Fortalezas visibles</h2>
            ${renderList(diagnosis ? diagnosis.strengths : [], "Sin fortalezas destacadas en esta corrida.")}
          </div>

          <div class="section">
            <h2>Qué todavía no sabemos</h2>
            ${renderList(diagnosis ? diagnosis.unknowns : [], "No hay observaciones adicionales.")}
          </div>

          <div class="section">
            <h2>Hallazgos completos</h2>
            ${renderFindings(analysis.findings)}
          </div>

          <details>
            <summary>Ver respuesta JSON cruda</summary>
            <pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>
          </details>

          <div class="section">
            <h2>Meta</h2>
            <p>${escapeHtml(`Timestamp: ${apiMeta.timestamp_utc || "n/a"} · Warnings: ${(apiMeta.warnings || []).length}`)}</p>
          </div>
        </div>
      `;
    }

    async function readWorkflowFile(file) {
      const text = await file.text();
      try {
        return JSON.parse(text);
      } catch (error) {
        throw new Error("El archivo no contiene un JSON válido.");
      }
    }

    function setLoading(isLoading) {
      submitBtn.disabled = isLoading;
      submitBtn.textContent = isLoading ? "Evaluando..." : "Evaluar workflow";
    }

    function updateFileName() {
      const file = fileInput.files && fileInput.files[0];
      fileName.textContent = file ? file.name : "Selecciona o arrastra un JSON";
    }

    ["dragenter", "dragover"].forEach((eventName) => {
      dropzone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropzone.classList.add("dragging");
      });
    });

    ["dragleave", "drop"].forEach((eventName) => {
      dropzone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropzone.classList.remove("dragging");
      });
    });

    dropzone.addEventListener("drop", (event) => {
      const file = event.dataTransfer?.files?.[0];
      if (!file) return;
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(file);
      fileInput.files = dataTransfer.files;
      updateFileName();
    });

    fileInput.addEventListener("change", updateFileName);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      setMessage("");

      const file = fileInput.files && fileInput.files[0];
      if (!file) {
        setMessage("Selecciona un archivo JSON antes de evaluar.", "error");
        return;
      }

      const apiKey = apiKeyInput.value.trim();
      if (!apiKey) {
        setMessage("Ingresa la API key del Juez para poder llamar la evaluación.", "error");
        return;
      }

      setLoading(true);
      setMessage("Leyendo workflow y enviándolo al motor de diagnóstico...", "info");

      try {
        const workflow = await readWorkflowFile(file);
        const response = await fetch("/v1/n8n/analyze", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-API-KEY": apiKey
          },
          body: JSON.stringify({
            workflow,
            include_graph: includeGraphInput.checked,
            include_diagnosis: includeDiagnosisInput.checked,
            diagnosis_mode: diagnosisModeInput.value
          })
        });

        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          const detail = typeof data.detail === "string"
            ? data.detail
            : Array.isArray(data.detail)
              ? data.detail.map((item) => item.msg || JSON.stringify(item)).join(" | ")
              : "No se pudo completar la evaluación.";
          throw new Error(detail);
        }

        renderResults(data);
        setMessage("Evaluación completada.", "info");
      } catch (error) {
        const message = error instanceof Error ? error.message : "Ocurrió un error inesperado.";
        setMessage(message, "error");
      } finally {
        setLoading(false);
      }
    });
  </script>
</body>
</html>
""".strip()
