#!/usr/bin/env python3
"""MotiveWave -> Journalit: register MotiveWave trades, simulated and live, as Journalit notes.

MotiveWave keeps every fill of every account it is connected to, the
`simulated` account included, in `<workspace>/config/trade_history.csv`. It
rewrites that file on its auto save (every `autoSaveInterval` seconds of the
workspace's `config.json`) and on exit. This engine reads the file in every
workspace, rebuilds round-trip trades per workspace, account and contract
(a trade opens when the position leaves zero and closes when it returns to
zero; a reversal is split into a close and a new open), and writes each closed
trade as a Journalit trade note in the same place and format Journalit uses:

    <journal folder>/YYYY/Qn/MM/Www/trades/<INSTRUMENT>-DDMMYY-T<n>.md

Journalit indexes a new note under `trades/` when Obsidian sees the file, so
nothing is sent to any server. The Obsidian plugin runs `sync` when the vault
opens; its "Force sync" button first runs `deleted`, lets the user tick which
deleted notes to bring back, then runs `sync --restore <keys>`. Each note
written is printed on stdout as `new|restored<TAB>vault-relative path`.

`trade_history.csv` layout, decoded from the files themselves: line 1 maps the
short keys used below to instruments and accounts (`1=NQZ6.CME.RITHMIC,...`).
Each later line is one fill: 0 fill id, 1 instrument key, 3 account key,
5 order id, 8 side B/S, 10 fill time in epoch milliseconds written in base 32,
11 price, 12 quantity. The realised PnL column is ignored: it is 0 on live
accounts, and Journalit recomputes PnL from entries and exits anyway.

What is written: direction, instrument, contract, every fill as an entry or an
exit with its time in the workspace's time zone, gross PnL from the
instrument's point value, commission 0 (set real costs in Journalit), and the
account name, mapped by `accounts` in the settings (for example `simulated` ->
`Forward Testing`), otherwise the MotiveWave account id. Also, when MotiveWave
has saved what they need: the initial stop (and the R it implies), from the
order log, and MAE/MFE in dollars, from MotiveWave's tick cache; see the
section "stop, MAE and MFE". What is not saved yet is retried on every later
sync for three days and only ever added to the note, never written over a
value already there. Setup, thesis and images are left to the trader.

A trade is never written twice. Each one is recorded in a per-vault state file
outside the vault, keyed by workspace, account and first fill. Before writing,
the journal is also searched for a note on the same account and side whose
first entry is within two minutes and at the same price, so a trade that was
already registered by hand is linked, not duplicated. Trades opened before
`since` are ignored, so old history is not imported. A note deleted from the
vault stays deleted unless its trade is named in `--restore`; it is then
written again from MotiveWave (edits made to the deleted note are not
recoverable).

    python motivewave_journalit.py --vault VAULT [--config SETTINGS.json] sync [--dry-run] [--restore KEY ...]
    python motivewave_journalit.py --vault VAULT [--config SETTINGS.json] deleted

Settings (all optional): workspacesDir, journalFolder, since (YYYY-MM-DD),
accounts {MotiveWave id: Journalit name}, instruments {root: {name,
dollarPerPoint, tickSize}}. State and log live in `%LOCALAPPDATA%` (Windows) or
`$XDG_STATE_HOME`/`~/.local/state`, under `motivewave-journalit/`.

Standard library only; Python 3.9+.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import re
import struct
import sys
import uuid
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from hashlib import sha1
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path.cwd()  # the Obsidian vault; set from --vault in main()
DEFAULT_TIMEZONE = "America/New_York"
MATCH_SECONDS = 120
MONTH_CODE = re.compile(r"[FGHJKMNQUVXZ]\d{1,2}$")

# Contract root -> Journalit instrument. NQ1/ES1 are Journalit's own default names.
# ENQ and EP are the CQG roots of NQ and ES.
DEFAULT_INSTRUMENTS = {
    "NQ": {"name": "NQ1", "dollarPerPoint": 20, "tickSize": 0.25},
    "ENQ": {"name": "NQ1", "dollarPerPoint": 20, "tickSize": 0.25},
    "MNQ": {"name": "MNQ", "dollarPerPoint": 2, "tickSize": 0.25},
    "ES": {"name": "ES1", "dollarPerPoint": 50, "tickSize": 0.25},
    "EP": {"name": "ES1", "dollarPerPoint": 50, "tickSize": 0.25},
    "MES": {"name": "MES", "dollarPerPoint": 5, "tickSize": 0.25},
    "YM": {"name": "YM", "dollarPerPoint": 5, "tickSize": 1},
    "MYM": {"name": "MYM", "dollarPerPoint": 0.5, "tickSize": 1},
    "RTY": {"name": "RTY", "dollarPerPoint": 50, "tickSize": 0.1},
    "M2K": {"name": "M2K", "dollarPerPoint": 5, "tickSize": 0.1},
    "CL": {"name": "CL", "dollarPerPoint": 1000, "tickSize": 0.01},
    "MCL": {"name": "MCL", "dollarPerPoint": 100, "tickSize": 0.01},
    "GC": {"name": "GC", "dollarPerPoint": 100, "tickSize": 0.1},
    "MGC": {"name": "MGC", "dollarPerPoint": 10, "tickSize": 0.1},
    "SI": {"name": "SI", "dollarPerPoint": 5000, "tickSize": 0.005},
}


def default_state_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "motivewave-journalit"


STATE_DIR = default_state_dir()
LOCK_PATH = STATE_DIR / "sync.lock"
LOG_PATH = STATE_DIR / "sync.log"

log = logging.getLogger("motivewave_journalit")


# --------------------------------------------------------------------------- MotiveWave


@dataclass(frozen=True)
class Fill:
    workspace: str
    fill_id: str
    order_id: str
    account: str
    symbol: str
    side: int  # +1 buy, -1 sell
    time_ms: int
    price: float
    qty: float


@dataclass
class Trip:
    workspace: str
    account: str
    symbol: str
    direction: int
    entries: list[tuple[int, float, float]] = field(default_factory=list)
    exits: list[tuple[int, float, float]] = field(default_factory=list)
    fill_ids: list[str] = field(default_factory=list)
    exit_orders: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        first_time = self.entries[0][0]
        return f"{self.workspace}|{self.account}|{self.fill_ids[0]}|{first_time}"


def read_fills(workspace: Path) -> list[Fill]:
    path = workspace / "config" / "trade_history.csv"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return []
    names = dict(part.split("=", 1) for part in lines[0].split(",") if "=" in part)
    fills = []
    for number, line in enumerate(lines[1:], start=2):
        cols = line.split(",")
        try:
            side = {"B": 1, "S": -1}[cols[8]]
            fills.append(
                Fill(
                    workspace=workspace.name,
                    fill_id=cols[0],
                    order_id=cols[5],
                    account=names.get(cols[3], cols[3]),
                    symbol=names.get(cols[1], cols[1]),
                    side=side,
                    time_ms=int(cols[10], 32),
                    price=float(cols[11]),
                    qty=float(cols[12]),
                )
            )
        except (IndexError, KeyError, ValueError):
            log.warning("%s:%d: unreadable fill, skipped: %s", path, number, line)
    return fills


def workspace_timezone(workspace: Path) -> tzinfo:
    name = DEFAULT_TIMEZONE
    try:
        config = json.loads((workspace / "config" / "config.json").read_text(encoding="utf-8"))
        name = config.get("timeZone") or DEFAULT_TIMEZONE
    except (OSError, ValueError):
        pass
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        log.warning("time zone %s unknown to Python (Windows: py -m pip install tzdata); using local time", name)
        return datetime.now().astimezone().tzinfo


def round_trips(fills: list[Fill]) -> list[Trip]:
    """Closed trades, flat to flat, per workspace, account and contract."""
    position: dict[tuple[str, str, str], float] = {}
    current: dict[tuple[str, str, str], Trip] = {}
    closed = []
    for fill in sorted(fills, key=lambda f: (f.workspace, f.time_ms)):
        book = (fill.workspace, fill.account, fill.symbol)
        remaining = fill.qty
        while remaining > 1e-9:
            held = position.get(book, 0.0)
            if abs(held) < 1e-9:
                current[book] = Trip(fill.workspace, fill.account, fill.symbol, fill.side)
            trip = current[book]
            if fill.side == trip.direction:
                trip.entries.append((fill.time_ms, fill.price, remaining))
                position[book] = held + fill.side * remaining
                remaining = 0.0
            else:
                taken = min(remaining, abs(held))
                trip.exits.append((fill.time_ms, fill.price, taken))
                trip.exit_orders.append(fill.order_id)
                position[book] = held + fill.side * taken
                remaining -= taken
                if abs(position[book]) < 1e-9:
                    position[book] = 0.0
                    closed.append(current.pop(book))
            if fill.fill_id not in trip.fill_ids:
                trip.fill_ids.append(fill.fill_id)
    return closed


def contract_of(symbol: str) -> str:
    return symbol.split(".")[0]


def root_of(symbol: str) -> str:
    return MONTH_CODE.sub("", contract_of(symbol))


def gross_pnl(trip: Trip, point_value: float) -> float:
    entry_value = sum(price * qty for _, price, qty in trip.entries)
    exit_value = sum(price * qty for _, price, qty in trip.exits)
    return round((exit_value - entry_value) * trip.direction * point_value, 2)


def r_multiple(trip: Trip, pnl: float, stop: float, point_value: float) -> float | None:
    qty = sum(q for _, _, q in trip.entries)
    average = sum(p * q for _, p, q in trip.entries) / qty
    risk = abs(average - stop) * qty * point_value
    return round(pnl / risk, 4) if risk > 0 else None


# --------------------------------------------------------------------------- stop, MAE and MFE
#
# Stop: `<workspace>/config/journal.csv` logs every order event. The initial stop
# is the price of the first stop order (type STP*/TRAIL*) submitted on the
# closing side between the first entry and the last exit. When the log has no
# fill event for the trade's last exit order yet, it has not been saved past
# the trade, and the stop is retried on a later sync.
#
# MAE/MFE: MotiveWave caches every tick it received in
# `historical_data/<FEED>/<CONTRACT>/<hour start ms>.tick_data`; older days are
# zipped per day as `<day start ms>.tick_data.zip`. Layout, decoded from the
# files: big-endian; at byte 9 four longs (hour start, hour end, first tick,
# last tick); at 41 a short column count and (id, type) pairs; two bytes; then
# 45-byte records whose first two fields are the tick's offset from the hour
# start in ms and its price. A 28-byte file marks an hour with no trading.
# MAE/MFE are the lowest and highest open plus realised PnL the trade showed at
# any tick between its first entry and last exit, in dollars, which is
# Journalit's `dollar` MAE/MFE mode. Hours that are missing, or that end before
# the exit, are retried on a later sync.

TICK_COLUMNS = ((1, 4), (2, 4), (3, 4), (5, 1), (6, 4), (7, 4), (8, 4), (9, 4), (10, 8), (11, 8))
TICK_RECORD = struct.Struct(">ifibffffqq")
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
RETRY_DAYS = 3


@dataclass
class OrderLog:
    stops: dict[tuple[str, str], list[tuple[int, int, float]]]  # (account, symbol) -> (time, side, price)
    filled: set[tuple[str, str]]  # (account, order id) with a fill event


def read_order_log(workspace: Path) -> OrderLog:
    orders = OrderLog(defaultdict(list), set())
    path = workspace / "config" / "journal.csv"
    if not path.exists():
        return orders
    with open(path, encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 15 or row[1] != "ORDER":
                continue
            symbol, account, order_id, kind, action, status = row[5], row[6], row[7], row[11], row[12], row[13]
            if status in ("FILLED", "PARTIALLY_FILLED"):
                orders.filled.add((account, order_id))
            elif status == "SUBMITTED" and kind.startswith(("STP", "TRAIL")):
                try:
                    orders.stops[(account, symbol)].append((int(row[0]), 1 if action == "BUY" else -1, float(row[14])))
                except ValueError:
                    pass
    return orders


def initial_stop(trip: Trip, orders: OrderLog) -> float | None:
    opened, closed = trip.entries[0][0], trip.exits[-1][0]
    placed = [
        stop
        for stop in orders.stops.get((trip.account, trip.symbol), [])
        if stop[1] == -trip.direction and opened <= stop[0] <= closed
    ]
    return min(placed)[2] if placed else None


class TickStore:
    def __init__(self, base: Path):
        self.base = base
        self.zips: dict[Path, list[tuple[int, Path]]] = {}

    def _hour(self, folder: Path, hour: int) -> bytes | None:
        raw = folder / f"{hour}.tick_data"
        if raw.exists():
            return raw.read_bytes()
        if folder not in self.zips:
            self.zips[folder] = [(int(p.name.split(".")[0]), p) for p in folder.glob("*.tick_data.zip")]
        for day, archive in self.zips[folder]:
            if day <= hour < day + DAY_MS:
                with zipfile.ZipFile(archive) as bundle:
                    if f"{hour}.tick_data" in bundle.namelist():
                        return bundle.read(f"{hour}.tick_data")
        return None

    def window(self, symbol: str, start: int, end: int) -> list[tuple[int, float]] | None:
        """Ticks (ms, price) from start to end inclusive; None if any hour is missing or incomplete."""
        contract, _, feed = symbol.rpartition(".")
        folder = self.base / feed / contract
        ticks = []
        for hour in range(start - start % HOUR_MS, end + 1, HOUR_MS):
            data = self._hour(folder, hour)
            if data is None:
                return None
            if len(data) < 43:  # no trading that hour
                continue
            hour_start, _, _, last = struct.unpack_from(">qqqq", data, 9)
            count = struct.unpack_from(">h", data, 41)[0]
            columns = tuple(struct.unpack_from(">hh", data, 43 + 4 * i) for i in range(count))
            if columns != TICK_COLUMNS:
                log.warning("unknown tick layout in %s/%s.tick_data", folder, hour)
                return None
            if hour <= end < hour + HOUR_MS and last < end - 1000:
                return None
            offset = 43 + 4 * count + 2
            usable = (len(data) - offset) // TICK_RECORD.size * TICK_RECORD.size
            for record in TICK_RECORD.iter_unpack(data[offset : offset + usable]):
                when = hour_start + record[0]
                if start <= when <= end:
                    ticks.append((when, record[1]))
        return ticks


def excursion(trip: Trip, ticks: list[tuple[int, float]], point_value: float) -> tuple[float, float]:
    fills = sorted([(t, p, q, True) for t, p, q in trip.entries] + [(t, p, q, False) for t, p, q in trip.exits])
    index, open_qty, cost, realised, worst, best = 0, 0.0, 0.0, 0.0, 0.0, 0.0
    for when, price in ticks:
        while index < len(fills) and fills[index][0] <= when:
            _, fill_price, qty, is_entry = fills[index]
            index += 1
            if is_entry:
                open_qty += qty
                cost += fill_price * qty
            else:
                average = cost / open_qty
                realised += (fill_price - average) * qty * trip.direction * point_value
                cost -= average * qty
                open_qty -= qty
        if open_qty > 1e-9:
            pnl = realised + (price - cost / open_qty) * open_qty * trip.direction * point_value
            worst, best = min(worst, pnl), max(best, pnl)
    return round(worst, 2), round(best, 2)


def trade_details(trip: Trip, orders: OrderLog, ticks: TickStore, point_value: float) -> tuple[dict[str, float], list[str]]:
    """Stop and MAE/MFE that can be read now, and the names of those still pending."""
    found: dict[str, float] = {}
    pending = []
    if (trip.account, trip.exit_orders[-1]) in orders.filled:
        stop = initial_stop(trip, orders)
        if stop is not None:
            found["stopLoss"] = stop
    else:
        pending.append("stop")
    window = ticks.window(trip.symbol, trip.entries[0][0], trip.exits[-1][0])
    if window is None:
        pending.append("excursion")
    else:
        found["mae"], found["mfe"] = excursion(trip, window, point_value)
    return found, pending


# --------------------------------------------------------------------------- Journalit


def iso_week_folder(day: date) -> str:
    iso_year, week, _ = day.isocalendar()
    name = f"W{week:02d}"
    return name if iso_year == day.year else f"{name}-{iso_year}"


def week_dir(journal: Path, day: date) -> Path:
    quarter = math.ceil(day.month / 3)
    return journal / str(day.year) / f"Q{quarter}" / f"{day.month:02d}" / iso_week_folder(day)


def format_date(day: date, pattern: str) -> str:
    return (
        pattern.replace("YYYY", f"{day.year:04d}")
        .replace("YY", f"{day.year % 100:02d}")
        .replace("MM", f"{day.month:02d}")
        .replace("DD", f"{day.day:02d}")
    )


def ticker_for_filename(name: str) -> str:
    """Journalit's own sanitiser: upper case, path characters removed, 20 chars."""
    ticker = re.sub(r'[\\/:*?"<>|+]', "", name.strip().upper())
    ticker = re.sub(r"[\s.\[\](){}#%&=;]", "-", ticker)
    ticker = re.sub(r"--+", "-", ticker).strip("-")
    return ticker[:20].rstrip("-") or "UNKNOWN"


