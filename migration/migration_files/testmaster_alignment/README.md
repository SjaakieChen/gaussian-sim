# TestMaster Python Alignment Package

This package contains TMPython statement classes that are ready to copy to the
machine Python working directory.

The machine-copy bundle is also collected under:

```text
migration\migration_files\testmaster_alignment\
```

Recommended machine locations:

```text
D:\TestMasterData\<active-process>\Python\testmaster_alignment\  (process-specific test/development)
D:\TestMasterData\System\Python\testmaster_alignment\            (shared stable code)
```

Do not copy this folder into `.venv`. The venv holds installed packages; this
folder holds your project code.

On a shared machine, prefer the process-specific folder while testing. Put code
under `System\Python` only when every process/user should import the same
version.

## TMPython Modules

Use these values in `TMPython_ExecuteScript` when `WorkingDirectory` contains
the copied packages, for example:

```text
D:\TestMasterData\<active-process>\Python\
```

| Use case | Module | Class |
| --- | --- | --- |
| One-axis movement checkout | `testmaster_alignment.movement_command_test_step` | `MovementCommandTestStep` |
| Default blind power loop | `testmaster_alignment.alignment_step` | `BlindAlignStep` |
| Blind power J | `testmaster_alignment.blind.blind_power_j_step` | `BlindPowerJStep` |
| Blind power J Newton | `testmaster_alignment.blind.blind_power_j_newton_step` | `BlindPowerJNewtonStep` |
| Blind power J Gradient | `testmaster_alignment.blind.blind_power_j_gradient_step` | `BlindPowerJGradientStep` |
| Blind power J Best-of-9 | `testmaster_alignment.blind.blind_power_j_best_of_9_step` | `BlindPowerJBestOf9Step` |
| Fixed-Z J-matrix | `testmaster_alignment.assisted.fixed_z_j_matrix_step` | `FixedZJMatrixStep` |
| Position solve | `testmaster_alignment.assisted.position_solve_step` | `PositionSolveStep` |
| Position solve with visible steps | `testmaster_alignment.assisted.position_solve_j_steps_step` | `PositionSolveJStepsStep` |
| Move toward absolute targets | `testmaster_alignment.assisted.target_position_step` | `TargetPositionStep` |
| Apply vision-computed offsets | `testmaster_alignment.assisted.vision_offset_step` | `VisionOffsetStep` |

The image-recognition statement lives in the sibling `testmaster_vision`
package:

| Use case | Module | Class |
| --- | --- | --- |
| Read saved camera image | `testmaster_vision.image_step` | `ImageRecognitionStep` |

## Contract

Alignment classes expect input JSON with:

```json
{
  "schema_version": 1,
  "iteration": 0,
  "machine": {
    "power_mw": 0.0,
    "positions_um": {}
  },
  "vision": {},
  "targets": {},
  "model": {},
  "limits": {
    "allowed_stages": [],
    "max_step_um": 1.0
  },
  "algorithm": {},
  "state": {}
}
```

`ImageRecognitionStep` only needs `schema_version`, `vision.image_path`, and
optional `algorithm` settings such as `threshold`, `polarity`, and
`min_area_px`.

Every class returns:

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
  "message": "status text",
  "state": {}
}
```

Valid actions are `move`, `done`, and `abort`. The shared output helper can
format up to two moves per call, but the checkout input and most examples use
`max_moves_per_call: 1` so YASE only has to validate and execute one axis at a
time.

YASE/TestMaster must still validate all moves before executing hardware motion.
Python only proposes the next action.

## Examples

Example JSON inputs are in:

```text
testmaster_alignment\examples\
testmaster_vision\examples\
```

Run a local smoke test from the repository root:

```powershell
python -m pytest tests\test_testmaster_alignment.py
```

Full machine setup is documented in:

```text
yase_process\YASE_TMPYTHON_SETUP.md
```
