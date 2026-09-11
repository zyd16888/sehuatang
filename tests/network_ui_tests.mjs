import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const html = readFileSync(new URL("../web/index.html", import.meta.url), "utf8");
const script = new vm.Script(html.slice(html.indexOf("async function refreshNetwork()"), html.indexOf("async function refreshRuns()")) + "\nrefreshNetwork;");
const esc = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
const base = {source: "x1080x", proxy: "http://proxy.test:17891", target: "https://site.test", state: "healthy", attempts: 2, retries: 1, failed: 0, completed: 1, timeout_rate: .5, average_ms: 500, last_success: 1000};

function page(api) {
  const elements = {networkSource: {value: ""}, networkNote: {}, networkLines: {innerHTML: ""}};
  const context = {panelRequest: 0, $: id => elements[id], esc, api, fmtTime: value => value.toISOString()};
  return {elements, context, refresh: script.runInNewContext(context)};
}

test("actual page shows retry recovery separately from final failures", async () => {
  const ui = page(async () => ({lines: [base]}));
  await ui.refresh();
  assert.match(ui.elements.networkLines.innerHTML, /2 \/ 1/);
  assert.match(ui.elements.networkLines.innerHTML, /最终失败 0 \/ 已完成 1/);
  assert.match(ui.elements.networkLines.innerHTML, /50.0%/);
  assert.match(ui.elements.networkLines.innerHTML, /1970-01-01T00:16:40/);
});

test("source selection and stale data never imply a healthy line", async () => {
  const ui = page(async () => ({lines: [base, {...base, source: "javbee", state: "stale", timeout_rate: null, average_ms: null}]}));
  ui.elements.networkSource.value = "javbee";
  await ui.refresh();
  const text = ui.elements.networkLines.innerHTML;
  assert.doesNotMatch(text, /x1080x|badge ok/);
  assert.match(text, /暂无近期数据/);
});

test("error details are escaped and diagnosis gives actionable evidence", async () => {
  const ui = page(async () => ({lines: [{...base, proxy: '<img src=x onerror="bad()">', state: "suspect", diagnostic: {at: 1000, outcome: "network_error", evidence: "proxy_unreachable"}}]}));
  await ui.refresh();
  const text = ui.elements.networkLines.innerHTML;
  assert.doesNotMatch(text, /<img/);
  assert.match(text, /&lt;img/);
  assert.match(text, /爬虫无法连接代理端口/);
});

test("empty and failed responses remove misleading prior status", async () => {
  const ui = page(async () => ({lines: []}));
  await ui.refresh();
  assert.match(ui.elements.networkNote.textContent, /尚未发起请求不代表线路正常/);
  ui.context.api = async () => {throw Error("offline");};
  ui.elements.networkLines.innerHTML = "old healthy";
  await assert.rejects(ui.refresh(), /offline/);
  assert.equal(ui.elements.networkLines.innerHTML, "");
  assert.match(ui.elements.networkNote.textContent, /读取失败/);
});

test("late refresh cannot overwrite a newer source selection", async () => {
  let resolve;
  const ui = page(() => new Promise(done => {resolve = done;}));
  const pending = ui.refresh();
  ui.context.panelRequest++;
  ui.elements.networkLines.innerHTML = "new selection";
  resolve({lines: [base]});
  await pending;
  assert.equal(ui.elements.networkLines.innerHTML, "new selection");
});

test("entire inline script parses after network tab integration", () => {
  new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
});
