(function () {
  "use strict";

  const API_BASE = "/api/v1/admin";
  const DEMO_ADMIN_TOKEN = "atlas-demo-admin-token-v1";
  const TOKEN_STORAGE_KEY = "altas.control-center.admin-token";
  const REFRESH_INTERVAL_MS = 30_000;

  const ENDPOINTS = {
    overview: [`${API_BASE}/overview`],
    tenants: [`${API_BASE}/tenants?limit=200`],
    stores: [`${API_BASE}/stores?limit=300`],
    devices: [`${API_BASE}/devices?limit=300`],
    agents: [`${API_BASE}/agents?limit=300`],
    jobs: [`${API_BASE}/jobs?limit=300`],
    // Generic repository routes are the current control-plane contract. Short
    // aliases remain as a compatibility fallback for later API revisions.
    usage: [
      `${API_BASE}/usage_events?limit=300`,
      `${API_BASE}/usage?limit=300`,
    ],
    audit: [
      `${API_BASE}/audit_logs?limit=300`,
      `${API_BASE}/audit?limit=300`,
    ],
  };

  const VIEW_META = {
    overview: {
      title: "Fleet overview",
      kicker: "Operations / network",
    },
    fleet: {
      title: "Managed fleet",
      kicker: "Devices / agents",
    },
    stores: {
      title: "Store directory",
      kicker: "Rooftops / scope",
    },
    jobs: {
      title: "Dispatch ledger",
      kicker: "Jobs / execution",
    },
    audit: {
      title: "Audit record",
      kicker: "Events / accountability",
    },
  };

  const state = {
    token: readSessionToken(),
    currentView: "overview",
    loading: false,
    lastSyncAt: null,
    nextRefreshAt: 0,
    jobFilter: "all",
    auditSearch: "",
    data: {
      overview: null,
      tenants: [],
      stores: [],
      devices: [],
      agents: [],
      jobs: [],
      usage: [],
      audit: [],
    },
    errors: {},
  };

  const dom = {};

  class ApiError extends Error {
    constructor(message, status, code) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.code = code;
    }
  }

  function readSessionToken() {
    try {
      const existing = window.sessionStorage.getItem(TOKEN_STORAGE_KEY);
      if (existing) {
        return existing;
      }
      window.sessionStorage.setItem(TOKEN_STORAGE_KEY, DEMO_ADMIN_TOKEN);
    } catch (_error) {
      // Some privacy modes reject session storage. The token remains in memory.
    }
    return DEMO_ADMIN_TOKEN;
  }

  function saveSessionToken(token) {
    state.token = token || DEMO_ADMIN_TOKEN;
    try {
      window.sessionStorage.setItem(TOKEN_STORAGE_KEY, state.token);
    } catch (_error) {
      // In-memory auth remains usable for the life of this page.
    }
  }

  function cacheDom() {
    dom.navItems = Array.from(document.querySelectorAll("[data-view]"));
    dom.viewPanels = Array.from(document.querySelectorAll("[data-view-panel]"));
    dom.viewTitle = document.getElementById("view-title");
    dom.viewKicker = document.getElementById("view-kicker");
    dom.routeAnnouncer = document.getElementById("route-announcer");
    dom.railToggle = document.getElementById("rail-toggle");
    dom.railClose = document.getElementById("rail-close");
    dom.railScrim = document.getElementById("rail-scrim");
    dom.refreshButton = document.getElementById("refresh-button");
    dom.errorRetry = document.getElementById("error-retry");
    dom.globalError = document.getElementById("global-error");
    dom.globalErrorMessage = document.getElementById("global-error-message");
    dom.connectionDot = document.getElementById("connection-dot");
    dom.connectionLabel = document.getElementById("connection-label");
    dom.lastSync = document.getElementById("last-sync");
    dom.authButton = document.getElementById("auth-button");
    dom.authDialog = document.getElementById("auth-dialog");
    dom.authForm = document.getElementById("auth-form");
    dom.authClose = document.getElementById("auth-close");
    dom.adminToken = document.getElementById("admin-token");
    dom.resetDemoToken = document.getElementById("reset-demo-token");
    dom.toastRegion = document.getElementById("toast-region");

    dom.overviewMetrics = document.getElementById("overview-metrics");
    dom.networkReadiness = document.getElementById("network-readiness");
    dom.usageSignal = document.getElementById("usage-signal");
    dom.overviewJobs = document.getElementById("overview-jobs");

    dom.fleetStatline = document.getElementById("fleet-statline");
    dom.fleetDevices = document.getElementById("fleet-devices");
    dom.fleetAgents = document.getElementById("fleet-agents");
    dom.deviceCount = document.getElementById("device-count");
    dom.agentCount = document.getElementById("agent-count");

    dom.storeDirectory = document.getElementById("store-directory");

    dom.jobForm = document.getElementById("job-form");
    dom.jobTenant = document.getElementById("job-tenant");
    dom.jobStore = document.getElementById("job-store");
    dom.jobAgent = document.getElementById("job-agent");
    dom.jobDevice = document.getElementById("job-device");
    dom.jobCapability = document.getElementById("job-capability");
    dom.jobPayload = document.getElementById("job-payload");
    dom.jobIdempotency = document.getElementById("job-idempotency");
    dom.jobFormError = document.getElementById("job-form-error");
    dom.queueJobButton = document.getElementById("queue-job-button");
    dom.jobFilter = document.getElementById("job-filter");
    dom.jobsLedger = document.getElementById("jobs-ledger");

    dom.auditSearch = document.getElementById("audit-search");
    dom.auditResultCount = document.getElementById("audit-result-count");
    dom.auditLedger = document.getElementById("audit-ledger");
  }

  function bindEvents() {
    dom.navItems.forEach((button) => {
      button.addEventListener("click", () => showView(button.dataset.view));
    });

    document.querySelectorAll("[data-go-view]").forEach((button) => {
      button.addEventListener("click", () => showView(button.dataset.goView));
    });

    dom.railToggle.addEventListener("click", toggleRail);
    dom.railClose.addEventListener("click", () =>
      closeRail({ restoreFocus: true }),
    );
    dom.railScrim.addEventListener("click", () =>
      closeRail({ restoreFocus: true }),
    );
    dom.refreshButton.addEventListener("click", () => refreshAll());
    dom.errorRetry.addEventListener("click", () => refreshAll());
    dom.authButton.addEventListener("click", openAuthDialog);
    dom.authClose.addEventListener("click", closeAuthDialog);
    dom.authForm.addEventListener("submit", handleAuthSubmit);
    dom.resetDemoToken.addEventListener("click", useDemoToken);

    dom.jobTenant.addEventListener("change", () => updateJobStoreOptions(false));
    dom.jobStore.addEventListener("change", () => updateJobAssignmentOptions(false));
    dom.jobForm.addEventListener("submit", handleQueueJob);

    dom.jobFilter.addEventListener("click", (event) => {
      const button = event.target.closest("[data-job-filter]");
      if (!button) return;
      state.jobFilter = button.dataset.jobFilter || "all";
      Array.from(dom.jobFilter.querySelectorAll("button")).forEach((item) => {
        const selected = item === button;
        item.classList.toggle("is-active", selected);
        item.setAttribute("aria-pressed", String(selected));
      });
      renderJobs();
    });

    dom.auditSearch.addEventListener("input", () => {
      state.auditSearch = dom.auditSearch.value.trim().toLowerCase();
      renderAudit();
    });

    window.addEventListener("hashchange", routeFromHash);
    document.addEventListener("visibilitychange", () => {
      if (
        document.visibilityState === "visible" &&
        Date.now() >= state.nextRefreshAt &&
        !state.loading
      ) {
        refreshAll({ silent: true });
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeRail({ restoreFocus: true });
      }
    });
  }

  function routeFromHash() {
    const hashView = window.location.hash.replace(/^#/, "");
    showView(VIEW_META[hashView] ? hashView : "overview", { updateHash: false });
  }

  function showView(viewName, options = {}) {
    if (!VIEW_META[viewName]) return;
    state.currentView = viewName;
    dom.navItems.forEach((button) => {
      const selected = button.dataset.view === viewName;
      button.classList.toggle("is-active", selected);
      if (selected) {
        button.setAttribute("aria-current", "page");
      } else {
        button.removeAttribute("aria-current");
      }
    });
    dom.viewPanels.forEach((panel) => {
      const selected = panel.dataset.viewPanel === viewName;
      panel.hidden = !selected;
      panel.classList.toggle("is-visible", selected);
    });
    dom.viewTitle.textContent = VIEW_META[viewName].title;
    dom.viewKicker.textContent = VIEW_META[viewName].kicker;
    dom.routeAnnouncer.textContent = `${VIEW_META[viewName].title} section selected`;
    document.title = `${VIEW_META[viewName].title} — Atlas Control Center`;
    if (options.updateHash !== false) {
      window.history.replaceState(null, "", `#${viewName}`);
    }
    closeRail();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function toggleRail() {
    const shouldOpen = !document.body.classList.contains("is-rail-open");
    document.body.classList.toggle("is-rail-open", shouldOpen);
    dom.railToggle.setAttribute("aria-expanded", String(shouldOpen));
    dom.railToggle.setAttribute(
      "aria-label",
      shouldOpen ? "Navigation open" : "Open navigation",
    );
  }

  function closeRail(options = {}) {
    const wasOpen = document.body.classList.contains("is-rail-open");
    document.body.classList.remove("is-rail-open");
    dom.railToggle.setAttribute("aria-expanded", "false");
    dom.railToggle.setAttribute("aria-label", "Open navigation");
    if (options.restoreFocus && wasOpen) {
      dom.railToggle.focus();
    }
  }

  async function apiRequest(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    headers.set("Authorization", `Bearer ${state.token}`);
    if (options.body) {
      headers.set("Content-Type", "application/json");
    }

    let response;
    try {
      response = await window.fetch(path, {
        ...options,
        headers,
        cache: "no-store",
      });
    } catch (_error) {
      throw new ApiError("Unable to reach the local control plane.", 0, "network_error");
    }

    const bodyText = await response.text();
    let body = null;
    if (bodyText) {
      try {
        body = JSON.parse(bodyText);
      } catch (_error) {
        body = null;
      }
    }

    if (!response.ok) {
      const detail = body && body.detail;
      const code =
        (detail && typeof detail === "object" && detail.code) ||
        (typeof detail === "string" && detail) ||
        `http_${response.status}`;
      throw new ApiError(friendlyApiMessage(code, response.status), response.status, code);
    }

    return body || {};
  }

  async function loadEndpoint(name, paths) {
    let lastError = null;
    for (const path of paths) {
      try {
        return await apiRequest(path);
      } catch (error) {
        lastError = error;
        if (!(error instanceof ApiError) || error.status !== 404) {
          break;
        }
      }
    }
    throw lastError || new ApiError(`Unable to load ${name}.`, 0, "unknown_error");
  }

  async function refreshAll(options = {}) {
    if (state.loading) return;
    state.loading = true;
    setConnectionState("pending");
    dom.refreshButton.classList.add("is-spinning");
    dom.refreshButton.disabled = true;

    const entries = Object.entries(ENDPOINTS);
    const results = await Promise.all(
      entries.map(async ([name, paths]) => {
        try {
          const payload = await loadEndpoint(name, paths);
          return { name, payload, error: null };
        } catch (error) {
          return { name, payload: null, error };
        }
      }),
    );

    const errors = {};
    let successCount = 0;
    let unauthorized = false;
    results.forEach((result) => {
      if (result.error) {
        errors[result.name] = result.error;
        unauthorized = unauthorized || result.error.status === 401;
        return;
      }
      successCount += 1;
      if (result.name === "overview") {
        state.data.overview = result.payload;
      } else {
        state.data[result.name] = unwrapItems(result.payload);
      }
    });
    state.errors = errors;
    state.loading = false;
    dom.refreshButton.classList.remove("is-spinning");
    dom.refreshButton.disabled = false;

    if (successCount > 0) {
      state.lastSyncAt = new Date();
      state.nextRefreshAt = Date.now() + REFRESH_INTERVAL_MS;
      setConnectionState(successCount === entries.length ? "online" : "partial");
    } else {
      state.nextRefreshAt = Date.now() + REFRESH_INTERVAL_MS;
      setConnectionState("error");
    }

    renderAll();
    updateGlobalError(successCount, entries.length, unauthorized);
    updateSyncReadout();

    if (unauthorized) {
      openAuthDialog();
    } else if (!options.silent && successCount > 0 && successCount < entries.length) {
      showToast("Partial refresh", "Some control-plane records could not be loaded.", "error");
    }
  }

  function unwrapItems(payload) {
    return payload && Array.isArray(payload.items) ? payload.items : [];
  }

  function setConnectionState(mode) {
    dom.connectionDot.className = "connection-dot";
    if (mode === "online") {
      dom.connectionDot.classList.add("is-online");
      dom.connectionLabel.textContent = "Control plane online";
    } else if (mode === "partial") {
      dom.connectionDot.classList.add("is-pending");
      dom.connectionLabel.textContent = "Partial signal";
    } else if (mode === "error") {
      dom.connectionDot.classList.add("is-error");
      dom.connectionLabel.textContent = "Connection failed";
    } else {
      dom.connectionDot.classList.add("is-pending");
      dom.connectionLabel.textContent = "Synchronizing";
    }
  }

  function updateGlobalError(successCount, totalCount, unauthorized) {
    if (successCount === totalCount) {
      dom.globalError.hidden = true;
      return;
    }
    dom.globalError.hidden = false;
    if (unauthorized) {
      dom.globalErrorMessage.textContent =
        "The current tab token was rejected. Enter the configured local admin token to reconnect.";
    } else if (successCount > 0) {
      dom.globalErrorMessage.textContent =
        "Some ledgers did not answer the latest refresh. Available records remain visible below.";
    } else {
      dom.globalErrorMessage.textContent =
        "No admin endpoint answered the latest refresh. Check that the local service is running.";
    }
  }

  function updateSyncReadout() {
    if (!state.lastSyncAt) {
      dom.lastSync.textContent = "No successful sync";
      return;
    }
    dom.lastSync.textContent = `Synced ${formatRelative(state.lastSyncAt)}`;
  }

  function renderAll() {
    renderOverview();
    renderFleet();
    renderStores();
    populateJobForm();
    renderJobs();
    renderAudit();
  }

  function renderOverview() {
    renderOverviewMetrics();
    renderNetworkReadiness();
    renderUsageSignal();
    renderOverviewJobs();
  }

  function renderOverviewMetrics() {
    if (state.errors.overview && !state.data.overview) {
      renderErrorState(dom.overviewMetrics, "Overview unavailable", state.errors.overview);
      dom.overviewMetrics.setAttribute("aria-busy", "false");
      return;
    }

    const overview = state.data.overview || {};
    const metrics = [
      {
        label: "Dealer groups",
        value: formatInteger(overview.tenants),
        note: "Active tenant records",
      },
      {
        label: "Active rooftops",
        value: formatInteger(overview.stores),
        note: "Store scope online",
      },
      {
        label: "Worker devices",
        value: formatInteger(overview.devices),
        note: "Authorized runtimes",
      },
      {
        label: "Queued jobs",
        value: formatInteger(overview.queued_jobs),
        note: `${formatInteger(overview.running_jobs)} currently running`,
      },
      {
        label: "Failed jobs",
        value: formatInteger(overview.failed_jobs),
        note: "Requires operator review",
        alert: Number(overview.failed_jobs || 0) > 0,
      },
      {
        label: "Gateway tokens",
        value: formatCompactNumber(overview.usage_tokens),
        note: `${formatCurrencyMicros(overview.usage_cost_micros)} recorded cost`,
      },
    ];

    const fragment = document.createDocumentFragment();
    metrics.forEach((metric) => {
      const list = createElement("dl", `metric-cell${metric.alert ? " is-alert" : ""}`);
      const term = createElement("dt", "", metric.label);
      const value = createElement("dd", "", metric.value);
      const note = createElement("small", "", metric.note);
      list.append(term, value, note);
      fragment.append(list);
    });
    dom.overviewMetrics.replaceChildren(fragment);
    dom.overviewMetrics.setAttribute("aria-busy", "false");
  }

  function renderNetworkReadiness() {
    if (state.errors.devices && state.data.devices.length === 0) {
      renderErrorState(dom.networkReadiness, "Fleet signal unavailable", state.errors.devices);
      dom.networkReadiness.setAttribute("aria-busy", "false");
      return;
    }
    const devices = state.data.devices;
    if (devices.length === 0) {
      renderEmptyState(
        dom.networkReadiness,
        "No registered workers",
        "Register a managed device to begin receiving heartbeats.",
        "00",
      );
      dom.networkReadiness.setAttribute("aria-busy", "false");
      return;
    }

    const deviceStates = devices.map(getDeviceConnection);
    const active = devices.filter((device) => device.status === "active").length;
    const online = deviceStates.filter((item) => item.key === "online").length;
    const attention = deviceStates.filter((item) => ["stale", "awaiting"].includes(item.key)).length;
    const disabled = devices.filter((device) => device.status !== "active").length;
    const readiness = active > 0 ? Math.round((online / active) * 100) : 0;

    const wrapper = createElement("div", "readiness-display");
    const dial = createElement("div", "readiness-dial");
    dial.style.setProperty("--readiness", `${Math.max(0, Math.min(360, readiness * 3.6))}deg`);
    const dialText = createElement("div");
    dialText.append(
      createElement("strong", "", `${readiness}%`),
      createElement("small", "", "Ready now"),
    );
    dial.append(dialText);

    const rows = createElement("div", "readiness-rows");
    [
      ["Online heartbeat", online],
      ["Awaiting / stale", attention],
      ["Administratively disabled", disabled],
      ["Registered total", devices.length],
    ].forEach(([label, value]) => {
      const row = createElement("div", "readiness-row");
      row.append(createElement("span", "", label), createElement("strong", "", String(value)));
      rows.append(row);
    });

    wrapper.append(dial, rows);
    dom.networkReadiness.replaceChildren(wrapper);
    dom.networkReadiness.setAttribute("aria-busy", "false");
  }

  function renderUsageSignal() {
    if (state.errors.usage && state.data.usage.length === 0) {
      renderErrorState(dom.usageSignal, "Usage ledger unavailable", state.errors.usage);
      dom.usageSignal.setAttribute("aria-busy", "false");
      return;
    }
    const events = sortByDate(state.data.usage, "created_at", true);
    if (events.length === 0) {
      renderEmptyState(
        dom.usageSignal,
        "Awaiting first model call",
        "Gateway token and cost activity will appear after an entitled worker runs.",
        "AI",
      );
      dom.usageSignal.setAttribute("aria-busy", "false");
      return;
    }

    const overview = state.data.overview || {};
    const totalTokens = Number(overview.usage_tokens) || events.reduce(
      (sum, event) => sum + Number(event.total_tokens || 0),
      0,
    );
    const recent = events.slice(-16);
    const maxTokens = Math.max(1, ...recent.map((event) => Number(event.total_tokens || 0)));
    const providers = new Set(events.map((event) => textValue(event.provider)).filter(Boolean));

    const wrapper = createElement("div", "usage-readout");
    const total = createElement("div", "usage-total");
    const totalText = createElement("div");
    totalText.append(
      createElement("strong", "", formatCompactNumber(totalTokens)),
      createElement("small", "", "Cumulative tokens"),
    );
    total.append(
      totalText,
      createElement("small", "", formatCurrencyMicros(overview.usage_cost_micros)),
    );

    const bars = createElement("div", "usage-bars");
    bars.setAttribute("aria-label", "Recent model request token volume");
    recent.forEach((event) => {
      const count = Number(event.total_tokens || 0);
      const bar = createElement("span", "usage-bar");
      bar.style.setProperty("--bar-height", `${Math.max(4, Math.round((count / maxTokens) * 92))}px`);
      bar.title = `${formatInteger(count)} tokens — ${formatTimestamp(event.created_at)}`;
      bars.append(bar);
    });

    const footnote = createElement("p", "usage-footnote");
    footnote.append(
      createElement("span", "", `${events.length} request${events.length === 1 ? "" : "s"}`),
      createElement("span", "", providers.size ? `${providers.size} route${providers.size === 1 ? "" : "s"}` : "Mock route"),
    );

    wrapper.append(total, bars, footnote);
    dom.usageSignal.replaceChildren(wrapper);
    dom.usageSignal.setAttribute("aria-busy", "false");
  }

  function renderOverviewJobs() {
    if (state.errors.jobs && state.data.jobs.length === 0) {
      renderErrorState(dom.overviewJobs, "Jobs unavailable", state.errors.jobs);
      dom.overviewJobs.setAttribute("aria-busy", "false");
      return;
    }
    const jobs = sortByDate(state.data.jobs, "created_at").slice(0, 6);
    if (jobs.length === 0) {
      renderEmptyState(
        dom.overviewJobs,
        "No work dispatched",
        "Queue a policy-bound fixed-ops job to start the ledger.",
        "J0",
      );
      dom.overviewJobs.setAttribute("aria-busy", "false");
      return;
    }

    const table = buildTable(
      "Recent Atlas jobs",
      [
        ["Job", (job) => primarySecondary(shortId(job.id), textValue(job.requested_by) || "control plane")],
        ["Capability", (job) => codeCell(job.capability)],
        ["Store", (job) => entityCell(findById(state.data.stores, job.store_id), job.store_id)],
        ["State", (job) => statusChip(job.status)],
        ["Updated", (job) => timeCell(job.updated_at || job.created_at)],
      ],
      jobs,
    );
    dom.overviewJobs.replaceChildren(table);
    dom.overviewJobs.setAttribute("aria-busy", "false");
  }

  function renderFleet() {
    renderFleetStats();
    renderDeviceTable();
    renderAgentTable();
  }

  function renderFleetStats() {
    const devices = state.data.devices;
    const agents = state.data.agents;
    const states = devices.map(getDeviceConnection);
    const rows = [
      ["Registered devices", devices.length],
      ["Online now", states.filter((item) => item.key === "online").length],
      ["Active agents", agents.filter((agent) => agent.status === "active").length],
      ["Needs attention", states.filter((item) => ["stale", "awaiting", "offline"].includes(item.key)).length],
    ];
    const fragment = document.createDocumentFragment();
    rows.forEach(([label, value]) => {
      const item = createElement("div", "statline-item");
      item.append(createElement("span", "", label), createElement("strong", "", String(value)));
      fragment.append(item);
    });
    dom.fleetStatline.replaceChildren(fragment);
  }

  function renderDeviceTable() {
    dom.deviceCount.textContent = `${state.data.devices.length} device${state.data.devices.length === 1 ? "" : "s"}`;
    if (state.errors.devices && state.data.devices.length === 0) {
      renderErrorState(dom.fleetDevices, "Devices unavailable", state.errors.devices);
      dom.fleetDevices.setAttribute("aria-busy", "false");
      return;
    }
    if (state.data.devices.length === 0) {
      renderEmptyState(
        dom.fleetDevices,
        "No device registrations",
        "Install and approve an Atlas worker to populate this inventory.",
        "D0",
      );
      dom.fleetDevices.setAttribute("aria-busy", "false");
      return;
    }

    const table = buildTable(
      "Registered Atlas worker devices",
      [
        ["Device", (device) => primarySecondary(device.name, shortId(device.id))],
        ["Rooftop", (device) => entityCell(findById(state.data.stores, device.store_id), device.store_id)],
        ["Connection", (device) => {
          const connection = getDeviceConnection(device);
          const wrapper = createElement("div");
          wrapper.append(statusChip(connection.label, connection.tone));
          if (device.health_status) {
            wrapper.append(createElement("small", "cell-secondary", humanize(device.health_status)));
          }
          return wrapper;
        }],
        ["Runtime", (device) => primarySecondary(device.worker_version || "Not reported", lastSeenLabel(device.last_heartbeat_at))],
        ["State", (device) => statusChip(device.status)],
        ["Control", (device) => createDeviceToggle(device)],
      ],
      sortByName(state.data.devices, "name"),
    );
    dom.fleetDevices.replaceChildren(table);
    dom.fleetDevices.setAttribute("aria-busy", "false");
  }

  function renderAgentTable() {
    dom.agentCount.textContent = `${state.data.agents.length} agent${state.data.agents.length === 1 ? "" : "s"}`;
    if (state.errors.agents && state.data.agents.length === 0) {
      renderErrorState(dom.fleetAgents, "Agents unavailable", state.errors.agents);
      dom.fleetAgents.setAttribute("aria-busy", "false");
      return;
    }
    if (state.data.agents.length === 0) {
      renderEmptyState(
        dom.fleetAgents,
        "No agent assignments",
        "Bind an Atlas agent to an entitled rooftop and device.",
        "A0",
      );
      dom.fleetAgents.setAttribute("aria-busy", "false");
      return;
    }

    const table = buildTable(
      "Assigned Atlas agents",
      [
        ["Agent", (agent) => primarySecondary(agent.name, shortId(agent.id))],
        ["Rooftop", (agent) => entityCell(findById(state.data.stores, agent.store_id), agent.store_id)],
        ["Assigned device", (agent) => entityCell(findById(state.data.devices, agent.device_id), agent.device_id)],
        ["State", (agent) => statusChip(agent.status)],
        ["Updated", (agent) => timeCell(agent.updated_at || agent.created_at)],
      ],
      sortByName(state.data.agents, "name"),
    );
    dom.fleetAgents.replaceChildren(table);
    dom.fleetAgents.setAttribute("aria-busy", "false");
  }

  function renderStores() {
    if (state.errors.stores && state.data.stores.length === 0) {
      renderErrorState(dom.storeDirectory, "Store directory unavailable", state.errors.stores);
      dom.storeDirectory.setAttribute("aria-busy", "false");
      return;
    }
    if (state.data.stores.length === 0) {
      renderEmptyState(
        dom.storeDirectory,
        "No rooftops configured",
        "Add a dealership store before assigning workers and Tekion context.",
        "S0",
      );
      dom.storeDirectory.setAttribute("aria-busy", "false");
      return;
    }

    const fragment = document.createDocumentFragment();
    sortByName(state.data.stores, "name").forEach((store) => {
      const tenant = findById(state.data.tenants, store.tenant_id);
      const agents = state.data.agents.filter((agent) => agent.store_id === store.id);
      const devices = state.data.devices.filter((device) => device.store_id === store.id);
      const onlineDevices = devices.filter((device) => getDeviceConnection(device).key === "online");
      const record = createElement("article", "store-record");
      record.setAttribute("aria-label", `${textValue(store.name) || "Store"} record`);

      const primary = createElement("div", "store-primary");
      primary.append(
        createElement("span", "section-code", tenant ? tenant.name : "Unassigned dealer group"),
        createElement("h3", "", textValue(store.name) || "Unnamed store"),
        createElement("p", "", `${shortId(store.id)} / ${textValue(store.external_ref) || "NO EXTERNAL REF"}`),
      );

      const stateDatum = createStoreDatum("Store state", statusChip(store.status));
      const agentDatum = createStoreDatum(
        "Assigned agents",
        createElement("strong", "", `${agents.length} worker${agents.length === 1 ? "" : "s"}`),
      );
      const deviceDatum = createStoreDatum(
        "Device posture",
        createElement("strong", "", `${onlineDevices.length}/${devices.length} online`),
      );
      const integrationDatum = createElement("div", "store-datum");
      integrationDatum.append(
        createElement("span", "", "Data integration"),
        createElement("strong", "", "Tekion service data"),
        statusChip("Fixture adapter", "is-fixture"),
      );

      record.append(primary, stateDatum, agentDatum, deviceDatum, integrationDatum);
      fragment.append(record);
    });
    dom.storeDirectory.replaceChildren(fragment);
    dom.storeDirectory.setAttribute("aria-busy", "false");
  }

  function createStoreDatum(label, valueNode) {
    const datum = createElement("div", "store-datum");
    datum.append(createElement("span", "", label), valueNode);
    return datum;
  }

  function populateJobForm() {
    const currentTenant = dom.jobTenant.value;
    const activeTenants = state.data.tenants.filter((tenant) => tenant.status === "active");
    fillSelect(
      dom.jobTenant,
      "Select tenant",
      activeTenants,
      (tenant) => tenant.id,
      (tenant) => tenant.name,
      currentTenant,
    );
    if (!dom.jobTenant.value && activeTenants.length > 0) {
      dom.jobTenant.value = activeTenants[0].id;
    }
    updateJobStoreOptions(true);
  }

  function updateJobStoreOptions(preserveSelection) {
    const tenantId = dom.jobTenant.value;
    const currentStore = preserveSelection ? dom.jobStore.value : "";
    const stores = state.data.stores.filter(
      (store) => store.tenant_id === tenantId && store.status === "active",
    );
    fillSelect(
      dom.jobStore,
      "Select store",
      stores,
      (store) => store.id,
      (store) => store.name,
      currentStore,
    );
    dom.jobStore.disabled = stores.length === 0;

    if (!dom.jobStore.value && stores.length > 0) {
      const withAgent = stores.find((store) =>
        state.data.agents.some(
          (agent) => agent.store_id === store.id && agent.status === "active",
        ),
      );
      dom.jobStore.value = (withAgent || stores[0]).id;
    }
    updateJobAssignmentOptions(preserveSelection);
  }

  function updateJobAssignmentOptions(preserveSelection) {
    const storeId = dom.jobStore.value;
    const currentAgent = preserveSelection ? dom.jobAgent.value : "";
    const currentDevice = preserveSelection ? dom.jobDevice.value : "";
    const agents = state.data.agents.filter(
      (agent) => agent.store_id === storeId && agent.status === "active",
    );
    const devices = state.data.devices.filter(
      (device) => device.store_id === storeId && device.status === "active",
    );

    fillSelect(
      dom.jobAgent,
      "Select agent",
      agents,
      (agent) => agent.id,
      (agent) => agent.name,
      currentAgent,
    );
    dom.jobAgent.disabled = agents.length === 0;
    if (!dom.jobAgent.value && agents.length > 0) {
      dom.jobAgent.value = agents[0].id;
    }

    fillSelect(
      dom.jobDevice,
      "Any assigned device",
      devices,
      (device) => device.id,
      (device) => device.name,
      currentDevice,
    );
    dom.jobDevice.disabled = devices.length === 0;
    const selectedAgent = findById(agents, dom.jobAgent.value);
    if (
      !dom.jobDevice.value &&
      selectedAgent &&
      devices.some((device) => device.id === selectedAgent.device_id)
    ) {
      dom.jobDevice.value = selectedAgent.device_id;
    }
  }

  function fillSelect(select, placeholder, items, getValue, getLabel, preferredValue) {
    const fragment = document.createDocumentFragment();
    const placeholderOption = createElement("option", "", placeholder);
    placeholderOption.value = "";
    fragment.append(placeholderOption);
    items.forEach((item) => {
      const option = createElement("option", "", textValue(getLabel(item)) || shortId(getValue(item)));
      option.value = textValue(getValue(item));
      fragment.append(option);
    });
    select.replaceChildren(fragment);
    if (preferredValue && items.some((item) => textValue(getValue(item)) === preferredValue)) {
      select.value = preferredValue;
    }
  }

  async function handleQueueJob(event) {
    event.preventDefault();
    hideJobFormError();
    if (!dom.jobForm.reportValidity()) return;

    let payload;
    try {
      payload = JSON.parse(dom.jobPayload.value);
      if (!payload || Array.isArray(payload) || typeof payload !== "object") {
        throw new Error("payload_not_object");
      }
    } catch (_error) {
      showJobFormError("Payload must be a valid JSON object.");
      dom.jobPayload.focus();
      return;
    }

    const body = {
      tenant_id: dom.jobTenant.value,
      store_id: dom.jobStore.value,
      agent_id: dom.jobAgent.value,
      device_id: dom.jobDevice.value || null,
      capability: dom.jobCapability.value.trim(),
      payload,
      idempotency_key: dom.jobIdempotency.value.trim() || null,
    };

    dom.queueJobButton.disabled = true;
    dom.queueJobButton.firstElementChild.textContent = "Checking policy…";
    try {
      const response = await apiRequest(`${API_BASE}/jobs`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      const job = response.job;
      const created = response.created !== false;
      if (job && job.id) {
        const existingIndex = state.data.jobs.findIndex((item) => item.id === job.id);
        if (existingIndex >= 0) {
          state.data.jobs.splice(existingIndex, 1, job);
        } else {
          state.data.jobs.unshift(job);
        }
      }
      dom.jobIdempotency.value = "";
      renderJobs();
      renderOverviewJobs();
      showToast(
        created ? "Job queued" : "Job deduplicated",
        created
          ? "The entitled worker can claim this dispatch on its next poll."
          : "The existing idempotent dispatch was returned without creating a duplicate.",
      );
      refreshAll({ silent: true });
    } catch (error) {
      if (error.status === 401) openAuthDialog();
      showJobFormError(error.message || "The control plane rejected this dispatch.");
    } finally {
      dom.queueJobButton.disabled = false;
      dom.queueJobButton.firstElementChild.textContent = "Queue approved job";
    }
  }

  function showJobFormError(message) {
    dom.jobFormError.textContent = message;
    dom.jobFormError.hidden = false;
  }

  function hideJobFormError() {
    dom.jobFormError.textContent = "";
    dom.jobFormError.hidden = true;
  }

  function renderJobs() {
    if (state.errors.jobs && state.data.jobs.length === 0) {
      renderErrorState(dom.jobsLedger, "Job ledger unavailable", state.errors.jobs);
      dom.jobsLedger.setAttribute("aria-busy", "false");
      return;
    }
    const allJobs = sortByDate(state.data.jobs, "created_at");
    const jobs = state.jobFilter === "all"
      ? allJobs
      : allJobs.filter((job) => job.status === state.jobFilter);
    if (jobs.length === 0) {
      renderEmptyState(
        dom.jobsLedger,
        state.jobFilter === "all" ? "No jobs in the ledger" : `No ${state.jobFilter} jobs`,
        state.jobFilter === "all"
          ? "Use the dispatch form to queue the first policy-bound job."
          : "Try another filter or wait for worker activity.",
        "J0",
      );
      dom.jobsLedger.setAttribute("aria-busy", "false");
      return;
    }

    const table = buildTable(
      "Atlas dispatch and execution history",
      [
        ["Job", (job) => primarySecondary(shortId(job.id), textValue(job.requested_by) || "local admin")],
        ["Capability", (job) => codeCell(job.capability)],
        ["Context", (job) => {
          const store = findById(state.data.stores, job.store_id);
          const agent = findById(state.data.agents, job.agent_id);
          return primarySecondary(
            store ? store.name : shortId(job.store_id),
            agent ? agent.name : shortId(job.agent_id),
          );
        }],
        ["State", (job) => {
          const wrapper = createElement("div");
          wrapper.append(statusChip(job.status));
          if (job.error) {
            wrapper.append(createElement("small", "cell-secondary", truncate(job.error, 70)));
          }
          return wrapper;
        }],
        ["Created", (job) => timeCell(job.created_at)],
        ["Completed", (job) => job.completed_at ? timeCell(job.completed_at) : createElement("span", "cell-secondary", "Pending")],
      ],
      jobs,
    );
    dom.jobsLedger.replaceChildren(table);
    dom.jobsLedger.setAttribute("aria-busy", "false");
  }

  function renderAudit() {
    if (state.errors.audit && state.data.audit.length === 0) {
      renderErrorState(dom.auditLedger, "Audit ledger unavailable", state.errors.audit);
      dom.auditResultCount.textContent = "0 events";
      dom.auditLedger.setAttribute("aria-busy", "false");
      return;
    }

    const events = sortByDate(state.data.audit, "created_at").filter((event) => {
      if (!state.auditSearch) return true;
      const searchable = [
        event.action,
        event.actor_type,
        event.outcome,
        event.resource_type,
        event.resource_id,
        event.tenant_id,
        event.store_id,
        event.agent_id,
        compactJson(event.details, 500),
      ].map(textValue).join(" ").toLowerCase();
      return searchable.includes(state.auditSearch);
    });
    dom.auditResultCount.textContent = `${events.length} event${events.length === 1 ? "" : "s"}`;

    if (events.length === 0) {
      renderEmptyState(
        dom.auditLedger,
        state.auditSearch ? "No matching events" : "Audit record is empty",
        state.auditSearch
          ? "Try a broader action, actor, outcome, or resource search."
          : "Policy, worker, and admin activity will be recorded here.",
        "L0",
      );
      dom.auditLedger.setAttribute("aria-busy", "false");
      return;
    }

    const fragment = document.createDocumentFragment();
    events.forEach((event) => {
      const outcome = textValue(event.outcome) || "unknown";
      const record = createElement(
        "article",
        `audit-event ${auditOutcomeClass(outcome)}`,
      );
      const time = createElement("div", "audit-time");
      const timeElement = createElement("time", "", formatTimestamp(event.created_at));
      timeElement.dateTime = textValue(event.created_at);
      time.append(timeElement, createElement("span", "", formatRelative(event.created_at)));

      const axis = createElement("span", "audit-axis");
      axis.setAttribute("aria-hidden", "true");

      const action = createElement("div", "audit-action");
      action.append(
        createElement("strong", "", textValue(event.action) || "Unspecified action"),
        createElement("span", "cell-secondary", humanize(event.actor_type || "unknown actor")),
      );

      const details = createElement("div", "audit-details");
      details.append(
        createElement("strong", "", describeAuditEvent(event)),
        createElement("span", "", resourceLabel(event)),
      );

      const context = createElement("div", "audit-context");
      context.append(
        createElement("span", "", `T / ${shortId(event.tenant_id)}`),
        createElement("span", "", `S / ${shortId(event.store_id)}`),
      );

      record.append(time, axis, action, details, context, statusChip(outcome));
      fragment.append(record);
    });
    dom.auditLedger.replaceChildren(fragment);
    dom.auditLedger.setAttribute("aria-busy", "false");
  }

  function describeAuditEvent(event) {
    const details = event.details && typeof event.details === "object" ? event.details : {};
    if (details.reason) return `Reason: ${truncate(details.reason, 120)}`;
    if (details.capability) return `Capability: ${truncate(details.capability, 120)}`;
    if (details.status) return `Status set to ${humanize(details.status)}`;
    if (details.provider) return `Model response via ${truncate(details.provider, 80)}`;
    if (event.resource_type) return `${humanize(event.resource_type)} record changed`;
    return "Control-plane event recorded";
  }

  function resourceLabel(event) {
    const type = textValue(event.resource_type);
    const id = textValue(event.resource_id);
    if (!type && !id) return "No resource identifier";
    return `${type || "resource"} / ${shortId(id)}`;
  }

  function auditOutcomeClass(outcome) {
    const normalized = normalizeStatus(outcome);
    if (["succeeded", "success", "created", "deduplicated", "allowed", "active"].includes(normalized)) {
      return "is-success";
    }
    if (["failed", "denied", "error", "disabled"].includes(normalized)) {
      return "is-failure";
    }
    return "";
  }

  function createDeviceToggle(device) {
    const active = device.status === "active";
    const button = createElement(
      "button",
      `table-action${active ? " is-danger" : ""}`,
      active ? "Disable" : "Enable",
    );
    button.type = "button";
    button.setAttribute(
      "aria-label",
      `${active ? "Disable" : "Enable"} ${textValue(device.name) || "worker device"}`,
    );
    button.addEventListener("click", () => toggleDevice(device, button));
    return button;
  }

  async function toggleDevice(device, button) {
    const shouldEnable = device.status !== "active";
    const action = shouldEnable ? "enable" : "disable";
    const deviceName = textValue(device.name) || shortId(device.id);
    const confirmed = window.confirm(
      `${action === "disable" ? "Disable" : "Enable"} ${deviceName}?\n\n${
        shouldEnable
          ? "The device may request new leases again."
          : "The worker will be denied new paid work until re-enabled."
      }`,
    );
    if (!confirmed) return;

    button.disabled = true;
    button.textContent = shouldEnable ? "Enabling…" : "Disabling…";
    try {
      const response = await apiRequest(
        `${API_BASE}/devices/${encodeURIComponent(device.id)}/toggle`,
        {
          method: "POST",
          body: JSON.stringify({
            enabled: shouldEnable,
            reason: "Atlas Control Center operator action",
          }),
        },
      );
      const updated = response.resource;
      if (updated && updated.id) {
        const index = state.data.devices.findIndex((item) => item.id === updated.id);
        if (index >= 0) state.data.devices.splice(index, 1, updated);
      }
      renderFleet();
      renderStores();
      renderNetworkReadiness();
      showToast(
        shouldEnable ? "Worker enabled" : "Worker disabled",
        `${deviceName} is now ${shouldEnable ? "eligible for authorization" : "blocked from new leases"}.`,
      );
      refreshAll({ silent: true });
    } catch (error) {
      if (error.status === 401) openAuthDialog();
      showToast("State change failed", error.message || "The device was not changed.", "error");
      button.disabled = false;
      button.textContent = shouldEnable ? "Enable" : "Disable";
    }
  }

  function openAuthDialog() {
    dom.adminToken.value = "";
    if (typeof dom.authDialog.showModal === "function") {
      if (!dom.authDialog.open) dom.authDialog.showModal();
    } else {
      dom.authDialog.setAttribute("open", "");
    }
    window.setTimeout(() => dom.adminToken.focus(), 0);
  }

  function closeAuthDialog() {
    dom.adminToken.value = "";
    if (typeof dom.authDialog.close === "function" && dom.authDialog.open) {
      dom.authDialog.close();
    } else {
      dom.authDialog.removeAttribute("open");
    }
  }

  function handleAuthSubmit(event) {
    event.preventDefault();
    const nextToken = dom.adminToken.value.trim() || DEMO_ADMIN_TOKEN;
    saveSessionToken(nextToken);
    closeAuthDialog();
    showToast("Session auth updated", "Reconnecting this tab to the local control plane.");
    refreshAll();
  }

  function useDemoToken() {
    saveSessionToken(DEMO_ADMIN_TOKEN);
    closeAuthDialog();
    showToast("Demo auth restored", "The local prototype bearer is active for this tab.");
    refreshAll();
  }

  function showToast(title, message, mode = "success") {
    const toast = createElement("div", `toast${mode === "error" ? " is-error" : ""}`);
    toast.append(
      createElement("strong", "", title),
      createElement("p", "", message),
    );
    dom.toastRegion.append(toast);
    window.setTimeout(() => toast.remove(), 4_800);
  }

  function buildTable(captionText, columns, rows) {
    const table = createElement("table", "data-table");
    table.append(createElement("caption", "", captionText));
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    columns.forEach(([label]) => headRow.append(createElement("th", "", label)));
    head.append(headRow);

    const body = document.createElement("tbody");
    rows.forEach((record) => {
      const row = document.createElement("tr");
      columns.forEach(([label, getCell]) => {
        const cell = document.createElement("td");
        cell.dataset.label = label;
        const value = getCell(record);
        if (value instanceof Node) {
          cell.append(value);
        } else {
          cell.textContent = textValue(value) || "—";
        }
        row.append(cell);
      });
      body.append(row);
    });
    table.append(head, body);
    return table;
  }

  function primarySecondary(primary, secondary) {
    const wrapper = createElement("div");
    wrapper.append(
      createElement("span", "cell-primary", textValue(primary) || "Unknown"),
      createElement("span", "cell-secondary", textValue(secondary) || "—"),
    );
    return wrapper;
  }

  function codeCell(value) {
    return createElement("code", "id-code", textValue(value) || "—");
  }

  function entityCell(entity, fallbackId) {
    if (entity) return primarySecondary(entity.name, shortId(entity.id));
    return primarySecondary("Unresolved", shortId(fallbackId));
  }

  function timeCell(value) {
    const wrapper = createElement("div");
    const time = createElement("time", "time-code", formatTimestamp(value));
    time.dateTime = textValue(value);
    wrapper.append(time, createElement("span", "cell-secondary", formatRelative(value)));
    return wrapper;
  }

  function statusChip(value, forcedTone) {
    const label = textValue(value) || "unknown";
    const tone = forcedTone || statusTone(label);
    return createElement("span", `status-chip ${tone}`, humanize(label));
  }

  function statusTone(value) {
    const normalized = normalizeStatus(value);
    if (["active", "healthy", "succeeded", "success", "online", "created", "allowed"].includes(normalized)) {
      return `is-${normalized === "success" || normalized === "created" || normalized === "allowed" ? "succeeded" : normalized}`;
    }
    if (["queued", "running", "trialing", "attention", "awaiting"].includes(normalized)) {
      return normalized === "awaiting" ? "is-attention" : `is-${normalized}`;
    }
    if (["disabled", "failed", "offline", "past_due", "canceled", "denied", "error"].includes(normalized)) {
      if (normalized === "denied" || normalized === "error") return "is-failed";
      if (normalized === "past_due") return "is-past-due";
      return `is-${normalized}`;
    }
    if (["paused", "stale", "deduplicated"].includes(normalized)) {
      return normalized === "deduplicated" ? "is-succeeded" : `is-${normalized}`;
    }
    return "is-unknown";
  }

  function getDeviceConnection(device) {
    if (device.status !== "active") {
      return { key: "offline", label: "Disabled", tone: "is-disabled" };
    }
    if (!device.last_heartbeat_at) {
      return { key: "awaiting", label: "Awaiting pulse", tone: "is-attention" };
    }
    const heartbeatTime = new Date(device.last_heartbeat_at).getTime();
    if (!Number.isFinite(heartbeatTime)) {
      return { key: "stale", label: "Signal unknown", tone: "is-stale" };
    }
    const ageSeconds = Math.max(0, (Date.now() - heartbeatTime) / 1000);
    if (device.health_status === "degraded" || ageSeconds > 600) {
      return { key: "offline", label: "Offline", tone: "is-offline" };
    }
    if (ageSeconds > 150) {
      return { key: "stale", label: "Stale pulse", tone: "is-stale" };
    }
    return { key: "online", label: "Online", tone: "is-online" };
  }

  function lastSeenLabel(value) {
    return value ? `Heartbeat ${formatRelative(value)}` : "No heartbeat received";
  }

  function renderEmptyState(container, title, message, code) {
    const wrapper = createElement("div", "empty-state");
    const content = createElement("div");
    const glyph = createElement("span", "empty-glyph");
    glyph.append(createElement("span", "", code));
    content.append(
      glyph,
      createElement("h4", "", title),
      createElement("p", "", message),
    );
    wrapper.append(content);
    container.replaceChildren(wrapper);
  }

  function renderErrorState(container, title, error) {
    const wrapper = createElement("div", "error-state");
    const content = createElement("div");
    const glyph = createElement("span", "empty-glyph");
    glyph.append(createElement("span", "", "!"));
    content.append(
      glyph,
      createElement("h4", "", title),
      createElement("p", "", error && error.message ? error.message : "The latest request failed."),
    );
    wrapper.append(content);
    container.replaceChildren(wrapper);
  }

  function createElement(tagName, className = "", text) {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function findById(items, id) {
    return items.find((item) => item.id === id) || null;
  }

  function sortByName(items, key) {
    return [...items].sort((a, b) =>
      textValue(a[key]).localeCompare(textValue(b[key]), undefined, { sensitivity: "base" }),
    );
  }

  function sortByDate(items, key, ascending = false) {
    return [...items].sort((a, b) => {
      const aTime = new Date(a[key] || 0).getTime() || 0;
      const bTime = new Date(b[key] || 0).getTime() || 0;
      return ascending ? aTime - bTime : bTime - aTime;
    });
  }

  function textValue(value) {
    if (value === null || value === undefined) return "";
    if (["string", "number", "boolean"].includes(typeof value)) return String(value);
    return "";
  }

  function humanize(value) {
    const text = textValue(value).replace(/[._-]+/g, " ").trim();
    return text ? text.replace(/\b\w/g, (letter) => letter.toUpperCase()) : "Unknown";
  }

  function normalizeStatus(value) {
    return textValue(value).trim().toLowerCase().replace(/[\s.-]+/g, "_");
  }

  function shortId(value) {
    const text = textValue(value);
    if (!text) return "—";
    if (text.length <= 25) return text;
    return `${text.slice(0, 14)}…${text.slice(-7)}`;
  }

  function truncate(value, limit) {
    const text = textValue(value);
    return text.length > limit ? `${text.slice(0, Math.max(0, limit - 1))}…` : text;
  }

  function compactJson(value, limit) {
    if (!value || typeof value !== "object") return "";
    try {
      return truncate(JSON.stringify(value), limit);
    } catch (_error) {
      return "";
    }
  }

  function formatInteger(value) {
    const number = Number(value);
    return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(
      Number.isFinite(number) ? number : 0,
    );
  }

  function formatCompactNumber(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "0";
    return new Intl.NumberFormat("en-US", {
      notation: number >= 10_000 ? "compact" : "standard",
      maximumFractionDigits: 1,
    }).format(number);
  }

  function formatCurrencyMicros(value) {
    const micros = Number(value);
    const dollars = Number.isFinite(micros) ? micros / 1_000_000 : 0;
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: dollars < 10 ? 4 : 2,
    }).format(dollars);
  }

  function formatTimestamp(value) {
    const date = value instanceof Date ? value : new Date(value);
    if (!Number.isFinite(date.getTime())) return "Time unknown";
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  }

  function formatRelative(value) {
    const date = value instanceof Date ? value : new Date(value);
    if (!Number.isFinite(date.getTime())) return "at an unknown time";
    const seconds = Math.round((date.getTime() - Date.now()) / 1000);
    const absolute = Math.abs(seconds);
    const formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
    if (absolute < 60) return formatter.format(seconds, "second");
    if (absolute < 3_600) return formatter.format(Math.round(seconds / 60), "minute");
    if (absolute < 86_400) return formatter.format(Math.round(seconds / 3_600), "hour");
    if (absolute < 2_592_000) return formatter.format(Math.round(seconds / 86_400), "day");
    return formatter.format(Math.round(seconds / 2_592_000), "month");
  }

  function friendlyApiMessage(code, status) {
    const messages = {
      admin_bearer_required: "An administrator token is required for this control plane.",
      admin_authentication_failed: "The administrator token was rejected.",
      tenant_inactive: "The selected tenant is not active.",
      store_inactive: "The selected store is not active for this tenant.",
      agent_inactive: "The selected agent is not active for this store.",
      device_inactive: "The selected device is not active for this store.",
      subscription_inactive: "This tenant does not have an active subscription.",
      entitlement_inactive: "The selected store is not entitled for this capability.",
      resource_not_found: "The requested control-plane record no longer exists.",
      network_error: "Unable to reach the local control plane.",
    };
    if (messages[code]) return messages[code];
    if (status === 401) return "Administrator authentication failed.";
    if (status === 404) return "The requested control-plane endpoint was not found.";
    if (status === 409) return "The control plane rejected this state transition.";
    return `The control plane returned HTTP ${status || "error"}.`;
  }

  function initialize() {
    cacheDom();
    bindEvents();
    routeFromHash();
    refreshAll();
    window.setInterval(() => {
      updateSyncReadout();
      if (
        document.visibilityState === "visible" &&
        Date.now() >= state.nextRefreshAt &&
        !state.loading
      ) {
        refreshAll({ silent: true });
      }
    }, 1_000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
