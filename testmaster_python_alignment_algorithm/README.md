# TestMaster Python Alignment Algorithm Package

This folder contains the Python files intended to be copied to the
TestMaster/YASE machine. The files are split by the information the algorithm
needs:

- `blind/`: power-only algorithms. They need current power, current positions,
  machine limits, iteration number, and the returned `state` from the previous
  call. They do not need vision.
- `assisted/`: non-blind algorithms. They need vision results, target
  positions, or relative stage offsets calculated outside the blind power loop.
- `contracts.py`: shared JSON input/output helpers.
- `alignment_step.py`: compatibility wrapper exposing `BlindAlignStep`, matching
  the earlier README examples.
- `SUBPROCESS_MAP.md`: recommended one-subprocess-per-algorithm layout for
  YASE/TestMaster.
- `TESTMASTER_DOC_AUDIT.md`: notes from the original TestMaster/Yase PDF
  manuals that affect deployment.

YASE/TestMaster must still own all hardware motion, waits, safety checks,
interlocks, and error handling. Python only proposes the next action.

## 1. Copy To The Machine

Recommended machine layout:

```text
#SM_SYSTEM#\Python\testmaster_python_alignment_algorithm\
```

Configure `#SM_CONFIG#\TMPython.ini` so the parent folder is the working
directory:

```ini
[Python_310]
WorkingDirectory = "#SM_SYSTEM#\Python\"
PythonInterpreter = "C:\Users\operator\AppData\Local\Programs\Python\Python310\python.exe"
LogDirectory = "#SM_SYSTEM#\Python\log"
```

With that layout, use these module/class names in `TMPython_ExecuteScript`:

| Use case | Module | Class |
| --- | --- | --- |
| Default blind power loop | `testmaster_python_alignment_algorithm.alignment_step` | `BlindAlignStep` |
| Blind power J auto fallback | `testmaster_python_alignment_algorithm.blind.blind_power_j_step` | `BlindPowerJStep` |
| Blind power J Newton | `testmaster_python_alignment_algorithm.blind.blind_power_j_newton_step` | `BlindPowerJNewtonStep` |
| Blind power J Gradient | `testmaster_python_alignment_algorithm.blind.blind_power_j_gradient_step` | `BlindPowerJGradientStep` |
| Blind power J Best-of-9 | `testmaster_python_alignment_algorithm.blind.blind_power_j_best_of_9_step` | `BlindPowerJBestOf9Step` |
| Fixed-Z J-matrix | `testmaster_python_alignment_algorithm.assisted.fixed_z_j_matrix_step` | `FixedZJMatrixStep` |
| Position solve | `testmaster_python_alignment_algorithm.assisted.position_solve_step` | `PositionSolveStep` |
| Position solve with visible steps | `testmaster_python_alignment_algorithm.assisted.position_solve_j_steps_step` | `PositionSolveJStepsStep` |
| Move toward absolute targets | `testmaster_python_alignment_algorithm.assisted.target_position_step` | `TargetPositionStep` |
| Apply vision-computed offsets | `testmaster_python_alignment_algorithm.assisted.vision_offset_step` | `VisionOffsetStep` |

If you instead set `WorkingDirectory` directly to
`#SM_SYSTEM#\Python\testmaster_python_alignment_algorithm\`, remove the
`testmaster_python_alignment_algorithm.` prefix from the module names.

## 2. Independent YASE Subprocesses

Create one YASE subprocess per algorithm in:

```text
#SM_PROCESS#\SUB_Alignment\
```

Recommended subprocess names:

| YASE subprocess | Python class |
| --- | --- |
| `SUB_PY_BlindPowerJ.xseq` | `BlindPowerJStep` |
| `SUB_PY_BlindPowerJNewton.xseq` | `BlindPowerJNewtonStep` |
| `SUB_PY_BlindPowerJGradient.xseq` | `BlindPowerJGradientStep` |
| `SUB_PY_BlindPowerJBestOf9.xseq` | `BlindPowerJBestOf9Step` |
| `SUB_PY_FixedZJMatrix.xseq` | `FixedZJMatrixStep` |
| `SUB_PY_PositionSolve.xseq` | `PositionSolveStep` |
| `SUB_PY_PositionSolveJSteps.xseq` | `PositionSolveJStepsStep` |

See `SUBPROCESS_MAP.md` for the full module/class table and suggested
subsequence parameters.

## 3. YASE Call Loop

The YASE sequence should do this every loop:

```text
Read current stage positions with QueryStage
Read current optical power
Run vision if this algorithm needs vision
Build input JSON

TMPython_ExecuteScript
    Interpreter = Python_310
    Module      = testmaster_python_alignment_algorithm.alignment_step
    Class       = BlindAlignStep
    Input JSON  = s_PythonInputJson
    Result JSON = s_PythonResultJson

