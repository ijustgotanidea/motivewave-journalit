// Plugin tests against the built dist/main.js, with Obsidian and child_process mocked.
//
//     python build.py && node tests/plugin.test.cjs
const assert = require("assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const Module = require("module");

// ---- Obsidian mock: just enough DOM and API for the plugin.
class El {
  constructor(tag, options = {}) {
    Object.assign(this, { tag, children: [], style: {}, text: options.text || "", type: options.type, listeners: {}, checked: false });
  }
  createEl(tag, options) { const el = new El(tag, options); this.children.push(el); return el; }
  createDiv(options) { return this.createEl("div", options); }
  createSpan(options) { return this.createEl("span", options); }
  setText(text) { this.text = text; }
  empty() { this.children = []; }
  addEventListener(event, handler) { this.listeners[event] = handler; }
  all(tag) { return this.children.flatMap((c) => [...(c.tag === tag ? [c] : []), ...c.all(tag)]); }
}
class Button {
  setButtonText(t) { this.text = t; return this; }
  setCta() { return this; }
  setDisabled(d) { this.disabled = d; return this; }
  onClick(f) { this.click = f; return this; }
}
class Field {
  constructor() { this.inputEl = {}; }
  setPlaceholder() { return this; }
  setValue(v) { this.value = v; return this; }
  onChange(f) { this.change = f; return this; }
}
let ui;
class Setting {
  constructor() { this.descEl = new El("div"); }
  setName(n) { this.name = n; ui.settings.push(this); return this; }
  setDesc() { return this; }
  addButton(cb) { const b = new Button(); ui.buttons.push(b); cb(b); return this; }
  addToggle(cb) { this.field = new Field(); cb(this.field); return this; }
  addText(cb) { this.field = new Field(); cb(this.field); return this; }
  addTextArea(cb) { this.field = new Field(); cb(this.field); return this; }
}
class Modal {
  constructor(app) { this.app = app; this.titleEl = new El("h2"); this.contentEl = new El("div"); }
  open() { ui.modal = this; this.onOpen(); }
  close() { this.onClose(); }
}
class FileSystemAdapter {
  constructor(base) { this.base = base; }
  getBasePath() { return this.base; }
}
const notices = [];
const obsidian = {
  Modal, Setting, FileSystemAdapter,
  moment: { locale: () => "en" },
  Notice: class { constructor(message) { notices.push(message); } },
  Plugin: class {
    constructor(app, manifest) { this.app = app; this.manifest = manifest; this.stored = null; }
    addCommand(c) { this.command = c; }
    addSettingTab(tab) { this.tab = tab; }
    async loadData() { return this.stored; }
    async saveData(d) { this.stored = JSON.parse(JSON.stringify(d)); }
  },
  PluginSettingTab: class { constructor(app) { this.app = app; this.containerEl = new El("div"); } },
};

// ---- child_process mock: records calls, answers like the engine.
let calls, respond;
const child = {
  execFile(command, args, options, callback) {
    calls.push({ command, args });
    setTimeout(() => {
      const result = respond(command, args);
      callback(result.error || null, result.stdout || "", result.stderr || "");
    }, 5);
  },
};
const load = Module._load;
Module._load = function (request, ...rest) {
  if (request === "obsidian") return obsidian;
  if (request === "child_process") return child;
  return load.call(this, request, ...rest);
};
const Plugin = require(path.join(__dirname, "..", "dist", "main.js"));

const deleted = [1, 2, 3].map((i) => ({
  key: `ws|simulated|SIM-${i}|${i}`, note: `!Journalit/x/NQ1-231026-T${i}.md`, opened: `2026-10-23 09:4${i}:00`,
  account: "Forward Testing", instrument: "NQ1", direction: "short", size: 1, entry: 29100, exit: 29120, pnl: -400,
}));
const engineArgs = (call) => call.args.slice(call.args.findIndex((a) => a === "--config") + 2);

async function start(stored = null) {
  ui = { settings: [], buttons: [], modal: null };
  calls = [];
  notices.length = 0;
  const vault = fs.mkdtempSync(path.join(os.tmpdir(), "mwj-vault-"));
  const dir = ".obsidian/plugins/motivewave-journalit";
  fs.mkdirSync(path.join(vault, dir), { recursive: true });
  let ready;
  const app = { vault: { adapter: new FileSystemAdapter(vault) }, workspace: { onLayoutReady: (f) => (ready = f) } };
  const plugin = new Plugin(app, { dir });
  plugin.stored = stored;
  await plugin.onload();
  plugin.tab.display();
  return { plugin, vault, dir, ready: () => ready && ready() };
}
const settle = () => new Promise((r) => setTimeout(r, 60));

const tests = {
  async "first load sets since to today, writes the engine and syncs on startup"() {
    respond = () => ({ stdout: "new\tJ/a.md\n" });
    const { plugin, vault, dir, ready } = await start();
    assert.match(plugin.stored.since, /^\d{4}-\d{2}-\d{2}$/);
    ready();
    await settle();
    const engine = path.join(vault, dir, "motivewave_journalit.py");
    assert.ok(fs.readFileSync(engine, "utf8").includes("def sync("));
    assert.deepEqual(calls[0].args.slice(-6), [engine, "--vault", vault, "--config", path.join(vault, dir, "data.json"), "sync"]);
    assert.deepEqual(notices, ["MotiveWave: 1 new in Journalit."]);
  },

  async "startup sync can be turned off"() {
    respond = () => ({ stdout: "" });
    const { ready } = await start({ syncOnStartup: false, since: "2026-01-01" });
    ready();
    await settle();
    assert.equal(calls.length, 0);
  },

  async "force sync asks which deleted trades to restore and restores only the ticked ones"() {
    respond = (_, args) => ({ stdout: args.includes("deleted") ? JSON.stringify(deleted) : "restored\tx1\nrestored\tx3\n" });
    const { plugin } = await start({ since: "2026-01-01", syncOnStartup: false });
    const settingsButton = ui.buttons[0];
    const done = settingsButton.click();
    await settle();
    assert.equal(ui.modal.titleEl.text, "Restore deleted trades");
    const rows = ui.modal.contentEl.all("span").map((s) => s.text);
    assert.equal(rows[0], "2026-10-23 09:41:00 · Forward Testing · NQ1 short 1 @ 29100 → 29120 · −$400 · NQ1-231026-T1");
    assert.equal(settingsButton.text, "Syncing…");
    const confirm = ui.buttons[ui.buttons.length - 1];
    assert.equal(confirm.text, "Restore 3 and sync");
    const boxes = ui.modal.contentEl.all("input");
    boxes[1].checked = false;
    boxes[1].listeners.change();
    assert.equal(confirm.text, "Restore 2 and sync");
    confirm.click();
    await done;
    assert.deepEqual(calls.map(engineArgs), [["deleted"], ["sync", "--restore", "ws|simulated|SIM-1|1", "ws|simulated|SIM-3|3"]]);
    assert.deepEqual(notices, ["MotiveWave: 2 restored in Journalit."]);
    assert.equal(settingsButton.text, "Sync now");
    assert.equal(plugin.command.name, "Force sync");
  },

  async "cancel runs nothing after the listing; unticking all only syncs"() {
    respond = (_, args) => ({ stdout: args.includes("deleted") ? JSON.stringify(deleted) : "" });
    const { plugin } = await start({ since: "2026-01-01", syncOnStartup: false });
    let done = plugin.forceSync();
    await settle();
    ui.buttons[ui.buttons.length - 2].click(); // Cancel
    await done;
    assert.deepEqual(calls.map(engineArgs), [["deleted"]]);

    calls.length = 0;
    done = plugin.forceSync();
    await settle();
    for (const box of ui.modal.contentEl.all("input")) { box.checked = false; box.listeners.change(); }
    const confirm = ui.buttons[ui.buttons.length - 1];
    assert.equal(confirm.text, "Sync only");
    confirm.click();
    await done;
    assert.deepEqual(calls.map(engineArgs), [["deleted"], ["sync"]]);
    assert.deepEqual(notices, ["MotiveWave: no new trades."]);
  },

  async "a missing Python falls through to the next candidate, then explains"() {
    respond = (command) =>
      command === "py" || command === "python3"
        ? { error: Object.assign(new Error("spawn ENOENT"), { code: "ENOENT" }) }
        : command === "python"
          ? { error: Object.assign(new Error("exit 9009"), { code: 9009 }), stderr: "Python was not found; run without arguments to install from the Microsoft Store" }
          : { stdout: "" };
    const { plugin } = await start({ since: "2026-01-01", syncOnStartup: false });
    await plugin.forceSync();
    assert.ok(calls.length >= 2);
    assert.match(notices[0], /Python 3 was not found/);
  },

  async "a custom python command with arguments is split"() {
    respond = () => ({ stdout: "[]" });
    const { plugin } = await start({ since: "2026-01-01", syncOnStartup: false, python: "py -3.12" });
    await plugin.engine(["deleted"]);
    assert.equal(calls[0].command, "py");
    assert.equal(calls[0].args[0], "-3.12");
  },

  async "account and instrument lines are parsed into settings, bad lines reported"() {
    respond = () => ({ stdout: "" });
    const { plugin } = await start({ since: "2026-01-01", syncOnStartup: false });
    const accounts = ui.settings.find((s) => s.name === "Account names");
    accounts.field.change("simulated = Forward Testing\nnonsense\nLIVE-1 = Prop 50K");
    assert.deepEqual(plugin.settings.accounts, { simulated: "Forward Testing", "LIVE-1": "Prop 50K" });
    assert.match(accounts.descEl.children[0].text, /nonsense/);
    const instruments = ui.settings.find((s) => s.name === "Instruments");
    instruments.field.change("NQ = NQ, 20, 0.25\nZB = ZB, abc, 1");
    assert.deepEqual(plugin.settings.instruments, { NQ: { name: "NQ", dollarPerPoint: 20, tickSize: 0.25 } });
    assert.match(instruments.descEl.children[0].text, /ZB/);
    assert.deepEqual(plugin.stored.accounts, plugin.settings.accounts);
  },
};

(async () => {
  let failed = 0;
  for (const [name, test] of Object.entries(tests)) {
    try {
      await test();
      console.log(`ok   ${name}`);
    } catch (error) {
      failed += 1;
      console.log(`FAIL ${name}\n${error.stack}`);
    }
  }
  console.log(failed ? `${failed} failed` : `${Object.keys(tests).length} passed`);
  process.exit(failed ? 1 : 0);
})();
