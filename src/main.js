// MotiveWave -> Journalit, Obsidian plugin.
//
// All trade logic lives in the Python engine (motivewave_journalit.py). It is
// embedded below by build.py and written next to this file on load, so the
// plugin installs as the usual main.js + manifest.json pair. This file starts
// the engine, passes it the vault and this plugin's data.json as settings, and
// shows what it wrote. Desktop only: it needs Python and the local MotiveWave
// files.
const { Modal, Notice, Plugin, PluginSettingTab, Setting, FileSystemAdapter, moment } = require("obsidian");
const { execFile } = require("child_process");
const fs = require("fs");
const path = require("path");

const ENGINE_SOURCE = __ENGINE_SOURCE__;
const ENGINE_FILE = "motivewave_journalit.py";

const DEFAULTS = {
  syncOnStartup: true,
  since: "", // set to the install day on first load, so old history is not imported
  workspacesDir: "",
  journalFolder: "",
  accounts: { simulated: "Simulated" },
  instruments: {},
  python: "",
};

const STRINGS = {
  en: {
    command: "Force sync",
    missingTool: "MotiveWave: the sync engine could not be written to the plugin folder.",
    failed: "MotiveWave: sync failed.",
    noPython: "MotiveWave: Python 3 was not found. Install it from python.org, or set the Python command in the plugin settings.",
    created: (n) => `${n} new`,
    restored: (n) => `${n} restored`,
    wrote: (parts) => `MotiveWave: ${parts} in Journalit.`,
    nothing: "MotiveWave: no new trades.",
    modalTitle: "Restore deleted trades",
    modalText:
      "These trades were deleted from Journalit. The ticked ones will be written again from MotiveWave " +
      "(anything you had written in them does not come back). Untick the ones that should stay deleted.",
    cancel: "Cancel",
    restoreAndSync: (n) => `Restore ${n} and sync`,
    syncOnly: "Sync only",
    forceName: "Force sync",
    forceDesc:
      "Reads MotiveWave now and registers the closed trades Journalit is missing, with stop, MAE and MFE when " +
      "MotiveWave has saved them. If trades were deleted from Journalit, a list opens to choose which to restore. " +
      "Only what MotiveWave has already written to disk is seen.",
    syncNow: "Sync now",
    syncing: "Syncing…",
    startupName: "Sync when Obsidian opens",
    startupDesc: "Registers new closed trades every time the vault opens. Deleted notes are only restored by Force sync.",
    sinceName: "Register trades from",
    sinceDesc: "Trades opened before this date (YYYY-MM-DD) are ignored, so old history is not imported.",
    workspacesName: "MotiveWave workspaces folder",
    workspacesDesc: "The folder that holds one subfolder per MotiveWave workspace. Leave empty for the default.",
    journalName: "Journalit journal folder",
    journalDesc: "Leave empty to use Journalit's own setting (default !Journalit).",
    accountsName: "Account names",
    accountsDesc:
      "One per line: MotiveWave account id = Journalit account name. The simulated account's id is \"simulated\". " +
      "Accounts not listed keep their MotiveWave id.",
    instrumentsName: "Instruments",
    instrumentsDesc: (defaults) =>
      "One per line: contract root = Journalit instrument, dollars per point, tick size " +
      `(e.g. NQ = NQ1, 20, 0.25). Adds to or overrides the built-in list: ${defaults}.`,
    pythonName: "Python command",
    pythonDesc: "Leave empty to find Python 3 automatically (py -3, python, python3). Otherwise a full path to python.",
    badLines: (lines) => `Ignored, not in the expected format: ${lines}`,
    stateDesc: (dir) => `State and log: ${dir}`,
  },
  pt: {
    command: "Forçar sync",
    missingTool: "MotiveWave: não foi possível gravar o motor de sync na pasta do plugin.",
    failed: "MotiveWave: sync falhou.",
    noPython: "MotiveWave: Python 3 não foi encontrado. Instale pelo python.org ou informe o comando do Python nas configurações do plugin.",
    created: (n) => `${n} nova(s)`,
    restored: (n) => `${n} recuperada(s)`,
    wrote: (parts) => `MotiveWave: ${parts} no Journalit.`,
    nothing: "MotiveWave: nenhuma trade nova.",
    modalTitle: "Recuperar trades deletadas",
    modalText:
      "Estas trades foram deletadas do Journalit. As marcadas serão recriadas a partir do MotiveWave " +
      "(o que você tinha escrito nelas não volta). Desmarque as que devem continuar deletadas.",
    cancel: "Cancelar",
    restoreAndSync: (n) => `Recuperar ${n} e sincronizar`,
    syncOnly: "Só sincronizar",
    forceName: "Forçar sync",
    forceDesc:
      "Lê o MotiveWave agora e registra no Journalit as trades fechadas que faltam, com stop, MAE e MFE quando o " +
      "MotiveWave já os salvou. Se houver trades deletadas do Journalit, abre uma lista para escolher quais recuperar. " +
      "Só entra o que o MotiveWave já gravou em disco.",
    syncNow: "Sincronizar agora",
    syncing: "Sincronizando…",
    startupName: "Sincronizar ao abrir o Obsidian",
    startupDesc: "Registra as trades fechadas novas sempre que o vault abre. Notas deletadas só voltam pelo Forçar sync.",
    sinceName: "Registrar trades a partir de",
    sinceDesc: "Trades abertas antes desta data (AAAA-MM-DD) são ignoradas, para não importar o histórico antigo.",
    workspacesName: "Pasta de workspaces do MotiveWave",
    workspacesDesc: "A pasta com uma subpasta por workspace do MotiveWave. Deixe vazio para usar o padrão.",
    journalName: "Pasta do diário do Journalit",
    journalDesc: "Deixe vazio para usar a configuração do próprio Journalit (padrão !Journalit).",
    accountsName: "Nomes das contas",
    accountsDesc:
      "Uma por linha: id da conta no MotiveWave = nome da conta no Journalit. A conta simulada tem o id \"simulated\". " +
      "Contas fora da lista ficam com o id do MotiveWave.",
    instrumentsName: "Instrumentos",
    instrumentsDesc: (defaults) =>
      "Um por linha: raiz do contrato = instrumento no Journalit, dólares por ponto, tick " +
      `(ex.: NQ = NQ1, 20, 0.25). Soma ou substitui a lista embutida: ${defaults}.`,
    pythonName: "Comando do Python",
    pythonDesc: "Deixe vazio para achar o Python 3 sozinho (py -3, python, python3). Ou o caminho completo do python.",
    badLines: (lines) => `Ignorado, fora do formato: ${lines}`,
    stateDesc: (dir) => `Estado e log: ${dir}`,
  },
};

