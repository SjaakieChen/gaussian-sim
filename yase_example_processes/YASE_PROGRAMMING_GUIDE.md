# Programming Yase/TestMaster for Ball-Lens Auto-Alignment

This guide is based on the local process repository, `Yase_TM_HB_Sep_2018.pdf`, and `TestMaster Documentation 2020.1.10 (1).pdf`. It is a programming and requirements guide only. It is not a hardware-validated operating procedure.

Because this system can move precision hardware and optical components, do not deploy new automated motion or alignment logic until the missing machine-specific information listed below is supplied and checked on the actual TestMaster installation.

## 1. What Yase Is

Yase is the sequence editor used to create and edit TestMaster user programs. In this project those programs are XML sequence files with the `.xseq` extension.

Core terms from the manuals:

- A **statement** is a predefined TestMaster command with input and/or output parameters.
- A **prototype** is the syntax definition of a statement. In programmer terms, statements and prototypes are effectively the same interface.
- A **sequence** is the user-defined program saved as `.xseq`.
- A running sequence is a **process**.
- A **subsequence** is a sequence whose filename starts with `SUB_`; TestMaster/Yase can expose it as a callable `SEQ::SUB_...` statement.

Important manual references:

- Yase manual page 6: statement, sequence, process, perspective definitions.
- Yase manual pages 39 to 42: editor workflow, parameters, variables, labels, saving into the process directory.
- Yase manual page 58: using and converting variables/constants in sequence parameters.
- Yase manual page 60: AutoCheck can mass-correct sequences, but must not be used blindly because statement changes can corrupt behavior.
- Yase manual page 71: subsequences are stored with the `SUB_` prefix and can be used like statements.
- TestMaster manual pages 43 to 47: project/process organization and symbolic paths.
- TestMaster manual pages 1017 to 1021: Stage statement library.
- TestMaster manual pages 1135 to 1141: VariableIO and `processvar.ini` / `systemvar.ini`.
- TestMaster manual pages 1156 to 1172: subsequence declaration/call and XSEQ flow control.

## 2. How to Program in This Project

Use Yase/TestMaster as the source of truth for editing `.xseq` files. The files are XML, but direct text edits are risky because statement parameters, prototype compatibility, and local TestMaster libraries must stay consistent.

Normal workflow:

1. Open TestMaster and switch to the Yase perspective if needed: `Window > Open Perspective > Other... > Yase`.
2. Import prototypes from the running local server or from the process prototype file. The Yase manual recommends `127.0.0.1` for local server import.
3. Open or create a sequence in the process folder, normally `#SM_PROCESS#`.
4. Build logic from statements and callable `SEQ::SUB_...` subsequences.
5. Use variables for measured values, recipe values, and returned errors. Constants are acceptable only for stable values that are genuinely fixed.
6. Save sequences into the process directory, not an arbitrary Yase default folder.
7. Run Yase Problems/AutoCheck before execution, but treat automatic correction as a review item, not proof of correctness.

The repo already contains `prototypes.xml`, which lists available statement interfaces, including Stage, VariableIO, DataFile, XSEQDefinition, XSEQExecutionControl, XSEQFlowControl, AdvancedIMAQ, VisionAssistantIntegrator, AdvancedAlignment, and many system `SEQ::SUB_SYS...` helpers.

## 3. Repository Map

| Path | Role |
| --- | --- |
| `MAIN_PROCESS.xseq` | Manual/dropdown harness. It checks stage fiducials, initializes equipment, then calls selected ball-lens positioning/save routines. |
| `SUB_Process/` | Higher-level workflows. `SUB_Main_Process_HFA.xseq` is the closest complete process chain. |
| `SUB_Positioning/` | Position moves, saved position routines, dispense/UV positions, ball-lens pickup/drip/fix moves. |
| `SUB_MachineVision/` | Camera/Vision Assistant corrections for pickup, fixing, and chip views. |
| `SUB_Alignment/` | Optical/power alignment logic. `SUB_MainAlignmentHFA.xseq` is the main active-alignment example. |
| `SUB_DispenseUV/` | Dispense, UV cure, and gripper release routines. |
| `SUB_Initializing/` | Equipment/TIA initialization and overload checks. |
| `SUB_DataHandling/` | Process data, final power reads, reports, log data. |
| `SUB_Vision/` | Camera lighting/exposure presets. |
| `Processvar.ini` | Process recipe values: user dialog values, camera settings, taught positions, offsets, TIA settings, ROI, UV/cure settings, process data. |
| `Process.ini` | TestMaster process configuration and menu buttons. Several button targets reference files not present in this repo, so the repo may be partial. |
| `IOPointDB.txt` | Only contains two camera exposure IOPoints in this checkout. The real machine I/O database is required before coding vacuum/interlocks. |
| `*.set` | TestMaster/LabVIEW serialized panel and algorithm settings, including scan/alignment settings. Inspect these in TestMaster panels, not as plain text. |

