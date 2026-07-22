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

- Machine `X` is the optical/laser direction.
- Machine `Z` is the lateral top-view transverse direction.
- Machine `Y` is the vertical/clearance direction.
- Holder `1` is the left ball/lens tower and maps to `Align_*1`.
- Holder `2` is the right ball/lens tower and maps to `Align_*2`.
- The checked-in convention says linear-axis signs are already correct; no
  simulator sign flips are applied.

For v6 reviewed top-view correction this means:

```text
image X residual -> tower machine Z correction
image Y residual -> tower machine X correction
```

For v6 side-view mirror correction:

```text
full image Y -> mirror-local Y -> vertical flip -> tower machine Y correction
```

The side-view sign remains a machine-check item because the example production
side correction adjusts pitch/roll rather than ball tower Y. V6 therefore logs
mirror-transform diagnostics and fails closed when the side reference is
missing or ambiguous.

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
- Confirm the side-view mirror Y sign with a deliberately small reviewed test
  move before using side-view Y correction operationally.
