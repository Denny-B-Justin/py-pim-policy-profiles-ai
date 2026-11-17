/* PIM Policy Profiles AI – client helpers (toasts, script loader, navigation niceties). */
(function () {
  "use strict";

  var ICONS = { success: "circle-check", info: "info", warning: "triangle-alert", error: "octagon-x", loading: "loader-circle" };

  function container() {
    var c = document.getElementById("toaster");
    if (!c) {
      c = document.createElement("div");
      c.id = "toaster";
      c.className = "pim-toaster";
      document.body.appendChild(c);
    }
    return c;
  }

  /** Sonner-style toast (top-right, rich colours). */
  function toast(type, message, description) {
    type = ICONS[type] ? type : "info";
    var el = document.createElement("div");
    el.className = "pim-toast";
    el.setAttribute("data-type", type);
    var ico = document.createElement("span");
    ico.className = "lucide lucide-" + ICONS[type] + (type === "loading" ? " animate-spin" : "");
    ico.setAttribute("data-icon", "");
    var body = document.createElement("div");
    body.setAttribute("data-content", "");
    var title = document.createElement("div");
    title.setAttribute("data-title", "");
    title.textContent = message;
    body.appendChild(title);
    if (description) {
      var d = document.createElement("div");
      d.setAttribute("data-description", "");
      d.textContent = description;
      body.appendChild(d);
    }
    el.appendChild(ico);
    el.appendChild(body);
    var c = container();
    c.insertBefore(el, c.firstChild);
    requestAnimationFrame(function () { el.setAttribute("data-visible", "true"); });
    var ttl = type === "error" ? 6000 : 4000;
    setTimeout(function () {
      el.removeAttribute("data-visible");
      setTimeout(function () { el.remove(); }, 300);
    }, ttl);
  }

  /** Load a script / stylesheet once (used for Leaflet and Redoc, which are not auto-loaded). */
  var loaded = {};
  function loadScript(src) {
    if (loaded[src]) return loaded[src];
    loaded[src] = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src; s.async = true;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error("Failed to load " + src)); };
      document.head.appendChild(s);
    });
    return loaded[src];
  }
  function loadCss(href) {
    if (loaded[href]) return loaded[href];
    loaded[href] = new Promise(function (resolve) {
      var l = document.createElement("link");
      l.rel = "stylesheet"; l.href = href; l.onload = function () { resolve(); };
      document.head.appendChild(l);
    });
    return loaded[href];
  }

  /** URL of an app route / asset relative to the (possibly prefixed) base path. */
  function basePath() {
    var p = window.location.pathname;
    return p.endsWith("/") ? p : p.replace(/[^/]*$/, "");
  }

  /** Redoc reference of the v1 partner API (app/api-docs/page.tsx) – the bundle is vendored in assets/vendor/redoc. */
  function initRedoc() {
    var el = document.getElementById("redoc-container");
    if (!el || el.dataset.ready) return;
    el.dataset.ready = "1";
    loadScript(basePath() + "assets/vendor/redoc/redoc.standalone.js").then(function () {
      window.Redoc.init(basePath() + "api/v1/openapi.json", {
        hideDownloadButton: false, expandResponses: "all", scrollYOffset: 56,
        theme: { colors: { primary: { main: "#0284c7" } } }
      }, el);
    }).catch(function () {
      el.dataset.ready = "";
      el.textContent = "The API reference could not be loaded.";
    });
  }

  window.pim = { toast: toast, loadScript: loadScript, loadCss: loadCss, basePath: basePath, dirty: false,
                 initRedoc: initRedoc };


  /* ── combobox (utils/ui.py::combobox) ───────────────────────────────────────── */
  function cbClose(root) {
    var pop = root.querySelector("[data-cb-pop]"), tr = root.querySelector("[data-cb-trigger]");
    if (pop) pop.style.display = "none";
    if (tr) tr.setAttribute("aria-expanded", "false");
  }
  function cbFilter(root, q) {
    q = (q || "").toLowerCase().trim();
    var n = 0, items = root.querySelectorAll("[data-cb-item]");
    items.forEach(function (it) {
      var ok = it.dataset.value === "" ? !q : (!q || it.dataset.search.indexOf(q) >= 0);   // "None" row only for an empty query
      it.style.display = ok ? "" : "none"; it.dataset.active = "false";
      if (ok) n += 1;
    });
    var empty = root.querySelector("[data-cb-empty]");
    if (empty) empty.classList.toggle("hidden", n > 0);
    var first = root.querySelector("[data-cb-item]:not([style*='display: none'])");
    if (first) first.dataset.active = "true";
  }
  function cbSelect(root, item) {
    var val = item.dataset.value || null;
    root.querySelectorAll("[data-cb-item]").forEach(function (it) {
      var c = it.querySelector(".cb-check");
      if (c) { c.classList.toggle("opacity-100", it === item); c.classList.toggle("opacity-0", it !== item); }
    });
    var lab = root.querySelector(".cb-label"), tr = root.querySelector("[data-cb-trigger]");
    lab.textContent = "";
    if (val) {
      var wrap = document.createElement("span"); wrap.className = "flex items-center gap-2";
      if (item.dataset.badge) {
        var chip = document.createElement("span");
        chip.className = "text-xs font-mono text-muted-foreground bg-muted px-1.5 py-0.5 rounded shrink-0";
        chip.textContent = item.dataset.badge; wrap.appendChild(chip);
      }
      wrap.appendChild(document.createTextNode(item.dataset.label || val)); lab.appendChild(wrap);
    } else {
      var ph = document.createElement("span"); ph.className = "text-muted-foreground";
      ph.textContent = tr.dataset.placeholder || "Select…"; lab.appendChild(ph);
    }
    cbClose(root);
    window.pim.dirty = true;
    window.dash_clientside.set_props(root.dataset.cb, { data: val });
  }
  document.addEventListener("click", function (e) {
    var t = e.target;
    var trig = t.closest && t.closest("[data-cb-trigger]");
    document.querySelectorAll("[data-cb]").forEach(function (root) {
      if (trig && root.contains(trig)) return;
      if (!root.contains(t)) cbClose(root);
    });
    if (trig) {
      var root = trig.closest("[data-cb]"), pop = root.querySelector("[data-cb-pop]");
      var open = pop.style.display === "none";
      pop.style.display = open ? "" : "none"; trig.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) { var s = root.querySelector(".cb-search"); s.value = ""; cbFilter(root, ""); setTimeout(function () { s.focus(); }, 0); }
      return;
    }
    var item = t.closest && t.closest("[data-cb-item]");
    if (item) cbSelect(item.closest("[data-cb]"), item);
  });
  document.addEventListener("input", function (e) {
    var s = e.target;
    if (s && s.matches && s.matches(".cb-search")) cbFilter(s.closest("[data-cb]"), s.value);
  });
  document.addEventListener("mouseover", function (e) {
    var it = e.target.closest && e.target.closest("[data-cb-item]");
    if (!it) return;
    it.closest("[data-cb]").querySelectorAll("[data-cb-item]").forEach(function (x) { x.dataset.active = x === it ? "true" : "false"; });
  });
  document.addEventListener("keydown", function (e) {
    var s = e.target;
    if (!(s && s.matches && s.matches(".cb-search"))) return;
    var root = s.closest("[data-cb]");
    var vis = Array.prototype.filter.call(root.querySelectorAll("[data-cb-item]"), function (x) { return x.style.display !== "none"; });
    var idx = vis.findIndex(function (x) { return x.dataset.active === "true"; });
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      idx = e.key === "ArrowDown" ? Math.min(vis.length - 1, idx + 1) : Math.max(0, idx - 1);
      vis.forEach(function (x, i) { x.dataset.active = i === idx ? "true" : "false"; });
      if (vis[idx]) vis[idx].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault(); if (vis[idx]) cbSelect(root, vis[idx]);
    } else if (e.key === "Escape") { cbClose(root); root.querySelector("[data-cb-trigger]").focus(); }
  });

  /* ── unsaved-changes guard for the record form (RecordForm.tsx) ────────────── */
  document.addEventListener("input", function (e) { if (e.target.closest && e.target.closest("#rf-form")) window.pim.dirty = true; });
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("#rf-form .Select-menu-outer")) window.pim.dirty = true;
  });
  window.addEventListener("beforeunload", function (e) {
    if (window.pim.dirty && document.getElementById("rf-form")) { e.preventDefault(); e.returnValue = ""; }
  });

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    pim: {
      toast: function (data) {
        if (data && data.message) toast(data.type, data.message, data.description);
        return window.dash_clientside.no_update;
      },
      cancelForm: function (n) {
        if (!n) return window.dash_clientside.no_update;
        if (window.pim.dirty && !window.confirm("Discard your unsaved changes?")) return window.dash_clientside.no_update;
        window.pim.dirty = false;
        if (window.history.length > 1) window.history.back();
        else window.dash_clientside.set_props("url", { search: "?page=repository" });
        return window.dash_clientside.no_update;
      },
      clearDirty: function () { window.pim.dirty = false; return window.dash_clientside.no_update; },
      navigated: function (title) {
        window.pim.dirty = false;
        if (title) document.title = title;
        window.scrollTo(0, 0);
        return window.dash_clientside.no_update;

      },
      toggleSection: function (n, bodyId, upId, downId) {
        var open = (n || 0) % 2 === 0;   // starts open
        var body = document.getElementById(bodyId), up = document.getElementById(upId), down = document.getElementById(downId);
        if (body) body.style.display = open ? "" : "none";
        if (up) up.style.display = open ? "" : "none";
        if (down) down.style.display = open ? "none" : "";
        return window.dash_clientside.no_update;
      },
      filterCountries: function (search, only) {
        var q = (search || "").toLowerCase().trim();
        var pimaOnly = !!(only && only.length);
        var rows = document.querySelectorAll("#countries-body tr[data-name]");
        var shown = 0;
        rows.forEach(function (r) {
          var ok = (!q || r.dataset.name.indexOf(q) >= 0 || r.dataset.iso.indexOf(q) >= 0) && (!pimaOnly || r.dataset.pima === "1");
          r.style.display = ok ? "" : "none";
          if (ok) { shown += 1; var idx = r.querySelector(".row-idx"); if (idx) idx.textContent = String(shown); }
        });
        var empty = document.getElementById("countries-empty-row");
        if (empty) {
          empty.style.display = shown === 0 ? "" : "none";
          var cell = document.getElementById("countries-empty");
          if (cell) cell.textContent = "No countries match \u201c" + (search || "") + "\u201d";
        }
        return shown === 0 ? "" : "Showing " + shown + " of " + rows.length + " countries";
      }
    }
  });

  /* Coach: example-question chips fill the chat input (the original's onClick={() => setInput(q)}). */
  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-chat-q]") : null;
    if (!b) return;
    window.dash_clientside.set_props("chat-input", { value: b.getAttribute("data-chat-q") });
    var i = document.getElementById("chat-input");
    if (i) i.focus();
  });

  /* Coach: keep the newest message in view (smooth), like messagesEndRef.scrollIntoView. */
  window.dash_clientside = window.dash_clientside || {};
  window.dash_clientside.pimChat = {
    scrollEnd: function () {
      setTimeout(function () {
        var el = document.getElementById("chat-end");
        if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "end" });
      }, 50);
      return window.dash_clientside.no_update;
    }
  };
})();
