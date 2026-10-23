"""Engine tests on a synthetic MotiveWave install and Obsidian vault.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import shutil
import struct
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import motivewave_journalit as mj  # noqa: E402

ET = ZoneInfo("America/New_York")
SYMBOL = "NQZ6.CME.RITHMIC"
NOTE = "Journal/2026/Q4/10/W43/trades/NQ1-221026-T1.md"


def ms(hour: int, minute: int, second: int, day: int = 22) -> int:
    return int(datetime(2026, 10, day, hour, minute, second, tzinfo=ET).timestamp() * 1000)


def base32(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuv"
    out = ""
    while value:
        out, value = digits[value % 32] + out, value // 32
    return out


class Fixture:
    """A vault with Journalit settings, and a MotiveWave folder with one Rithmic workspace."""

    def __init__(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.vault = self.base / "vault"
        settings = self.vault / ".obsidian" / "plugins" / "journalit"
        settings.mkdir(parents=True)
        (settings / "data.json").write_text(
            json.dumps(
                {
                    "general": {"journalFolderPath": "Journal"},
                    "trade": {"dateFormat": "DDMMYY"},
                    "templates": {"defaultTrade": "tpl"},
                    "reviewV2": {"tradeTemplates": [{"id": "tpl", "version": 10}]},
                }
            ),
            encoding="utf-8",
        )
        self.workspace = self.base / "MotiveWave" / "workspaces" / "ws1" / "config"
        self.workspace.mkdir(parents=True)
        (self.workspace / "config.json").write_text(json.dumps({"timeZone": "America/New_York"}), encoding="utf-8")
        self.ticks = self.base / "MotiveWave" / "historical_data" / "RITHMIC" / "NQZ6.CME"
        self.ticks.mkdir(parents=True)
        self.fills: list[str] = []
        self.orders: list[list[str]] = []
        mj.ROOT = self.vault
        mj.STATE_DIR = self.base / "state"
        self.settings = {"workspacesDir": str(self.base / "MotiveWave" / "workspaces"), "since": "2026-10-01"}

    def close(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def config(self, **overrides) -> dict:
        path = self.base / "settings.json"
        path.write_text(json.dumps({**self.settings, **overrides}), encoding="utf-8")
        return mj.load_config(path)

    def fill(self, fill_id: str, order_id: str, side: str, when: int, price: float, qty: int = 1, account: str = "simulated"):
        key = {"simulated": "a", "LIVE-1": "b"}[account]
        self.fills.append(f"{fill_id},1,,{key},,{order_id},,,{side},N,{base32(when)},{price},{qty},0,0,0")
        self.orders.append(
            [str(when), "ORDER", "ORDER_FILLED", "", "", SYMBOL, account, order_id, "", "", str(when), "MKT",
             "BUY" if side == "B" else "SELL", "FILLED", str(price), str(qty), "DAY", str(qty), "", str(price), str(when), "x"]
        )

    def stop_order(self, order_id: str, side: str, when: int, price: float, account: str = "simulated"):
        self.orders.append(
            [str(when), "ORDER", "ORDER_MODIFIED", "", "", SYMBOL, account, order_id, "", "", str(when), "STP",
             "BUY" if side == "B" else "SELL", "SUBMITTED", str(price), "1", "DAY", "", "1", "", "", "x"]
        )

    def save(self, with_journal: bool = True) -> None:
        header = f"1={SYMBOL},a=simulated,b=LIVE-1"
        (self.workspace / "trade_history.csv").write_text("\n".join([header, *self.fills]) + "\n", encoding="utf-8")
        journal = self.workspace / "journal.csv"
        if with_journal:
            lines = ["Time, Type, Action, Connection, Symbol, Exchange, OrderId, Order Time, Order Status, Message"]
            lines += [",".join(row) for row in sorted(self.orders, key=lambda row: int(row[0]))]
            journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif journal.exists():
            journal.unlink()

    def tick_hour(self, hour_start: int, ticks: list[tuple[int, float]]) -> None:
        """One hour of ticks in MotiveWave's layout (see TickStore)."""
        last = ticks[-1][0]
        data = struct.pack(">IiB", 0x3281BB98, 426, 40)
        data += struct.pack(">qqqq", hour_start, hour_start + mj.HOUR_MS, ticks[0][0], last)
        data += struct.pack(">h", len(mj.TICK_COLUMNS))
        data += b"".join(struct.pack(">hh", *column) for column in mj.TICK_COLUMNS)
        data += b"\x00\x00"
        for when, price in ticks:
            data += mj.TICK_RECORD.pack(when - hour_start, price, 1, 1, price - 0.25, price, 1.0, 1.0, 0, 0)
        (self.ticks / f"{hour_start}.tick_data").write_bytes(data)

    def losing_long(self) -> None:
        """Long 1 NQ at 29,552.50, stop 29,534 placed after entry, stopped out at 29,533.75."""
        self.fill("SIM-1", "SIM-1", "B", ms(9, 58, 34), 29552.5)
        self.stop_order("SIM-2", "S", ms(9, 58, 40), 29534)
        self.fill("SIM-2", "SIM-2", "S", ms(10, 0, 7), 29533.75)

    def losing_long_ticks(self) -> None:
        self.tick_hour(ms(9, 0, 0), [(ms(9, 58, 34), 29552.5), (ms(9, 59, 0), 29570.25), (ms(9, 59, 30), 29534.25)])
        self.tick_hour(ms(10, 0, 0), [(ms(10, 0, 7), 29533.75), (ms(10, 5, 0), 29540.0)])

    def note(self, relative: str = NOTE) -> dict[str, str]:
        text = (self.vault / relative).read_text(encoding="utf-8")
        front = text.split("---")[1]
        return {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in front.splitlines() if line and not line.startswith(" ")}


