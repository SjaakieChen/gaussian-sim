# Migration v6 YASE Vision Workflow

Before copying or editing these files on the machine, read the repository root:

```text
MACHINE_CONFIGURATION.md
COMMON_MISTAKES.md
```

Also read:

```text
migration_v6\MOTION_SAFETY_AUDIT.md
```

Migration v6 combines the v4 direct hardcoded-position sequence style with the
v5 Python vision review/memory layer. Python never moves hardware. Python only
records reviewed image features and proposes flat move fields; YASE validates,
asks the operator, and calls `MoveStage`.

## Copy Layout

Copy these YASE folders under the Python Automation process:

```text
migration_v6\SUB_v6_standard_positions\*.xseq
  -> D:\TestMasterData\Process\Python_Automation\SUB_v6_standard_positions\

migration_v6\SUB_v6_vision_workflow\*.xseq
  -> D:\TestMasterData\Process\Python_Automation\SUB_v6_vision_workflow\
```

Copy these Python/runtime files into `python_env`:

```text
migration_v6\python_vision_geometry\
migration_v6\vision_recognition_lab.py
migration_v6\requirements.txt
```

Copy the standard position evidence folder into `python_env`:

```text
migration_v6\standard_positions_v4\
  -> D:\TestMasterData\Process\Python_Automation\python_env\standard_positions_v4\
```

## Entry Points

- `SUB_v6_standard_positions\SUB_V6MoveToPosition_*.xseq` moves to one
  hardcoded v4/new-head standard position and sets exposure, zoom, and all
  lights to the v6 constants.
- `SUB_v6_vision_workflow\SUB_V6CaptureReviewRecord_*_ReadOnly.xseq` grabs
  `CAM_12`, saves the verified BMP bridge image, opens the review UI, and
  records the fixed capture ID into `v6_vision_memory.json`.
- `SUB_v6_vision_workflow\SUB_V6OffsetCorrection_*_Guarded.xseq` asks Python
  for bounded offset corrections from the latest reviewed capture, then applies
  up to three slow, operator-confirmed tower moves.
- `SUB_v6_vision_workflow\SUB_V6TransitionMove_*_Guarded.xseq` moves from one
  reviewed vision position to the next by applying the standard relative
  position delta to the current live machine position once, storing the
  anchored target in `v6_vision_memory.json` until the transition completes.
  This preserves gross and fine tower offsets without recalculating a drifting
  target after each one-axis move.
- `SUB_v6_vision_workflow\SUB_V6MainWorkflow_Guarded.xseq` chains the full
  process with two capture/correction passes at each correction point.

## Motion And Offset Rules

- Standard approach files use `VelocityCameraMedium`,
  `VelocityAlignMedium`, and `VelocityZoom`.
- Standard position and transition moves raise the active tower Y to a derived
  clearance before tower X/Z motion, then lower Y to the final target.
- Offset correction files use `VelocityAlignXSlow` for `Align_X*` and
  `VelocityAlignSlow` for `Align_Y*`/`Align_Z*`.
- No v6 close-to-chip sequence uses fast velocities.
- Top/gross correction maps image X to tower Z and image Y to tower X.
- Fine top correction compares the live ball-to-rectangle offset against the
  standard ball-to-rectangle offset, so X and Z can both be corrected.
- Side correction treats the side image as a mirror view. It flips the mirror
  Y coordinate first, because the trench bottom/chip-side direction appears at
  the top of the mirror, then maps the remaining vertical error to tower Y.
- Side correction fails closed unless both the ball and a side-reference line
  are present in the reviewed side session.

These files are statically XML/test verified in the repo. They still need a
machine-side checkout before being treated as an operator procedure.
