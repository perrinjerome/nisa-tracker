import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test, before } from "node:test";
import { JSDOM } from "jsdom";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

function render() {
  const pythons = [path.join(repoRoot, ".venv/bin/python"), "python3"];
  for (const py of pythons) {
    const r = spawnSync(py, ["tests/ui/render.py"], { cwd: repoRoot });
    if (r.status === 0) return r.stdout.toString("utf8");
    if (py === pythons[pythons.length - 1]) {
      throw new Error(`render.py failed: ${r.stderr}`);
    }
  }
}

let html;
let domain = [];
let fetchStub = () => Promise.resolve({ ok: true, json: async () => ({}) });

function setupDom(html, overrides = {}) {
  const charts = [];
  const win = new JSDOM(html, {
    runScripts: "dangerously",
    url: "http://localhost/",
    beforeParse(w) {
      w.__charts = charts;
      w.Chart = class {
        constructor(el, cfg) {
          this.element = el;
          this.data = cfg.data;
          this.options = cfg.options;
          this.updateCalls = 0;
          charts.push(this);
          w.__projChart = this;
        }
        update() {
          this.updateCalls += 1;
        }
      };
      w.fetch = (url, init) => (overrides.fetch || fetchStub)(url, init);
    },
  });
  const { window } = win;
  return { window, chart: async () => (await flush(), charts[0]) };
}

const flush = () => new Promise((r) => setTimeout(r, 5));

before(() => {
  html = render();
});

test("page renders chat + charts when configured", async () => {
  const { window } = setupDom(html);
  assert.ok(window.__charts.length >= 2);
  assert.equal(window.__charts[0].data.datasets.length, 3);
  assert.ok(window.__projChart);
  assert.notEqual(window.__charts[0].element.id, window.__projChart.element.id);
  assert.ok(window.document.getElementById("chatMessages"));
  assert.ok(window.document.getElementById("chatForm"));
  window.close();
});

test("range buttons slice the price chart", async () => {
  const { window, chart } = setupDom(html);
  const c = await chart();
  const initial = c.data.labels.length;
  assert.ok(initial >= 2);
  assert.ok(window.document.getElementById("rangeBtn1y").classList.contains("active"));
  window.document.getElementById("rangeBtnAll").click();
  assert.ok(c.data.labels.length > initial);
  assert.ok(window.document.getElementById("rangeBtnAll").classList.contains("active"));
  assert.ok(!window.document.getElementById("rangeBtn1y").classList.contains("active"));
  window.close();
});

test("buy toggle shows/hides the buy section", () => {
  const { window } = setupDom(html);
  const section = window.document.getElementById("buy");
  const before = section.style.display === "none" ? "hidden" : "shown";
  window.document.getElementById("buyToggle").click();
  const after = section.style.display === "none" ? "hidden" : "shown";
  assert.notEqual(before, after);
  window.close();
});

test("buy form shows server error message", async () => {
  const { window } = setupDom(html, {
    fetch: (url, init) =>
      Promise.resolve({ ok: false, status: 422, json: async () => ({ error: "bad amount" }) }),
  });
  const form = window.document.getElementById("buyForm");
  form.elements.date.value = "2026-09-01";
  form.elements.fund.value = "JP90C000H1T1";
  form.elements.amount.value = "50000";
  form.elements.account.value = "NISA (つみたて)";
  form.requestSubmit();
  await flush();
  const msg = window.document.getElementById("buyMsg");
  assert.match(msg.textContent, /bad amount/);
  assert.match(msg.className, /warning/);
  window.close();
});

test("buy form posts the correct payload", async () => {
  let posted = null;
  const { window } = setupDom(html, {
    fetch: (url, init) => {
      posted = JSON.parse(init.body);
      return Promise.resolve({ ok: true });
    },
  });
  const form = window.document.getElementById("buyForm");
  form.elements.date.value = "2026-09-01";
  form.elements.fund.value = "JP90C000H1T1";
  form.elements.amount.value = "50000";
  form.elements.account.value = "NISA (つみたて)";
  form.requestSubmit();
  await flush();
  assert.deepEqual(posted, {
    date: "2026-09-01",
    fund: "JP90C000H1T1",
    amount: "50000",
    account: "NISA (つみたて)",
  });
  window.close();
});