Parse result JSON
If action == abort: stop through the approved YASE error path
If action == done: finish this alignment phase
If action == move: validate stage names, distances, limits, and target positions
Move requested stage or stages
Wait for motion complete
Copy output.state into the next input JSON state field
Repeat
```

For the blind algorithm, the `state` field is important. YASE should pass the
returned `state` object back in the next call. That makes the algorithm safe
even if TestMaster creates a fresh Python object for every call.

## 4. Standard Input JSON

Every statement expects schema version `1`.

```json
{
  "schema_version": 1,
  "phase": "blind_align",
  "iteration": 0,
  "run_id": "operator-or-process-run-id",
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
  "algorithm": {},
  "state": {}
}
```

Required fields:

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | yes | Must be `1`. |
| `phase` | recommended | Human/debug label such as `vision_coarse` or `blind_align`. |
| `iteration` | yes | Loop count controlled by YASE. |
| `machine.power_mw` | for blind | Latest optical power reading in mW. |
| `machine.positions_um` | yes | Latest absolute stage positions from YASE. |
| `vision` | for assisted | Vision output or `{}`. |
| `targets` | for assisted | Absolute target positions or path points. |
| `model` | for assisted | Optional model/J-matrix data from a previous calculation. |
| `limits.allowed_stages` | yes | Stages Python is allowed to request. |
| `limits.max_step_um` | yes | Maximum relative move Python may request in one call. |
| `algorithm` | optional | Algorithm-specific settings. |
| `state` | for blind | Previous output `state`. Use `{}` for the first call. |

The code also accepts the older flat keys `power_mw`, `positions_um`, and
`target_positions_um`. Use the nested shape above for new subprocesses.

## 5. Standard Output JSON

Python returns one of three actions:

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
  "message": "probing Align_X1 by 1 um",
  "state": {}
}
```

Valid actions:

| Action | Meaning |
| --- | --- |
| `move` | YASE should validate and execute the requested relative move. |
| `done` | This algorithm phase is complete. |
| `abort` | YASE should stop through the approved machine error path. |

The output supports at most two moves in one call:

```json
{
  "schema_version": 1,
  "action": "move",
  "move_count": 2,
  "stage1": "Align_X1",
  "distance1_um": 0.5,
  "stage2": "Align_Z1",
  "distance2_um": -0.5,
  "message": "two-axis correction"
}
```

For first machine tests, keep `move_count` effectively limited to `1`.

## 6. Blind Power Algorithms

Use this when you do not trust vision enough to calculate a target and only
want power feedback.

```text
Module: testmaster_python_alignment_algorithm.blind.blind_power_j_step
Class:  BlindPowerJStep
```

Compatibility names:

```text
Module: testmaster_python_alignment_algorithm.alignment_step
Class:  BlindAlignStep
```

Other blind dropdown methods:

```text
Module: testmaster_python_alignment_algorithm.blind.blind_power_j_newton_step
Class:  BlindPowerJNewtonStep

Module: testmaster_python_alignment_algorithm.blind.blind_power_j_gradient_step
Class:  BlindPowerJGradientStep

Module: testmaster_python_alignment_algorithm.blind.blind_power_j_best_of_9_step
Class:  BlindPowerJBestOf9Step
```

Needs:

- `machine.power_mw`
- `machine.positions_um`
- `limits.allowed_stages`
- `limits.max_step_um`
- `state` from previous Python output

Example input file:

```text
testmaster_python_alignment_algorithm/examples/blind_power_input.json
```

Important algorithm settings:

```json
{
  "algorithm": {
    "axis_stages": ["Align_X1", "Align_Z1", "Align_X2", "Align_Z2"],
    "step_um": [2.0, 1.0, 0.5, 0.25],
    "max_iterations": 200,
    "power_abs_tolerance_mw": 0.000000001,
    "power_rel_tolerance": 0.0001
  }
}
```

How it behaves:

1. Store the first power/position as the best known point.
2. Probe one allowed stage by one step.
3. On the next call, compare the new power against the best power.
4. If better, keep that position.
5. If worse, request a move back to the previous best position.
6. Continue through stages, directions, and smaller step sizes.
7. Return `done` when the schedule is exhausted or `max_iterations` is reached.

## 7. Position Solve

Use this when a model, vision script, or recipe has calculated absolute target
positions for the solved pose.

```text
Module: testmaster_python_alignment_algorithm.assisted.position_solve_step
Class:  PositionSolveStep
```

Needs:

- `machine.positions_um`
- `targets.positions_um` or `model.target_positions_um`
- `limits.allowed_stages`
- `limits.max_step_um`

The statement returns bounded relative moves toward the supplied target. It
does not calculate the optical model on the machine unless you add that model
data upstream.

## 8. Position Solve With Visible Steps

Use this when the upstream model has produced a path of intermediate target
poses and you want YASE to move through them separately.

