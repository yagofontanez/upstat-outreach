(() => {
  const path = window.location.pathname;

  function escapeHtml(s) {
    return String(s).replace(
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

  function logLine(logEl, ev) {
    const div = document.createElement("div");
    if (ev.type === "log") {
      div.className = "log-line";
      div.textContent = ev.message;
    } else if (ev.type === "item") {
      div.className = "item";
      const ok = /ok|✓/i.test(ev.status || "");
      const fail = /falh|fail/i.test(ev.status || "");
      const cls = ok ? "ok" : fail ? "fail" : "";
      div.innerHTML =
        `<span class="idx">[${ev.index}/${ev.total}]</span>` +
        `<span class="name">${escapeHtml(ev.name || "").slice(0, 60)}</span>` +
        `<span class="status ${cls}">${escapeHtml(ev.status || "")}</span>`;
    } else if (ev.type === "done") {
      div.className = "done";
      div.textContent = ev.message || "Concluído.";
    } else if (ev.type === "fatal") {
      div.className = "fatal";
      div.textContent = ev.message || "Erro fatal.";
    }
    logEl.appendChild(div);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function streamJob(jobId, logEl, summaryEl, terminalEl) {
    const es = new EventSource(`/api/jobs/${jobId}/stream`);
    const headDot = terminalEl?.querySelector(".terminal-head .dot");
    headDot?.classList.add("live");

    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        logLine(logEl, ev);
        if (ev.type === "done" || ev.type === "fatal") {
          es.close();
          headDot?.classList.remove("live");
          if (summaryEl) {
            summaryEl.innerHTML =
              ev.type === "done" ? '<a href="/review">→ open review</a>' : "";
          }
        }
      } catch {}
    };
    es.onerror = () => {
      es.close();
      headDot?.classList.remove("live");
    };
  }

  function setEmailPreview(prefix, email) {
    document.getElementById(`${prefix}-subject`).textContent =
      email.subject || "—";
    document.getElementById(`${prefix}-html`).innerHTML = email.html || "";
    document.getElementById(`${prefix}-text`).textContent = email.text || "";
  }

  function wirePreviewTabs(attr, htmlId, textId) {
    document.querySelectorAll(`button[${attr}]`).forEach((btn) => {
      btn.addEventListener("click", () => {
        const showText = btn.getAttribute(attr) === "text";
        document
          .querySelectorAll(`button[${attr}]`)
          .forEach((b) => b.classList.toggle("active", b === btn));
        document.getElementById(htmlId).classList.toggle("hidden", showText);
        document.getElementById(textId).classList.toggle("hidden", !showText);
      });
    });
  }

  if (path === "/scrape") {
    const form = document.getElementById("scrape-form");
    const progress = document.getElementById("progress");
    const logEl = document.getElementById("scrape-log");
    const summaryEl = document.getElementById("scrape-summary");

    form.querySelectorAll("[data-fill]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const input = form.elements[btn.dataset.fill];
        if (!input) return;
        input.value = btn.dataset.value || "";
        input.focus();
      });
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const body = {
        term: fd.get("term"),
        city: fd.get("city"),
        max: fd.get("max"),
      };
      progress.classList.remove("hidden");
      logEl.textContent = "";
      summaryEl.textContent = "";
      const submitBtn = form.querySelector("button[type=submit]");
      submitBtn.disabled = true;
      submitBtn.textContent = "running…";
      try {
        const res = await fetch("/api/scrape", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          alert("falha ao iniciar");
          return;
        }
        const { jobId } = await res.json();
        streamJob(jobId, logEl, summaryEl, progress);
      } finally {
        setTimeout(() => {
          submitBtn.disabled = false;
          submitBtn.textContent = "run scrape";
        }, 2000);
      }
    });
  }

  if (path === "/personalize") {
    const form = document.getElementById("personalize-form");
    const progress = document.getElementById("progress");
    const logEl = document.getElementById("personalize-log");
    const summaryEl = document.getElementById("personalize-summary");

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const force = document.getElementById("force").checked;
      progress.classList.remove("hidden");
      logEl.textContent = "";
      summaryEl.textContent = "";
      const submitBtn = form.querySelector("button[type=submit]");
      submitBtn.disabled = true;
      submitBtn.textContent = "running…";
      try {
        const res = await fetch("/api/personalize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ force }),
        });
        if (!res.ok) {
          alert("falha ao iniciar");
          return;
        }
        const { jobId } = await res.json();
        streamJob(jobId, logEl, summaryEl, progress);
      } finally {
        setTimeout(() => {
          submitBtn.disabled = false;
          submitBtn.textContent = "run personalize";
        }, 2000);
      }
    });
  }

  if (path === "/send") {
    const progress = document.getElementById("progress");
    const logEl = document.getElementById("send-log");
    const summaryEl = document.getElementById("send-summary");

    async function startSend(body, submitBtn, originalLabel) {
      progress.classList.remove("hidden");
      progress.scrollIntoView({ behavior: "smooth", block: "nearest" });
      logEl.textContent = "";
      summaryEl.textContent = "";
      submitBtn.disabled = true;
      submitBtn.textContent = "sending…";
      try {
        const res = await fetch("/api/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          alert("falha ao iniciar");
          return;
        }
        const { jobId } = await res.json();
        streamJob(jobId, logEl, summaryEl, progress);
      } finally {
        setTimeout(() => {
          submitBtn.disabled = false;
          submitBtn.textContent = originalLabel;
        }, 2000);
      }
    }

    document.getElementById("test-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const btn = e.target.querySelector("button[type=submit]");
      startSend({ testEmail: fd.get("testEmail") }, btn, "send test");
    });

    document.getElementById("send-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const btn = e.target.querySelector("button[type=submit]");
      const limit = fd.get("limit");
      const body = {};
      if (limit) body.limit = limit;
      const msg = limit
        ? `dispatch first ${limit} approved leads?`
        : "dispatch to ALL approved leads in the queue?";
      if (!confirm(msg)) return;
      startSend(body, btn, "dispatch");
    });
  }

  if (path === "/template") {
    const form = document.getElementById("template-form");
    const status = document.getElementById("template-status");
    wirePreviewTabs(
      "data-preview-tab",
      "template-preview-html",
      "template-preview-text",
    );

    async function previewTemplate() {
      const fd = new FormData(form);
      const res = await fetch("/api/template/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subject: fd.get("subject"),
          body: fd.get("body"),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "preview failed");
      setEmailPreview("template-preview", data.email);
    }

    document
      .getElementById("template-preview-btn")
      .addEventListener("click", async () => {
        status.textContent = "";
        try {
          await previewTemplate();
        } catch (err) {
          status.textContent = err.message;
          status.className = "form-status fail";
        }
      });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      status.textContent = "saving…";
      status.className = "form-status";
      const res = await fetch("/api/template", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subject: fd.get("subject"),
          body: fd.get("body"),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        status.textContent = data.error || "save failed";
        status.className = "form-status fail";
        return;
      }
      status.textContent = "saved";
      status.className = "form-status ok";
      await previewTemplate().catch(() => {});
    });

    previewTemplate().catch(() => {});
  }

  if (path === "/review") {
    const tbody = document.querySelector(".leads-table tbody");
    if (!tbody) return;

    async function updateLead(key, payload) {
      const res = await fetch(`/api/leads/${encodeURIComponent(key)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      return res.ok;
    }

    async function bulkUpdate(keys, status) {
      if (keys.length === 0) return;
      await fetch("/api/leads/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keys, status }),
      });
    }

    function getRows() {
      return [...tbody.querySelectorAll("tr")].filter(
        (r) => !r.classList.contains("pers-row"),
      );
    }
    function getCheckedRows() {
      return getRows().filter((r) => r.querySelector(".row-check")?.checked);
    }
    function getActiveRows() {
      return getRows().filter((r) => !r.classList.contains("removed"));
    }

    function persRowFor(row) {
      const next = row.nextElementSibling;
      return next?.classList.contains("pers-row") ? next : null;
    }

    function removeRowVisual(row) {
      const pers = persRowFor(row);
      row.classList.add("removed");
      if (pers) pers.classList.add("removed");
      setTimeout(() => {
        row.remove();
        if (pers) pers.remove();
      }, 280);
    }

    let focusIdx = 0;
    function rows() {
      return getActiveRows();
    }
    function paintFocus() {
      rows().forEach((r, i) => r.classList.toggle("selected", i === focusIdx));
      const r = rows()[focusIdx];
      if (r) r.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    function clampFocus() {
      const max = rows().length - 1;
      if (focusIdx > max) focusIdx = Math.max(0, max);
      if (focusIdx < 0) focusIdx = 0;
    }

    paintFocus();

    const previewModal = document.getElementById("email-preview-modal");
    wirePreviewTabs(
      "data-lead-preview-tab",
      "lead-preview-html",
      "lead-preview-text",
    );
    document.querySelectorAll("[data-modal-close]").forEach((el) => {
      el.addEventListener("click", () => {
        previewModal.classList.add("hidden");
        previewModal.setAttribute("aria-hidden", "true");
      });
    });

    tbody.addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-action]");
      if (!btn) return;
      const row = btn.closest("tr");
      const key = row.dataset.key;
      const action = btn.dataset.action;
      if (action === "preview") {
        const pers = persRowFor(row);
        await updateLead(key, {
          email: row.querySelector(".email-input").value,
          personalizedHook: pers?.querySelector(".pers-hook")?.value || "",
        });
        const res = await fetch(`/api/leads/${encodeURIComponent(key)}/preview`);
        if (!res.ok) {
          alert("preview failed");
          return;
        }
        const data = await res.json();
        setEmailPreview("lead-preview", data.email);
        previewModal.classList.remove("hidden");
        previewModal.setAttribute("aria-hidden", "false");
      } else if (action === "approve") {
        const email = row
          .querySelector(".email-input")
          .value.trim()
          .toLowerCase();
        if (!email) {
          row.querySelector(".email-input").focus();
          row.querySelector(".email-input").style.borderColor = "var(--danger)";
          setTimeout(
            () => (row.querySelector(".email-input").style.borderColor = ""),
            1200,
          );
          return;
        }
        if (await updateLead(key, { status: "approved", email }))
          removeRowVisual(row);
      } else if (action === "reject") {
        if (await updateLead(key, { status: "rejected" })) removeRowVisual(row);
      }
      setTimeout(() => {
        clampFocus();
        paintFocus();
      }, 320);
    });

    tbody.addEventListener("change", async (e) => {
      const inp = e.target.closest(".email-input");
      if (!inp) return;
      const row = inp.closest("tr");
      await updateLead(row.dataset.key, { email: inp.value });
    });

    tbody.addEventListener("blur", async (e) => {
      const hook = e.target.closest(".pers-hook");
      if (!hook) return;
      const row = e.target.closest("tr");
      const key = row.dataset.key;
      await updateLead(key, { personalizedHook: hook.value });
    }, true);

    document.getElementById("check-all")?.addEventListener("change", (e) => {
      getActiveRows().forEach((r) => {
        const cb = r.querySelector(".row-check");
        if (cb) cb.checked = e.target.checked;
      });
    });

    document.querySelectorAll("button[data-bulk]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const kind = btn.dataset.bulk;
        const active = getActiveRows();
        let targets, status;
        if (kind === "approve-with-email") {
          targets = active.filter((r) =>
            r.querySelector(".email-input").value.trim(),
          );
          status = "approved";
        } else if (kind === "reject-no-email") {
          targets = active.filter(
            (r) => !r.querySelector(".email-input").value.trim(),
          );
          status = "rejected";
        } else if (kind === "approve-selected") {
          targets = getCheckedRows().filter((r) =>
            r.querySelector(".email-input").value.trim(),
          );
          status = "approved";
        } else if (kind === "reject-selected") {
          targets = getCheckedRows();
          status = "rejected";
        }
        if (!targets || targets.length === 0) {
          alert("// nothing to act on");
          return;
        }
        if (
          !confirm(
            `${status === "approved" ? "approve" : "drop"} ${targets.length} leads?`,
          )
        )
          return;
        const keys = targets.map((r) => r.dataset.key);
        await bulkUpdate(keys, status);
        targets.forEach(removeRowVisual);
        setTimeout(() => {
          clampFocus();
          paintFocus();
        }, 320);
      });
    });

    document.addEventListener("keydown", (e) => {
      const tag = (e.target.tagName || "").toLowerCase();
      const isInput = tag === "input" || tag === "textarea";
      if (isInput && e.key !== "Escape") return;
      const row = rows()[focusIdx];
      if (!row) return;

      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        focusIdx++;
        clampFocus();
        paintFocus();
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        focusIdx--;
        clampFocus();
        paintFocus();
      } else if (e.key === "y") {
        e.preventDefault();
        row.querySelector("button[data-action=approve]")?.click();
      } else if (e.key === "n") {
        e.preventDefault();
        row.querySelector("button[data-action=reject]")?.click();
      } else if (e.key === "x") {
        e.preventDefault();
        const cb = row.querySelector(".row-check");
        if (cb) cb.checked = !cb.checked;
      } else if (e.key === "e" || e.key === "/") {
        e.preventDefault();
        row.querySelector(".email-input")?.focus();
      } else if (e.key === "Escape") {
        if (isInput) e.target.blur();
      }
    });
  }
})();
