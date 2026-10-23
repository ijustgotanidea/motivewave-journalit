# Changelog

## 1.0.0 — 2026-10-23

First public release.

- Registers closed MotiveWave trades, from the simulated account and from live accounts, as Journalit trade
  notes, in Journalit's folder layout and note format.
- Fills in the initial stop, R, MAE and MFE from MotiveWave's order log and tick cache. Details that are not
  saved yet are retried for three days.
- Links trades already entered by hand instead of duplicating them.
- Syncs when Obsidian opens. Force sync lists deleted trades and restores the ones you tick.
- Settings for accounts, instruments, start date, folders and the Python command. English and Portuguese
  interface.
- Windows, macOS and Linux: MotiveWave's data folder is found where MotiveWave itself keeps it on each system,
  and on macOS Python is found in Homebrew, python.org and Command Line Tools installs even when Obsidian is
  started from the Dock. macOS has not been run on real hardware yet.
