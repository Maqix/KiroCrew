// Desktop first run (kanban #36, RFC one-chat first run §5.1): a fresh install
// of the desktop app must land on the first-run chat, and on the main chat once
// the first run has graduated — the same rule the web dashboard follows.
//
// The shell carries no landing logic of its own, on purpose: the cold boot
// loads the dashboard ROOT, the SPA's catch-all route sends `/` to `/chat`, and
// the chat page picks the session (remembered > main chat > first-run chat >
// first row) from the boot payload's `main_slot` / `first_run_slot`. These pins
// hold that chain together, so a later change that makes the cold boot open a
// specific route (which would bypass the SPA's rule) fails here instead of
// silently landing a new user somewhere else.

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.join(__dirname, "..");
const WEBSITE = path.join(ROOT, "..");
const MAIN_SOURCE = fs.readFileSync(path.join(ROOT, "main.js"), "utf-8");
const GATEWAY_SOURCE = fs.readFileSync(path.join(ROOT, "gateway-supervisor.js"), "utf-8");
const APP_SOURCE = fs.readFileSync(path.join(WEBSITE, "src", "App.tsx"), "utf-8");
const LANDING_SOURCE = fs.readFileSync(
  path.join(WEBSITE, "src", "pages", "chat", "useChatPageSessionController.ts"),
  "utf-8",
);

/** The source of one top-level-in-closure function, by name. */
function fnSource(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} not found`);
  let depth = 0;
  for (let i = source.indexOf("{", start); i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`${name} has no closing brace`);
}

describe("desktop first run lands where the web dashboard does", () => {
  it("the cold boot connects the main window without naming a route", () => {
    // No initialPath: a route here would override the SPA's landing rule.
    assert.match(MAIN_SOURCE, /await gateway\.connect\(mainWindow\);/);
    assert.match(GATEWAY_SOURCE, /connect: showLoadingThenConnect,/);
    assert.match(
      GATEWAY_SOURCE,
      /async function showLoadingThenConnect\([\s\S]*?\{ reconnect = false, initialPath = "" \} = \{\},[\s\S]*?\)/,
    );
  });

  it("with no initial path the window loads the dashboard root (plus the sign-in token)", () => {
    const dashboardEntryUrl = vm.runInNewContext(
      `(${fnSource(GATEWAY_SOURCE, "dashboardEntryUrl")})`,
      { URL },
    );
    assert.equal(dashboardEntryUrl("http://127.0.0.1:6776", ""), "http://127.0.0.1:6776/");
    assert.equal(
      dashboardEntryUrl("http://127.0.0.1:6776", "", "tok"),
      "http://127.0.0.1:6776/?token=tok",
    );
    // The one-shot new-session intent still names its own route.
    assert.equal(
      dashboardEntryUrl("http://127.0.0.1:6776", "/chat?new=1"),
      "http://127.0.0.1:6776/chat?new=1",
    );
  });

  it("the SPA sends the root to the chat page, keeping the query", () => {
    assert.match(APP_SOURCE, /<Route path="\*" element=\{<ChatRedirect \/>\} \/>/);
    assert.match(
      APP_SOURCE,
      /function ChatRedirect\(\) \{ const \{ search \} = useLocation\(\); return <Navigate to=\{'\/chat' \+ search\} replace \/> \}/,
    );
  });

  it("the chat page's landing rule prefers the main chat, then the first-run chat", () => {
    assert.match(
      LANDING_SOURCE,
      /const target = remembered \?\? mainHere \?\? firstRunHere \?\? filteredSlots\[0\]\.key/,
    );
    // Embedded hosts (popouts, companion panels) keep their own choice.
    assert.match(LANDING_SOURCE, /const firstRunHere = !embedded && firstRunSlot/);
  });
});