```text
Module: testmaster_python_alignment_algorithm.assisted.position_solve_j_steps_step
Class:  PositionSolveJStepsStep
```

Needs:

- `machine.positions_um`
- `targets.path_um`, or a final target as fallback
- `limits.allowed_stages`
- `limits.max_step_um`
- returned `state` from the previous call

## 9. Fixed-Z J-Matrix

Use this when YASE or a vision/model step supplies either transverse targets or
a fixed-Z response matrix. This class never requests `Align_Y*` moves.

```text
Module: testmaster_python_alignment_algorithm.assisted.fixed_z_j_matrix_step
Class:  FixedZJMatrixStep
```

Needs one of:

- `model.beam_error` plus `model.j_matrix`; or
- transverse target positions such as `Align_X1`, `Align_Z1`, `Align_X2`,
  `Align_Z2`.

Example model block:

```json
{
  "model": {
    "beam_error": {
      "x_um": -0.4,
      "x_angle_mrad": 0.1
    },
    "j_matrix": {
      "x": [[1.0, 0.0], [0.0, 1.0]]
    },
    "response_stages": {
      "x": ["Align_X1", "Align_X2"]
    }
  }
}
```

## 10. Non-Blind Helper: Absolute Target Positions

Use this when vision or a recipe has already calculated absolute stage targets.

```text
Module: testmaster_python_alignment_algorithm.assisted.target_position_step
Class:  TargetPositionStep
```

Needs:

- `machine.positions_um`
- `targets.positions_um`, `model.target_positions_um`, or `vision.target_positions_um`
- `limits.allowed_stages`
- `limits.max_step_um`

Example:

```json
{
  "schema_version": 1,
  "phase": "vision_coarse",
  "iteration": 0,
  "machine": {
    "power_mw": 0.0,
    "positions_um": {
      "Align_X1": 10.0,
      "Align_Z1": -2.0
    }
  },
  "targets": {
    "positions_um": {
      "Align_X1": 11.5,
      "Align_Z1": -1.0
    }
  },
  "limits": {
    "allowed_stages": ["Align_X1", "Align_Z1"],
    "max_step_um": 2.0
  },
  "algorithm": {
    "stage_order": ["Align_X1", "Align_Z1"],
    "tolerance_um": 0.05,
    "max_moves_per_call": 1
  }
}
```

The statement returns a relative move toward the target. YASE should call it
again after each move until it returns `done`.

## 11. Non-Blind Helper: Vision Relative Offsets

Use this when the vision script directly calculates relative stage corrections.

```text
Module: testmaster_python_alignment_algorithm.assisted.vision_offset_step
Class:  VisionOffsetStep
```

Needs:

- `vision.stage_offsets_um`
- `vision.confidence`
- `limits.allowed_stages`
- `limits.max_step_um`

Example:

```json
{
  "schema_version": 1,
  "phase": "vision_coarse",
  "iteration": 0,
  "machine": {
    "power_mw": 0.0,
    "positions_um": {
      "Align_X1": 10.0,
      "Align_Z1": -2.0
    }
  },
  "vision": {
    "source": "vision_assistant",
    "confidence": 0.92,
    "stage_offsets_um": {
      "Align_X1": 1.5,
      "Align_Z1": 1.0
    }
  },
  "limits": {
    "allowed_stages": ["Align_X1", "Align_Z1"],
    "max_step_um": 2.0
  },
  "algorithm": {
    "min_confidence": 0.8,
    "max_moves_per_call": 1
  }
}
```

YASE should rerun vision before calling this again. If it sends the same offsets
again without rerunning vision, Python will request the same correction again.

## 12. Local Test Without TestMaster

On your development PC, these files can be imported without the real
`testmaster_pyexec` package because `tmpython_compat.py` provides a fallback
base class.

Example:

```powershell
@'
import json
from testmaster_python_alignment_algorithm.alignment_step import BlindAlignStep

with open("testmaster_python_alignment_algorithm/examples/blind_power_input.json", "r", encoding="utf-8") as f:
    params = json.load(f)

print(json.dumps(BlindAlignStep().run(params), indent=2))
'@ | python -
```

On the real machine, install/configure the TestMaster TMPython support package
as described in:

```text
yase_process/YASE_PYTHON_INTEGRATION_README.md
```

## 13. Safety Rules For YASE

Before executing any Python-requested move, YASE must check:

- `action` is one of `move`, `done`, or `abort`;
- `schema_version` is `1`;
- every requested stage is in the allowed list for this phase;
- every distance is finite;
- every distance is within `limits.max_step_um`;
- the resulting absolute target position is inside machine soft limits;
- vacuum, interlocks, fiducials, TIA overload, and operator abort state are OK;
- motion completed before the next Python call.

Python is not the safety layer. It is only the calculation layer.
