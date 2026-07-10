# SUB_alignment_solving

YASE-side files for migration v1 alignment solving.

## Files

| File | Purpose |
| --- | --- |
| `SUB_FixedZAlignmentSolving_ReadOnly.xseq` | Calls `python_alignment_solving.fixed_z_alignment_solver.FixedZAlignmentSolveStep`, displays the returned JSON, and stores the input/output JSON in `processvar.ini` and `#SM_PROCESS#\Python\log`. It does not move hardware. |
| `SUB_ApplyFixedZAlignmentSolveMove.xseq` | Applies one already-parsed `stage1` / `distance1_um` move with fiducial, allowed-stage, max-step, velocity, wait, and axis-error checks. |

The checked-in `yase_process\prototypes.xml` does not currently include JSON
parse statements, so this template intentionally stops before motion. After the
machine has imported the JSON/TMPython prototypes, parse:

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