def number(value: float) -> str:
    value = round(value, 6)
    return str(int(value)) if float(value).is_integer() else repr(value)


def yaml_string(value: str) -> str:
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._%-]*", value) else json.dumps(value, ensure_ascii=False)


@dataclass
class JournalNote:
    path: Path
    account: str | None
    direction: str | None
    first_entry: datetime | None
    first_price: float | None


def read_journal_notes(journal: Path) -> list[JournalNote]:
    notes = []
    for path in journal.rglob("trades/*.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
        if not match:
            continue
        front = match.group(1)
        account = re.search(r"^account:\s*\r?\n\s+-\s*(.+?)\s*$", front, re.M)
        direction = re.search(r"^direction:\s*(\S+)", front, re.M)
        entry = re.search(r"^entries:\s*\r?\n\s+-\s*time:\s*(\S+)\s*\r?\n\s+price:\s*(\S+)", front, re.M)
        first_entry = first_price = None
        if entry:
            try:
                first_entry = datetime.fromisoformat(entry.group(1).strip("\"'"))
                first_price = float(entry.group(2))
            except ValueError:
                pass
        notes.append(
            JournalNote(
                path=path,
                account=account.group(1).strip("\"'") if account else None,
                direction=direction.group(1) if direction else None,
                first_entry=first_entry,
                first_price=first_price,
            )
        )
    return notes


def find_existing(notes: list[JournalNote], account: str, direction: str, entry: datetime, price: float) -> Path | None:
    for note in notes:
        if note.account != account or note.direction != direction or note.first_entry is None:
            continue
        when = note.first_entry
        if when.tzinfo is not None:
            when = when.astimezone(entry.tzinfo).replace(tzinfo=None)
        if abs((when - entry.replace(tzinfo=None)).total_seconds()) <= MATCH_SECONDS and abs(note.first_price - price) < 1e-6:
            return note.path
    return None


def next_trade_number(folder: Path, ticker: str, stamp: str, planned: set[Path]) -> int:
    """Journalit's rule: one more than the highest T<n> for this ticker and day."""
    pattern = re.compile(rf"^{re.escape(ticker)}-{re.escape(stamp)}-T(\d+)\.md$", re.I)
    names = [child.name for child in folder.iterdir()] if folder.exists() else []
    names += [path.name for path in planned if path.parent == folder]
    numbers = [int(found.group(1)) for found in map(pattern.match, names) if found]
    return max(numbers, default=0) + 1


def journalit_settings() -> dict:
    try:
        return json.loads((ROOT / ".obsidian" / "plugins" / "journalit" / "data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def journalit_template() -> tuple[str | None, int | None, str]:
    settings = journalit_settings()
    template_id = settings.get("templates", {}).get("defaultTrade")
    version = next(
        (t.get("version") for t in settings.get("reviewV2", {}).get("tradeTemplates", []) if t.get("id") == template_id),
        None,
    )
    return template_id, version, settings.get("trade", {}).get("dateFormat") or "DDMMYY"


def render_note(
    trip: Trip,
    tz: tzinfo,
    instrument: dict,
    account: str,
    drc_link: str,
    template: tuple[str | None, int | None],
    found: dict[str, float],
) -> str:
    def stamp(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, tz).strftime("%Y-%m-%dT%H:%M:%S")

    point_value = float(instrument["dollarPerPoint"])
    tick_size = float(instrument["tickSize"])
    pnl = gross_pnl(trip, point_value)
    stop = found.get("stopLoss")
    r_value = r_multiple(trip, pnl, stop, point_value) if stop is not None else None
    template_id, template_version = template

    lines = ["---", "type: trade", "tradeStatus: CLOSED", f"tradeId: trade_{uuid.uuid4()}", "schemaVersion: 1", "tradeRevision: 1"]
    if template_id:
        lines.append(f"templateId: {template_id}")
    if template_version is not None:
        lines.append(f"templateVersion: {template_version}")
    lines += [
        f"direction: {'long' if trip.direction > 0 else 'short'}",
        f"instrument: {yaml_string(instrument['name'])}",
        "assetType: futures",
        "hasExplicitExitPrice: true",
        f"pnl: {number(pnl)}",
        f"rMultiple: {number(r_value) if r_value is not None else 0}",
        "entries:",
    ]
    for ms, price, qty in trip.entries:
        lines += [f"  - time: {stamp(ms)}", f"    price: {number(price)}", f"    size: {number(qty)}"]
    lines.append("exits:")
    for ms, price, qty in trip.exits:
        lines += [
            f"  - time: {stamp(ms)}",
            f"    price: {number(price)}",
            f"    size: {number(qty)}",
            "    hasExplicitPrice: true",
        ]
    lines += [
        "commission: 0",
        "commissionType: fixed",
        "swap: 0",
        "fees: 0",
    ]
    lines += [f"{key}: {number(found[key])}" for key in ("stopLoss", "mae", "mfe") if key in found]
    lines += [
        "setup: []",
        "mistake: []",
        "account:",
        f"  - {yaml_string(account)}",
        "images: []",
        "tags: []",
        'thesis: ""',
        f"contractSymbol: {yaml_string(contract_of(trip.symbol))}",
        f"dollarPerPoint: {number(point_value)}",
        f"tickSize: {number(tick_size)}",
        f"tickValue: {number(point_value * tick_size)}",
        "useDirectPnLInput: false",
        f"journalitDrc: {json.dumps(drc_link, ensure_ascii=False)}",
        "---",
        "",
        "# Trade Notes",
        "",
        "<!-- User Notes Below -->",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- sync


def default_workspaces_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "MotiveWave" / "workspaces"
    candidates = [  # not verified outside Windows; set workspacesDir if none of these is right
        Path.home() / "Library" / "Application Support" / "MotiveWave" / "workspaces",
        Path.home() / "Library" / "MotiveWave" / "workspaces",
        Path.home() / ".motivewave" / "workspaces",
        Path.home() / "MotiveWave" / "workspaces",
    ]
    return next((path for path in candidates if path.exists()), candidates[0])


def load_config(path: Path | None) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8")) if path is not None and path.exists() else {}
    instruments = dict(DEFAULT_INSTRUMENTS)
    for root, spec in (raw.get("instruments") or {}).items():
        try:
            instruments[root.upper()] = {
                "name": str(spec["name"]),
                "dollarPerPoint": float(spec["dollarPerPoint"]),
                "tickSize": float(spec["tickSize"]),
            }
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"instrument {root!r} needs name, dollarPerPoint and tickSize") from None
    workspaces = (raw.get("workspacesDir") or "").strip()
    folder = (raw.get("journalFolder") or "").strip() or journalit_settings().get("general", {}).get("journalFolderPath") or "!Journalit"
    return {
        "workspacesDir": Path(os.path.expandvars(os.path.expanduser(workspaces))) if workspaces else default_workspaces_dir(),
        "journalRoot": ROOT / folder,
        "since": date.fromisoformat(raw["since"]) if raw.get("since") else date.today(),
        "accounts": dict(raw.get("accounts") or {}),
        "instruments": instruments,
    }


def state_path() -> Path:
    """One state file per vault, so two vaults on one computer never share registrations."""
    vault = os.path.normcase(str(ROOT.resolve()))
    return STATE_DIR / f"state-{sha1(vault.encode('utf-8')).hexdigest()[:12]}.json"


def load_state() -> dict:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "registered": {}}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = state_path()
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, target)


def history_files(config: dict) -> list[Path]:
    base = config["workspacesDir"]
    return sorted(base.glob("*/config/trade_history.csv")) if base.exists() else []


def patch_note(path: Path, values: dict[str, float]) -> list[str]:
    """Add fields the note does not have yet; values already in the note win.

    `rMultiple` is only replaced when `stopLoss` is added here and the note
    still carries the placeholder 0.
    """
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\r?\n(.*?\r?\n)---", text, re.S)
    if not match:
        return []
    front = match.group(1)
    added = []
    for key, value in values.items():
        if key == "rMultiple":
            continue
        if re.search(rf"^{key}:", front, re.M) is None:
            front += f"{key}: {number(value)}\n"
            added.append(key)
    if "stopLoss" in added and "rMultiple" in values:
        current = re.search(r"^rMultiple:[ \t]*(\S*)[ \t]*$", front, re.M)
        if current is None:
            front += f"rMultiple: {number(values['rMultiple'])}\n"
            added.append("rMultiple")
        elif current.group(1) in ("0", ""):
            front = front[: current.start()] + f"rMultiple: {number(values['rMultiple'])}" + front[current.end() :]
            added.append("rMultiple")
    if added:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text[: match.start(1)] + front + text[match.end(1) :])
    return added


