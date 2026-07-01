# Yase Machine Data Collection Checklist

Use this checklist to fill a real machine site-data file from:

```text
examples/machine_site_data.template.json
```

Generate or refresh the template from the current Yase inventory:

```powershell
python tools\extract_yase_machine_inventory.py --output YASE_MACHINE_INTERFACE_INVENTORY.json
python tools\validate_machine_site_data.py --inventory YASE_MACHINE_INTERFACE_INVENTORY.json --write-template examples\machine_site_data.template.json
```

Validate the filled file:

```powershell
python tools\validate_machine_site_data.py --inventory YASE_MACHINE_INTERFACE_INVENTORY.json --site <filled-machine-site-data.json>
```

Do not treat this checklist as permission to move the machine. The filled site-data file must pass validation and be reviewed by the machine owner before any new auto-alignment sequence is run.

## Scope

This checklist is derived from existing Yase production files only. It excludes:

```text
SUB_Positioning/SUB_Test_DrawCircle_AlignX1Z1.xseq
Test/test1111111111.xseq
```

The inventory currently warns that these test-named files are inside production folders and are included unless a machine owner says otherwise:

```text
SUB_Positioning/SUB_MoveToPickup_BallLens_test.xseq
SUB_Process/SUB_test.xseq
```

## 1. Machine Identity And Sources

Fill `metadata`.

Collect:

- Machine/station ID.
- Exact process checkout path on the machine.
- Date of verification.
- Person verifying the data.
- Source files or screenshots used for verification.

Acceptable sources:

- Live machine `systemvar.ini`.
- Live machine `Processvar.ini`.
- TestMaster/Yase IO point database export.
- Motion controller axis configuration export.
- Screenshots from Yase dialogs showing selected TIA/optic-switch configuration.
- TestMaster/Yase parameter pages for hidden modules such as spiral scan and touchdown.
- Machine owner signoff notes.

## 2. Axis Limits And Safe Positions

Fill `axes.<axis>`.

Axes extracted from current Yase files:

```text
Align_X1
Align_Y1
Align_Z1
Align_Roll1
Align_Pitch1
Align_Yaw1
Align_X2
Align_Y2
Align_Z2
Align_Roll2
Align_Pitch2
Align_Yaw2
Camera_X
Camera_Y
Camera_Z
Zoom
```

For each axis collect:

- Soft limit minimum and maximum in Yase units.
- A known safe position.
- Maximum allowed relative step for new alignment code.
- Maximum allowed velocity key from `[MainVelocity]`.
- Collision notes for lens, gripper, chip, waveguide/taper, camera, dispenser, UV head, and hatch.
- Source and `verified=true`.

Specific checks:

- Confirm that holder/lens `1` is left and holder/lens `2` is right.
- Confirm the user-accepted axis signs are also correct in the real deployed axis configuration.
- Confirm whether rotations are degrees, microradians, or another unit before allowing roll/pitch/yaw moves.
- Confirm whether `Align_Y*` is safe to move while a ball lens is near the chip/waveguide.

## 3. MainVelocity

Fill `main_velocity`.

Export live `systemvar.ini` section:

```text
[MainVelocity]
```

Keys required by current Yase files:

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

For each key collect:

- Numeric speed in `um/s` or the correct native unit if the axis is rotational.
- Source.
- `verified=true`.

Decision needed:

- Which velocity keys are allowed for coarse search.
- Which velocity keys are allowed for fine search.
- Which velocity keys are allowed for pickup, touchdown, retreat, and recovery.

## 4. Digital IO

Fill `digital_io`.

Digital IO lines extracted from current Yase files:

```text
AirPressure_OK
Vacuum_OK
TrayFixed
Gripper_Open
TIA1_PowerOn
Limit_Door_Open
AdjustMode
VacuumDevice
Gripper2OpenClose
OpticSwitch
TIA1_Reset
TIA2_Reset
Dispenser1_Extend
Dispenser1_Initiate
UV_Extend
UV_On
$S_TIAOverload
```

For each fixed line collect:

