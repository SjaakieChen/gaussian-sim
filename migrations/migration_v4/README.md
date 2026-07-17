# Migration v4 Direct Default Positioning Bundle

Before copying or editing these files on the machine, read the repository root:

```text
MACHINE_CONFIGURATION.md
COMMON_MISTAKES.md
```

This bundle replaces the fragile v3 default-position wrapper/helper pattern
with direct operator-facing YASE sequences. The YASE files stay inside
`SUB_...` folders. The vision runtime Python files are loose in this folder so
they can be copied directly into the configured TMPython working directory:

```text
D:\TestMasterData\Process\Python_Automation\python_env
```

## Copy layout

Copy these YASE folders under the Python Automation process:

```text
migration_v4\SUB_default_positioning\*.xseq
  -> D:\TestMasterData\Process\Python_Automation\SUB_default_positioning\

migration_v4\SUB_vision_recognition\*.xseq
  -> D:\TestMasterData\Process\Python_Automation\SUB_vision_recognition\
```

Copy these loose vision runtime files into `python_env`:

```text
vision_recognition_lab.py
requirements.txt
```

The default-position movement sequences do not call TMPython. They contain the
known target constants directly and use YASE `MoveStage`, `SetAnalogOut`, and
`SEQ::SUB_SYS_AxisWaitFinishList`.

`default_positions.json` is the source/audit copy used by repo tests and for
regenerating or checking the hard-coded targets. The v4 `.xseq` files do not
read it at runtime. You may copy it alongside the bundle for traceability, but
it is not required in `python_env` for the YASE programs to run.

## Default-position entry points

Run one of these direct YASE programs:

```text
SUB_DefaultPosition_1.0.0_pick_ball_1.xseq
SUB_DefaultPosition_2.0.0_pick_ball_2.xseq
SUB_DefaultPosition_3.0.0_cam_view_1_wide.xseq
SUB_DefaultPosition_4.0.0_cam_view_1_side.xseq
SUB_DefaultPosition_5.0.0_back_view_after_trench.xseq
SUB_DefaultPosition_6.0.0_full_above_trench.xseq
```

Position `2.0.0` still has no known targets and fails closed before hardware.

## What changed from v3

- Destination labels reachable by `Goto` are plain `L_...` labels, not
  `@L_...`.
- Target positions are constants in each sequence; v4 does not read
  Microcombsys process-position variables.
- Camera X/Z use `VelocityCameraXFast`, Camera Y uses
  `VelocityCameraFast`, alignment axes use `VelocityAlignFast`, and Zoom uses
  `VelocityZoom`.
- Each sequence performs `StageCheckAllFiducialed` before motion and shows one
  operator confirmation dialog before the hardware section.
- The wait pattern follows the working example sequences:
  `MoveStage ... No sync Absolute` followed by
  `SEQ::SUB_SYS_AxisWaitFinishList`.

These files have static XML and label validation in the repo. They have not
yet been machine-run verified.
