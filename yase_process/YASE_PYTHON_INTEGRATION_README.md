# YASE Python Integration README

This document defines the expected format for calling Python from a
YASE/TestMaster sequence during closed-loop ball-lens alignment.

It is a programming guide only. It is not a hardware-validated machine
procedure.

For general YASE programming and hardware-facing statement details, see:

- `YASE_PROGRAMMING_GUIDE.md`
- `YASE_2_LENS_AUTO_ALIGNMENT_FUNCTION_REFERENCE.md`
- `YASE_MACHINE_INTERFACE_AUDIT.md`

For ready-to-copy Python statement files and per-algorithm JSON examples, see:

- `../testmaster_python_alignment_algorithm/README.md`
- `../testmaster_python_alignment_algorithm/SUBPROCESS_MAP.md`

## 1. Purpose

The intended architecture is:

```text
YASE/TestMaster owns:
  - hardware motion
  - IO
  - camera triggering
  - TIA/power reads
  - safety checks
  - waits, aborts, and logging

Python owns:
  - alignment state
  - blind search logic
  - vision/math post-processing if needed
  - deciding the next requested movement
```

Python must not directly drive the machine axes. Python should return a
movement request. YASE must validate the request before executing it.

The normal loop is:

```text
read current machine state
call Python
parse Python result
validate requested move
move machine
wait for axes
check move errors
read new power
repeat
```

## 2. Primary Integration Path

Use the TestMaster Python extension and the YASE statement:

```text
TMPython_ExecuteScript
```

For the copy-ready package in this repo, the default blind example uses:

```text
Interpreter: Python_310
Module:      testmaster_python_alignment_algorithm.alignment_step
Class:       BlindAlignStep
```

The exact parameter names shown in YASE depend on the installed TestMaster
prototype. After machine configuration, re-import prototypes in YASE and use
the imported `TMPython_ExecuteScript` parameter names as the source of truth.

Conceptually, each call provides:

```text
Interpreter name  -> Python_310
Python module     -> testmaster_python_alignment_algorithm.alignment_step
Python class      -> BlindAlignStep
Input JSON        -> current alignment state
Output JSON       -> next action requested by Python
```

## 3. YASE Call Pattern

The alignment sequence should call Python once per decision step.

Pseudo-YASE flow:

```text
L_Start
  StageCheckAllFiducialed -> d_Fiducialed
  ifnum d_Fiducialed <> 1
    Goto L_Error

  GetStringVar Process "" Alignment TIA_Tx -> s_PowerMeter
  GetNumVar System "" MainVelocity VelocityAlignXSlow -> d_Velocity

  set 0 -> d_Iteration

L_Loop
  QueryStage Align_X1 Absolute -> d_Align_X1
  QueryStage Align_Z1 Absolute -> d_Align_Z1
  QueryStage Align_Y1 Absolute -> d_Align_Y1
  QueryStage Align_X2 Absolute -> d_Align_X2
  QueryStage Align_Z2 Absolute -> d_Align_Z2
  QueryStage Align_Y2 Absolute -> d_Align_Y2

  SEQ::SUB_SysReadAveragePower s_PowerMeter 10 10
    -> d_Power_mW, d_Power_dBm, d_Power_mA

  Build input JSON string s_PythonInputJson

  TMPython_ExecuteScript
    Interpreter = Python_310
    Module      = testmaster_python_alignment_algorithm.alignment_step
    Class       = BlindAlignStep
    Input JSON  = s_PythonInputJson
    Result JSON = s_PythonResultJson

  Parse s_PythonResultJson:
    action
    stage1
    distance1_um
    optional stage2
    optional distance2_um
    message

  if action = "done"
    Goto L_Done

  if action = "abort"
    Goto L_Error

  Validate requested move:
    - action must be "move"
    - stage names must be allowed
    - distances must be finite
    - distances must be within configured step limits
    - target positions must be inside safe axis limits
    - vacuum/gripper/lens-held checks must still pass
    - TIA must not be overloaded or saturated

  MoveStage stage1 d_Velocity distance1_um No sync Relative
  if stage2 is not empty
    MoveStage stage2 d_Velocity distance2_um No sync Relative

  SEQ::SUB_SYS_AxisWaitFinishList "stage1,stage2"
  SEQ::SUB_SysCheckAxisMove stage1 stage2 "" "" "" ""
    -> d_ErrorType, s_ErrorMessage

  ifnum d_ErrorType <> 0
    Goto L_Error

  calc d_Iteration + 1 -> d_Iteration
  Goto L_Loop

L_Done
  Store final positions and power
  Return ErrorType = 0
  EndSeq

L_Error
  Stop without releasing held lenses unless the approved abort procedure says so
  Return ErrorType <> 0
  EndSeq
```