def fill_in(trip: Trip, record: dict, orders: OrderLog, ticks: TickStore, point_value: float, dry_run: bool) -> bool:
    """Retry the stop and MAE/MFE of a note written before they were readable. True if the record changed."""
    path = ROOT / record["path"]
    if not path.exists():
        record.pop("pending")
        return True
    found, pending = trade_details(trip, orders, ticks, point_value)
    resolved = [name for name in record["pending"] if name not in pending]
    values: dict[str, float] = {}
    if "stop" in resolved and "stopLoss" in found:
        values["stopLoss"] = found["stopLoss"]
        r_value = r_multiple(trip, gross_pnl(trip, point_value), found["stopLoss"], point_value)
        if r_value is not None:
            values["rMultiple"] = r_value
    if "excursion" in resolved:
        values["mae"], values["mfe"] = found["mae"], found["mfe"]
    still = [name for name in record["pending"] if name in pending]
    if still and datetime.now().timestamp() * 1000 - trip.exits[-1][0] > RETRY_DAYS * DAY_MS:
        log.info("gave up on %s for %s: MotiveWave never saved it", ", ".join(still), record["path"])
        still = []
    if dry_run:
        if values:
            log.info("would add %s to %s", ", ".join(values), record["path"])
        return False
    if values:
        added = patch_note(path, values)
        log.info("added %s to %s", ", ".join(added) or "nothing (already filled in)", record["path"])
    if still == record["pending"]:
        return False
    if still:
        record["pending"] = still
    else:
        record.pop("pending")
    return True


