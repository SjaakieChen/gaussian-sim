# YASE TMPython Setup

For the checked-out Python Automation machine, this older setup guide is
secondary to the repository-level `MACHINE_CONFIGURATION.md`. Use that file as
the authority for actual paths, interpreter names, and TMPython parameter names
before copying or editing any `.xseq`.

Legacy note: root-level `testmaster_alignment` and `testmaster_vision` packages
have been removed from the active checkout. Current machine-facing Python lives
under `migrations\migration_v*\...`; this folder is retained as example YASE
process data.

Use this checklist to prepare the machine Python environment used by
`TMPython_ExecuteScript`.

For the ready-to-copy migration bundle, start here:

```text
..\migrations\README.md
```

The bundles are versioned:

```text
..\migrations\migration_v1\
..\migrations\migration_v2\
..\migrations\migration_v3\
```

## 1. Machine Paths

From the machine screenshots:

- The active config folder appears to be `D:\TestMasterData\config`.
- `MasterMain.ini` defines project folders such as `System`, `Process`, `Data`,
  `User`, and `Log` under `#SM_PROJECT#`.
- The TestMaster application itself is on `C:\TestMaster\TestMaster`.

Keep those separate:

| Location | Use |
| --- | --- |
| `C:\TestMaster\TestMaster\...` | TestMaster program files and statement libraries. Do not put your Python project here. |
| `D:\TestMasterData\config` | Machine configuration INI files. Put `TMPython.ini` here if this is the active `#SM_CONFIG#`. |
| `D:\TestMasterData\System\Python` | Recommended place for the shared venv, requirements, shared stable code, and shared logs. |
| `D:\TestMasterData\<active-process>\Python` | Recommended place for process-owned Python statement files during test/development. |

`TMPython.ini` may not exist yet. That is normal if TMPython has not been
configured on the machine before. The TestMaster manual says the file belongs
at:

```text
#SM_CONFIG#\TMPython.ini
```

For your screenshot, that likely means:

```text
D:\TestMasterData\config\TMPython.ini
```

Before creating it, confirm that `D:\TestMasterData\config` is the active
config folder on the machine.

Also confirm the real `#SM_SYSTEM#` folder before creating the Python folder.
Your `MasterMain.ini` screenshot shows:

```ini
[path_settings]
system_dir_path = "#SM_PROJECT#\\System"
```

If the active project expands this to a different D-drive folder, use that
actual `System` folder instead of `D:\TestMasterData\System`. The examples
below assume:

```text
#SM_SYSTEM# = D:\TestMasterData\System
```

## 2. Target Python Layout

Recommended self-contained process layout:

```text
D:\TestMasterData\<active-process>\Python\
  .venv\
  testmaster_alignment\
  testmaster_vision\
  requirements.txt
  log\
```

This keeps code, installed packages, and TMPython logs together for one process
and avoids changing package versions for other users.

Optional shared interpreter layout:

```text
D:\TestMasterData\System\Python\
  .venv\
  requirements.txt
  log\
```

With the shared interpreter layout, the process-owned code layout is:

```text
D:\TestMasterData\<active-process>\Python\
  testmaster_alignment\
  testmaster_vision\
  log\
```

`<active-process>` means the real folder TestMaster resolves as `#SM_PROCESS#`
for the process you are editing. If TMPython does not expand `#SM_PROCESS#` in
`TMPython.ini`, use the absolute D-drive process path.

Keep these roles separate:

| Path | Purpose |
| --- | --- |
| `.venv\` | Python interpreter environment and installed packages. |
| `testmaster_alignment\` | Alignment package files imported by TMPython. |
| `testmaster_vision\` | Vision-recognition Python files imported by TMPython. |
| `requirements.txt` | Package list to install into `.venv`. |
| `log\` | TMPython log output. |

Do not put your own `.py` files inside `.venv`.

On a shared machine, do not overwrite a global Python working folder that other
processes already use. For the most isolated setup, keep the venv and code
together under the active process's `Python` folder.

Do not make users edit the same `TMPython.ini` section when switching process.
Keep one central `TMPython.ini`, but create multiple named sections. Each YASE
file chooses its own section through the `Interpreter` field.

## 3. Create The Folder And Venv

Run PowerShell on the machine.

```powershell
$Base = "D:\TestMasterData\<active-process>\Python"
$MachinePython = "C:\Users\operator\AppData\Local\Programs\Python\Python310\python.exe"

New-Item -ItemType Directory -Force $Base
New-Item -ItemType Directory -Force "$Base\log"
cd $Base