Use the TestMaster JSON statement library, if available, to build and parse
JSON values. If JSON statements are not configured, keep the first test simple:
pass a fixed JSON string to Python and display the returned JSON string before
adding motion.

## 4. Input JSON Contract

All Python calls should use this input format. The Python package also accepts
the older flat `power_mw`, `positions_um`, and `target_positions_um` keys for
early bench tests, but new YASE subprocesses should prefer the nested form.

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
      "Align_Y2": 250.0
    }
  },
  "vision": {
    "source": "vision_assistant",
    "lens1_x_px": 1234.5,
    "lens1_y_px": 987.6,
    "lens2_x_px": 1300.1,
    "lens2_y_px": 990.2,
    "waveguide_x_px": 1500.0,
    "waveguide_y_px": 1001.0,
    "confidence": 0.92
  },
  "targets": {},
  "model": {},
  "limits": {
    "allowed_stages": [
      "Align_X1",
      "Align_Z1",
      "Align_X2",
      "Align_Z2"
    ],
    "max_step_um": 2.0,
    "max_abs_um": {
      "Align_X1": 100.0,
      "Align_Z1": 100.0,
      "Align_X2": 100.0,
      "Align_Z2": 100.0
    }
  }
}
```

Field meanings:

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | yes | Contract version. Use `1` for this document. |
| `phase` | yes | Current alignment phase, for example `vision_coarse` or `blind_align`. |
| `iteration` | yes | Loop count controlled by YASE. |
| `machine.power_mw` | yes | Latest measured optical power in mW. |
| `machine.positions_um` | yes | Latest absolute axis positions from `QueryStage`. |
| `vision` | yes | Vision-derived values. Use `{}` if no vision data is available. |
| `targets` | no | Absolute target positions or path points for non-blind algorithms. |
| `model` | no | Optional model/J-matrix values from an upstream calculation. |
| `limits` | yes | Limits that Python should respect before proposing a move. |

YASE must still enforce limits after Python returns. The `limits` block helps
Python choose sensible moves; it is not a safety guarantee.

## 5. Output JSON Contract

Python must return exactly one of these actions:

```text
move
done
abort
```

Move example:

```json
{
  "schema_version": 1,
  "action": "move",
  "move_count": 1,
  "stage1": "Align_X1",
  "distance1_um": 1.0,
  "stage2": "Align_X2",
  "distance2_um": -1.0,
  "moves": [
    {
      "stage": "Align_X1",
      "distance_um": 1.0,
      "mode": "relative"
    },
    {
      "stage": "Align_X2",
      "distance_um": -1.0,
      "mode": "relative"
    }
  ],
  "message": "sample differential X at +1 um",
  "state": {
    "step_um": 1.0,
    "axis": "differential_x",
    "best_power_mw": 0.0123
  }
}
```

Done example:

```json
{
  "schema_version": 1,
  "action": "done",
  "move_count": 0,
  "stage1": "",
  "distance1_um": 0.0,
  "moves": [],
  "message": "alignment complete",
  "state": {
    "best_power_mw": 0.145,
    "iterations": 42
  }
}
```

Abort example:

```json
{
  "schema_version": 1,
  "action": "abort",
  "move_count": 0,
  "stage1": "",
  "distance1_um": 0.0,
  "moves": [],
  "message": "power dropped below allowed floor",
  "state": {
    "last_power_mw": 0.0
  }
}
```

Field meanings:

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | yes | Must be `1`. |
| `action` | yes | `move`, `done`, or `abort`. |
| `move_count` | yes | Number of requested stage moves. Use `0`, `1`, or `2`. |
| `stage1` | yes | First stage to move, or empty string when no move is requested. |
| `distance1_um` | yes | First relative movement in um. |
| `stage2` | no | Optional second stage to move. |
| `distance2_um` | no | Optional second relative movement in um. |
| `moves` | recommended | Structured list of relative moves for logging or future parsing. |
| `message` | yes | Human-readable reason/status for logs. |
| `state` | no | Python-owned diagnostic or algorithm state. |

Do not return absolute target coordinates for the blind loop. Return small
relative moves in micrometres. YASE should use `MoveStage ... Relative`.

## 6. Python Code Format

The Python package should be placed under the working directory configured in
`TMPython.ini`. The copy-ready package in this repo is:

Expected folder:

```text
#SM_SYSTEM#\Python\testmaster_python_alignment_algorithm\
```

The code below is only an illustrative minimal statement. For the real files to
copy, use `../testmaster_python_alignment_algorithm/`.

Minimal Python statement:

```python
from __future__ import annotations