## 4. Existing Control Structure

### `MAIN_PROCESS.xseq`

This is currently a manual process selector. It:

- calls `StageCheckAllFiducialed`;
- calls `SEQ::SUB_MainInitEquipment`;
- reads dropdown items from `#SM_DATA#\DONOTDELETE\DropDownItems.txt`;
- branches on the selected process name;
- calls:
  - `SEQ::SUB_MoveToPickup_BallLens`;
  - `SEQ::SUB_SavePickUP_ballLens`;
  - `SEQ::SUB_Move_dripping`;
  - `SEQ::SUB_SavePos_dripping`;
  - `SEQ::SUB_Move_Fixing`;
  - `SEQ::SUB_SavePos_Fixing`;
- calls `SEQ::SUB_SYS_AbortAllSequence` when the selected process name is `Cancel`.

This is not yet a complete two-lens automated production flow. It is a useful manual commissioning harness.

### Existing HFA process chain

`SUB_Process/SUB_Main_Process_HFA.xseq` is the closest complete process example:

1. `SUB_Init_Process`
2. pickup flow, currently called as `SEQ::SUB_MainPickupFA`
3. chip/fiber alignment setup via `SEQ::SUB_MainChipFA`
4. optical alignment via `SEQ::SUB_MainAlignmentHFA`
5. fiber offset correction via `SEQ::SUB_MoveFiberByOffset_FA`
6. predispense, dispense, aligned position, hatch, cure, safe position

Important gap: this repo contains `SUB_Process/SUB_MainPickupLA.xseq`, but `SUB_Main_Process_HFA.xseq` calls `SEQ::SUB_MainPickupFA`. Verify on the machine whether `SUB_MainPickupFA.xseq` exists in another process/system folder, whether prototypes are stale, or whether the local file was renamed.

## 5. Existing Subprocess Patterns

### Positioning pattern

The ball-lens move routines use this pattern:

1. Check all stages are fiducialed.
2. Read velocity values from system `MainVelocity` using `GetNumVar`.
3. Read target positions from `Processvar.ini` using `GetNumVar`.
4. Move camera/zoom and alignment axes with `MoveStage`.
5. Use `No sync` moves for parallel motion, then explicitly wait with `SEQ::SUB_SYS_AxisWaitFinishList`.
6. Return `ErrorType`, `ErrorMessage`, and `SequenceName` with XSEQDefinition return statements.

Examples:

- `SUB_Positioning/SUB_MoveToPickup_BallLens.xseq` reads `[PosPickUp_BallLens]`, moves `Zoom`, `Camera_Y`, `Camera_X`, `Camera_Z`, then `Align_Y1`, `Align_Z1`, `Align_X1`, `Align_Y1`, `Align_Yaw1`, `Align_Roll1`. It opens `Positioning_OpenManuelMovePanel` for `Tower1`, then calls `SEQ::SUB_SYS_Vacuum_OnOff` for vacuum channel `Gripper`.
- `SUB_Positioning/SUB_Move_dripping.xseq` reads `[Pos_Dripping]` and performs the same basic camera/Align_1 move pattern, with a relative `Align_Y1` approach move first.
- `SUB_Positioning/SUB_Move_Fixing.xseq` reads `[Pos_Fixing]`, performs the same move pattern, then calls `SEQ::SUB_SYS_DMS_Touchdown_Universal` on `Align_Y1` with `Force1`.
- `SUB_Positioning/SUB_SavePickUP_ballLens.xseq`, `SUB_SavePos_dripping.xseq`, and `SUB_SavePos_Fixing.xseq` query current `Align_1` and camera positions and write them back into `Processvar.ini`.

### Machine vision pattern

The vision correction routines use:

1. camera settings from `Processvar.ini`;
2. `Grab` from `AdvancedIMAQ`;
3. a customer Vision Assistant Integrator statement;
4. `VA_TM_GetValue` to extract measured points;
5. `MoveStage` relative corrections.

Examples:

- `SUB_MachineVision/SUB_Fix_BallLens_Correction.xseq` runs `FixingPos1_12032026`, reads a fitted ball-lens circle center/radius and laser mid-edge point, then moves `Align_X1` and `Align_Z1`.
- `SUB_MachineVision/SUB_Pick_Top_Correction.xseq` detects fiber and gripper positions, then corrects `Align_X2` and `Align_Z2`.
- `SUB_MachineVision/SUB_Chip_Top_Correction.xseq` detects chip/top features and corrects `Align_Yaw2`, `Align_Z2`, and `Align_X2`.
- `SUB_MachineVision/SUB_Chip_MirrorFront_Correction.xseq` corrects `Align_Roll2`.
- `SUB_MachineVision/SUB_Chip_MirrorSide_Correction.xseq` corrects `Align_Pitch2`.

The Vision Assistant scripts themselves are not in this repo. Their source/configuration must be exported from the TestMaster/customer module installation before modifying the vision logic.

### Optical alignment pattern

`SUB_Alignment/SUB_MainAlignmentHFA.xseq` is the key active-alignment example:

- checks fiducials;
- records starting positions for `Align_X2`, `Align_Y2`, `Align_Z2`, `Align_Roll2`, `Align_Pitch2`, `Align_Yaw2`;
- performs DMS touchdown on `Align_Y2` with `Force2`;
- reads optical power through `SEQ::SUB_SysReadAveragePower` using variable `TIA`;
- runs rough/fine `AdvAlign_SpiralScan` using setup names `TIA1_RoughSpiralScan` and `TIA1_FineSpiralScan`;
- contains `MetrologyLineScan` calls for `Align_Yaw2`, `Align_Roll2`, `Align_Pitch2`, and fine variants;
- records final positions and `Power_Final`.

The exact scan ranges, step widths, meters, thresholds, and axes are stored in `.set` files/panels and must be checked in TestMaster before reuse.

## 6. How to Add a New Safe Subsequence

For a new feature, create a process-local `SUB_*.xseq` in the relevant folder. Keep it small and testable. Do not start by editing the master production flow.

Recommended sequence skeleton:

1. `DeclareNumParam` / `DeclareStrParam` for all inputs that should be controlled by the caller.
2. Initialize `d_ErrorType`, `s_ErrorMessage`, `s_SequenceName`, and `s_MasterSequenceName`.
3. `StageCheckAllFiducialed`; if false, route to error handling.
4. Read all recipe values from `Processvar.ini` with `GetNumVar` / `GetStringVar`.
5. Read shared machine velocities from system variables, not hard-coded constants.
6. Move through safe intermediate positions first.
7. For parallel `No sync` moves, immediately wait on the exact moved axes.
8. Use `SEQ::SUB_SYS_ErrorHandler` after system helpers that return errors.
9. Return all errors with `ReturnNumParam` / `ReturnStrParam`.
10. Log timing with `SEQ::SUB_SysTimeHandler`.

For motion, prefer established system helpers such as:

- `SEQ::SUB_SYS_MoveToPos_Safe`;
- `SEQ::SUB_SYS_MoveToPos_Pick`;
- `SEQ::SUB_SYS_MoveToPos_Chip`;
- `SEQ::SUB_SYS_AxisWaitFinishList`;
- `SEQ::SUB_SYS_DMS_Touchdown_Universal`;
- `SEQ::SUB_SYS_Vacuum_OnOff`;
- `SEQ::SUB_SYS_Gripper_OpenClose`;
- `SEQ::SUB_SYS_ErrorHandler`.

Only use raw `MoveStage` when the axis, target, velocity, approach path, limit, and collision volume are known.

## 7. Proposed Architecture for Two Ball Lenses

The final project should be split into bounded, reviewable subprocesses. A single large sequence would be hard to validate.

Proposed new process layers:

1. `SUB_BallLens_Preflight`
   - Check fiducials, interlocks, protective rooms, safe positions, vacuum pressure, gripper state, DMS sensors, laser state, TIA readiness, camera readiness, and recipe completeness.
2. `SUB_BallLens_Pick_Left` and `SUB_BallLens_Pick_Right`
   - Generalize the existing pickup pattern, but parameterize lens index, tower/stage group, pickup position section, vacuum channel, pressure sensor, and gripper/tweezer identity.
3. `SUB_BallLens_CheckHeld_Left` and `SUB_BallLens_CheckHeld_Right`
   - Confirm the ball is held by pressure feedback and/or vision. Do not rely on valve state alone.
4. `SUB_BallLens_CoarseVisionLocate`
   - Find ball-lens centers, laser beam/reference, and waveguide/chip features. Store measured points and confidence values.
