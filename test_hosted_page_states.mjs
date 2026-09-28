#!/usr/bin/env node
/**
 * DOM tests for the four hosted pages: nav, not-approved, loading,
 * empty, read error, and a missing private_pages chunk.
 * Never prints field values.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = join(dirname(fileURLToPath(import.meta.url)));
const NOT_APPROVED = "this account isn't approved. ask ang to add it.";
const LOADING = "loading…";
const LOAD_ERROR = "couldn't load. refresh or sign in again.";
const NAV_TEXT = "codes · templates · vendors · stats";
const PAGES = [
  { file: "codes.html", rowId: "codes-list", empty: null },
  { file: "templates.html", rowId: "templates-grid", empty: "no templates saved yet." },
  { file: "vendors.html", rowId: "vendor-grid", empty: "no vendors saved yet." },
  { file: "stats.html", rowId: "property-body", empty: "no stats saved yet." },
];

class ClassList {
  constructor(el) {
    this.el = el;
    this.names = new Set();
  }
  add(name) {
    this.names.add(name);
  }
  remove(name) {
    this.names.delete(name);
  }
  toggle(name, force) {
    const on = force === undefined ? !this.names.has(name) : !!force;
    if (on) this.names.add(name);
    else this.names.delete(name);
    return on;
  }
  contains(name) {
    return this.names.has(name);
  }
}

class Node {
  constructor(tag) {
    this.tagName = String(tag || "").toUpperCase();
    this.children = [];
    this.parent = null;
    this.id = "";
    this.classList = new ClassList(this);
    this.attrs = {};
    this._text = "";
    this._html = "";
  }
  set className(value) {
    this.classList.names = new Set(String(value).split(/\s+/).filter(Boolean));
  }
  get className() {
    return [...this.classList.names].join(" ");
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
    this._html = "";
  }
  get textContent() {
    if (this.children.length) return this.children.map((child) => child.textContent).join("");
    return this._text;
  }
  set innerHTML(value) {
    this._html = String(value);
    this.children = [];
    this._text = "";
  }
  get innerHTML() {
    return this._html;
  }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
  }
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
  }
  appendChild(child) {
    child.parent = this;
    this.children.push(child);
    this._text = "";
    return child;
  }
  replaceChildren(...kids) {
    this.children.forEach((child) => {
      child.parent = null;
    });
    this.children = [];
    kids.forEach((child) => this.appendChild(child));
  }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((child) => child !== this);
    this.parent = null;
  }
  replaceWith(next) {
    if (!this.parent) return;
    const index = this.parent.children.indexOf(this);
    next.parent = this.parent;
    this.parent.children.splice(index, 1, next);
    this.parent = null;
  }
  contains(node) {
    if (node === this) return true;
    return this.children.some((child) => child.contains && child.contains(node));
  }
}

function walk(node, visit) {
  visit(node);
  (node.children || []).forEach((child) => walk(child, visit));
}

function createDocument() {
  const byId = new Map();
  const body = new Node("body");
  const document = {
    body,
    createElement(tag) {
      return new Node(tag);
    },
    createTextNode(text) {
      const node = new Node("#text");
      node.textContent = text;
      return node;
    },
    getElementById(id) {
      return byId.get(id) || null;
    },
    querySelector(selector) {
      return document.querySelectorAll(selector)[0] || null;
    },
    querySelectorAll(selector) {
      const found = [];
      walk(body, (node) => {
        if (selector === "a[href]" && node.tagName === "A" && node.getAttribute("href") != null) found.push(node);
        if (selector === ".pages-only" && node.classList.contains("pages-only")) found.push(node);
        if (selector === ".notes-section" && node.classList.contains("notes-section")) found.push(node);
        if (selector === ".codes-intro" && node.classList.contains("codes-intro")) found.push(node);
      });
      return found;
    },
  };
  function add(id, tag, parent) {
    const node = new Node(tag || "div");
    node.id = id;
    byId.set(id, node);
    (parent || body).appendChild(node);
    return node;
  }
  const app = add("app-wrap", "div");
  add("gate-wrap", "div");
  add("page-nav", "nav", app);
  add("signout-btn", "button", app);
  add("page-status", "p", app);
  const data = add("page-data", "div", app);
  add("codes-list", "div", data);
  add("templates-grid", "div", data);
  add("vendor-grid", "div", data);
  add("property-body", "tbody", data);
  const notes = add("notes", "section", data);
  notes.className = "notes-section";
  const intro = add("intro", "div", data);
  intro.className = "codes-intro";
  add("page-history-panel", "div", data);
  add("updated", "span", app);
  return document;
}

function loadState(html, document, location) {
  const start = html.indexOf("// hosted-page-state\n");
  const end = html.indexOf("// hosted-page-state-end");
  assert.ok(start >= 0 && end > start, "hosted page state block missing");
  const block = html.slice(start, end);
  const factory = new Function(
    "document",
    "location",
    `function clearPropertyList() {}
     function clearTemplates() {}
     function clearVendors() {}
     function clearStatsView() {}
     function stopNotes() {}
     const gateInput = { focus() {} };
     ${block}
     return {
       applyHostedNav,
       showNotApproved,
       showLoading,
       showLoadError,
       showReady,
       showEmpty: typeof showEmpty === "function" ? showEmpty : null,
       assemblePrivatePage: typeof assemblePrivatePage === "function" ? assemblePrivatePage : null,
       presentPrivatePage: typeof presentPrivatePage === "function" ? presentPrivatePage : null,
     };`
  );
  return factory(document, location);
}

function anchors(document) {
  return document.querySelectorAll("a[href]");
}

for (const page of PAGES) {
  const html = readFileSync(join(root, "docs", page.file), "utf8");

  {
    const document = createDocument();
    const location = { hostname: "padsplit-scrapper.web.app", pathname: `/${page.file}` };
    const nav = document.getElementById("page-nav");
    const dash = document.createElement("a");
    dash.setAttribute("href", "./index.html");
    dash.textContent = "Dashboard";
    nav.appendChild(dash);
    const outside = document.createElement("a");
    outside.setAttribute("href", "./kpi-history.html");
    outside.textContent = "KPI History";
    document.body.appendChild(outside);
    const legacy = document.createElement("section");
    legacy.className = "pages-only";
    document.body.appendChild(legacy);
    const api = loadState(html, document, location);
    api.applyHostedNav();
    assert.equal(nav.textContent, NAV_TEXT);
    const current = page.file.replace(".html", "");
    const currentNode = nav.children.find((child) => child.textContent === current);
    assert.equal(currentNode.tagName, "SPAN");
    nav.children.forEach((child) => {
      if (child.tagName === "A") assert.notEqual(child.textContent, current);
    });
    anchors(document).forEach((link) => {
      const href = link.getAttribute("href") || "";
      assert.equal(href.includes("index.html"), false);
      assert.equal(href.includes("kpi-history.html"), false);
    });
    assert.equal(document.querySelectorAll(".pages-only").length, 0);
  }

  {
    const document = createDocument();
    const location = { hostname: "example.github.io", pathname: `/${page.file}` };
    const nav = document.getElementById("page-nav");
    const dash = document.createElement("a");
    dash.setAttribute("href", "./index.html");
    dash.textContent = "Dashboard";
    nav.appendChild(dash);
    const api = loadState(html, document, location);
    api.applyHostedNav();
    assert.equal(nav.textContent, "Dashboard");
    assert.equal(nav.children[0].tagName, "A");
  }

  {
    const document = createDocument();
    const location = { hostname: "padsplit-scrapper.web.app", pathname: `/${page.file}` };
    const row = document.getElementById(page.rowId);
    row.innerHTML = "<div>row</div>";
    const api = loadState(html, document, location);
    api.showNotApproved();
    const status = document.getElementById("page-status");
    assert.equal(status.textContent, NOT_APPROVED);
    assert.equal(status.classList.contains("hidden"), false);
    assert.equal(document.getElementById("page-nav").classList.contains("hidden"), true);
    assert.equal(document.getElementById("page-data").classList.contains("hidden"), true);
    assert.equal(document.getElementById("signout-btn").classList.contains("hidden"), false);
    assert.equal(document.getElementById("app-wrap").classList.contains("hidden"), false);
    assert.equal(row.innerHTML, "");
  }

  {
    const document = createDocument();
    const location = { hostname: "padsplit-scrapper.web.app", pathname: `/${page.file}` };
    const row = document.getElementById(page.rowId);
    row.innerHTML = "<div>row</div>";
    const api = loadState(html, document, location);
    api.showLoading();
    assert.equal(document.getElementById("page-status").textContent, LOADING);
    assert.equal(document.getElementById("page-nav").classList.contains("hidden"), false);
    assert.equal(document.getElementById("page-data").classList.contains("hidden"), true);
    assert.equal(row.innerHTML, "");
  }

  {
    const document = createDocument();
    const location = { hostname: "padsplit-scrapper.web.app", pathname: `/${page.file}` };
    const row = document.getElementById(page.rowId);
    row.innerHTML = "<div>partial</div>";
    const api = loadState(html, document, location);
    api.showLoadError();
    assert.equal(document.getElementById("page-status").textContent, LOAD_ERROR);
    assert.equal(document.getElementById("page-data").classList.contains("hidden"), true);
    assert.equal(row.innerHTML, "");
  }

  if (page.empty) {
    {
      const document = createDocument();
      const location = { hostname: "padsplit-scrapper.web.app", pathname: `/${page.file}` };
      const row = document.getElementById(page.rowId);
      row.innerHTML = "<div>stale</div>";
      const api = loadState(html, document, location);
      let rendered = 0;
      api.presentPrivatePage({ exists: false, data: null }, [], () => {
        rendered += 1;
      });
      assert.equal(rendered, 0);
      assert.equal(document.getElementById("page-status").textContent, page.empty);
      assert.equal(row.innerHTML, "");
    }

    {
      const document = createDocument();
      const location = { hostname: "padsplit-scrapper.web.app", pathname: `/${page.file}` };
      const row = document.getElementById(page.rowId);
      row.innerHTML = "<div>partial</div>";
      const api = loadState(html, document, location);
      let rendered = 0;
      api.presentPrivatePage(
        { exists: true, data: { chunk_count: 2 } },
        [{ exists: true, data: { json: "{\"a\":1}" } }],
        () => {
          rendered += 1;
          row.innerHTML = "<div>half</div>";
        }
      );
      assert.equal(rendered, 0);
      assert.equal(document.getElementById("page-status").textContent, LOAD_ERROR);
      assert.equal(row.innerHTML, "");
    }

    {
      const document = createDocument();
      const location = { hostname: "padsplit-scrapper.web.app", pathname: `/${page.file}` };
      const row = document.getElementById(page.rowId);
      const api = loadState(html, document, location);
      const payload = page.file === "templates.html"
        ? "{\"fields\":{\"n0\":\"x\"}}"
        : page.file === "vendors.html"
          ? "{\"vendors\":[{\"id\":\"v\"}]}"
          : "{\"kpis\":{}}";
      api.presentPrivatePage(
        { exists: true, data: { chunk_count: 1, json: payload } },
        [],
        () => {
          row.innerHTML = "<div>partial</div>";
          throw new Error("render failed");
        }
      );
      assert.equal(document.getElementById("page-status").textContent, LOAD_ERROR);
      assert.equal(row.innerHTML, "");
    }
  } else {
    assert.match(html, /no codes saved for \$\{property\.address\} yet\./);
    assert.equal(html.includes("no codes saved yet."), false);
    assert.equal(html.includes("assemblePrivatePage"), false);
  }
}

console.log("hosted page states ok");
