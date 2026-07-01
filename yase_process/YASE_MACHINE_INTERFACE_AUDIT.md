# Yase Machine Interface Audit

This audit is based on the checked-in Yase files in this process directory. It deliberately excludes the untested circle subsequence:

```text
SUB_Positioning/SUB_Test_DrawCircle_AlignX1Z1.xseq
```

For production evidence I also ignored the `Test/` folder. Disabled rows marked with `Label="*"` are called out only when they affect interpretation.

This file is not a machine recipe. It is the evidence map for what must be exact before writing or running two-ball-lens auto-alignment code on the real system.

## Reproducible Inventory

The machine-interface inventory can be regenerated from XML with:

```powershell
python tools\extract_yase_machine_inventory.py --output YASE_MACHINE_INTERFACE_INVENTORY.json
```

The extractor is covered by:

```powershell
python -m unittest discover -s tests
```

The generated inventory currently reports:

- Production `.xseq` files parsed: `49`
- Explicitly excluded file: `SUB_Positioning/SUB_Test_DrawCircle_AlignX1Z1.xseq`
- Skipped `Test/` folder file: `Test/test1111111111.xseq`
- XML parse failures: `0`
- Test-named files that are still inside production folders and therefore included: `SUB_Positioning/SUB_MoveToPickup_BallLens_test.xseq`, `SUB_Process/SUB_test.xseq`

Those test-named files should not be treated as approved production behavior unless the machine owner confirms they are intentionally deployed.

## Machine Site Data Gate

The inventory identifies names used by Yase. The separate site-data file must supply the real machine facts behind those names.

Generate the template:

```powershell
python tools\validate_machine_site_data.py --inventory YASE_MACHINE_INTERFACE_INVENTORY.json --write-template examples\machine_site_data.template.json
```

Validate a filled site-data file:

```powershell
python tools\validate_machine_site_data.py --inventory YASE_MACHINE_INTERFACE_INVENTORY.json --site examples\machine_site_data.template.json
```

The generated template intentionally fails validation until it is filled with real machine values, sources, and verification flags. Current template validation reports hundreds of issues because it still contains placeholders for axis limits, IO polarity, velocity values, hidden module behavior, process variables, collision clearance, and machine-motion approval.

Do not use a passing syntax check or a passing simulator test as permission to move the machine. A real site-data file must pass this readiness gate before any new auto-alignment sequence should be considered for machine execution.

Use `YASE_MACHINE_DATA_COLLECTION_CHECKLIST.md` while collecting the machine exports and confirmations needed to fill the site-data file.

## Confirmed Inputs

Full convention reference: [`YASE_MACHINE_CONVENTIONS.md`](YASE_MACHINE_CONVENTIONS.md).

User-confirmed facts:

- Holder/lens `1` is the left lens; holder/lens `2` is the right lens.
- Numbering `1` = left and `2` = right applies to grippers, vacuum channels, force sensors, and numbered add-ons.
- Linear axes: machine X/Z = transverse plane, machine Y = optical direction; signs are correct.
- Rotation body frame: **+Z is the nose** (machine +Y / optical axis). `Align_Rolln` = roll about nose, `Align_Pitchn` = pitch about body +X, `Align_Yawn` = yaw about body +Y; units = degrees.
- **TIA reading = optical power received** at the detector (alignment objective and final-power metric).
- TIA channel selection (`TIA_Lo`, `TIA_Tx`, `TIA_Rx`) can be manual on the machine.

Gripper/vacuum helper mapping per convention:

| Holder | Gripper helper | Vacuum helper | Pressure sensor |
|---|---|---|---|
| 1 (left) | `Gripper1` | `Gripper` | `Gripper1_Pressure` |
| 2 (right) | `Gripper2` | `CH2` | `Gripper2_Pressure` |

`CH3` is auxiliary (not holder 1 or 2). Some legacy sequences still call `Gripper2`/`CH3` in left-lens flows; reconcile on machine before production use.

IO polarity (`VacuumDevice On`, `Gripper2OpenClose On`, helper `on`/`off`) is still unverified.

## Production Sequence Entry Points

The checked-in `MAIN_PROCESS.xseq` active calls are:

