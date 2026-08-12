import assert from "node:assert/strict";
import test from "node:test";

async function render(path = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${path}`, {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the Unprompted landing page", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Unprompted — Remember the hard parts<\/title>/i);
  assert.match(html, /VOICE-FIRST · RETRIEVAL PRACTICE/);
  assert.match(html, />Remember<\/span>/);
  assert.match(html, />the hard parts\.<\/em>/);
  assert.match(html, /Recognition feels fluent\./);
  assert.match(html, /Two honest minutes\./);
  assert.match(html, /A map you can read/);
  assert.match(html, /Same honesty\./);
  assert.match(html, /NO STREAKS/);
  assert.doesNotMatch(html, />Devmax</);
  assert.doesNotMatch(html, /react-loading-skeleton|Your site is taking shape/);
});

test("server-renders the public privacy policy", async () => {
  const response = await render("/privacy");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Privacy — Unprompted/);
  assert.match(html, /Anthropic AI processing/);
  assert.match(html, /You may decline AI processing/);
  assert.match(html, /permanently delete the account/);
});
