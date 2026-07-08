# TMPython Call Guide

This guide explains how to call the Python migration classes from
`TMPython_ExecuteScript`.

## Machine Folder Layout

The most self-contained layout is to keep the venv, Python packages, and logs
under the active process folder:

```text
D:\TestMasterData\<active-process>\Python\
  .venv\
  testmaster_alignment\
  testmaster_vision\
  log\
```

This avoids changing package versions for other users/processes.

An optional shared-venv layout is:

```text
D:\TestMasterData\System\Python\
  .venv\
D:\TestMasterData\<active-process>\Python\
  testmaster_alignment\
  testmaster_vision\
  log\
```

Copy these folders from this bundle:

```text
migration\migration_files\testmaster_alignment\
migration\migration_files\testmaster_vision\
```

Do not copy the packages into `.venv`.

## TMPython.ini

Create or edit the active machine config file:

```text
#SM_CONFIG#\TMPython.ini
```

This bundle includes a loose template at:

```text
migration\TMPython.ini
```

Copy that template to the machine config folder and replace the placeholder
process paths.

On a shared machine, use one central `TMPython.ini` with multiple named
sections. Do not make users manually change the same section whenever they
switch process. Each process/test should get its own section name, and each
YASE file should use that section in its `Interpreter` field.

Recommended pattern:

```ini
[Python_310_SHARED]
WorkingDirectory = "D:\TestMasterData\System\Python\"
PythonInterpreter = "D:\TestMasterData\System\Python\.venv\Scripts\python.exe"
LogDirectory = "D:\TestMasterData\System\Python\log"

[Python_310_ALIGNMENT_TEST]
WorkingDirectory = "D:\TestMasterData\<active-process>\Python\"
PythonInterpreter = "D:\TestMasterData\<active-process>\Python\.venv\Scripts\python.exe"
LogDirectory = "D:\TestMasterData\<active-process>\Python\log"

[Python_310_OTHER_PROCESS]
WorkingDirectory = "D:\TestMasterData\<other-process>\Python\"
PythonInterpreter = "D:\TestMasterData\<other-process>\Python\.venv\Scripts\python.exe"
LogDirectory = "D:\TestMasterData\<other-process>\Python\log"
```

For this checkout, use:

```ini
[Python_310_ALIGNMENT_TEST]
WorkingDirectory = "D:\TestMasterData\<active-process>\Python\"
PythonInterpreter = "D:\TestMasterData\<active-process>\Python\.venv\Scripts\python.exe"
LogDirectory = "D:\TestMasterData\<active-process>\Python\log"
```

`Interpreter` in YASE is the INI section name. `PythonInterpreter` is the
actual `python.exe` that runs the class. `WorkingDirectory` is the folder that
contains `testmaster_alignment` and `testmaster_vision`.

So if a process uses:

```text
Interpreter = Python_310_ALIGNMENT_TEST
```

then TestMaster reads the `[Python_310_ALIGNMENT_TEST]` section and imports
Python files from that section's `WorkingDirectory`. Other users can keep
using their own section without changing yours.

## How A YASE Call Maps To Python

In `TMPython_ExecuteScript`:

```text
Interpreter = Python_310_ALIGNMENT_TEST
Module      = testmaster_alignment.movement_command_test_step
Class       = MovementCommandTestStep
Input JSON  = s_PythonInputJson
Result JSON = s_PythonResultJson
```

This means:

```python
from testmaster_alignment.movement_command_test_step import MovementCommandTestStep

result = MovementCommandTestStep().run(input_json)
```

## First Read-Only Test

Use the YASE template:

```text
migration\yase_files\SUB_TMPython_MovementCommand_ReadOnly.xseq
```

For an example that also stores the JSON input and output, use:

```text
migration\yase_files\SUB_TMPython_JsonInOut_StoreExample.xseq
```

That example keeps the JSON in YASE variables:

```text
s_PythonInputJson
s_PythonResultJson
```

It stores the latest values in `processvar.ini`:

```text
[PythonMigration]
LastInputJson
LastResultJson
```

It also writes optional files:

