# Yase Simulation Interpreter

This package executes the relevant subset of Yase `.xseq` files against a local simulation state.

It currently supports:

- XML `.xseq` parsing.
- Labels, `Goto`, `ifnum`, `ifstring`, `BEGIN`, `ELSE`, `END`, and `EndSeq`.
- Disabled/commented Yase rows represented as `Label="*"` or `//` labels in XML.
- Local sequence variables.
- `DeclareNumParam`, `DeclareStrParam`, `ReturnNumParam`, and `ReturnStrParam`.
- Basic standard statements: `SetString`, `set`, `calc`, `NumToString`, `StringToNum`, `InRange`, dialogs, timers, and status output.
- INI variable access through `GetNumVar`, `SetNumVar`, `GetStringVar`, `SetStringVar`, and `KeyAvailable`.
- Stage simulation through `StageCheckAllFiducialed`, `MoveStage`, `QueryStage`, `SUB_SYS_AxisWaitFinishList`, and `SUB_SysCheckAxisMove`.
- IO simulation through `SetDigOut`, `GetDigOut`, `GetDigIn`, `SetAnalogOut`, and `GetAnalogIn`.
- Power simulation through `GetPower` and `SUB_SysReadAveragePower`.
- Process subsequence calls when the target `.xseq` can be found locally.

## Axis Convention

The real Yase/TestMaster machine uses:

```text
machine X/Z plane = transverse plane
machine Y         = laser direction
```

The simulation uses:

```text
simulation x/y plane = transverse plane
simulation z         = laser direction
```

The interpreter therefore maps stage axes as:

```text
machine X -> simulation x
machine Z -> simulation y
machine Y -> simulation z
```

Example:

```text
MoveStage Align_X1 Relative +5 -> actor Align1 x += 5
MoveStage Align_Z1 Relative +5 -> actor Align1 y += 5
MoveStage Align_Y1 Relative +5 -> actor Align1 z += 5
```

## Rotation Convention

Body frame uses **+Z as the nose** (forward along the optical axis). Nose aligns with machine **+Y** and simulation **+z**.

| Yase axis | Rotation | About axis |
|---|---|---|
| `Align_Rolln` | Roll | Body +Z (nose / optical) |
| `Align_Pitchn` | Pitch | Body +X (machine X) |
| `Align_Yawn` | Yaw | Body +Y (machine Z) |

Units are degrees. See [`YASE_MACHINE_CONVENTIONS.md`](../yase_process/YASE_MACHINE_CONVENTIONS.md) for holder numbering and IO mapping.

Rotary positions are stored in `stage_positions` but no optical geometry is inferred from them yet.

## Holder And Peripheral Numbering

**1 = left**, **2 = right** for lenses, grippers, vacuum, and numbered add-ons.

```text
holder 1 -> Align1, Gripper1, vacuum helper Gripper, Gripper1_Pressure
holder 2 -> Align2, Gripper2, vacuum helper CH2, Gripper2_Pressure
```

## TIA

TIA readings represent **optical power received** (mW). Channel routing is configured via `[Alignment]` process variables or `process_variables.Alignment` in the sim config.

## Gaussian Sim Project Layout

This migrated copy lives inside the Gaussian simulator project:

```text
yase_sim/      Python interpreter package
yase_process/  copied YASE process root, subprocesses, INI/config files
```

The alignment lab discovers `yase_process/**/*.xseq` files and exposes them as
`YASE: ...` choices in the algorithm dropdown. `Run algorithm` executes the
subprocess against the current lens simulation. `Show` replays the resulting
stage moves in the canvas.

## Running A Sequence

From the Gaussian sim project root:

```powershell
python -m yase_sim SUB_Positioning\SUB_Test_DrawCircle_AlignX1Z1.xseq --root yase_process --config yase_process\examples\yase_sim_config.json --json
```

With trace:

```powershell
python -m yase_sim SUB_Positioning\SUB_Test_DrawCircle_AlignX1Z1.xseq --root yase_process --config yase_process\examples\yase_sim_config.json --trace
```

With an input parameter for a subsequence:

```powershell
python -m yase_sim SUB_Positioning\SUB_MoveFiberByOffset_FA.xseq --root yase_process --config yase_process\examples\yase_sim_config.json --param "Select FA[HFA, VFA]=LA_Wide"
```

## Original Batch Harness

The original Microcombsys checkout kept a larger batch harness for every
production `.xseq`. In this migrated Gaussian project, use the Gaussian pytest
suite for the active integration checks.

Production `.xseq` files are split into simulation tiers (see [`tests/xseq_simulatable.json`](../tests/xseq_simulatable.json) and [`tests/xseq_expected_failures.json`](../tests/xseq_expected_failures.json)):

| Tier | Policy |
|------|--------|
| **A** | Zero warnings — motion, power, and data-handling sequences that do not call unmodeled hardware |
| **B** | Must report an **unmodeled** warning at the documented first failure point |
| **C** | Other sequences (e.g. read-before-assign on optional vars) — informational only |

```powershell
python -m pytest tests\test_yase_integration.py
```

Run the full Gaussian test suite:

```powershell
python -m pytest
```

