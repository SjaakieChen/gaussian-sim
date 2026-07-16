# SUB_alignment_solving v2

YASE-side files for migration v2 staged fixed-Z ball placement.

Before editing these files, read:

```text
..\..\..\MACHINE_CONFIGURATION.md
```

| File | Purpose |
| --- | --- |
| `SUB_FixedZStagedBallPlacement_ReadOnly.xseq` | Calls `fixed_z_staged_ball_placement.FixedZStagedBallPlacementStep`, displays/stores returned JSON, and does not move hardware. |
| `SUB_ApplyFixedZStagedBallMove.xseq` | Applies one already-parsed absolute move from the Python result after fiducial, allowed-stage, max-delta, velocity, popup-confirmation, wait, and axis-error checks. |

The Python Automation machine uses the `TMPython_ExecuteScript` fields
`ParamIn` and `ParamOut`, not `Input JSON` and `Result JSON`. The read-only
sequence writes diagnostic JSON to:

```text
D:\TestMasterData\Process\Python_Automation\python_env\log\fixed_z_staged_ball_placement_input.json
D:\TestMasterData\Process\Python_Automation\python_env\log\fixed_z_staged_ball_placement_result.json
```

After JSON extraction statements are available on the machine, parse these
Python result fields:

```text
action
stage1
target1_um
distance1_um
delta1_um
move_mode1
confirm_text1
```

For each returned move, pass the parsed fields into
`SUB_ApplyFixedZStagedBallMove.xseq`:

```text
Stage           = stage1
TargetUm        = target1_um
MaxSingleMoveUm = limits.max_single_move_um
ConfirmText     = confirm_text1
```

`SUB_ApplyFixedZStagedBallMove.xseq` uses `DisplayExtdSelectionDialog` before
`MoveStage`. Button 1 is `Abort` and executes a `Goto` to the user-abort path.
Button 2 is `Move` and skips that `Goto`, allowing the absolute `MoveStage`.
