# Migration v6 Motion Safety Audit

This audit records the evidence used for v6 axis mapping and movement order.
It is not a substitute for machine-side checkout with real limits and
interlocks.

## Evidence Read

- `MACHINE_CONFIGURATION.md`
- `COMMON_MISTAKES.md`
- `migrations/migration_v4/SUB_default_positioning/*.xseq`
- `migrations/migration_v2/SUB_alignment_solving/SUB_ApplyFixedZStagedBallMove.xseq`
- `yase_example_processes/YASE_MACHINE_CONVENTIONS.md`
- `yase_example_processes/YASE_MACHINE_INTERFACE_AUDIT.md`
- `yase_example_processes/SUB_Positioning/*.xseq`
- `yase_example_processes/SUB_MachineVision/*.xseq`

## Axis Conclusions

- Holder `1` is the left ball/lens tower and maps to `Align_*1`.
- Holder `2` is the right ball/lens tower and maps to `Align_*2`.
- V6 schema 2 uses canonical machine-axis keys, not camera-relative labels.
- Legacy `x`, `y`, and `z` inputs are normalized at the boundary.

The V6 reviewed-vision contract is:

```text
image right               -> positive machine_x_um -> Align_X*
image up                  -> positive machine_z_um -> Align_Z*
mirror-corrected vertical -> machine_y_um          -> Align_Y*
```

Because image Y increases downward, direct top-view image Y has the opposite
sign from `machine_z_um`. A correction also has the opposite sign from the
measured residual: for example, a ball right of its target requires negative
`machine_x_um`.

Fine-top measurements add the recorded camera X/Z displacement between the
reference and ball captures before calculating the remaining tower correction.
The scale is rejected when view, zoom, or image dimensions differ.

For the V6 side mirror:

```text
full image Y -> mirror-local Y -> vertical flip
300 um / reviewed trench-line separation -> side scale
flipped ball-to-trench-top residual -> tower machine Y correction
```

The side-view sign remains a machine-check item. Repository evidence cannot
prove the physical direction of the target machine. V6 logs the full transform
and fails closed for missing, duplicate, reversed, out-of-ROI, or implausibly
separated ruler features.

## Movement-Order Conclusions

Migration v4 proves the direct hardcoded XML style, but it used fast velocities
and moved tower lateral axes before tower Y. That direct order is not enough
for close-to-chip v6 motion.

The safer pattern appears in two independent sources:

- The fixed-Z staged workflow raises `Align_Y*` to a safe clearance coordinate,
  moves lateral axes, then lowers `Align_Y*` to the solved coordinate.
- The production-like positioning examples move the active tower Y upward to a
  clearance coordinate such as `10000`, `15000`, or a positive relative
  clearance move before moving `Align_Z*` and `Align_X*`, then lower Y to the
  final approach coordinate.

V6 follows that pattern:

```text
all towers with lateral targets -> clearance Y
camera X/Z/zoom/Y approach
active tower machine Z
active tower machine X
active tower machine Y -> final target
settings/exposure/lights
```

For generated v6 standard-position files, the clearance coordinate is derived
from the maximum standard Y recorded for the same tower in the v4 standard
positions:

```text
tower_1 clearance Y = max standard tower_1 Y
tower_2 clearance Y = max standard tower_2 Y
```

For v6 transition files, Python creates one anchored transition target in
`v6_vision_memory.json` and reuses it until the transition completes. This is
important because the transition sequence loops through one move at a time; it
must not rebase the target from the updated current position after each move.

Before every generated V6 `MoveStage`, YASE queries the current coordinate and
calculates:

```text
predicted constant-speed duration = abs(target - current) / selected velocity
```

The move is rejected before issuance when that value exceeds `40 s`. This
leaves a `5 s` margin below the observed `45 s`
`SUB_SYS_AxisWaitFinish` timeout. After issuance, V6 uses only
`SUB_SYS_AxisWaitFinishList` and branches explicitly on `Timeout`; the previous
stacked `SUB_SysCheckAxisMove` call was removed.

Image-derived lateral correction is allowed only while the active tower is at
or above the reviewed standard-position Y for that view. Smaller machine Y is
treated as downward. A side Y correction is not planned until the top-view
correction for that ball has converged and the top-to-side transition is
recorded complete.

For ball 2, Python projects each bounded X/Z segment into the common final
rectangle-relative frame. It evaluates both Z-then-X and X-then-Z, rejects any
path whose minimum ball-to-ball surface gap is not strictly positive, and uses
the valid order with the larger minimum gap. This check ignores the real
vertical separation while the second ball is raised, so it is conservative.

The final read-only layout check uses a 500 um ball diameter and requires
strictly positive source-to-ball, ball-to-ball, ball-to-taper, and
ball-to-trench-floor gaps. At the requested `(289, 0, 0)` and `(989, 0, 0)`
centers, those nominal surface gaps are `39`, `200`, `39`, and `50` um.

These are reviewed common-frame geometry checks. The repository does not have
a calibrated transform for every raw stage coordinate, gripper body, trench
edge, or camera assembly, so it cannot prove the complete physical swept
volume from code alone.

## Remaining Machine-Side Checks

- Confirm real per-axis soft limits and collision volumes.
- Confirm the derived clearance Y values are physically safe on the machine.
- Commission the raw stage-to-ball, gripper, trench, and camera collision
  envelopes; the common-frame sphere checks do not replace them.
- Confirm that the observed `45 s` wait timeout still applies and that the
  `40 s` pre-move duration budget is sufficient for acceleration and settling
  on every axis.
- Confirm image-right/`Align_X*` and image-up/`Align_Z*` correction signs with
  deliberately small guarded moves.
- Confirm the side-view mirror Y sign with a deliberately small reviewed test
  move before using side-view Y correction operationally.