The original batch expectation files remain in the source Microcombsys checkout,
not in this migrated Gaussian app copy.

UI/IO simulation (`DropDownDialog`, `WaitDigIn`, dialogs) is documented in [`YASE_SIM_UI_IO_REFERENCE.md`](../yase_process/YASE_SIM_UI_IO_REFERENCE.md).

## Honest Simulation Policy

- **Repo `.xseq` subprocesses** — if `Callee.xseq` exists anywhere in this repository, `SEQ::Callee` is executed recursively.
- **Explicit native handlers** — `MoveStage`, gripper/vacuum, `SEQ::SUB_SysReadAveragePower`, axis-wait sync, etc.
- **Unmodeled** — vision (`Grab`, `VA_TM_*`, `AdvAlign_*`, `Metrology*`, `VIS_*`), and machine-only `SEQ::SUB_SYS_*` calls with no repo file → **warning** (or `NotImplementedError` with `strict_unknown=True`). No fake success outputs.

## Simulation Config

The interpreter reads `Processvar.ini` by default, but this repository does not include every runtime value used by the sequences. In particular, the checked-in files do not include a full `systemvar.ini`.

Use the JSON config to provide:

- `system_variables`, especially `[MainVelocity]`.
- Initial stage positions.
- Digital input states, such as vacuum OK sensors.
- Analog input states, such as force or pressure.
- Simulated power meter models.
- Dialog policy.
- `dropdown_selection` and per-title `dropdown_selections` for `DropDownDialog`.

The included `examples/yase_sim_config.json` is a runnable development config, not a machine-validated recipe.

## Power Model

Static meter:

```json
"TIA1": {"model": "static", "mw": 0.1}
```

Gaussian meter:

```json
"TIA1": {
  "model": "gaussian",
  "peak_mw": 1.0,
  "floor_mw": 0.001,
  "actor": "Align1",
  "target": {"x": 0.0, "y": 0.0, "z": 0.0},
  "sigma_um": {"x": 10.0, "y": 10.0, "z": 100.0}
}
```

For a quick internal two-lens placeholder, use the `actors` form:

```json
"TIA1": {
  "model": "gaussian",
  "peak_mw": 1.0,
  "floor_mw": 0.001,
  "actors": {
    "Align1": {
      "target": {"x": 0.0, "y": 0.0, "z": 0.0},
      "sigma_um": {"x": 10.0, "y": 10.0, "z": 100.0}
    },
    "Align2": {
      "target": {"x": 0.0, "y": 0.0, "z": 0.0},
      "sigma_um": {"x": 10.0, "y": 10.0, "z": 100.0}
    }
  }
}
```

This is only a generic optical objective function.

For the actual two-ball-lens experiment, use the external Gaussian sim model:

```json
"power": {
  "gaussian_sim_path": "..",
  "TIA1": {
    "model": "external_gaussian_ball_lens",
    "lens_actor_map": ["Align1", "Align2"],
    "stage_um_to_m": 0.000001
  },
  "TIA2": {
    "model": "external_gaussian_ball_lens",
    "lens_actor_map": ["Align1", "Align2"],
    "stage_um_to_m": 0.000001
  }
}
```

For command-line runs, this imports `interactive_setup.py` from the local Gaussian sim checkout and evaluates its default two-ball-lens SiN taper setup. The GUI alignment adapter bypasses this import path and measures power directly from the current in-memory lab layout. The Yase machine-stage coordinates are treated as relative offsets from the nominal Gaussian layout:

```text
Align_Xn -> Gaussian ball n x_offset
Align_Zn -> Gaussian ball n y_offset
Align_Yn -> Gaussian ball n z position
```

All Yase stage values are in um and are converted to metres before calling the Gaussian sim.

## Manual Machine Setup Values

Some real machine setup is manual, such as TIA channel choice. Represent those choices in the simulation config as process-variable overrides:

```json
"process_variables": {
  "Alignment": {
    "TIA_Lo": "TIA1",
    "TIA_Tx": "TIA1",
    "TIA_Rx": "TIA2"
  }
}
```

The same config records confirmed holder mapping:

```json
"holder_map": {
  "1": {
    "lens": "leftmost_ball_lens",
    "actor": "Align1",
    "gripper": "Gripper1",
    "vacuum": "Vacuum1"
  }
}
```

User-confirmed fact: gripper/vacuum 1 is the left-most ball lens.

## Missing Machine-Specific Information

The interpreter can run the control logic, but the simulator still needs machine-verified values for:

- IO polarity and pressure thresholds for gripper/vacuum hold confirmation.
- Real `systemvar.ini` `[MainVelocity]` values (checkout uses development placeholders).
- Machine-validated `[Alignment]` TIA and optic-switch routing.
- Optical effect of roll/pitch/yaw in the power model.
- Camera calibration, vision models, and `.set` scan parameters.
- Safe travel limits and collision volumes.
- Hidden module behavior (`AdvAlign_SpiralScan`, `MetrologyLineScan`, etc.).

Confirmed conventions are documented in [`YASE_MACHINE_CONVENTIONS.md`](../yase_process/YASE_MACHINE_CONVENTIONS.md).
