# SUB_alignment_solving

YASE-side files for migration v1 alignment solving.

Migration v1 is legacy and is not the current machine-motion path. Use
`migrations\migration_v2` for staged machine motion. The TMPython/pathing fields
in these v1 templates are kept aligned with the current Python Automation
machine only to avoid stale copy/paste examples.

## Files

| File | Purpose |
| --- | --- |
| `SUB_FixedZAlignmentSolving_ReadOnly.xseq` | Calls `fixed_z_alignment_solver.FixedZAlignmentSolveStep`, displays the returned JSON, and stores the input/output JSON in `processvar.ini` and `D:\TestMasterData\Process\Python_Automation\python_env\log`. It does not move hardware. |
| `SUB_ApplyFixedZAlignmentSolveMove.xseq` | Applies one already-parsed `stage1` / `distance1_um` move with fiducial, allowed-stage, max-step, velocity, wait, and axis-error checks. |

The current Python Automation TMPython prototype uses `ParamIn` and `ParamOut`.
After the machine has imported the JSON/TMPython prototypes, parse:

```text
action
stage1
distance1_um
stage2
distance2_um
```

Then apply the normal YASE safety checks before `MoveStage`:

```text
StageCheckAllFiducialed
validate action = move
validate stage is in allowed stages
validate abs(distance_um) <= max_step_um
MoveStage stage velocity distance_um No sync Relative
SEQ::SUB_SYS_AxisWaitFinishList
SEQ::SUB_SysCheckAxisMove
```

Or pass the parsed fields into `SUB_ApplyFixedZAlignmentSolveMove.xseq`:

```text
Stage      = stage1
DistanceUm = distance1_um
MaxStepUm  = limits.max_step_um
```