- `SUB_MainInitEquipment`
- `SUB_MoveToPickup_BallLens`
- `SUB_SavePickUP_ballLens`
- `SUB_Move_dripping`
- `SUB_SavePos_dripping`
- `SUB_Move_Fixing`
- `SUB_SavePos_Fixing`
- `SUB_SYS_AbortAllSequence`

There is also an HFA process flow in `SUB_Process/SUB_Main_Process_HFA.xseq`:

- `SUB_Init_Process`
- `SUB_MainPickupFA`
- `SUB_MainChipFA`
- `SUB_MainAlignmentHFA`
- `SUB_MoveFiberByOffset_FA`
- `SUB_SYS_Predispense`
- `SUB_Dispense_HFA`
- `SUB_SYS_MoveToPos_Aligned`
- `SUB_Cure_HFA`
- `SUB_SYS_MoveToPos_Safe`

`Process.ini` menu entries reference files such as `Master_FA_V2.xseq`, `SUB_LoadTray.xseq`, and `SUB_UnloadTray_Manual.xseq` that are not present in this checkout. That means the local repo is not the complete deployed machine menu state.

The generated inventory also shows `SUB_Calibrate_Needles.xseq` is present, while the other referenced process menu targets above are missing from this checkout.

## Machine Handoff Inventory

Active production files contain these machine-facing statements:

| Statement | Count | Why it matters |
|---|---:|---|
| `SetAnalogOut` | 101 | camera exposure, illumination, UV analog outputs |
| `MoveStage` | 93 | direct axis motion |
| `QueryStage` | 85 | reads absolute axis position |
| `SEQ::SUB_SYS_AxisWaitFinishList` | 72 | waits for motion completion |
| `VA_TM_GetValue` | 25 | pulls values out of hidden vision modules |
| `StageCheckAllFiducialed` | 23 | stage reference/homing interlock |
| `SetDigOut` | 22 | vacuum, gripper, UV, dispenser, optic switch, reset IO |
| `InRange` | 21 | process/vision/power acceptance windows |
| `TIARange` | 16 | changes/reads TIA gain range |
| `SEQ::SUB_SYS_ErrorHandler` | 15 | sequence error propagation |
| `SEQ::SUB_SysReadAveragePower` | 9 | optical power measurement |
| `GetDigIn` | 9 | digital interlocks/sensors |
| `Grab` | 6 | camera acquisition |
| `AdvAlign_SpiralScan` | 3 | hidden alignment optimizer |
| `SEQ::SUB_SYS_Gripper_OpenClose` | 3 | hidden gripper command |
| `SEQ::SUB_SYS_Vacuum_OnOff` | 3 | hidden vacuum command |
| `SEQ::SUB_SYS_DMS_Touchdown_Universal*` | 3 | hidden force/touchdown motion |

These are the statements where syntax correctness is not enough. Axis names, units, signs, current machine state, IO polarity, and physical clearance must also be correct.

## Stage Names

Active production files use these stage names:

- Holder/lens axes: `Align_X1`, `Align_Y1`, `Align_Z1`, `Align_Roll1`, `Align_Pitch1`, `Align_Yaw1`
- Holder/lens axes: `Align_X2`, `Align_Y2`, `Align_Z2`, `Align_Roll2`, `Align_Pitch2`, `Align_Yaw2`
- Camera axes: `Camera_X`, `Camera_Y`, `Camera_Z`
- Zoom axis: `Zoom`

For simulation/code reasoning, use the confirmed convention:

```text
machine X -> transverse x
machine Z -> transverse y
machine Y -> optical-axis z
1 -> left lens
2 -> right lens
```

Still required before production motion:

- Actual safe travel limits for every `Align_*`, `Camera_*`, and `Zoom` axis.
- Collision volumes for lenses, grippers, chip, taper, camera, dispenser, UV head, and hatch.
- Safe approach/retreat poses for holding two lenses at once.
- Whether absolute positions in `Processvar.ini` are current for the physical fixture.
- Maximum allowed step sizes and speed caps for alignment search.

## MainVelocity

`MainVelocity` is the system speed table. It is read from the system variable namespace:

```text
GetNumVar System "" MainVelocity VelocityAlignMedium -> d_Vel_Align_Medium
MoveStage ... Velocity [um/s]=d_Vel_Align_Medium
```

