# Yase Simulation Interpreter

The simulation interpreter is implemented in `yase_sim/`.

Run it from this process directory:

```powershell
python -m yase_sim SUB_Positioning\SUB_Test_DrawCircle_AlignX1Z1.xseq --config examples\yase_sim_config.json --json
```

The example config is:

```text
examples/yase_sim_config.json
```

The config supplies simulation-only values that are not fully present in the checked-in process files, such as system velocities, initial stage positions, digital/analog inputs, and a simple power model.

## What It Interprets

The interpreter reads real Yase `.xseq` XML files. It does not require a separate pseudo-language.

Supported sequence behavior:

- Labels and `Goto`.
- `ifnum`, `ifstring`, `BEGIN`, `ELSE`, `END`.
- Single-line Yase `if/ELSE` patterns.
- `EndSeq`.
- Disabled/commented Yase rows represented in XML as `Label="*"` or `//` labels are skipped.
- Local sequence variables.
- Input/output parameters with `DeclareNumParam`, `DeclareStrParam`, `ReturnNumParam`, and `ReturnStrParam`.
- Process subsequence calls when the target `.xseq` exists locally.

Supported standard statements:

- `SetString`
- `DisplayStatus`
- `DisplayDialog`
- `DisplayExtdSelectionDialog`
- `GetTimer`
- `set`
- `calc`
- `NumToString`
- `StringToNum`
- `InRange`
- `Delay`

Supported variable/INI statements:

- `GetNumVar`
- `SetNumVar`
- `GetStringVar`
- `SetStringVar`
- `KeyAvailable`

Supported stage/motion statements:

- `StageCheckAllFiducialed`
- `MoveStage`
- `QueryStage`
- `SEQ::SUB_SYS_AxisWaitFinishList`
- `SEQ::SUB_SysCheckAxisMove`

Supported IO statements:

- `SetDigOut`
- `GetDigOut`
- `GetDigIn`
- `SetAnalogOut`
- `GetAnalogIn`
- `SEQ::SUB_SYS_Gripper_OpenClose`
- `SEQ::SUB_SYS_Vacuum_OnOff`

Supported power/TIA statements:

- `GetPower`
- `SEQ::SUB_SysReadAveragePower`
- `TIARange`

Power reads can use either a simple built-in model or the external Gaussian two-ball-lens simulator. The example config uses the external model for `TIA1` and `TIA2`.

## Axis Mapping

The requested simulation convention is different from the machine convention.

Machine/Yase:

```text
X/Z plane = transverse plane
Y         = laser direction
```

Simulation:

```text
x/y plane = transverse plane
z         = laser direction
```

Implemented mapping:

```text
machine X -> simulation x
machine Z -> simulation y
machine Y -> simulation z
```

Examples:

```text
MoveStage Align_X1 Relative +5 -> simulation actor Align1 x += 5
MoveStage Align_Z1 Relative +5 -> simulation actor Align1 y += 5
MoveStage Align_Y1 Relative +5 -> simulation actor Align1 z += 5
```

The interpreter records both:

- raw Yase stage positions, for example `Align_Z1 = -2000`;
- simulation actor positions, for example `Align1.y = -2000`.

## Gaussian Experiment Model

The interpreter can now use the Gaussian simulation in:

```text
C:\Users\chenj\OneDrive - Imperial College London\Imec\Coding\Gaussian sim
```

The relevant Gaussian sim facts are:

- `interactive_setup.py` defines the default experiment.
- The source is an 808 nm elliptical Gaussian waist.
- Source power defaults to 300 mW.
- The detector is a SiN inverse taper.
- There are two 500 um sapphire ball lenses.
- Default geometry is:
  - laser waist to ball 1 front: 39 um;
  - ball 1 thickness: 500 um;
  - ball 1 back to ball 2 front: 200 um;
  - ball 2 thickness: 500 um;
  - ball 2 back to taper: 39 um.
- The default final taper plane is at 1278 um.
- The default aligned received power is about 77.07 mW.

The Yase simulator maps Yase stages into the Gaussian sim lens poses as relative offsets from the nominal Gaussian layout:

```text
Align_X1 -> Gaussian ball 1 x_offset
Align_Z1 -> Gaussian ball 1 y_offset
Align_Y1 -> Gaussian ball 1 position

Align_X2 -> Gaussian ball 2 x_offset
Align_Z2 -> Gaussian ball 2 y_offset
Align_Y2 -> Gaussian ball 2 position
```

The conversion is:

```text
Yase stage value in um * 1e-6 -> Gaussian sim metres
```

This is configured in `examples/yase_sim_config.json`:

```json
"power": {
  "gaussian_sim_path": "..\\..\\Gaussian sim",
  "TIA1": {
    "model": "external_gaussian_ball_lens",
    "lens_actor_map": ["Align1", "Align2"],
    "stage_um_to_m": 0.000001
  }
}
```

## Manual TIA Assignment

You confirmed that some TIA setup is manual on the machine. In the simulator, that means the Yase sequence may read TIA names from process variables even though the operator would normally choose or configure them in the real UI.

The example config now injects this into the simulated process INI:

```json
"process_variables": {
  "Alignment": {
    "TIA_Lo": "TIA1",
    "TIA_Tx": "TIA1",
    "TIA_Rx": "TIA2"
  }
}
```

This is not claiming those are the final real channel assignments. It is the simulation representation of "the user has manually selected these TIA channels before running the sequence."

## Holder, Axis, Gripper, And Vacuum Mapping

You confirmed:

```text
holder/lens 1 = left-most ball lens
holder/lens 2 = right-most ball lens
gripper/vacuum 1 = left-most ball lens
the current machine-to-simulation axis signs are correct
```

The example config records that as:

```json
"holder_map": {
  "1": {
    "lens": "leftmost_ball_lens",
    "actor": "Align1",
    "gripper": "Gripper1",
    "vacuum": "Vacuum1"
  },
  "2": {
    "lens": "rightmost_ball_lens",
    "actor": "Align2",
    "gripper": "Gripper2",
    "vacuum": "Vacuum2"
  }
}
```

This is enough for the simulator to label which held object belongs to each holder and to keep the confirmed stage sign convention. It is not yet enough to simulate real pneumatic behavior, because the actual digital output line names and sensor thresholds still need to be verified.

## MainVelocity

`MainVelocity` is the system-variable section that stores shared speed settings. Existing Yase files read it like this:

```text
GetNumVar System "" MainVelocity VelocityAlignXSlow -> d_Vel_Align_XSlow
MoveStage Align_X1 Velocity [um/s]=d_Vel_Align_XSlow ...
```

So `MainVelocity` is not an alignment threshold. It is a central speed table in `systemvar.ini`. The checked-in repo does not include the real machine `systemvar.ini`, so the simulator config supplies placeholder values for testing.

Common keys used by current subprocesses include:

```text
VelocityAlignXSlow
VelocityAlignSlow
VelocityAlignMedium
VelocityAlignFast
VelocityAlignXFast
VelocityAlignXXFast
VelocityCameraXSlow
VelocityCameraSlow
VelocityCameraMedium
VelocityCameraFast
VelocityCameraXFast
VelocityRotSlow
VelocityRotMedium
VelocityRotFast
VelocityRotXFast
VelocityZoom
```

Examples:

- `SUB_Positioning/SUB_MoveToPickup_BallLens.xseq` reads camera and align velocities, then uses them for `Zoom`, `Camera_*`, and `Align_*` moves.
- `SUB_Positioning/SUB_MoveFiberByOffset_FA.xseq` reads `VelocityAlignMedium`, then uses it for relative `Align_Y2` and `Align_Z2` offsets.
- `SUB_Positioning/SUB_Test_DrawCircle_AlignX1Z1.xseq` reads `VelocityAlignXSlow`, then uses it for the small circle test.

## Visible Thresholds And Limits

These are thresholds visible in the checked-in `.xseq` and `.ini` files. Values stored only in the live machine `systemvar.ini` or generated runtime process variables still need to be read from the real Yase system.

### Interlocks

- `SUB_Initializing/SUB_MainInitEquipment.xseq` checks `AirPressure_OK == 1`.
- `SUB_Initializing/SUB_MainInitEquipment.xseq` checks `Vacuum_OK == 1`.
- Many sequences check `StageCheckAllFiducialed` or `StateAxisHoming` before moving.

### TIA

- `SUB_Initializing/SUB_CheckTIAOverload_T.xseq` declares `SwitchLimitAnalog = 5.0`.
- `SUB_Initializing/SUB_TIAInit_T.xseq` declares `SwitchLimitAnalog = 5.0`.
- `Processvar.ini [MainInitEquipment]` has `TIA1Range = 5`, `TIA2Range = 5`, `TIA1Wavelength = 1550`, `TIA2Wavelength = 1500`, and TIA polarity settings.

### Alignment And Touchdown