test("chat sends history and renders the stored reply", async () => {
  let sent = null;
  const { window } = setupDom(html, {
    fetch: (url, init) => {
      if (url === "/api/models") {
        return Promise.resolve({ ok: true, json: async () => ({}) });
      }
      if (url === "/api/conversations") {
        return Promise.resolve({ ok: true, json: async () => ({ conversations: [] }) });
      }
      if (url === "/api/chat") {
        sent = JSON.parse(init.body);
        return Promise.resolve({
          ok: true,
          json: async () => ({ reply: "こんにちは", conversation_id: 7 }),
        });
      }
      if (url === "/api/conversations/7") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id: 7,
            title: "質問",
            updated_at: "2026-09-07T00:00:00",
            messages: [
              { id: 1, role: "user", content: "今の総額は？" },
              { id: 2, role: "assistant", content: "こんにちは" },
            ],
          }),
        });
      }
      return fetchStub(url, init);
    },
  });
  const input = window.document.getElementById("chatInput");
  input.value = "今の総額は？";
  window.document.getElementById("chatForm").requestSubmit();
  await flush();
  const msgs = window.document.getElementById("chatMessages");
  assert.equal(msgs.children.length, 2);
  assert.equal(msgs.children[0].textContent, "今の総額は？");
  assert.match(msgs.children[1].textContent, /こんにちは/);
  assert.ok(msgs.children[1].classList.contains("ai"));
  assert.deepEqual(sent.messages.map((m) => m.role), ["user"]);
  assert.equal(sent.messages[0].content, "今の総額は？");
  assert.equal(sent.conversation_id, undefined);
  assert.equal(input.value, "");
  window.close();
});

test("conversation list resumes a saved conversation", async () => {
  const { window } = setupDom(html, {
    fetch: (url) => {
      if (url === "/api/conversations") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            conversations: [{ id: 5, title: "資産の話", updated_at: "2026-09-07T00:00:01", count: 2 }],
          }),
        });
      }
      if (url === "/api/conversations/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id: 5,
            title: "資産の話",
            updated_at: "2026-09-07T00:00:01",
            messages: [
              { id: 1, role: "user", content: "おしえて" },
              { id: 2, role: "assistant", content: "**答え**" },
            ],
          }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    },
  });
  await flush();
  const msgs = window.document.getElementById("chatMessages");
  assert.equal(msgs.children.length, 2);
  assert.ok(msgs.children[1].querySelector("strong"));
  assert.equal(msgs.children[1].querySelector("strong").textContent, "答え");
  const items = window.document.querySelectorAll(".chat-item");
  assert.equal(items.length, 1);
  assert.ok(items[0].classList.contains("sel"));
  assert.equal(items[0].textContent.trim(), "資産の話×");
  assert.equal(window.document.getElementById("chatTitle").textContent, "資産の話");
  window.close();
});

test("new chat button clears the open conversation", async () => {
  const { window } = setupDom(html, {
    fetch: (url) => {
      if (url === "/api/conversations") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            conversations: [{ id: 1, title: "A", updated_at: "2026-09-07T00:00:00", count: 1 }],
          }),
        });
      }
      if (url === "/api/conversations/1") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id: 1,
            title: "A",
            updated_at: "2026-09-07T00:00:00",
            messages: [{ id: 1, role: "user", content: "hi" }],
          }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    },
  });
  await flush();
  const msgs = window.document.getElementById("chatMessages");
  assert.equal(msgs.children.length, 1);
  window.document.getElementById("chatNew").click();
  assert.equal(msgs.children.length, 0);
  assert.equal(window.document.getElementById("chatTitle").textContent, "");
  assert.ok(!window.document.querySelector(".chat-item.sel"));
  window.close();
});

test("chat renders markdown as html without executing raw html", async () => {
  const { window } = setupDom(html, {
    fetch: (url) => {
      if (url === "/api/conversations/1") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id: 1,
            title: "x",
            updated_at: "2026-09-07T00:00:00",
            messages: [
              { id: 1, role: "user", content: "xss?" },
              { id: 2, role: "assistant", content: "<img src=x onerror=alert(1)>" },
            ],
          }),
        });
      }
      if (url === "/api/conversations") {
        return Promise.resolve({ ok: true, json: async () => ({ conversations: [] }) });
      }
      return Promise.resolve({ ok: true, json: async () => ({ reply: "<img src=x onerror=alert(1)>", conversation_id: 1 }) });
    },
  });
  const input = window.document.getElementById("chatInput");
  input.value = "xss?";
  window.document.getElementById("chatForm").requestSubmit();
  await flush();
  const msgs = window.document.getElementById("chatMessages");
  assert.equal(msgs.querySelectorAll("img").length, 0);
  assert.match(msgs.children[1].textContent, /<\/?img/);
  window.close();
});

