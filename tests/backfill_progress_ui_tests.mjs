import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

// 执行页面实际刷新函数，避免只测一个与页面脱节的键解析器。
const html = readFileSync(new URL("../web/index.html", import.meta.url), "utf8");
const start = html.indexOf("async function refreshBackfill()");
const end = html.indexOf("let logRefreshSequence", start);
assert.ok(start >= 0 && end > start);
const script = new vm.Script(html.slice(start, end) + "\nrefreshBackfill;");

function page(data, source = "x1080x") {
  const elements = { bfSource: { value: source }, progressBody: { innerHTML: "" } };
  const refresh = script.runInNewContext({
    $: (id) => elements[id],
    esc: (value) => String(value),
    api: async (path) => {
      assert.equal(path, "/api/backfill-progress");
      return data;
    },
  });
  return { elements, refresh, body: () => elements.progressBody.innerHTML };
}

test("renders scoped and legacy checkpoints without merging different ranges", async () => {
  const ui = page({ progress: {
    "range-a:x1080x:5206": 349,
    "range-b:x1080x:5206": 12,
    "x1080x:5212": 459,
    "range-a:sehuatang:103": 18,
  } });
  await ui.refresh();
  assert.equal((ui.body().match(/<tr>/g) || []).length, 3);
  assert.match(ui.body(), /range-a:x1080x:5206.*349/);
  assert.match(ui.body(), /range-b:x1080x:5206.*12/);
  assert.match(ui.body(), /x1080x:5212.*459/);
  assert.doesNotMatch(ui.body(), /sehuatang|暂无检查点/);
});

test("switching source selects the source field rather than a scope prefix", async () => {
  const ui = page({ progress: {
    "range-a:x1080x:5206": 349,
    "x1080x:sehuatang:103": 18,
    "sehuatang:104": 7,
  } });
  await ui.refresh();
  assert.equal((ui.body().match(/<tr>/g) || []).length, 1);
  assert.doesNotMatch(ui.body(), /sehuatang/);
  ui.elements.bfSource.value = "sehuatang";
  await ui.refresh();
  assert.equal((ui.body().match(/<tr>/g) || []).length, 2);
  assert.match(ui.body(), /x1080x:sehuatang:103.*18/);
  assert.match(ui.body(), /sehuatang:104.*7/);
  assert.doesNotMatch(ui.body(), /range-a:x1080x/);
});

test("empty state appears only when the selected source has no checkpoints", async () => {
  for (const data of [{}, { progress: {} }, { progress: { "range:sehuatang:103": 3 } }]) {
    const ui = page(data);
    await ui.refresh();
    assert.match(ui.body(), /该来源暂无检查点/);
  }
});