import math
from typing import Any

from tmpython.statement import TMPythonStatementJ


class BlindAlignStep(TMPythonStatementJ):
    def __init__(self) -> None:
        super().__init__()
        self.best_power_mw = -math.inf
        self.next_sign = 1.0

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        schema_version = int(params_in.get("schema_version", 0))
        if schema_version != 1:
            return self._abort(f"unsupported schema_version: {schema_version}")

        power_mw = float(params_in.get("power_mw", 0.0))
        iteration = int(params_in.get("iteration", 0))
        limits = dict(params_in.get("limits", {}))
        allowed_stages = set(limits.get("allowed_stages", []))
        max_step_um = float(limits.get("max_step_um", 1.0))

        if power_mw > self.best_power_mw:
            self.best_power_mw = power_mw

        if iteration >= 100:
            return {
                "schema_version": 1,
                "action": "done",
                "move_count": 0,
                "stage1": "",
                "distance1_um": 0.0,
                "message": "maximum iterations reached",
                "state": {"best_power_mw": self.best_power_mw},
            }

        stage = "Align_X1"
        if stage not in allowed_stages:
            return self._abort(f"stage not allowed: {stage}")

        distance_um = min(1.0, max_step_um) * self.next_sign
        self.next_sign *= -1.0

        return {
            "schema_version": 1,
            "action": "move",
            "move_count": 1,
            "stage1": stage,
            "distance1_um": distance_um,
            "message": "example blind alignment step",
            "state": {
                "best_power_mw": self.best_power_mw,
                "iteration": iteration,
            },
        }

    def _abort(self, message: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "action": "abort",
            "move_count": 0,
            "stage1": "",
            "distance1_um": 0.0,
            "message": message,
        }
```

TestMaster starts an independent Python process for a running sequence
instance. Repeated Python calls in the same sequence instance can therefore
preserve object/process state, depending on the installed TMPython behavior.
Still pass all critical machine state from YASE every call; do not rely only
on Python memory for safety.

## 7. Vision Data Options

Recommended first implementation:

```text
YASE grabs image
YASE runs existing Vision Assistant script
YASE extracts coordinates with VA_TM_GetValue
YASE passes numeric coordinates to Python in the vision block
```

This uses the machine's existing camera and calibration workflow.

Alternative implementation:

```text
YASE grabs/saves image
YASE passes image path to Python
Python processes the image
Python returns movement request
```

Only use the image-path approach after the image save format, camera
calibration, pixel-to-stage transform, lighting, and failure criteria are
defined.

## 8. Machine Configuration

Perform these steps on the actual TestMaster machine.

### 8.1 Back Up Current Configuration

Back up the active machine files before changing anything:

```text
#SM_CONFIG#\Sequencer.ini
#SM_CONFIG#\TMPython.ini
#SM_PROCESS#
```

### 8.2 Install Python

Install a machine-approved Python interpreter. Record its absolute path, for
example:

```text
C:\Users\operator\AppData\Local\Programs\Python\Python310\python.exe
```

### 8.3 Install TestMaster Python Support

Install the TestMaster Python package into the same interpreter:

```powershell
"C:\Path\To\Python310\python.exe" -m pip install --upgrade "C:\path\to\testmaster_pyexec-<version>.zip"
```

The `testmaster_pyexec` package must come from the TestMaster/vendor
installation package.

### 8.4 Configure TMPython.ini

Create or edit:

```text
#SM_CONFIG#\TMPython.ini
```

Example:

```ini
[Python_310]
WorkingDirectory = "#SM_SYSTEM#\Python\"
PythonInterpreter = "C:\Users\operator\AppData\Local\Programs\Python\Python310\python.exe"
LogDirectory = "#SM_SYSTEM#\Python\log"
```

Notes:

- `WorkingDirectory` is where `alignment_step.py` should live.
- `PythonInterpreter` must be an absolute Windows path.
- `LogDirectory` is recommended for debugging.

### 8.5 Add TMPython Statements To The Sequencer

In TestMaster:

```text
Sequencer -> Config Statements...
```

Then:

1. Select `Local machine`.
2. Use the `Process` tab for initial testing.
3. In `Statement Locations`, click `Add LLB...`.
4. Add the TMPython statement library:

```text
#SM_ROOT#\core\Master_Progs\Sequencer.llb\TMPython\Statements
```

If the file browser does not understand `#SM_ROOT#`, browse to the real
TestMaster installation folder, for example:

```text
C:\TestMaster\TestMaster\core\Master_Progs\Sequencer.llb\TMPython\Statements
```

5. Also add the TestMaster JSON statement library if it is not already
   available and if the process will build/parse JSON inside YASE.
6. Click `Save & Recompile`.

### 8.6 Re-Import YASE Prototypes

Open YASE:

```text
Window -> Open Perspective -> Other... -> Yase
```

Then import prototypes:

```text
File -> Prototypes -> Import from server
```

Use the local TestMaster server, normally:

```text
127.0.0.1
```

After import, verify that the prototype list contains:

```text
TMPython_ExecuteScript
```

If it does not appear, do not write alignment logic yet. Fix the TestMaster
Python extension setup first.

## 9. First Machine Test

Before any movement, test only the Python call.

Use a Python class that returns a fixed result:

```python
from tmpython.statement import TMPythonStatementJ


class BlindAlignStep(TMPythonStatementJ):
    def run(self, params_in):
        return {
            "schema_version": 1,
            "action": "done",
            "move_count": 0,
            "stage1": "",
            "distance1_um": 0.0,
            "message": "Python call works",
            "state": {"received": params_in},
        }
```

Create a small YASE test sequence that:

```text
calls TMPython_ExecuteScript with {"schema_version": 1}
displays the returned JSON
does not move any axis
```

Only after that works should the sequence read stage positions and power. Add
one small relative move only after read-only calls are proven.

## 10. SystemExecute Fallback

The current checked-in `prototypes.xml` includes `SystemExecute`. It can call
external programs, including Python:

```text
SystemExecute
  command line = "C:\Path\To\python.exe alignment_step_cli.py input.json output.json"
  working directory = "#SM_SYSTEM#\Python"
  wait until completion? = TRUE
```

Use this only if `TMPython_ExecuteScript` cannot be enabled.

Reasons it is a fallback:

- state is harder to preserve between calls;
- JSON must be passed through files or command-line strings;
- error handling is weaker;
- command quoting and paths are more fragile;
- every call may start a new Python process.

If using this fallback, keep the same input and output JSON schemas defined in
this document.

## 11. Safety Rules For Python-Driven Alignment

Before any Python-requested move, YASE must check:

- stages are fiducialed;
- requested stage names are allowed for the current phase;
- requested distances are finite;
- requested distances are within the configured maximum step size;
- target absolute positions are inside safe limits;
- vacuum/gripper/lens-held checks still pass;
- TIA is not overloaded or saturated;
- the operator has not requested abort;
- the error handler preserves the approved safe state.

On Python `abort`, axis error, vacuum loss, overload, or invalid JSON, stop the
alignment loop and route to the approved YASE error/abort handling path. Do not
release lenses or move to a generic safe pose unless that exact behavior has
been validated on the machine.