const BUILT_IN = "NQ, ENQ, MNQ, ES, EP, MES, YM, MYM, RTY, M2K, CL, MCL, GC, MGC, SI";

function t() {
  return (moment?.locale?.() || "en").toLowerCase().startsWith("pt") ? STRINGS.pt : STRINGS.en;
}

function today() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

module.exports = class MotiveWaveJournalit extends Plugin {
  async onload() {
    this.settings = Object.assign({}, DEFAULTS, await this.loadData());
    if (!this.settings.since) {
      this.settings.since = today();
      await this.saveData(this.settings);
    }
    this.queue = Promise.resolve(); // one engine run at a time, in request order
    this.addCommand({ id: "force-sync", name: t().command, callback: () => this.forceSync() });
    this.addSettingTab(new MotiveWaveJournalitSettings(this.app, this));
    if (this.settings.syncOnStartup) {
      this.app.workspace.onLayoutReady(() => this.enqueue(() => this.runSync([], false)));
    }
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }

  enqueue(task) {
    const run = this.queue.then(task).catch((error) => {
      new Notice(`${t().failed}\n${error.message}`, 15000);
      console.error("motivewave-journalit", error);
    });
    this.queue = run;
    return run;
  }

  // Lists deleted trades; if any, asks which to restore; then syncs.
  forceSync() {
    return this.enqueue(async () => {
      const deleted = JSON.parse(await this.engine(["deleted"]));
      let keys = [];
      if (deleted.length > 0) {
        keys = await new RestoreModal(this.app, deleted).choose();
        if (keys === null) return; // cancelled: nothing runs
      }
      await this.runSync(keys, true);
    });
  }

  async runSync(keys, manual) {
    const stdout = await this.engine(["sync", ...(keys.length > 0 ? ["--restore", ...keys] : [])]);
    const kinds = stdout.split("\n").map((line) => line.split("\t")[0].trim()).filter(Boolean);
    const restored = kinds.filter((kind) => kind === "restored").length;
    const created = kinds.length - restored;
    const parts = [];
    if (created > 0) parts.push(t().created(created));
    if (restored > 0) parts.push(t().restored(restored));
    if (parts.length > 0) new Notice(t().wrote(parts.join(", ")), 10000);
    else if (manual) new Notice(t().nothing);
  }

  pluginDir() {
    return path.join(this.app.vault.adapter.getBasePath(), this.manifest.dir);
  }

  // Writes the embedded engine next to main.js when it is missing or outdated.
  enginePath() {
    const file = path.join(this.pluginDir(), ENGINE_FILE);
    let current = null;
    try {
      current = fs.readFileSync(file, "utf8");
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    if (current !== ENGINE_SOURCE) {
      try {
        fs.writeFileSync(file, ENGINE_SOURCE, "utf8");
      } catch (error) {
        throw new Error(`${t().missingTool}\n${error.message}`);
      }
    }
    return file;
  }

  pythons() {
    const custom = this.settings.python.trim();
    if (custom) {
      // A full path may contain spaces; anything else is "command [args]", e.g. "py -3".
      if (fs.existsSync(custom)) return [[custom, []]];
      const [command, ...prefix] = custom.split(/\s+/);
      return [[command, prefix]];
    }
    return process.platform === "win32"
      ? [["py", ["-3"]], ["python", []], ["python3", []]]
      : [["python3", []], ["python", []]];
  }

  engine(args) {
    if (!(this.app.vault.adapter instanceof FileSystemAdapter)) return Promise.resolve(args[0] === "deleted" ? "[]" : "");
    const vault = this.app.vault.adapter.getBasePath();
    const full = [
      this.enginePath(),
      "--vault",
      vault,
      "--config",
      path.join(this.pluginDir(), "data.json"),
      ...args,
    ];
    const candidates = this.pythons();
    const attempt = (index) =>
      new Promise((resolve, reject) => {
        const [command, prefix] = candidates[index];
        execFile(command, [...prefix, ...full], { cwd: vault, windowsHide: true }, (error, stdout, stderr) => {
          // Not installed: ENOENT, or the Windows Store alias that only prints an install hint (exit 9009).
          const missing = error && (error.code === "ENOENT" || error.code === 9009 || /Microsoft Store/i.test(stderr));
          if (missing && index + 1 < candidates.length) attempt(index + 1).then(resolve, reject);
          else if (missing) reject(new Error(t().noPython));
          else if (error) reject(new Error((stderr || error.message).trim().split("\n").slice(-3).join("\n")));
          else resolve(stdout);
        });
      });
    return attempt(0);
  }
};

class RestoreModal extends Modal {
  constructor(app, trades) {
    super(app);
    this.trades = trades;
    this.selected = new Set(trades.map((trade) => trade.key));
    this.result = null;
  }

  // Resolves with the keys to restore, or null if the modal was cancelled.
  choose() {
    return new Promise((resolve) => {
      this.resolve = resolve;
      this.open();
    });
  }

  onOpen() {
    const { contentEl } = this;
    this.titleEl.setText(t().modalTitle);
    contentEl.createEl("p", { text: t().modalText });

    const list = contentEl.createDiv();
    list.style.maxHeight = "50vh";
    list.style.overflowY = "auto";
    for (const trade of this.trades) {
      const row = list.createEl("label");
      row.style.display = "flex";
      row.style.gap = "0.6em";
      row.style.alignItems = "baseline";
      row.style.padding = "0.3em 0";
      const box = row.createEl("input", { type: "checkbox" });
      box.checked = true;
      box.addEventListener("change", () => {
        if (box.checked) this.selected.add(trade.key);
        else this.selected.delete(trade.key);
        this.refresh();
      });
      const pnl = trade.pnl === null ? "" : ` · ${trade.pnl < 0 ? "−" : "+"}$${Math.abs(trade.pnl).toLocaleString()}`;
      row.createSpan({
        text:
          `${trade.opened} · ${trade.account} · ${trade.instrument} ${trade.direction} ${trade.size} ` +
          `@ ${trade.entry} → ${trade.exit}${pnl} · ${path.basename(trade.note, ".md")}`,
      });
    }

    new Setting(contentEl)
      .addButton((button) => button.setButtonText(t().cancel).onClick(() => this.close()))
      .addButton((button) => {
        this.confirm = button;
        button.setCta().onClick(() => {
          this.result = [...this.selected];
          this.close();
        });
      });
    this.refresh();
  }

  refresh() {
    const count = this.selected.size;
    this.confirm.setButtonText(count > 0 ? t().restoreAndSync(count) : t().syncOnly);
  }

  onClose() {
    this.contentEl.empty();
    this.resolve(this.result);
  }
}

// "key = value" lines <-> object. Returns the object and the lines that did not parse.
function parseLines(text, parseValue) {
  const result = {};
  const bad = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const at = line.indexOf("=");
    const key = at > 0 ? line.slice(0, at).trim() : "";
    const value = at > 0 ? parseValue(line.slice(at + 1).trim()) : null;
    if (key && value !== null) result[key] = value;
    else bad.push(line);
  }
  return [result, bad];
}