- Purpose.
- Direction.
- Meaning of `1` or `On`.
- Meaning of `0` or `Off`.
- Safe state.
- Source.
- `verified=true`.

For variable line `$S_TIAOverload`, collect:

- Which parameter or TIA name resolves it.
- Possible actual digital lines, for example TIA overload channels.
- Meaning of digital state.
- Source.
- `verified=true`.

Mandatory checks:

- Does `VacuumDevice On` apply vacuum or release/vent vacuum?
- Does `Gripper2OpenClose On` open or close?
- Does `OpticSwitch On` select Rx, Tx, Lo, or something else?
- Are `TIA1_Reset` and `TIA2_Reset` momentary reset pulses?
- Is `UV_On` disabled and locked out during alignment work?
- Which sensor confirms each ball lens is actually held?

## 5. Analog IO

Fill `analog_io`.

Analog lines extracted from current Yase files:

```text
cam_12_ExpTime
Illu_Coax
Illu_1
Illu_2
UV_1_0-10V
UV_2_0-10V
$S_TIAName
```

For each fixed line collect:

- Purpose.
- Units.
- Safe minimum and maximum.
- Calibration.
- Source.
- `verified=true`.

For variable analog line `$S_TIAName`, collect:

- How the name is resolved.
- Possible analog lines or TIA channels.
- Units.
- Safe range.
- Source.
- `verified=true`.

Mandatory checks:

- Illumination values are safe for the camera and sample.
- Camera exposure values are in the correct unit.
- UV analog outputs are not changed by alignment code.
- TIA analog readback units match the `SwitchLimitAnalog = 5.0` threshold.

## 6. Holder, Gripper, And Vacuum Mapping

Fill `holder_mapping`. Convention values are pre-filled in `examples/machine_site_data.template.json`; set `verified=true` after on-machine IO polarity check.

Known user-confirmed physical mapping:

```text
holder/lens 1 = left lens
holder/lens 2 = right lens
```

Numbering `1` = left and `2` = right applies to grippers, vacuum helpers, and numbered add-ons. Full table: [`YASE_MACHINE_CONVENTIONS.md`](YASE_MACHINE_CONVENTIONS.md).

Convention mapping (verify IO polarity on machine):

```text
Holder 1: Gripper1, vacuum helper Gripper, Gripper1_Pressure, Gripper1_Open
Holder 2: Gripper2, vacuum helper CH2, Gripper2_Pressure, Gripper2_Open
CH3     : auxiliary (not holder 1 or 2)
```

Existing Yase helper names that still need polarity verification:

```text
Gripper2
Gripper
CH3
Gripper2OpenClose
VacuumDevice
Gripper_Open
Vacuum_OK
```

For holder `1` and holder `2`, collect:

- Physical lens side.
- Yase stage actor, expected `Align1` or `Align2`.
- Gripper helper name.
- Vacuum helper channel.
- Sensor confirming lens held.
- Sensor confirming lens released.
- Source.
- `verified=true`.

Mandatory check:

- Prove whether helper `Gripper2` means physical holder 2, the second actuator in a module, or something else.
- Prove whether vacuum channel `CH3` is connected to the left lens, right lens, both, or neither.
- Prove whether helper channel `Gripper` in `SUB_MoveToPickup_BallLens` is the same hardware as `CH3`.

## 7. Process Variables

Fill `process_variables.Alignment` and `process_variables.ProcessData`.

Alignment keys required:

```text
TIA_Lo
TIA_Tx
TIA_Rx
OpticSwitchLo
OpticSwitchTx
OpticSwitchRx
```

Process data keys relevant to final power and reporting:

```text
PowerLo_Dispensed
PowerRx_Dispensed
PowerTx_Dispensed
FinalPowerTarget_Lo
FinalPowerTarget_Rx
FinalPowerTarget_Tx
FinalPowerChangeRate
PowerLo_Final
PowerRx_Final
PowerTx_Final
PowerLo_Aligned
PowerRx_Aligned
PowerTx_Aligned
PowerRead_FA
```

For each key collect:

