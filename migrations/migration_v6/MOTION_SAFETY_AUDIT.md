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
camera X/Z/zoom/Y approach
active tower Y -> clearance
active tower Z
active tower X
active tower Y -> final target
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

## Remaining Machine-Side Checks

- Confirm real per-axis soft limits and collision volumes.
- Confirm the derived clearance Y values are physically safe on the machine.
- Confirm motion wait timeouts are compatible with the medium/slow velocities
  and worst-case deltas.
- Confirm image-right/`Align_X*` and image-up/`Align_Z*` correction signs with
  deliberately small guarded moves.
- Confirm the side-view mirror Y sign with a deliberately small reviewed test
  move before using side-view Y correction operationally.