def load_trips(config: dict) -> list[tuple[Trip, tzinfo, OrderLog]]:
    trips = []
    for history in history_files(config):
        workspace = history.parent.parent
        tz = workspace_timezone(workspace)
        orders = read_order_log(workspace)
        trips += [(trip, tz, orders) for trip in round_trips(read_fills(workspace))]
    trips.sort(key=lambda item: item[0].entries[0][0])
    return trips


def note_link(record: dict) -> str | None:
    """The state field holding the trade's note: "path" (written here) or "matched" (a note found in the journal)."""
    return "path" if "path" in record else "matched" if "matched" in record else None


def deleted_trades(config: dict) -> list[dict]:
    """Registered trades whose note is gone from the vault, newest first, for the restore picker.

    A note that was only moved or renamed is not listed; the next sync relinks it.
    """
    registered = load_state().get("registered", {})
    notes = read_journal_notes(config["journalRoot"])
    listed = []
    for trip, tz, _ in load_trips(config):
        record = registered.get(trip.key)
        link = note_link(record) if record else None
        if link is None or (ROOT / record[link]).exists():
            continue
        account = config["accounts"].get(trip.account, trip.account)
        direction = "long" if trip.direction > 0 else "short"
        opened = datetime.fromtimestamp(trip.entries[0][0] / 1000, tz)
        if find_existing(notes, account, direction, opened, trip.entries[0][1]):
            continue
        instrument = config["instruments"].get(root_of(trip.symbol))
        size = sum(q for _, _, q in trip.entries)
        listed.append(
            {
                "key": trip.key,
                "note": record[link],
                "opened": opened.strftime("%Y-%m-%d %H:%M:%S"),
                "account": account,
                "instrument": instrument["name"] if instrument else contract_of(trip.symbol),
                "direction": direction,
                "size": size,
                "entry": round(sum(p * q for _, p, q in trip.entries) / size, 2),
                "exit": round(sum(p * q for _, p, q in trip.exits) / size, 2),
                "pnl": gross_pnl(trip, float(instrument["dollarPerPoint"])) if instrument else None,
            }
        )
    return listed[::-1]