class RoundTrips(unittest.TestCase):
    def test_scale_in_and_reversal_split_into_flat_to_flat_trades(self):
        fill = lambda i, side, t, p, q: mj.Fill("w", str(i), str(i), "a", SYMBOL, side, t, p, q)  # noqa: E731
        trips = mj.round_trips([fill(1, 1, 1, 100.0, 1), fill(2, 1, 2, 101.0, 1), fill(3, -1, 3, 105.0, 3), fill(4, 1, 4, 103.0, 1)])
        self.assertEqual([t.direction for t in trips], [1, -1])
        self.assertEqual(trips[0].exits, [(3, 105.0, 2.0)])
        self.assertEqual(trips[1].entries, [(3, 105.0, 1.0)])
        self.assertEqual(mj.gross_pnl(trips[0], 20), 180.0)

    def test_open_position_is_not_a_trade(self):
        fill = mj.Fill("w", "1", "1", "a", SYMBOL, 1, 1, 100.0, 2)
        self.assertEqual(mj.round_trips([fill]), [])


class Sync(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def test_writes_note_with_stop_r_mae_mfe_where_journalit_expects_it(self):
        self.f.losing_long()
        self.f.losing_long_ticks()
        self.f.save()
        written = mj.sync(self.f.config(accounts={"simulated": "Forward Testing"}))
        self.assertEqual([(kind, p.relative_to(self.f.vault).as_posix()) for kind, p in written], [("new", NOTE)])
        note = self.f.note()
        self.assertEqual(note["account"], "")  # list follows on the next line
        self.assertIn("  - Forward Testing", (self.f.vault / NOTE).read_text(encoding="utf-8"))
        self.assertEqual(
            {k: note[k] for k in ("instrument", "direction", "pnl", "stopLoss", "rMultiple", "mae", "mfe", "templateId")},
            {"instrument": "NQ1", "direction": "long", "pnl": "-375", "stopLoss": "29534", "rMultiple": "-1.0135",
             "mae": "-365", "mfe": "355", "templateId": "tpl"},
        )
        self.assertIn("  - time: 2026-10-22T09:58:34", (self.f.vault / NOTE).read_text(encoding="utf-8"))

    def test_second_sync_writes_nothing_and_numbers_continue_per_day(self):
        self.f.losing_long()
        self.f.save()
        config = self.f.config()
        mj.sync(config)
        self.assertEqual(mj.sync(config), [])
        self.f.fill("SIM-3", "SIM-3", "S", ms(11, 0, 0), 29500.0)
        self.f.fill("SIM-4", "SIM-4", "B", ms(11, 5, 0), 29490.0)
        self.f.save()
        written = mj.sync(config)
        self.assertEqual([p.name for _, p in written], ["NQ1-221026-T2.md"])

    def test_trade_already_entered_by_hand_is_linked_not_duplicated(self):
        manual = self.f.vault / "Journal/2026/Q4/10/W43/trades/NQ1-221026-T1.md"
        manual.parent.mkdir(parents=True)
        manual.write_text(
            "---\ntype: trade\ndirection: long\nentries:\n  - time: 2026-10-22T09:58:00\n    price: 29552.5\n"
            "account:\n  - Simulated\n---\nmy notes\n",
            encoding="utf-8",
        )
        self.f.losing_long()
        self.f.save()
        self.assertEqual(mj.sync(self.f.config(accounts={"simulated": "Simulated"})), [])
        self.assertEqual(manual.read_text(encoding="utf-8").splitlines()[-1], "my notes")

    def test_trades_before_since_are_ignored(self):
        self.f.losing_long()
        self.f.save()
        self.assertEqual(mj.sync(self.f.config(since="2026-10-23")), [])

    def test_unsaved_details_are_added_later_without_overwriting_the_trader(self):
        self.f.losing_long()
        self.f.save(with_journal=False)  # neither order log nor ticks saved yet
        config = self.f.config()
        mj.sync(config)
        self.assertNotIn("mae", self.f.note())
        path = self.f.vault / NOTE
        path.write_text(path.read_text(encoding="utf-8").replace("fees: 0\n", "fees: 0\nstopLoss: 29540\n"), encoding="utf-8")

        self.f.losing_long_ticks()
        self.f.save()
        mj.sync(config)
        note = self.f.note()
        self.assertEqual((note["stopLoss"], note["rMultiple"], note["mae"], note["mfe"]), ("29540", "0", "-365", "355"))
        self.assertTrue(path.read_text(encoding="utf-8").endswith("<!-- User Notes Below -->\n"))

    def test_tick_hour_ending_before_the_exit_leaves_mae_pending(self):
        self.f.losing_long()
        self.f.tick_hour(ms(9, 0, 0), [(ms(9, 58, 34), 29552.5)])
        self.f.tick_hour(ms(10, 0, 0), [(ms(10, 0, 1), 29540.0)])  # file stops before the 10:00:07 exit
        self.f.save()
        mj.sync(self.f.config())
        self.assertNotIn("mae", self.f.note())
        self.assertEqual(self.f.note()["stopLoss"], "29534")


class DeletedNotes(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.f.losing_long()
        self.f.fill("SIM-3", "SIM-3", "S", ms(11, 0, 0), 29500.0)
        self.f.fill("SIM-4", "SIM-4", "B", ms(11, 5, 0), 29490.0)
        self.f.save()
        self.config = self.f.config()
        mj.sync(self.config)
        self.trades = self.f.vault / "Journal/2026/Q4/10/W43/trades"

    def tearDown(self):
        self.f.close()

    def test_deleted_notes_stay_deleted_on_plain_sync_and_are_listed(self):
        (self.trades / "NQ1-221026-T1.md").unlink()
        (self.trades / "NQ1-221026-T2.md").unlink()
        self.assertEqual(mj.sync(self.config), [])
        listed = mj.deleted_trades(self.config)
        self.assertEqual([item["note"].rsplit("/", 1)[1] for item in listed], ["NQ1-221026-T2.md", "NQ1-221026-T1.md"])
        self.assertEqual((listed[1]["direction"], listed[1]["pnl"], listed[1]["opened"]), ("long", -375.0, "2026-10-22 09:58:34"))

    def test_only_selected_deleted_notes_are_restored_under_their_old_names(self):
        (self.trades / "NQ1-221026-T1.md").unlink()
        (self.trades / "NQ1-221026-T2.md").unlink()
        first = next(item for item in mj.deleted_trades(self.config) if item["note"].endswith("T1.md"))
        written = mj.sync(self.config, restore=frozenset([first["key"]]))
        self.assertEqual([(kind, p.name) for kind, p in written], [("restored", "NQ1-221026-T1.md")])
        self.assertEqual([item["note"].rsplit("/", 1)[1] for item in mj.deleted_trades(self.config)], ["NQ1-221026-T2.md"])

    def test_renamed_note_is_relinked_not_listed_or_duplicated(self):
        (self.trades / "NQ1-221026-T1.md").rename(self.trades / "NQ1-221026-T7.md")
        self.assertEqual(mj.deleted_trades(self.config), [])
        keys = frozenset(item["key"] for item in mj.deleted_trades(self.config))
        self.assertEqual(mj.sync(self.config, restore=keys), [])
        self.assertEqual(sorted(p.name for p in self.trades.iterdir()), ["NQ1-221026-T2.md", "NQ1-221026-T7.md"])

    def test_state_is_per_vault(self):
        mj.ROOT = self.f.base / "other-vault"
        (mj.ROOT / ".obsidian").mkdir(parents=True)
        self.assertEqual(mj.deleted_trades(self.config), [])
        self.assertEqual(mj.load_state(), {"version": 1, "registered": {}})


class Settings(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def test_instrument_override_and_bad_instrument(self):
        config = self.f.config(instruments={"nq": {"name": "NQ", "dollarPerPoint": 20, "tickSize": 0.25}})
        self.assertEqual(config["instruments"]["NQ"]["name"], "NQ")
        self.assertEqual(config["instruments"]["MNQ"]["name"], "MNQ")
        with self.assertRaises(ValueError):
            self.f.config(instruments={"NQ": {"name": "NQ"}})

    def test_journal_folder_comes_from_journalit_unless_set(self):
        self.assertEqual(self.f.config()["journalRoot"], self.f.vault / "Journal")
        self.assertEqual(self.f.config(journalFolder="Trades")["journalRoot"], self.f.vault / "Trades")


if __name__ == "__main__":
    unittest.main()
