# Migration v3 Default Positioning Bundle

Before editing or copying these files to the machine, read the repository-level
[`MACHINE_CONFIGURATION.md`](../../MACHINE_CONFIGURATION.md). It is the
authority for the Python Automation process folders, sequence paths, and
machine-local conventions.

This bundle moves machine axes to the default positions recorded in:

```text
Standard position images\v2\standard_positions.json
```

The local JSON source used to generate and test the wrapper sequences is:

```text
migrations\migration_v3\dev_side\python_default_positioning\default_positions.json
```

## Machine Deployment

Copy the YASE files to:

```text
D:\TestMasterData\Process\Python_Automation\SUB_default_positioning\
```

The direct position sequences do not use TMPython at runtime. They contain the
known target values from the JSON and call the guarded apply sequences one
stage or camera setting at a time.

## Files

| File | Purpose |
| --- | --- |
| `dev_side\python_default_positioning\default_position_move_planner.py` | Local planner used by tests/regeneration. It is not called by the direct runtime `.xseq` wrappers. |
| `dev_side\python_default_positioning\default_positions.json` | Local copy of the current standard-position JSON used to generate and verify wrapper constants. |
| `SUB_default_positioning\SUB_ApplyDefaultPositionMove.xseq` | Applies one parsed absolute `MoveStage` after fiducial check, allowlist check, delta limit, velocity selection, popup confirmation, wait, and axis-error check. |
| `SUB_default_positioning\SUB_ApplyDefaultPositionExposure.xseq` | Applies one parsed `cam_12_ExpTime` setting after allowlist/range check and popup confirmation. |
| `SUB_default_positioning\SUB_DefaultPosition_*.xseq` | Operator-facing one-sequence-per-position wrappers. Each wrapper applies every known stage, zoom, and exposure setting for that JSON position by calling the guarded apply sequences. |

## Stage Mapping

The local planner and generated wrappers map JSON machine-position groups to
concrete YASE stage names:

```text
tower_1.x -> Align_X1
tower_1.y -> Align_Y1
tower_1.z -> Align_Z1
tower_2.x -> Align_X2
tower_2.y -> Align_Y2
tower_2.z -> Align_Z2
camera.x  -> Camera_X
camera.y  -> Camera_Y
camera.z  -> Camera_Z
zoom      -> Zoom
exposure  -> SetAnalogOut cam_12_ExpTime
```

Unknown JSON values are skipped. Special fields such as `z_near_vacuum` are
kept as non-motion notes and are not converted into motion automatically.

## Direct Position Run Pattern

For normal operation, run the matching wrapper sequence:

```text
SUB_DefaultPosition_001_pick_ball_1.xseq
SUB_DefaultPosition_002_pick_ball_2.xseq
SUB_DefaultPosition_003_cam_view_1_wide.xseq
SUB_DefaultPosition_004_cam_view_1_side.xseq
SUB_DefaultPosition_005_back_view_after_trench.xseq
SUB_DefaultPosition_006_full_above_trench.xseq
```

Each wrapper contains the known target values from `default_positions.json`.
It does not call `MoveStage` or `SetAnalogOut` directly. Instead, every action
is delegated to `SUB_ApplyDefaultPositionMove.xseq` or
`SUB_ApplyDefaultPositionExposure.xseq`, so the fiducial checks, stage/analog
allowlists, delta/range limits, velocities, popup confirmation, waits, and
axis checks stay in one place. Each child call moves one stage or sets one
camera value only.

Position `002` currently has only unknown values. Its wrapper exists, but it
returns an error and stops before hardware.