test("chat replies render markdown as html", async () => {
  const reply = [
    "# Title",
    "",
    "Some **bold** and `code`.",
    "",
    "- a",
    "- b",
    "",
    "| X | Y |",
    "|---|---|",
    "| 1 | 2 |",
  ].join("\n");
  const { window } = setupDom(html, {
    fetch: (url) => {
      if (url === "/api/conversations/1") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id: 1,
            title: "x",
            updated_at: "2026-09-07T00:00:00",
            messages: [
              { id: 1, role: "user", content: "md?" },
              { id: 2, role: "assistant", content: reply },
            ],
          }),
        });
      }
      if (url === "/api/conversations") {
        return Promise.resolve({ ok: true, json: async () => ({ conversations: [] }) });
      }
      return Promise.resolve({ ok: true, json: async () => ({ reply, conversation_id: 1 }) });
    },
  });
  const input = window.document.getElementById("chatInput");
  input.value = "md?";
  window.document.getElementById("chatForm").requestSubmit();
  await flush();
  const bubble = window.document.getElementById("chatMessages").children[1];
  assert.ok(bubble.querySelector("h1"));
  assert.ok(bubble.querySelector("strong"));
  assert.ok(bubble.querySelector("code"));
  assert.ok(bubble.querySelector("ul"));
  assert.ok(bubble.querySelectorAll("li").length === 2);
  assert.ok(bubble.querySelector("table"));
  assert.equal(bubble.querySelector("h1").textContent, "Title");
  window.close();
});

test("chat shows error bubble when the api fails", async () => {
  const { window } = setupDom(html, {
    fetch: () => Promise.resolve({ ok: false, status: 503, json: async () => ({ error: "not configured" }) }),
  });
  const input = window.document.getElementById("chatInput");
  input.value = "hi";
  window.document.getElementById("chatForm").requestSubmit();
  await flush();
  const msgs = window.document.getElementById("chatMessages");
  assert.equal(msgs.children.length, 2);
  assert.equal(msgs.children[0].textContent, "hi");
  assert.ok(msgs.children[0].classList.contains("usr"));
  assert.match(msgs.children[1].textContent, /not configured/);
  assert.ok(msgs.children[1].classList.contains("ai"));
  window.close();
});

test("chat shows an animated thinking indicator while pending", async () => {
  let resolveFetch;
  const { window } = setupDom(html, {
    fetch: () => new Promise((res) => { resolveFetch = res; }),
  });
  const input = window.document.getElementById("chatInput");
  input.value = "hi";
  window.document.getElementById("chatForm").requestSubmit();
  const status = window.document.getElementById("chatStatus");
  const dots = status.querySelector(".dots");
  assert.ok(dots, "thinking indicator shown while pending");
  assert.equal(dots.querySelectorAll("i").length, 3);
  resolveFetch({ ok: true, json: async () => ({ reply: "ok", conversation_id: 1 }) });
  await flush();
  assert.equal(window.document.getElementById("chatStatus").textContent, "");
  window.close();
});

test("chat model select loads from /api/models and is sent with messages", async () => {
  let sent = null;
  const { window } = setupDom(html, {
    fetch: (url, init) => {
      if (url === "/api/models") {
        return Promise.resolve({ ok: true, json: async () => ({ models: ["gpt-4o", "gpt-4o-mini"] }) });
      }
      sent = JSON.parse(init.body);
      return Promise.resolve({ ok: true, json: async () => ({ reply: "ok", conversation_id: 7 }) });
    },
  });
  await flush();
  const sel = window.document.getElementById("chatModel");
  assert.ok(sel);
  assert.deepEqual([...sel.options].map((o) => o.value), ["", "gpt-4o", "gpt-4o-mini"]);
  sel.value = "gpt-4o";
  sel.dispatchEvent(new window.Event("change"));
  const input = window.document.getElementById("chatInput");
  input.value = "hi";
  window.document.getElementById("chatForm").requestSubmit();
  await flush();
  assert.equal(sent.model, "gpt-4o");
  window.close();
});

test("chat uses default model when select has no models", async () => {
  let sent = null;
  const { window } = setupDom(html, {
    fetch: (url, init) => {
      if (url === "/api/models") {
        return Promise.resolve({ ok: false, json: async () => ({}) });
      }
      sent = JSON.parse(init.body);
      return Promise.resolve({ ok: true, json: async () => ({ reply: "ok", conversation_id: 7 }) });
    },
  });
  await flush();
  const input = window.document.getElementById("chatInput");
  input.value = "hi";
  window.document.getElementById("chatForm").requestSubmit();
  await flush();
  assert.equal(sent.model, undefined);
  assert.ok(!("model" in sent));
  window.close();
});

test("forecast form input recomputes projection", async () => {
  const { window } = setupDom(html);
  const form = window.document.querySelector(".fc-form");
  const valueEl = window.document.querySelector(".fc-value");
  const before = valueEl.textContent;
  form.elements.monthly_tsumitate.value = "12345";
  form.dispatchEvent(new window.Event("input", { bubbles: true }));
  await flush();
  assert.notEqual(valueEl.textContent, before);
  assert.match(valueEl.textContent, /[0-9]/);
  assert.ok(window.__projChart.updateCalls > 0);
  assert.equal(window.__projChart.data.datasets[0].data.length, 121);
  window.close();
});