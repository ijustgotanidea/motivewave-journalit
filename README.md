# MotiveWave → Journalit

*[Leia em português](README.pt-BR.md)*

An [Obsidian](https://obsidian.md) plugin that registers your closed **MotiveWave** trades, from the simulated
account and from live accounts, as **[Journalit](https://journalit.co)** trade notes. It also fills in the
initial stop, the R multiple, MAE and MFE.

You trade in MotiveWave and open Obsidian. The trades are already in your journal, filed by date, and ready for
you to add a setup, a thesis and screenshots.

> Not affiliated with MotiveWave or Journalit. The plugin reads files that MotiveWave writes on your computer.
> Their layout is undocumented, so a MotiveWave update could change it. See [Tested with](#tested-with).

## What it does

- **Reads every MotiveWave workspace and every account it holds**, the `simulated` account included. Fills are
  rebuilt into flat-to-flat trades: scaling in and out stays inside one trade, and a reversal becomes two.
- **Writes Journalit's own note format, in Journalit's own folders.** A trade goes to
  `!Journalit/2026/Q4/10/W43/trades/NQ1-221026-T1.md`, with every fill as an entry or an exit, so Journalit
  indexes it like a trade you entered by hand.
- **Fills in the stop, R, MAE and MFE.** The stop is the first stop order you placed for the trade, taken from
  MotiveWave's order log. MAE and MFE are computed tick by tick from MotiveWave's tick cache.
- **Never duplicates.** A trade you already entered by hand (same account, side, entry time within two minutes
  and entry price) is linked instead of written again. Running the sync twice writes nothing new.
- **Never overwrites you.** If the stop or the ticks were not saved yet, the plugin tries again on later syncs
  for three days. It only adds fields that are still empty.
- **Gets deleted trades back when you ask.** **Force sync** lists the trades you deleted from Journalit, and you
  tick which ones to restore.
- **Runs entirely on your computer.** Nothing is sent anywhere.

## Requirements

- **Obsidian desktop** 1.4 or newer, with the **Journalit** plugin installed and set up.
- **MotiveWave**, used on the same computer as the vault.
- **Python 3.9 or newer.** Only the standard library is used.
  - **Windows:** install it from [python.org](https://www.python.org/downloads/), then run
    `py -m pip install tzdata`. Without `tzdata`, trade times use your computer's time zone instead of
    MotiveWave's (usually New York).
  - **macOS:** any of [python.org](https://www.python.org/downloads/), Homebrew (`brew install python`), or
    Apple's Command Line Tools (`xcode-select --install`, which provides Python 3.9). Nothing else is needed.
  - **Linux:** the distribution's `python3`.

## Install

### Option 1: BRAT (recommended)

1. In Obsidian, install **BRAT** from Community plugins.
2. BRAT → *Add beta plugin* → `ijustgotanidea/motivewave-journalit`.
3. Settings → Community plugins → enable **MotiveWave → Journalit**.

### Option 2: manual

1. Download `main.js` and `manifest.json` from the latest
   [release](https://github.com/ijustgotanidea/motivewave-journalit/releases).
2. Put both files in `<your vault>/.obsidian/plugins/motivewave-journalit/` (create the folder).
3. Restart Obsidian. Then Settings → Community plugins → enable **MotiveWave → Journalit**.

### Option 3: from source

```bash
git clone https://github.com/ijustgotanidea/motivewave-journalit
cd motivewave-journalit
python scripts/install.py "C:/Users/you/Documents/My Vault"
```

Restart Obsidian and enable the plugin. Run the same command again to update; your settings are kept.

## First run

Open **Settings → MotiveWave → Journalit**:

1. **Register trades from**: the plugin sets this to the day you installed it, so your old history is not
   imported. Set an earlier date to import older trades.
2. **Account names**: map MotiveWave account ids to the names your Journalit accounts use, one per line:

   ```
   simulated = Forward Testing
   APEX-12345-01 = Apex 50K
   ```

   Accounts not listed keep their MotiveWave id. The simulated account's id is `simulated`.
3. Press **Sync now**.

## Daily use

- **Opening Obsidian** registers new closed trades (you can turn this off).
- **Sync now** in the settings, or the command **Force sync**, does the same on demand. If you deleted trades
  from Journalit, it first shows them:

  ![restore dialog: one row per deleted trade with a checkbox, Cancel, Restore N and sync](docs/restore-dialog.svg)

  Untick the ones that should stay deleted. *Cancel* does nothing; unticking all of them just syncs.
- After each trade, add your setup, thesis, mistakes and images in Journalit as usual.

> **When does a trade show up?** Only once MotiveWave has saved it to disk. MotiveWave saves when you close it,
> and its workspace auto save may save it sooner (every 2 minutes on the tested install). If a trade you just
> closed is missing, wait a couple of minutes, or close MotiveWave, then Force sync.

## What gets filled in

| Field | Source |
| --- | --- |
| Direction, instrument, contract, account | MotiveWave fills |
| Entries and exits (time, price, size) | MotiveWave fills, in the workspace's time zone |
| PnL | Gross, from the point value; commission is written as 0 |
| Stop loss | First stop order placed on the closing side during the trade |
| R multiple | Gross PnL ÷ (\|average entry − stop\| × size × point value) |
| MAE / MFE | Worst and best open-plus-realised PnL at any tick during the trade, in dollars |
| Setup, thesis, mistakes, images, commissions | Left for you |

A trade closed at market with no stop order gets no stop, and its R stays 0.

## Settings

| Setting | Default | Notes |
| --- | --- | --- |
| Sync when Obsidian opens | on | |
| Register trades from | install day | Older trades are ignored |
| MotiveWave workspaces folder | where MotiveWave keeps them | Windows `%APPDATA%\MotiveWave\workspaces`, macOS `~/Library/MotiveWave/workspaces`, Linux `~/.motivewave/workspaces` |
| Journalit journal folder | Journalit's own setting | Default `!Journalit` |
| Account names | `simulated = Simulated` | `MotiveWave id = Journalit name` |
| Instruments | built-in list | `ROOT = Journalit name, $ per point, tick size` |
| Python command | automatic | `py -3`, `python`, `python3`, or a full path |

Built-in instruments: NQ (`NQ1`), MNQ, ES (`ES1`), MES, YM, MYM, RTY, M2K, CL, MCL, GC, MGC, SI, plus ENQ and
EP (CQG's names for NQ and ES). Add other contracts, or rename these to match the instruments in your
Journalit, for example `NQ = NQ, 20, 0.25`.

## How it works

The plugin carries a small Python engine (`motivewave_journalit.py`, written to the plugin folder). It reads
three things in MotiveWave's data folder:

| File | Used for |
| --- | --- |
| `workspaces/<name>/config/trade_history.csv` | Every fill on every account |
| `workspaces/<name>/config/journal.csv` | Order events, for the stop |
| `historical_data/<feed>/<contract>/*.tick_data(.zip)` | Ticks, for MAE and MFE |

The engine writes only the new trade notes in your vault, plus a small state file and a log outside it:
`%LOCALAPPDATA%\motivewave-journalit\` on Windows, `~/Library/Application Support/motivewave-journalit/` on
macOS, `~/.local/state/motivewave-journalit/` on Linux. The state records which trades were already
registered. It is kept per vault, so two vaults never share it.

The engine also works from a terminal:

```bash
python motivewave_journalit.py --vault "C:/path/to/vault" --config "C:/path/to/vault/.obsidian/plugins/motivewave-journalit/data.json" sync --dry-run
```

## Tested with

- **Windows 11:** MotiveWave 7.1.2 with Rithmic and CQG connections, Journalit 1.9.3, Python 3.9 and 3.14.
- **Linux (Ubuntu 24.04):** the engine's test suite, plus a full sync over a copy of real MotiveWave data, with
  identical results to Windows.
- **macOS:** not yet run on a Mac. The data folders are the ones MotiveWave 7.1.2's own code uses
  (`~/Library/MotiveWave`), and the plugin's macOS Python lookup is covered by tests, but nobody has confirmed it
  on real hardware. If you try it on a Mac, please open an issue either way.

## Limitations

- **macOS is untested on real hardware** (see above). If the plugin finds no trades, check that
  `~/Library/MotiveWave/workspaces` exists, or set *MotiveWave workspaces folder*.
- **MAE/MFE need MotiveWave's tick cache**, which exists only for contracts whose ticks MotiveWave loaded or
  saved. Turning on *Save Live Ticks* in MotiveWave's historical data settings (Rithmic, CQG and CTS) keeps
  them. The cache layout was verified on Rithmic data. CQG's cache uses an older layout that is not read, so
  CQG trades get no MAE/MFE.
- **PnL and R are gross.** MotiveWave's commission table is usually empty, so the plugin writes commission 0.
  Enter your real costs in Journalit.
- **Only closed trades** are registered. An open position appears once it is flat.
- Restoring a deleted trade rebuilds it from MotiveWave. Anything you had written in the deleted note is gone.

## Troubleshooting

- **Nothing appears.** Check *Register trades from*, then Force sync. If the trade still does not appear, close
  MotiveWave so it saves, and Force sync again. The log says what happened: `sync.log` in the state folder
  above.
- **"Python 3 was not found".** Windows: install Python from python.org with *Add python.exe to PATH* ticked.
  macOS: install it with one of the options under Requirements. Or set *Python command* to the full path of
  `python3` (`python.exe` on Windows).
- **Times are a few hours off.** On Windows, run `py -m pip install tzdata`.
- **Wrong instrument name or point value.** Add a line under *Instruments*.
- **Wrong account name.** Add a line under *Account names*. Trades already written keep the old name; edit them
  in Journalit.

## Uninstall

Disable and remove the plugin in Obsidian. Your trade notes stay. To also forget which trades were registered,
delete the state folder above.

## Development

```bash
python -m unittest discover -s tests   # engine
python build.py && node tests/plugin.test.cjs   # plugin, with Obsidian mocked
```

`src/main.js` is the plugin and `src/motivewave_journalit.py` the engine. `build.py` embeds the engine into
`dist/main.js`. To publish a release: bump `version` in `manifest.json` and `versions.json`, run
`python build.py`, then create a GitHub release tagged with that version, attaching `dist/main.js` and
`dist/manifest.json`. BRAT installs from those two files.

## License

[MIT](LICENSE)