5. `SUB_BallLens_CoarseOpticalAcquire`
   - Use a bounded search volume around the vision estimate to find first measurable optical power. Stop on collision, force, limit, timeout, no-signal, saturation, or operator abort.
6. `SUB_BallLens_FineAlign`
   - Reuse the active alignment model: read average power, run bounded spiral/line/coordinate scans, reduce step sizes, verify repeatability, and record final coordinates.
7. `SUB_BallLens_VerifyAndLog`
   - Re-read power, store final positions, offsets, scan setup names, vacuum pressures, images, and pass/fail criteria.
8. `SUB_BallLens_SafeAbort`
   - Define exactly how to freeze, retract, keep/release vacuum, disable laser, and save diagnostics on every failure mode.

Do not assume the laser and waveguide unknown positions can be solved by an unbounded scan. The search must be limited by physical clearance, optical power limits, and known calibration uncertainty.

## 8. Information Still Needed

### Machine and axes

- Full `Hardware.ini`, `systemvar.ini`, production `IOPointDB.txt`, and current `Process.ini` from the machine.
- Axis list, controller mapping, travel limits, soft limits, user-coordinate origins, units, signs, and fiducial procedure.
- Which physical axes correspond to `Align_1`, `Align_2`, camera, gripper/tweezer, laser, chip, and waveguide motion.
- Safe approach/retract heights for every lens, tweezer, chip, and waveguide state.
- Protective room definitions and collision/no-go volumes.
- Current stage limit and room enforcement status.

### Vacuum tweezers and lens handling

- Number and names of vacuum channels, valves, pressure sensors, and any blow-off channels.
- Mapping from TestMaster channels such as `Gripper`, `CH1`, `CH2`, `CH3` to physical tweezers.
- Required vacuum thresholds for "lens held", "lens missing", and "leak".
- Ball lens diameters, tolerances, mass, material, coating sensitivity, and allowed contact force.
- Pickup tray geometry, pitch, lens presentation, and allowed correction range.
- Whether the final process only holds lenses during alignment or also dispenses/cures/places them permanently.

### Optical system

- Laser wavelength, maximum safe power, shutter/enable controls, and interlock behavior.
- Detector/TIA mapping: which of `TIA1`/`TIA2` measures the alignment metric for each lens.
- TIA calibration, range, polarity, dark offset, saturation limit, averaging time, and noise floor.
- Acceptance thresholds: minimum coupled power, repeatability, drift after hold/release, and allowed optimization time.

### Vision and calibration

- Camera calibration: pixel-to-stage scale, rotation, distortion, magnification/zoom dependence, and camera-to-stage transform.
- Source/configuration of all Vision Assistant Integrator routines, especially `FixingPos1_12032026` and `VIS_TM20_*`.
- Feature definitions for laser position and waveguide position in each view.
- Confidence/failure criteria for each vision result.
- Required image logging for traceability.

### Existing software completeness

- Confirm whether `SUB_MainPickupFA.xseq` is missing, generated elsewhere, or renamed.
- Export the current `#SM_SYSTEM#`, `#SM_CONFIG#`, customer modules, and generated prototypes from the machine.
- Verify all `.set` alignment/scan settings in TestMaster panels: scan axes, ranges, step widths, speed, thresholds, meters, and stop conditions.
- Confirm the meaning of local `Label="*"` rows in `.xseq` files. They are common in this repo and must be interpreted in the actual Yase/TestMaster UI before assuming they are enabled or disabled.

### Safety and validation

- Define the emergency-stop and software-abort behavior for each stage, vacuum valve, laser, and gripper.
- Define what must happen on pressure loss while holding a lens.
- Define what must happen on force/DMS contact before expected contact.
- Provide a dry-run test plan with no lens, dummy lens, low laser power, single-axis tests, then full sequence.
- Define who is authorized to approve recipe changes and who signs off machine-motion changes.

## 9. Immediate Next Steps

1. Export the full live TestMaster project/process/system folders from the machine.
2. In TestMaster, inspect the parameter panels for `TIA1_RoughSpiralScan`, `TIA1_FineSpiralScan`, and all `Align_*` line scans.
3. Build a sequence-call graph from the live system and reconcile missing/renamed sequences.
4. Create a safety requirements sheet before writing any new motion logic.
5. Implement only the preflight and read-only diagnostics first.
6. Validate one movement or one sensor at a time at reduced speed and with collision-safe fixtures.

The safest programming path is to extend the existing Yase subprocess architecture: recipe-driven `.xseq` files, explicit stage waits, TestMaster system helpers, machine-vision corrections, bounded active-alignment scans, and complete data logging.