- Actual value or rule for generation/selection.
- Source.
- `verified=true`.

Mandatory checks:

- The live machine really has a `[ProcessData]` section when `SUB_ReadFinalPower.xseq` runs.
- `FinalPowerTarget_*` units match `SUB_SysReadAveragePower` output.
- `FinalPowerChangeRate` is a fraction, not percent, because the code uses `1.0 - A21`.
- Tx final-power checks are active, but Tx change-rate `InRange` rows are disabled in `SUB_ReadFinalPower.xseq`.

## 8. Hidden Modules

Fill `hidden_modules`.

Hidden or external modules extracted from current Yase files include:

```text
AdvAlign_SpiralScan
SUB_SYS_DMS_Touchdown_Universal
SUB_SYS_DMS_Touchdown_Universal_Alignment
SUB_SYS_Gripper_OpenClose
SUB_SYS_Vacuum_OnOff
SUB_SysReadAveragePower
Grab
VA_TM_GetValue
MetrologyLineScan
MetrologyScanDisplay
VIS_TM20_ChipHFA_MirrorFront_17122023
VIS_TM20_ChipHFA_MirrorSide_18022024
VIS_TM20_ChipHFA_TopAlignment_18022024
VIS_TM20_PickTopHFA_Fiber_16022024
VIS_TM20_PickTopHFA_Gripper_24112023
```

For each module collect:

- Implementation reference or TestMaster/Yase parameter page.
- Inputs verified.
- Outputs verified.
- Side effects.
- Failure modes.
- Whether it is safe during two-lens alignment.
- `verified=true`.

Mandatory hidden-module checks:

- `AdvAlign_SpiralScan`: axes, scan range, step size, speed, timeout, threshold units, return behavior.
- Touchdown helpers: force threshold, units, direction, stop behavior, recovery on failure.
- Vision modules: pixel-to-um calibration, feature confidence, no-feature behavior, and whether both lenses can be seen.
- Power read helper: averaging count semantics, returned units, saturation behavior.

## 9. Alignment Policy

Fill `alignment_policy`.

Collect and decide:

- Objective function: maximize one channel, balance multiple channels, or meet a geometric/power target.
- Allowed search axes.
- Coarse step size.
- Fine step size.
- Maximum total travel per axis.
- Power saturation limit.
- Power noise floor.
- Stop conditions.
- Source.
- `verified=true`.

Minimum stop conditions:

- Stage error.
- TIA overload or saturation.
- Power below floor after search.
- Power drops faster than allowed.
- Vacuum/gripper hold lost.
- User abort.
- Axis soft-limit approach.
- Vision confidence failure.

## 10. Safety And Approval

Fill `safety` and `approval`.

Required before machine motion:

- Safe start pose is defined.
- Safe retreat pose is defined.
- Abort behavior is verified.
- Collision envelope is verified.
- Clearance with two lenses held is verified.
- Dry run without motion is available.
- Operator confirmation is required before motion.
- Machine owner approval is recorded.

The readiness validator requires `approval.approved_for_machine_motion=true`. That should only be set after review of the filled site-data file and a separate machine test plan.

## 11. Final Review Questions

Before writing production auto-alignment code, answer these explicitly:

1. Which deployed sequence is the true starting point: `MAIN_PROCESS.xseq`, `SUB_Main_Process_HFA.xseq`, or a missing menu file such as `Master_FA_V2.xseq`?
2. Are `SUB_MoveToPickup_BallLens_test.xseq` and `SUB_Process/SUB_test.xseq` intentionally deployed or should they be excluded?
3. What are the actual left and right vacuum channels?
4. What are the actual left and right gripper helper names?
5. Which TIA measures the laser/waveguide signal used for optimization?
6. Which optic switch state selects each optical path?
7. What is the maximum safe relative move per axis near the chip?
8. What is the safe retreat if power drops, vacuum is lost, or a stage errors?
9. Can the system hold both lenses simultaneously without collision through the entire search envelope?
10. Has the final filled site-data file passed `validate_machine_site_data.py`?