& $MachinePython -m venv .venv
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
```

Replace `$MachinePython` with the real installed Python path on the machine.
Replace `<active-process>` with the real active process folder name/path.
The final command should print:

```text
D:\TestMasterData\<active-process>\Python\.venv\Scripts\python.exe
```

If you do not know the installed Python path, check:

```powershell
where python
py -0p
```

Use a machine-approved Python version. Match the version expected by the
installed TestMaster TMPython support package.

## 4. Install Packages

Copy the repository `requirements.txt` to:

```text
D:\TestMasterData\<active-process>\Python\requirements.txt
```

It should contain the machine runtime packages:

```text
numpy
opencv-python-headless
pillow
```

Install into the venv:

```powershell
cd "D:\TestMasterData\<active-process>\Python"
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -c "import numpy, cv2, PIL; print('packages ok')"
```

Install the TestMaster Python support package into the same venv:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade "C:\path\to\testmaster_pyexec-<version>.zip"
```

The `testmaster_pyexec` package must come from the TestMaster/vendor install
package. Without it, `from tmpython.statement import TMPythonStatementJ` will
fail on the real machine.

## 5. Copy Python Files

For first machine tests, copy the packages into the process-owned working
directory:

```text
D:\TestMasterData\<active-process>\Python\testmaster_alignment\
D:\TestMasterData\<active-process>\Python\testmaster_vision\
```

Copy the repository `requirements.txt` into the same process Python folder:

```text
D:\TestMasterData\<active-process>\Python\requirements.txt
```

Do not copy the repository `.git`, `.pytest_cache`, `tests`, `testmaster documentation`,
or simulator files to the machine Python folder. The JSON examples inside the
two package folders are useful and can stay.

Example Python file:

```text
D:\TestMasterData\<active-process>\Python\testmaster_vision\image_step.py
```

If a folder is imported as a package, include an `__init__.py` file in it.

## 6. Create Or Edit TMPython.ini

Open or create:

```text
D:\TestMasterData\config\TMPython.ini
```

If nobody else uses the existing `[Python_310]` section, minimum contents can
be:

```ini
[Python_310]
WorkingDirectory = "D:\TestMasterData\System\Python\"
PythonInterpreter = "D:\TestMasterData\System\Python\.venv\Scripts\python.exe"
LogDirectory = "D:\TestMasterData\System\Python\log"
```

On a shared machine, avoid changing a section that another process already
uses. Add new named sections instead:

```ini
[Python_310_SHARED]
WorkingDirectory = "D:\TestMasterData\System\Python\"
PythonInterpreter = "D:\TestMasterData\System\Python\.venv\Scripts\python.exe"
LogDirectory = "D:\TestMasterData\System\Python\log"

[Python_310_PYTHON_AUTOMATION_INTERPRETER]
WorkingDirectory = "D:\TestMasterData\<active-process>\Python\"
PythonInterpreter = "D:\TestMasterData\<active-process>\Python\.venv\Scripts\python.exe"
LogDirectory = "D:\TestMasterData\<active-process>\Python\log"

[Python_310_OTHER_PROCESS]
WorkingDirectory = "D:\TestMasterData\<other-process>\Python\"
PythonInterpreter = "D:\TestMasterData\<other-process>\Python\.venv\Scripts\python.exe"
LogDirectory = "D:\TestMasterData\<other-process>\Python\log"
```

Then use `Interpreter = Python_310_PYTHON_AUTOMATION_INTERPRETER` in the YASE
`TMPython_ExecuteScript` statement. This keeps your test code isolated while
using your process-local venv and installed packages. Other users can use
their own section without manually changing yours.

Use Notepad from PowerShell:

```powershell
notepad "D:\TestMasterData\config\TMPython.ini"
```

If TestMaster does not find this interpreter after restart, check whether the
active `#SM_CONFIG#` is different from `D:\TestMasterData\config`. The correct
file is always:

```text
#SM_CONFIG#\TMPython.ini
```

Restart TestMaster/YASE after editing this file, or reload the TMPython
configuration if the machine procedure provides a reload action.

## 7. Add TMPython Statement Library

`TMPython.ini` only configures Python interpreters. YASE also needs the
TMPython statement library available in the Sequencer.

In TestMaster:

```text
Sequencer -> Config Statements...
```

Add the TMPython statement library from the TestMaster application install:

```text
C:\TestMaster\TestMaster\core\Master_Progs\Sequencer.llb\TMPython\Statements
```

Then save/recompile and re-import YASE prototypes from the server if required
by the machine workflow.

## 8. How YASE Selects Python Code

In a YASE `TMPython_ExecuteScript` statement:

```text
Interpreter = Python_310_PYTHON_AUTOMATION_INTERPRETER
Module      = testmaster_vision.image_step
Class       = ImageRecognitionStep
ParamIn     = s_PythonInputJson
ParamOut    = s_PythonResultJson
```

The fields map like this:

| YASE/TMPython field | Meaning |
| --- | --- |
| `Interpreter = Python_310_PYTHON_AUTOMATION_INTERPRETER` | Look up `[Python_310_PYTHON_AUTOMATION_INTERPRETER]` in `TMPython.ini`. |
| `PythonInterpreter` | The `python.exe` that runs the code. |
| `WorkingDirectory` | The folder Python imports your files from. |
| `Module` | The Python file/package path, using dots instead of slashes. |
| `Class` | The class inside that module whose `run(...)` method is called. |