It is not a threshold. It provides motion speeds in `um/s`.

Active production files read these keys:

- `VelocityAlignXSlow`
- `VelocityAlignSlow`
- `VelocityAlignMedium`
- `VelocityAlignFast`
- `VelocityAlignXFast`
- `VelocityAlignXXFast`
- `VelocityCameraXSlow`
- `VelocityCameraSlow`
- `VelocityCameraMedium`
- `VelocityCameraFast`
- `VelocityCameraXFast`
- `VelocityRotSlow`
- `VelocityRotMedium`
- `VelocityRotFast`
- `VelocityRotXFast`
- `VelocityZoom`

The real `systemvar.ini` is not in this checkout. Production code needs the real `[MainVelocity]` values from the machine, plus a deliberate decision about which speed tier is allowed for coarse search, fine search, pickup, touchdown, and retreat.

## Digital IO

Active production digital IO names:

- Inputs: `AirPressure_OK`, `Vacuum_OK`, `TrayFixed`, `Gripper_Open`, `TIA1_PowerOn`, `Limit_Door_Open`, variable TIA overload line `$S_TIAOverload`
- Outputs: `VacuumDevice`, `Gripper2OpenClose`, `OpticSwitch`, `TIA1_Reset`, `TIA2_Reset`, `Dispenser1_Extend`, `Dispenser1_Initiate`, `UV_Extend`, `UV_On`
- Teach/readback output: `AdjustMode`

Known active checks:

- `SUB_MainInitEquipment`: `AirPressure_OK == 1`
- `SUB_MainInitEquipment`: `Vacuum_OK == 1`
- `SUB_MainDeviceHandling`: waits for `TrayFixed`; timeout branch after `Counter > 5`
- `SUB_MainDeviceHandling`: checks `Gripper_Open == 1`
- `SUB_Main_Process_HFA`: reads `Limit_Door_Open`

Still required:

- Real IO point database for all of these lines.
- Polarity for every output: whether `On` applies vacuum, vents vacuum, opens a valve, closes a gripper, or energizes a solenoid.
- Which IO line confirms that left lens and right lens are physically held.
- Vacuum loss behavior during alignment.
- Whether `Gripper2OpenClose`, helper `Gripper2`, helper vacuum `CH3`, helper vacuum `Gripper`, and physical left/right holder numbers refer to the same hardware. The checked-in code does not prove this.

## Gripper And Vacuum Helpers

Active helper calls:

- `SUB_MoveToPickup_BallLens`: `SUB_SYS_Vacuum_OnOff`, channel `Gripper`, state `on`
- `SUB_MainPickupLA`: `SUB_SYS_Gripper_OpenClose`, gripper `Gripper2`, state `close`
- `SUB_MainPickupLA`: `SUB_SYS_Vacuum_OnOff`, channel `CH3`, state `off`
- `SUB_ReleaseGripperAfterUV`: `SUB_SYS_Gripper_OpenClose`, gripper `Gripper2`, state `open`

This is a naming ambiguity that must be resolved before two-lens code is written. The user-confirmed physical mapping is `1 = left`, `2 = right`, but the existing helper calls mostly name `Gripper2` and `CH3`.

## Analog IO

Active production analog outputs:

- `cam_12_ExpTime`
- `Illu_Coax`
- `Illu_1`
- `Illu_2`
- `UV_1_0-10V`
- `UV_2_0-10V`

Active analog input:

- variable TIA analog line `$S_TIAName`

The visible code uses these for camera exposure, illumination, UV output level, and TIA overload/range logic. Production needs actual voltage ranges, safe maximums, calibration, and whether any analog output changes are allowed during alignment.

## TIA And Power

Process variables read from `[MainInitEquipment]`:

- `TIA1Offset = 0`
- `TIA1Range = 5`
- `TIA1Wavelength = 1550`
- `TIA1Polarity = Non-Inverting`
- `TIA2Offset = 0`
- `TIA2Range = 5`
- `TIA2Wavelength = 1500`
- `TIA2Polarity = Inverting`
- `CalibrationFactor = 1000`

TIA range/overload logic:

- `SUB_CheckTIAOverload_T` declares `SwitchLimitAnalog = 5.0`.
- `SUB_TIAInit_T` declares `SwitchLimitAnalog = 5.0`.
- The code checks digital overload through `$S_TIAOverload`.
- It also reads analog TIA value from `$S_TIAName` and compares to `SwitchLimitAnalog`.
- It changes TIA gain range with `TIARange`.

Power reads:

- `SUB_MainAlignmentHFA`: reads average power from variable `$TIA` with delays `10`, `10`, and `20`.
- `SUB_ReadFinalPower`: reads `TIA_Rx`, `TIA_Tx`, `TIA_Lo` with delay `50`.
- TIA channel names and optic switch states come from process section `[Alignment]`.

Still required:

- Real `[Alignment]` values for `TIA_Lo`, `TIA_Tx`, `TIA_Rx`, `OpticSwitchLo`, `OpticSwitchTx`, `OpticSwitchRx`.
- Whether each TIA is measuring laser input, transmitted waveguide power, reflected/receive path, or another path.
- Saturation limits, noise floor, averaging count semantics, and whether range changes are allowed during closed-loop alignment.
- Whether maximizing one channel can harm another channel.

## Final Power Acceptance

`SUB_ReadFinalPower.xseq` reads these `[ProcessData]` values:

- `PowerLo_Dispensed`
- `PowerRx_Dispensed`
- `PowerTx_Dispensed`
- `FinalPowerTarget_Lo`
- `FinalPowerTarget_Rx`
- `FinalPowerTarget_Tx`
- `FinalPowerChangeRate`

Active checks in `SUB_ReadFinalPower.xseq`:

- `PowerLo_Final < FinalPowerTarget_Lo` triggers low-power handling.
- `PowerRx_Final < FinalPowerTarget_Rx` triggers low-power handling.
- `PowerTx_Final < FinalPowerTarget_Tx` triggers low-power handling.
- Lo and Rx change-rate checks are active with max `2.0`.
- Lo and Rx report checks are active with max `5.0`.

Important disabled rows:

- Tx change-rate `InRange` checks are disabled with `Label="*"` in `SUB_ReadFinalPower.xseq`.
- In `SUB_ReadFinalPower_Lo.xseq`, most Rx and Tx checks and final stores are disabled; it is effectively Lo-focused.

The current checked-in `Processvar.ini` does not contain a plain `[ProcessData]` section with the final target values. It contains sections like `[ProcessDataLA_Wide]` and `[ProcessDataData]`, but the active final-power sequence reads `[ProcessData]`. Production requires the live runtime `[ProcessData]`.

## Vision

Vision modules are hidden external implementations. The `.xseq` files show their inputs and output value names, not the algorithms.

Active modules:

- `VIS_TM20_ChipHFA_MirrorFront_17122023`
- `VIS_TM20_ChipHFA_MirrorSide_18022024`
- `VIS_TM20_ChipHFA_TopAlignment_18022024`
- `VIS_TM20_PickTopHFA_Fiber_16022024`
- `VIS_TM20_PickTopHFA_Gripper_24112023`
- `FixingPos1_12032026` appears through the vision integration module.

Visible acceptance windows:

- Mirror front: `d_Point_X_Left` in `5..1000`, `d_Point_X_Right` in `1000..3000`, distances in `50..300`.
- Mirror side: `d_Point_Y_Up` in `100..500`, `d_Point_Y_Down` in `1000..2000`, distances in `50..300`.
- Chip top: `d_Point_X_Left` in `5..1000`, `d_Point_X_Right` in `1000..3000`, distances in `10..200`.
- Pick top: fiber/gripper left/right X points in `5..3000`.

Still required:

- Camera pixel-to-micron calibration for each view.
- Camera-to-stage rotation and sign convention.
- Confidence/failure outputs from each vision module.
- Whether these vision scripts can see both lenses while held by vacuum tweezers.
- Which script can locate the unknown laser and unknown waveguide position for the final two-lens alignment.

## Touchdown And Force

Visible calls:

- `SUB_MainAlignmentHFA`: `Align_Y2`, `Force2`, step-up `15.0 um`
- `SUB_MainPickupLA`: `Align_Y2`, `Force2`, step-up `10.0 um`
- `SUB_Move_Fixing`: `Align_Y1`, `Force1`, step-up `250.0 um`

