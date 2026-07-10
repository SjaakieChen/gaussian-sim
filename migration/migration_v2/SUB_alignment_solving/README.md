# SUB_alignment_solving v2

YASE-side files for migration v2 staged fixed-Z ball placement.

| File | Purpose |
| --- | --- |
| `SUB_FixedZStagedBallPlacement_ReadOnly.xseq` | Calls `python_alignment_solving.fixed_z_staged_ball_placement.FixedZStagedBallPlacementStep`, displays/stores returned JSON, and does not move hardware. |
| `SUB_ApplyFixedZStagedBallMove.xseq` | Applies one already-parsed absolute move from the Python result after fiducial, allowed-stage, max-delta, velocity, popup-confirmation, wait, and axis-error checks. |

The checked-in `yase_process\prototypes.xml` does not currently include the
TMPython or JSON parse statements. After importing those prototypes on the
machine, parse these Python result fields:

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