The `Interpreter` value is an INI section name, not the Python executable
itself. The `PythonInterpreter` path inside that INI section is the executable.
The `Class` value is the Python class in the selected module that TMPython
instantiates and calls.

With:

```ini
WorkingDirectory = "D:\TestMasterData\<active-process>\Python\"
```

this YASE module:

```text
Module = testmaster_vision.image_step
```

loads:

```text
D:\TestMasterData\<active-process>\Python\testmaster_vision\image_step.py
```

and this YASE class:

```text
Class = ImageRecognitionStep
```

loads:

```python
from testmaster_vision.image_step import ImageRecognitionStep
```

## 9. Movement Command Checkout Statement

The copy-ready movement checkout statement is:

```text
D:\TestMasterData\<active-process>\Python\testmaster_alignment\movement_command_test_step.py
```

It returns one small relative movement request. It does not move hardware by
itself; YASE receives the JSON result, validates it, and then calls `MoveStage`.

Use this YASE configuration:

```text
Interpreter = Python_310_PYTHON_AUTOMATION_INTERPRETER
Module      = testmaster_alignment.movement_command_test_step
Class       = MovementCommandTestStep
ParamIn     = fixed JSON or s_PythonInputJson
ParamOut    = s_PythonResultJson
```

Start with this example input:

```json
{
  "schema_version": 1,
  "phase": "movement_command_test",
  "iteration": 0,
  "machine": {
    "power_mw": 0.0,
    "positions_um": {
      "Align_X1": 0.0
    }
  },
  "limits": {
    "allowed_stages": [
      "Align_X1"
    ],
    "max_step_um": 0.1
  },
  "algorithm": {
    "name": "movement_command_test",
    "stage": "Align_X1",
    "distance_um": 0.1
  }
}
```

Expected Python result:

```json
{
  "schema_version": 1,
  "action": "move",
  "move_count": 1,
  "stage1": "Align_X1",
  "distance1_um": 0.1,
  "moves": [
    {
      "stage": "Align_X1",
      "distance_um": 0.1,
      "mode": "relative"
    }
  ],
  "message": "movement command checkout requested Align_X1 relative move by 0.1 um",
  "state": {
    "algorithm": "movement_command_test",
    "requested_stage": "Align_X1",
    "requested_distance_um": 0.1,
    "max_step_um": 0.1,
    "test_only": true
  }
}
```

First run the statement and display/log `s_PythonResultJson` without moving.
After the returned JSON is confirmed, wire YASE to:

```text
if action = "move"
  confirm stage1 is exactly the approved test axis
  confirm abs(distance1_um) <= 0.1
  MoveStage stage1 <slow velocity> distance1_um No sync Relative
  wait for the axis to finish
  check the axis move result
```

Keep this first checkout to one axis and one move. The shared contract can
represent up to two moves, but one move per Python call is safer while proving
the TMPython, JSON parsing, and YASE motion wiring.

## 10. Image Recognition Statement

The copy-ready image statement is:

```text
D:\TestMasterData\<active-process>\Python\testmaster_vision\image_step.py
```

It reads `vision.image_path`, recognizes a bright or dark feature, and returns
feature coordinates in the result JSON. It never requests stage movement.

Example input:

```json
{
  "schema_version": 1,
  "phase": "vision_recognition",
  "vision": {
    "image_path": "D:\\TestMasterData\\System\\Python\\examples\\latest_camera_image.tif"
  },
  "algorithm": {
    "polarity": "bright",
    "threshold": 180,
    "min_area_px": 25
  },
  "limits": {
    "allowed_stages": [],
    "max_step_um": 0.0
  }
}
```

Use this YASE configuration:

```text
Interpreter = Python_310_PYTHON_AUTOMATION_INTERPRETER
Module      = testmaster_vision.image_step
Class       = ImageRecognitionStep
```

For the first machine test, pass fixed input JSON and do not move any axes.

Minimal class shape for any future TMPython file:

```python
from tmpython.statement import TMPythonStatementJ


class ImageRecognitionStep(TMPythonStatementJ):
    def run(self, params_in):
        return {
            "schema_version": 1,
            "action": "done",
            "move_count": 0,
            "stage1": "",
            "distance1_um": 0.0,
            "message": "Python vision test ran",
            "state": {"received": params_in},
        }
```

## 11. Checks

Check the venv Python:

```powershell
D:\TestMasterData\System\Python\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
```

Check packages:

```powershell
D:\TestMasterData\System\Python\.venv\Scripts\python.exe -m pip list
D:\TestMasterData\System\Python\.venv\Scripts\python.exe -c "import cv2, numpy, PIL; print('ok')"
```

Check the TMPython support package:

```powershell
D:\TestMasterData\System\Python\.venv\Scripts\python.exe -c "from tmpython.statement import TMPythonStatementJ; print('tmpython ok')"
```

If a package works in PowerShell but fails in YASE, `TMPython.ini` is probably
pointing to a different `python.exe`, or YASE is reading a different
`TMPython.ini` than the one you edited.