The actual touchdown threshold, force units, search direction, stop behavior, and error handling are hidden inside `SUB_SYS_DMS_Touchdown_Universal*`. Production needs the implementation or a verified machine manual description.

## Alignment Search

Visible `AdvAlign_SpiralScan` calls:

- `TIA1_RoughSpiralScan`, `Threshold optional = 10.0`
- `TIA1_FineSpiralScan`, `Threshold optional = 10.0`

`SpiralScan.set` contains binary/LabVIEW-style preset data with the named presets, but the repo does not provide a readable schema. Do not hand-decode those bytes for production. Read and export the preset parameters through Yase/TestMaster.

Still required:

- Scan axes for each preset.
- Scan pitch, radius/range, speed, timeout, threshold units, and return behavior.
- Whether the scan moves `Align_X2/Z2`, `Align_X2/Y2`, or another axis pair.
- Whether the scan is safe while both ball lenses are held.

## Process Variable Sections Needed

Important checked-in sections:

- Vision settings: `[Vision_Pickup_BallLens]`, `[Vision_Pickup_Top]`, `[Vision_Fixing]`, `[Vision_Chip_Top]`, `[Vision_Chip_MirrorFront]`, `[Vision_Chip_MirrorSide]`
- Stored positions: `[PosPickUp_BallLens]`, `[Pos_Dripping]`, `[Pos_Fixing]`, HFA/LA position sections
- Alignment offsets: `[Offsets_LA_Wide_Alignment]`, `[TravelOffset_AfterAlignment_LA_Wide]`
- Scaling: `[Scaling]`
- TIA setup: `[MainInitEquipment]`
- Runtime master state: `[Master_FA]`

Concrete checked-in examples:

- `[Scaling] HFA_Top_1 = 4.15`
- `[Scaling] HFA_Chip_YawAlignment = 2.2678`
- `[Offsets_LA_Wide_Alignment] Align_X2_Offset = 75.0`
- `[Offsets_LA_Wide_Alignment] Align_Z2_Offset = 133.0`
- `[TravelOffset_AfterAlignment_LA_Wide] Align_Y2_Offset = 500`
- `[TravelOffset_AfterAlignment_LA_Wide] Align_Z2_Offset = -2000`

Still required:

- Live process variables from the actual machine before running production alignment.
- Which process variant is intended: old `MAIN_PROCESS`, HFA flow, LA wide flow, or another deployed menu sequence.
- Confirmation that stored absolute positions match the current fixture and not an old teach state.

## Minimum Safe Requirements For Two-Lens Auto-Alignment

Before writing a machine-running auto-alignment sequence, we need:

1. Real `systemvar.ini [MainVelocity]` values and approved speed tiers.
2. Real IO mapping and polarity for vacuum, gripper, sensors, optic switch, UV, dispenser, hatch, and TIA resets.
3. Verified mapping from physical left/right lenses to `Align_*1`, `Align_*2`, gripper helper names, and vacuum helper channels.
4. Axis soft limits, hard limits, and collision envelope for every planned move.
5. Safe start, safe retreat, and abort behavior for both lenses held at the same time.
6. Live `[Alignment]` TIA/optic-switch assignments.
7. Live `[ProcessData]` final power targets and allowed change-rate policy.
8. TIA overload/range behavior and saturation/noise constraints.
9. Vision calibration and feature confidence/failure behavior.
10. Readable/exported `AdvAlign_SpiralScan` preset parameters.
11. Touchdown helper implementation or verified semantics for force thresholds, direction, and stop behavior.
12. A dry-run/simulation path that exercises logic without moving stages, followed by a machine test plan using only tiny bounded moves from a known safe pose.

## Current Risk Assessment

The repo gives enough information to parse and type-check many Yase calls, and enough to simulate generic motion and power reads. It does not yet prove that a new two-lens auto-alignment sequence can be run safely on the real machine.

The highest-risk unresolved items are:

- IO naming ambiguity around physical holder 1/2 vs `Gripper2`, `Gripper`, and `CH3`.
- Missing real `systemvar.ini [MainVelocity]` values.
- Missing live `[ProcessData]` targets.
- Hidden behavior of `AdvAlign_SpiralScan`, vision modules, and DMS touchdown helpers.
- Unknown collision envelope for two lenses held simultaneously.