- `SUB_Alignment/SUB_MainAlignmentHFA.xseq` sets `TIARange` gain to `3.0`.
- `SUB_Alignment/SUB_MainAlignmentHFA.xseq` calls `SUB_SYS_DMS_Touchdown_Universal_Alignment` with `Align_Y2`, `Force2`, and step-up `15.0 um`.
- `SUB_Positioning/SUB_Move_Fixing.xseq` calls `SUB_SYS_DMS_Touchdown_Universal` with `Align_Y1`, `Force1`, and step-up `250.0 um`.
- `SUB_Process/SUB_MainPickupLA.xseq` calls `SUB_SYS_DMS_Touchdown_Universal` with `Align_Y2`, `Force2`, and step-up `10.0 um`.
- `SUB_Alignment/SUB_MainAlignmentHFA.xseq` and `SUB_Process/SUB_Main_Process_HFA.xseq` call `AdvAlign_SpiralScan` with `Threshold optional = 10.0`.
- `SpiralScan.set` contains the named presets such as `TIA1_RoughSpiralScan` and `TIA1_FineSpiralScan`, but it is a binary/LabVIEW-style parameter file. The explicit `.xseq` `Threshold optional = 10.0` is the reliable visible value here; the remaining preset fields should be read through Yase/TestMaster rather than hand-decoded.

### Final Power Checks

`SUB_DataHandling/SUB_ReadFinalPower.xseq` reads these runtime process values from `[ProcessData]`:

```text
PowerLo_Dispensed
PowerRx_Dispensed
PowerTx_Dispensed
FinalPowerTarget_Lo
FinalPowerTarget_Rx
FinalPowerTarget_Tx
FinalPowerChangeRate
```

It checks:

```text
PowerLo_Final >= FinalPowerTarget_Lo
PowerRx_Final >= FinalPowerTarget_Rx
PowerTx_Final >= FinalPowerTarget_Tx
ChangeRate_Lo and ChangeRate_Rx in range [1 - FinalPowerChangeRate, 2.0]
reporting check for Lo and Rx uses [1 - FinalPowerChangeRate, 5.0]
```

The Tx final-power threshold checks are active, but the Tx change-rate `InRange` rows are disabled in the XML with `Label="*"`. In `SUB_ReadFinalPower_Lo.xseq`, most Rx and Tx checks/stores are disabled and the active checks are Lo-focused.

The current checked-in `Processvar.ini` does not define the plain `[ProcessData]` section, so the real target values still need to come from the live process data.

### Vision Acceptance Windows

- `SUB_MachineVision/SUB_Chip_MirrorFront_Correction.xseq`: left point `5..1000`, right point `1000..3000`, fiber-to-chip distances `50..300`.
- `SUB_MachineVision/SUB_Chip_MirrorSide_Correction.xseq`: upper point `100..500`, lower point `1000..2000`, fiber-to-chip distances `50..300`.
- `SUB_MachineVision/SUB_Chip_Top_Correction.xseq`: left point `5..1000`, right point `1000..3000`, fiber-to-path distances `10..200`.
- `SUB_MachineVision/SUB_Pick_Top_Correction.xseq`: fiber/gripper left and right points `5..3000`.

## What Is Stubbed

These statements are recognized as external/hardware/product modules, but their hidden behavior is not simulated yet:

- `AdvAlign_SpiralScan`
- `MetrologyLineScan`
- `MetrologyScanDisplay`
- `Grab`
- `VA_TM_GetValue`
- Vision Assistant VB modules such as `FixingPos1_12032026`
- Image window display functions

When these appear, the interpreter records missing information instead of pretending to know what the real module does.

## Verification Run

Current focused checks:

```powershell
python -m unittest tests.test_yase_sim
python -m yase_sim SUB_Positioning\SUB_Test_DrawCircle_AlignX1Z1.xseq --config examples\yase_sim_config.json --json
python -m yase_sim SUB_DataHandling\SUB_ReadFinalPower.xseq --config examples\yase_sim_config.json --json
python -m yase_sim SUB_Positioning\SUB_MoveFiberByOffset_FA.xseq --config examples\yase_sim_config.json --param "Select FA[HFA, VFA]=LA_Wide" --json
```

The circle test verifies that 20 `MoveStage` calls execute and the simulated `Align1` position returns to the starting point.

The fiber offset test verifies the important axis conversion:

```text
Align_Y2 +500  -> simulation Align2.z +500
Align_Z2 -2000 -> simulation Align2.y -2000
```

The power-read test verifies that `TIA1`/`TIA2` can call the external Gaussian sim and that Yase stage motion changes the calculated received power.

## Missing Setup Information

The interpreter can execute the control flow and simulate generic motion/power reads, but these device-specific facts are still needed before the simulator can be trusted for the real two-lens alignment process.

### Axis And Geometry

