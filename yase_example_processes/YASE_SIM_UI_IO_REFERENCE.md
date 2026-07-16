# YASE Simulatable UI and IO Reference

This document describes YASE statements that are **not hardware product modules** but still need simulation policy. They appear in production `.xseq` files and are handled explicitly by `yase_sim` (not reported as unmodeled errors).

For vision, alignment product modules, and machine-only `SEQ::` helpers, see the strict simulation policy in [`YASE_SIM_INTERPRETER.md`](YASE_SIM_INTERPRETER.md).

## Summary

| Statement | Library | Occurrences | Simulated? | Config keys |
|-----------|---------|-------------|------------|-------------|
| `DropDownDialog` | `product_modules\Functions\Display\` | 3 | Yes | `dropdown_selection`, `dropdown_selections` |
| `WaitDigIn` | `IO` | 1 | Yes | `digital_inputs` |
| `DisplayExtdDialog` | `Standard` | ~15 | Yes | `dialog_policy` (auto-OK) |
| `DisplayExtdSelectionDialog` | `Standard` | ~14 | Yes | `dialog_policy` |
| `UserDialog` | `Standard` | ~1–2 | Partial | process INI / future field overrides |
| `Positioning_OpenManuelMovePanel` | `Positioning` | ~6 | Partial | no-op (log only) |
| `ResolvePath` | `Standard` | few | Yes | returns repo root |
| `ReadDataFileString` | `DataFile` | few | Partial | fixture files under `examples/machine_exports/` |

## DropDownDialog

**What it does on the machine:** Shows a modal dialog with a drop-down list. The operator picks one item and clicks OK. The selected string is written to the `Selection` output parameter.

**Where it is used:**

- [`MAIN_PROCESS.xseq`](MAIN_PROCESS.xseq) — process type from `DropDownItems.txt` (`Title` = sequence name variable)
- [`SUB_Initializing/SUB_TIAInit_T.xseq`](SUB_Initializing/SUB_TIAInit_T.xseq) — `TIA1,TIA2,Abort` (`Title` = `TIASelect`)
- [`SUB_Initializing/SUB_CheckTIAOverload_T.xseq`](SUB_Initializing/SUB_CheckTIAOverload_T.xseq) — same TIA picker

**Simulation:**

```json
"dropdown_selection": "Cancel",
"dropdown_selections": {
  "TIASelect": "TIA1",
  "MAIN": "PickUpBallLens"
}
```

- `dropdown_selections` maps the dialog `Title` parameter to a fixed choice.
- Unknown titles fall back to `dropdown_selection`.

**MAIN_PROCESS note:** Items are loaded from `#SM_DATA#\DONOTDELETE\DropDownItems.txt` via `ReadDataFileString` before the dialog. Until that file is exported from the machine, provide a fixture or set `dropdown_selections` for the actual title string used at runtime.

## WaitDigIn

**What it does on the machine:** Polls a digital input line until it reaches the requested `State` (`On` or `Off`) or the timeout (ms) elapses.

**Output `Timeout` semantics (YASE naming):**

- `1` (true) → execute the **next** statement
- `0` (false) → **skip** the next statement

**Where it is used:**

- [`SUB_Calibrate_Needles.xseq`](SUB_Calibrate_Needles.xseq) — waits for `TipSensor` = `Off` after operator adjusts a needle, then either continues or `Goto` retries the adjustment loop.

**Simulation:**

```json
"digital_inputs": {
  "TipSensor": 0
}
```

- `0` = off, `1` = on.
- `TipSensor: 0` means the sensor reads Off immediately → skip the following `Goto` (exit retry loop).
- `TipSensor: 1` means still On → run the `Goto` (retry).

The simulator does not model polling delay; it evaluates the configured input state once per statement.

## DisplayExtdDialog

**Machine behavior:** Single-button modal (“Ok”). Blocks until dismissed.

**Simulation:** Auto-OK. No warning. Optional trace when `--trace` is enabled.

## DisplayExtdSelectionDialog

**Machine behavior:** Two buttons. If the operator picks the “cancel/skip” branch, the **next** statement is skipped (same skip-next-line pattern as `WaitDigIn` false).

**Simulation:**

```json
"dialog_policy": "button2"
```

| Policy | Behavior |
|--------|----------|
| `button2`, `skip`, `move`, `continue` | Skip next statement (take branch that avoids following `Goto`/action) |
| `button1`, `ok`, `abort` | Execute next statement |

## UserDialog

**Machine behavior:** Form defined in `Processvar.ini` / `UserDialog` INI sections (operator name, serial numbers, tray number, etc.).

**Simulation:** Currently auto-OK like `DisplayExtdDialog`. Field values are not yet driven from config; extend `yase_sim_config.json` with per-field overrides when needed.

## Positioning_OpenManuelMovePanel

**Machine behavior:** Opens a manual jog panel so the operator can fine-tune position (e.g. pickup tower).

**Simulation:** No-op. Sequences that depend on operator jogging cannot be fully automated until jog targets are modeled or taught positions in `Processvar.ini` are accurate.

## ResolvePath / ReadDataFileString

**Machine behavior:** Resolves TestMaster data paths and reads string data from machine files.

**Simulation:**

- `ResolvePath` → repository root (development default).
- `ReadDataFileString` → empty string unless a fixture is added under `examples/machine_exports/`.

## Related digital IO (not dialogs)

Standard `GetDigIn` / `SetDigOut` / `GetDigOut` use the same `digital_inputs` / `digital_outputs` maps in config. These are fully modeled and are not “unmodeled hardware.”

## See also

- [`examples/yase_sim_config.json`](examples/yase_sim_config.json) — runnable sim config
- [`YASE_SIM_INTERPRETER.md`](YASE_SIM_INTERPRETER.md) — full interpreter policy and SEQ:: resolution
- [`tools/extract_yase_machine_inventory.py`](tools/extract_yase_machine_inventory.py) — `sequence_call_resolution` manifest (repo `.xseq` vs machine-only)
