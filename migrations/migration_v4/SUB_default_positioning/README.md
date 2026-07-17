# SUB_default_positioning v4

Direct YASE programs for the known default positions.

These files are intended for:

```text
D:\TestMasterData\Process\Python_Automation\SUB_default_positioning\
```

They use hard-coded target constants from `migration_v4\default_positions.json`.
They do not call TMPython and do not read target positions from another
process. Each moving sequence:

- checks `StageCheckAllFiducialed`;
- reads required speeds from system `[MainVelocity]`;
- asks the operator to confirm the full target list;
- issues absolute `MoveStage` commands;
- waits with `SEQ::SUB_SYS_AxisWaitFinishList`;
- aborts on missing velocity, failed fiducial check, user abort, or wait
  timeout.

Position `2.0.0` has no known targets and returns an error before hardware.