function parseInstrument(value) {
  const parts = value.split(",").map((part) => part.trim());
  if (parts.length !== 3 || !parts[0]) return null;
  const dollarPerPoint = Number(parts[1]);
  const tickSize = Number(parts[2]);
  if (!(dollarPerPoint > 0) || !(tickSize > 0)) return null;
  return { name: parts[0], dollarPerPoint, tickSize };
}

class MotiveWaveJournalitSettings extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    const { containerEl } = this;
    const s = this.plugin.settings;
    const save = () => this.plugin.saveSettings();
    containerEl.empty();

    new Setting(containerEl)
      .setName(t().forceName)
      .setDesc(t().forceDesc)
      .addButton((button) =>
        button
          .setButtonText(t().syncNow)
          .setCta()
          .onClick(async () => {
            button.setDisabled(true).setButtonText(t().syncing);
            await this.plugin.forceSync();
            button.setDisabled(false).setButtonText(t().syncNow);
          })
      );

    new Setting(containerEl)
      .setName(t().startupName)
      .setDesc(t().startupDesc)
      .addToggle((toggle) => toggle.setValue(s.syncOnStartup).onChange((value) => ((s.syncOnStartup = value), save())));

    new Setting(containerEl)
      .setName(t().sinceName)
      .setDesc(t().sinceDesc)
      .addText((text) =>
        text.setPlaceholder("YYYY-MM-DD").setValue(s.since).onChange((value) => {
          if (/^\d{4}-\d{2}-\d{2}$/.test(value.trim())) {
            s.since = value.trim();
            save();
          }
        })
      );

    new Setting(containerEl)
      .setName(t().workspacesName)
      .setDesc(t().workspacesDesc)
      .addText((text) =>
        text
          .setPlaceholder(process.platform === "win32" ? "%APPDATA%\\MotiveWave\\workspaces" : "~/…/MotiveWave/workspaces")
          .setValue(s.workspacesDir)
          .onChange((value) => ((s.workspacesDir = value.trim()), save()))
      );

    new Setting(containerEl)
      .setName(t().journalName)
      .setDesc(t().journalDesc)
      .addText((text) =>
        text.setPlaceholder("!Journalit").setValue(s.journalFolder).onChange((value) => ((s.journalFolder = value.trim()), save()))
      );

    this.linesSetting(
      t().accountsName,
      t().accountsDesc,
      Object.entries(s.accounts).map(([id, name]) => `${id} = ${name}`).join("\n"),
      "simulated = Forward Testing",
      (value) => value || null,
      (parsed) => (s.accounts = parsed)
    );

    this.linesSetting(
      t().instrumentsName,
      t().instrumentsDesc(BUILT_IN),
      Object.entries(s.instruments)
        .map(([root, spec]) => `${root} = ${spec.name}, ${spec.dollarPerPoint}, ${spec.tickSize}`)
        .join("\n"),
      "NQ = NQ1, 20, 0.25",
      parseInstrument,
      (parsed) => (s.instruments = parsed)
    );

    new Setting(containerEl)
      .setName(t().pythonName)
      .setDesc(t().pythonDesc)
      .addText((text) => text.setPlaceholder("py -3").setValue(s.python).onChange((value) => ((s.python = value), save())));

    const stateDir =
      process.platform === "win32" ? "%LOCALAPPDATA%\\motivewave-journalit" : "~/.local/state/motivewave-journalit";
    containerEl.createEl("p", { text: t().stateDesc(stateDir), cls: "setting-item-description" });
  }

  linesSetting(name, desc, value, placeholder, parseValue, assign) {
    const setting = new Setting(this.containerEl).setName(name).setDesc(desc);
    const warning = setting.descEl.createDiv({ cls: "mod-warning" });
    setting.addTextArea((area) => {
      area.inputEl.rows = 4;
      area.setPlaceholder(placeholder).setValue(value).onChange((text) => {
        const [parsed, bad] = parseLines(text, parseValue);
        warning.setText(bad.length > 0 ? t().badLines(bad.join(" · ")) : "");
        assign(parsed);
        this.plugin.saveSettings();
      });
    });
  }
}