def sync(config: dict, dry_run: bool = False, restore: frozenset[str] = frozenset()) -> list[tuple[str, Path]]:
    """Write the closed trades Journalit lacks; returns ("new" | "restored", path) per note written.

    A registered trade whose note was deleted stays deleted unless its state key
    is in `restore` (see `deleted_trades`); then it is written again, under its
    old file name when that is free. A note that was only moved or renamed is
    found by account, side, first entry and price, and relinked instead.
    """
    journal = config["journalRoot"]
    state = load_state()
    registered = state.setdefault("registered", {})
    template_id, template_version, date_format = journalit_template()
    notes = read_journal_notes(journal)
    ticks = TickStore(config["workspacesDir"].parent / "historical_data")
    written: list[tuple[str, Path]] = []
    planned: set[Path] = set()

    for trip, tz, orders in load_trips(config):
        root = root_of(trip.symbol)
        instrument = config["instruments"].get(root)
        opened = datetime.fromtimestamp(trip.entries[0][0] / 1000, tz)
        account = config["accounts"].get(trip.account, trip.account)
        direction = "long" if trip.direction > 0 else "short"
        record = registered.get(trip.key)
        if record is None and opened.date() < config["since"]:
            continue
        link = note_link(record) if record is not None else None
        if record is not None and (link is None or (ROOT / record[link]).exists()):
            if record.get("pending") and instrument is not None:
                if fill_in(trip, record, orders, ticks, float(instrument["dollarPerPoint"]), dry_run):
                    save_state(state)
            continue

        existing = find_existing(notes, account, direction, opened, trip.entries[0][1])
        if existing:
            relative = existing.relative_to(ROOT).as_posix()
            if record is None:
                record = {"fills": trip.fill_ids, "at": datetime.now().isoformat(timespec="seconds"), "matched": relative}
                log.info("already in Journalit: %s %s %s -> %s", account, contract_of(trip.symbol), opened, relative)
            else:
                record[link] = relative
                log.info("moved, relinked: %s", relative)
            if not dry_run:
                registered[trip.key] = record
                save_state(state)
            continue
        if record is not None and trip.key not in restore:
            continue
        if instrument is None:
            log.warning("no instrument settings for %s (%s); add it in the plugin settings", root, trip.symbol)
            continue
        kind = "new" if record is None else "restored"

        day = opened.date()
        folder = week_dir(journal, day) / "trades"
        ticker = ticker_for_filename(instrument["name"])
        stamp = format_date(day, date_format)
        old = ROOT / record["path"] if record is not None and "path" in record else None
        if old is not None and old.parent == folder and old not in planned and not old.exists():
            path = old
        else:
            path = folder / f"{ticker}-{stamp}-T{next_trade_number(folder, ticker, stamp, planned)}.md"
        planned.add(path)
        drc = f"DRC-{stamp}"
        drc_link = f"[[{(week_dir(journal, day) / drc).relative_to(ROOT).as_posix()}|{drc}]]"
        found, pending = trade_details(trip, orders, ticks, float(instrument["dollarPerPoint"]))
        text = render_note(trip, tz, instrument, account, drc_link, (template_id, template_version), found)
        relative = path.relative_to(ROOT).as_posix()
        summary = ", ".join(f"{key} {number(value)}" for key, value in found.items()) or "no stop/MAE/MFE yet"

        if dry_run:
            log.info("would write (%s) %s (%s %s %s; %s)", kind, relative, account, direction, contract_of(trip.symbol), summary)
            written.append((kind, path))
            continue
        folder.mkdir(parents=True, exist_ok=True)
        with open(path, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        record = {"fills": trip.fill_ids, "at": datetime.now().isoformat(timespec="seconds"), "path": relative}
        if pending:
            record["pending"] = pending
        registered[trip.key] = record
        save_state(state)
        notes.append(JournalNote(path, account, direction, opened.replace(tzinfo=None), trip.entries[0][1]))
        written.append((kind, path))
        log.info("%s %s (%s %s %s; %s)", kind, relative, account, direction, contract_of(trip.symbol), summary)
    return written


# --------------------------------------------------------------------------- CLI


def acquire_lock():
    """Hold byte 0 of the lock file while syncing; None if another sync holds it."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_PATH, "a+")
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main() -> int:
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", type=Path, default=Path.cwd(), help="Obsidian vault folder (default: current folder)")
    parser.add_argument("--config", type=Path, help="settings JSON; the Obsidian plugin passes its data.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sync_cmd = sub.add_parser("sync", help="register closed trades once")
    sync_cmd.add_argument("--dry-run", action="store_true", help="print what would be written")
    sync_cmd.add_argument(
        "--restore", nargs="+", default=[], metavar="KEY", help="state keys of deleted trades to write again (see `deleted`)"
    )
    sub.add_parser("deleted", help="print, as JSON, the registered trades whose note was deleted")
    args = parser.parse_args()
    ROOT = args.vault.resolve()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=1, encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)

    config = load_config(args.config)
    if args.command == "deleted":
        print(json.dumps(deleted_trades(config)))
        return 0
    lock = acquire_lock()
    if lock is None:
        log.info("another sync is running; skipped")
        return 0
    written = sync(config, dry_run=args.dry_run, restore=frozenset(args.restore))
    for kind, path in written:
        print(f"{kind}\t{path.relative_to(ROOT).as_posix()}")
    restored = sum(kind == "restored" for kind, _ in written)
    log.info(
        "sync done: %d new, %d restored note(s)%s", len(written) - restored, restored, " (dry run)" if args.dry_run else ""
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
