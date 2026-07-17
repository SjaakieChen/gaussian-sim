# SUB_default_positioning v3

YASE-side files for migration v3 default-position movement.

Before editing these files, read:

```text
..\..\..\MACHINE_CONFIGURATION.md
```

| File | Purpose |
| --- | --- |
| `SUB_ApplyDefaultPositionMove.xseq` | Applies one already-parsed absolute stage move after fiducial, allowed-stage, max-delta, velocity, popup-confirmation, wait, and axis-error checks. |
| `SUB_ApplyDefaultPositionExposure.xseq` | Applies one already-parsed `cam_12_ExpTime` setting after analog-line allowlist, range check, and popup confirmation. |
| `SUB_DefaultPosition_1.0.0_pick_ball_1.xseq` | Applies all known settings for default position `1.0.0`. |
| `SUB_DefaultPosition_2.0.0_pick_ball_2.xseq` | Exists for completeness, but fails closed because `2.0.0` has no known settings in the JSON. |
| `SUB_DefaultPosition_3.0.0_cam_view_1_wide.xseq` | Applies all known settings for default position `3.0.0`, including `Zoom` and `cam_12_ExpTime`. |
| `SUB_DefaultPosition_4.0.0_cam_view_1_side.xseq` | Applies all known settings for default position `4.0.0`, including `Zoom` and `cam_12_ExpTime`. |
| `SUB_DefaultPosition_5.0.0_back_view_after_trench.xseq` | Applies all known settings for default position `5.0.0`, including `Zoom` and `cam_12_ExpTime`. |
| `SUB_DefaultPosition_6.0.0_full_above_trench.xseq` | Applies all known settings for default position `6.0.0`, including `Zoom`. |

Every stage move uses `DisplayExtdSelectionDialog` before `MoveStage`. Button 1
is `Abort` and executes a `Goto` to the user-abort path. Button 2 is `Move` and
skips that `Goto`, allowing the absolute `MoveStage`. The popup text names the
single stage, absolute target, queried current position, and computed delta.

The exposure-setting sequence uses the same popup pattern before
`SetAnalogOut` and names the analog line plus target value.

The `SUB_DefaultPosition_*` wrapper sequences are the direct operator entry
points. They do not contain direct `MoveStage` or `SetAnalogOut` statements.
They pass hard-coded values from `default_positions.json` into the guarded
apply sequences and stop after any nonzero `ErrorType`. Each wrapper child
call moves one stage or sets one camera value only.