- The relationship between `Align_X1/Y1/Z1` and lens-1 center.
- The relationship between `Align_X2/Y2/Z2` and lens-2 center.
- How pitch, roll, and yaw affect lens position and optical path.
- Safe travel limits and collision volumes.

### Vacuum Tweezers And Grippers

- Which digital outputs enable/disable each vacuum tweezer.
- Which sensors confirm lens 1 and lens 2 are actually held.
- Whether `On` means vacuum applied, valve open, or vented.
- Vacuum/pressure thresholds for safe operation.
- Required behavior if vacuum is lost during alignment.

### Runtime Variables

- The real `systemvar.ini`, especially `[MainVelocity]`.
- The real or operator-selected `[Alignment]` section values for `TIA_Lo`, `TIA_Tx`, `TIA_Rx`, and optic switch settings.
- The real `[ProcessData]` values used as power baselines and targets.

The current checked-in `Processvar.ini` does not contain all of these values. The interpreter reports them as missing when a sequence asks for them.

### Power Model

- Which TIA or power meter corresponds to each optical path.
- What optic switch state selects each path.
- The objective function for two lenses: maximize one channel, balance channels, or satisfy a geometric plus power condition.
- The relationship between lens positions, laser position, waveguide position, and measured power.
- Saturation/overload behavior and TIA range strategy.

The external Gaussian sim is now available for the default two-ball-lens model, but we still need the real mapping between the machine's TIA channels, optic switch states, and the specific optical signal being measured.

### Vision Model

- Camera pixel-to-um calibration for each view/zoom.
- Camera-to-stage rotation and sign conventions.
- Which vision script locates each lens, the laser, and the waveguide.
- Exact `VA_TM_GetValue` statement/control/element/index strings.
- Confidence thresholds and what to do if a feature is not found.

### Hidden Product Modules

The repository calls product modules whose behavior is not defined in `.xseq`:

- `AdvAlign_SpiralScan`
- `MetrologyLineScan`
- Vision Assistant VB modules
- DMS/touchdown helpers

To simulate these correctly, we need either their implementation details or a simplified mathematical model of their inputs, outputs, and side effects.

## Batch Test Policy

Production `.xseq` files use tiered expectations:

- **Tier A** (`tests/xseq_simulatable.json`) — must run with zero warnings.
- **Tier B** (`tests/xseq_expected_failures.json`) — must emit an unmodeled warning naming the first missing call.
- **Tier C** — all other production files; reported by `tools/run_all_xseq.py` but not required to pass.

Tests live in:

- `tests/test_all_xseq.py` — tier A clean runs + tier B expected failures
- `tests/xseq_batch.py` — discovery and runner helpers
- `tests/xseq_expectations.json` — optional behavioral assertions
- `tools/run_all_xseq.py` — CLI report generator with tier column

## SEQ:: Resolution

When the interpreter hits `SEQ::Callee`:

1. **Native handler** if explicitly modeled (`SEQ::SUB_SYS_Vacuum_OnOff`, `SEQ::SUB_SysReadAveragePower`, `SEQ::SUB_SYS_AxisWaitFinishList`, …).
2. **Repo subprocess** if `{Callee}.xseq` exists in this repository (library path + `rglob` fallback, including `SUB_Positioning/` for `system\POSITIONING` calls).
3. **Error** otherwise — warning `Unmodeled sequence call: ...` and `ErrorType=1` on sequence error outputs. No silent `Error=0`.

`tools/extract_yase_machine_inventory.py` emits `sequence_call_resolution` listing each callee as `repo_subprocess` or `machine_only`.

## UI / IO (simulated, not errors)

These are operator/interface statements, not hardware product modules. See [`YASE_SIM_UI_IO_REFERENCE.md`](YASE_SIM_UI_IO_REFERENCE.md).

- Dialogs: `DisplayExtdDialog`, `DisplayExtdSelectionDialog`, `DropDownDialog`, `UserDialog`
- IO wait: `WaitDigIn` (reads `digital_inputs`; skip-next-line semantics)
- Data paths: `ResolvePath`, `ReadDataFileString*`
- Manual jog panel: `Positioning_OpenManuelMovePanel` (no-op)

## Unmodeled Hardware (errors)

These produce `Unmodeled hardware/product module` warnings and do not write fake measurement outputs:

- `Grab`, `GrabAndSave`, `VA_TM_GetValue`
- `AdvAlign_SpiralScan`, `MetrologyLineScan`, `MetrologyScanDisplay`
- Vision integrator routines: names starting with `VIS_` and `FixingPos1_12032026`

`SUB_Process/SUB_MainPickupFA.xseq` is a thin wrapper around `SUB_MainPickupLA.xseq` for the missing HFA pickup entry point.
