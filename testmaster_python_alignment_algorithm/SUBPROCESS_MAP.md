# Recommended YASE Subprocess Map

Create these as separate `.xseq` subsequences under:

```text
#SM_PROCESS#\SUB_Alignment\
```

The TestMaster manual describes callable subsequences as sequence files whose
names start with `SUB_`. After creating them in YASE, add/import the process
subsequence directory through `Sequencer -> Config Statements...`, then
re-import prototypes in YASE so they appear as `SEQ::SUB_...` statements.

Each subprocess should own the TestMaster-side loop:

```text
QueryStage / read power / read vision or target data
Build JSON
TMPython_ExecuteScript
Parse returned JSON
Validate requested move
MoveStage relative
SEQ::SUB_SYS_AxisWaitFinishList
SEQ::SUB_SysCheckAxisMove
Repeat until action is done or abort
```

Do not let Python move hardware directly.

## Subprocesses

| YASE subprocess | Python module | Python class | Needs |
| --- | --- | --- | --- |
| `SUB_PY_BlindPowerJ.xseq` | `testmaster_python_alignment_algorithm.blind.blind_power_j_step` | `BlindPowerJStep` | Power, positions, limits, state |
| `SUB_PY_BlindPowerJNewton.xseq` | `testmaster_python_alignment_algorithm.blind.blind_power_j_newton_step` | `BlindPowerJNewtonStep` | Power, positions, limits, state |
| `SUB_PY_BlindPowerJGradient.xseq` | `testmaster_python_alignment_algorithm.blind.blind_power_j_gradient_step` | `BlindPowerJGradientStep` | Power, positions, limits, state |
| `SUB_PY_BlindPowerJBestOf9.xseq` | `testmaster_python_alignment_algorithm.blind.blind_power_j_best_of_9_step` | `BlindPowerJBestOf9Step` | Power, positions, limits, state |
| `SUB_PY_FixedZJMatrix.xseq` | `testmaster_python_alignment_algorithm.assisted.fixed_z_j_matrix_step` | `FixedZJMatrixStep` | Positions, limits, fixed-Z targets or model J-matrix |
| `SUB_PY_PositionSolve.xseq` | `testmaster_python_alignment_algorithm.assisted.position_solve_step` | `PositionSolveStep` | Positions, limits, solved target positions |
| `SUB_PY_PositionSolveJSteps.xseq` | `testmaster_python_alignment_algorithm.assisted.position_solve_j_steps_step` | `PositionSolveJStepsStep` | Positions, limits, target path, state |

## Common Subsequence Interface

Use the same interface for each `SUB_PY_*` subprocess so the calling process can
switch algorithms without changing its higher-level wiring.

Suggested input parameters:

| Name | Type | Meaning |
| --- | --- | --- |
| `s_AlgorithmConfigJson` | String | Algorithm-specific settings. Can be `{}`. |
| `d_MaxIterations` | DBL | Loop limit controlled by YASE. |
| `d_MaxStep_um` | DBL | Maximum relative move Python may request. |
| `s_AllowedStagesJson` | String | JSON array of allowed stages for this phase. |

Suggested return parameters:

| Name | Type | Meaning |
| --- | --- | --- |
| `d_ErrorType` | DBL | `0` on success, nonzero on abort/error. |
| `s_ErrorMessage` | String | Human-readable failure or done message. |
| `s_ResultJson` | String | Last Python output JSON for logging. |

Keep the detailed machine state inside the JSON payload, not as dozens of
subsequence parameters. That keeps the YASE interface stable as the algorithm
inputs evolve.

## Standard JSON Envelope

Preferred input shape:

```json
{
  "schema_version": 1,
  "run_id": "operator-or-process-run-id",
  "phase": "blind_align",
  "iteration": 0,
  "machine": {
    "power_mw": 0.0123,
    "positions_um": {
      "Align_X1": 10.0,
      "Align_Z1": -2.0,
      "Align_Y1": 100.0,
      "Align_X2": 5.0,
      "Align_Z2": 1.0,
      "Align_Y2": 600.0
    }
  },
  "vision": {},
  "targets": {},
  "model": {},
  "limits": {
    "allowed_stages": ["Align_X1", "Align_Z1", "Align_X2", "Align_Z2"],
    "max_step_um": 2.0
  },
  "algorithm": {
    "name": "blind_power_j"
  },
  "state": {}
}
```

The Python files also accept the older flat fields `power_mw`,
`positions_um`, and `target_positions_um` for simple early tests.

Preferred output shape:

```json
{
  "schema_version": 1,
  "action": "move",
  "move_count": 1,
  "stage1": "Align_X1",
  "distance1_um": 1.0,
  "moves": [
    {
      "stage": "Align_X1",
      "distance_um": 1.0,
      "mode": "relative"
    }
  ],
  "message": "next requested move",
  "state": {}
}
```

YASE can parse the flat `stage1`/`distance1_um` fields first. The `moves` array
is included for logging and for a future cleaner parser.

## Test Order On The Machine

1. Create one `SUB_PY_*` subprocess that calls Python and returns fixed `done`.
2. Replace fixed Python with the real class but do not execute moves; display
   returned JSON.
3. Add real `QueryStage` and power reads.
4. Allow only one small stage in `limits.allowed_stages`.
5. Execute one tiny relative move, wait, and check axis status.
6. Add the loop and pass returned `state` into the next call.
7. Expand allowed stages only after validation on the real machine.