```text
#SM_PROCESS#\Python\log\tmpython_last_input.json
#SM_PROCESS#\Python\log\tmpython_last_result.json
```

It calls:

```text
Module = testmaster_alignment.movement_command_test_step
Class  = MovementCommandTestStep
```

Expected output:

```text
migration\migration_files\output_examples\movement_command_test_output.json
```

Run this read-only first. It should display returned JSON and stop. Do not add
`MoveStage` until this call works.

## Classes

| Use case | Module | Class | Example input | Example output |
| --- | --- | --- | --- | --- |
| One-axis movement checkout | `testmaster_alignment.movement_command_test_step` | `MovementCommandTestStep` | `testmaster_alignment\examples\movement_command_test_input.json` | `output_examples\movement_command_test_output.json` |
| Default blind power loop | `testmaster_alignment.alignment_step` | `BlindAlignStep` | `testmaster_alignment\examples\blind_power_input.json` | `output_examples\blind_align_output.json` |
| Blind power pattern base | `testmaster_alignment.blind.blind_power_pattern_step` | `BlindPowerPatternStep` | `testmaster_alignment\examples\blind_power_input.json` | `output_examples\blind_power_pattern_output.json` |
| Blind power J | `testmaster_alignment.blind.blind_power_j_step` | `BlindPowerJStep` | `testmaster_alignment\examples\blind_power_input.json` | `output_examples\blind_power_j_output.json` |
| Blind power J Newton | `testmaster_alignment.blind.blind_power_j_newton_step` | `BlindPowerJNewtonStep` | `testmaster_alignment\examples\blind_power_input.json` | `output_examples\blind_power_j_newton_output.json` |
| Blind power J Gradient | `testmaster_alignment.blind.blind_power_j_gradient_step` | `BlindPowerJGradientStep` | `testmaster_alignment\examples\blind_power_input.json` | `output_examples\blind_power_j_gradient_output.json` |
| Blind power J Best-of-9 | `testmaster_alignment.blind.blind_power_j_best_of_9_step` | `BlindPowerJBestOf9Step` | `testmaster_alignment\examples\blind_power_input.json` | `output_examples\blind_power_j_best_of_9_output.json` |
| Fixed-Z J-matrix | `testmaster_alignment.assisted.fixed_z_j_matrix_step` | `FixedZJMatrixStep` | `testmaster_alignment\examples\fixed_z_j_matrix_input.json` | `output_examples\fixed_z_j_matrix_output.json` |
| Position solve | `testmaster_alignment.assisted.position_solve_step` | `PositionSolveStep` | `testmaster_alignment\examples\position_solve_input.json` | `output_examples\position_solve_output.json` |
| Position solve with visible steps | `testmaster_alignment.assisted.position_solve_j_steps_step` | `PositionSolveJStepsStep` | `testmaster_alignment\examples\position_solve_j_steps_input.json` | `output_examples\position_solve_j_steps_output.json` |
| Move toward absolute targets | `testmaster_alignment.assisted.target_position_step` | `TargetPositionStep` | `testmaster_alignment\examples\target_position_input.json` | `output_examples\target_position_output.json` |
| Apply vision-computed offsets | `testmaster_alignment.assisted.vision_offset_step` | `VisionOffsetStep` | `testmaster_alignment\examples\vision_offset_input.json` | `output_examples\vision_offset_output.json` |
| Read saved camera image | `testmaster_vision.image_step` | `ImageRecognitionStep` | `testmaster_vision\examples\image_recognition_input.json` | `output_examples\image_recognition_output.json` |

## Output Actions

Python returns one of three actions:

```text
move
done
abort
```

For `move`, YASE should read `stage1` and `distance1_um`, validate them, then
execute a relative `MoveStage` only after all machine safety checks pass.

The shared output helper supports up to two moves, but the first machine tests
should keep `move_count = 1`.

For `done`, YASE exits the loop normally.

For `abort`, YASE routes to the approved error/abort handling path.

## Local Smoke Test

From the repository root:

```powershell
python -m pytest tests\test_yase_integration.py tests\test_testmaster_alignment.py
python -m compileall migration\migration_files\testmaster_alignment migration\migration_files\testmaster_vision
```
