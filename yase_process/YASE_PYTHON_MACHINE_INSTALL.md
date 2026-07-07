# YASE/TestMaster Python Machine Install

This is the simple machine-side setup for running Python code from YASE through
`TMPython_ExecuteScript`.

## 1. Folder Layout

Recommended layout on the machine:

```text
C:\TestMasterData\System\Python\
  .venv\
  testmaster_python_alignment_algorithm\
  vision_recognition\
  requirements.txt
  log\
```

Meaning:

- `.venv\` contains the Python environment and installed packages.
- `testmaster_python_alignment_algorithm\` contains Python package files.
- `vision_recognition\` can contain your vision-specific Python files.
- `requirements.txt` lists packages to install.
- `log\` is for TMPython logs.

Do not put your own `.py` files inside `.venv`.

## 2. Create The Machine Python Folder

Open PowerShell on the machine.

```powershell
$Base = "C:\TestMasterData\System\Python"
New-Item -ItemType Directory -Force $Base
New-Item -ItemType Directory -Force "$Base\log"
cd $Base
```

## 3. Create The Virtual Environment

Set this to the real installed Python on the machine:

```powershell
$MachinePython = "C:\Users\operator\AppData\Local\Programs\Python\Python310\python.exe"
```

Create the venv:

```powershell
& $MachinePython -m venv .venv
```

Check the venv Python:

```powershell
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
```

Expected output should include:

```text
C:\TestMasterData\System\Python\.venv\Scripts\python.exe
```

## 4. Install Packages

Create `requirements.txt` in:

```text
C:\TestMasterData\System\Python\requirements.txt
```

Example contents:

```text
numpy
opencv-python-headless
pillow
```

Install the packages into the venv:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Check imports:

```powershell
.\.venv\Scripts\python.exe -c "import numpy, cv2, PIL; print('packages ok')"
```

## 5. Copy Python Files

Copy your Python package folders into the same parent folder:

```text
C:\TestMasterData\System\Python\testmaster_python_alignment_algorithm\
C:\TestMasterData\System\Python\vision_recognition\
```

Example:

```text
C:\TestMasterData\System\Python\
  testmaster_python_alignment_algorithm\
    __init__.py
    alignment_step.py
    assisted\
      __init__.py
      vision_offset_step.py
```

## 6. Configure TMPython.ini

Edit:

```text
#SM_CONFIG#\TMPython.ini
```

Example:

```ini
[Python_310]
WorkingDirectory = "C:\TestMasterData\System\Python\"
PythonInterpreter = "C:\TestMasterData\System\Python\.venv\Scripts\python.exe"
LogDirectory = "C:\TestMasterData\System\Python\log"
```

Modify `TMPython.ini` after:

1. Python is installed on the machine.
2. The venv has been created.
3. Required packages have been installed into the venv.
4. Your Python files have been copied into the working directory.

The section name `[Python_310]` is just a lookup name. It can be named
something else, but the YASE `Interpreter` field must match it exactly.

If `TMPython.ini` does not exist, that is possible. It usually means TMPython
has not been configured on that machine yet, or the file is stored in a
different active TestMaster config folder.

Before creating a new file, confirm the real `#SM_CONFIG#` folder used by the
machine. If the file is truly missing, create it as a plain text file named:

```text
TMPython.ini
```

and put it in the active `#SM_CONFIG#` folder:

```text
#SM_CONFIG#\TMPython.ini
```

Minimum contents:

```ini
[Python_310]
WorkingDirectory = "C:\TestMasterData\System\Python\"
PythonInterpreter = "C:\TestMasterData\System\Python\.venv\Scripts\python.exe"
LogDirectory = "C:\TestMasterData\System\Python\log"
```

After creating or editing `TMPython.ini`, restart TestMaster/YASE or reload the
TMPython/TestMaster configuration if the machine procedure provides a reload
button. Then verify that `TMPython_ExecuteScript` can see the interpreter name
used in the YASE statement.

## 7. What The TMPython.ini Fields Mean

`PythonInterpreter` chooses which Python runs:

```ini
PythonInterpreter = "C:\TestMasterData\System\Python\.venv\Scripts\python.exe"
```

That is the venv Python. Any packages installed with that executable are the
packages YASE will see.

`WorkingDirectory` chooses where Python looks for your code:

```ini
WorkingDirectory = "C:\TestMasterData\System\Python\"
```

If the YASE module is:

```text
testmaster_python_alignment_algorithm.alignment_step
```

Python loads:

```text
C:\TestMasterData\System\Python\testmaster_python_alignment_algorithm\alignment_step.py
```

## 8. Select Python Code In YASE

In the YASE `TMPython_ExecuteScript` statement, use:

```text
Interpreter = Python_310
Module      = testmaster_python_alignment_algorithm.alignment_step
Class       = BlindAlignStep
Input JSON  = s_PythonInputJson
Result JSON = s_PythonResultJson
```

This means:

```text
Interpreter = Python_310
```

looks up this INI section:

```ini
[Python_310]
```

Then:

```text
Module = testmaster_python_alignment_algorithm.alignment_step
Class  = BlindAlignStep
```

loads this class:

```python
from testmaster_python_alignment_algorithm.alignment_step import BlindAlignStep
```

So the file must exist here:

```text
C:\TestMasterData\System\Python\testmaster_python_alignment_algorithm\alignment_step.py
```

and it must contain:

```python
class BlindAlignStep:
    ...
```

For another file:

```text
C:\TestMasterData\System\Python\vision_recognition\image_step.py
```

with:

```python
class ImageRecognitionStep:
    ...
```

use this in YASE:

```text
Interpreter = Python_310
Module      = vision_recognition.image_step
Class       = ImageRecognitionStep
```

If the code is in a package folder, include an `__init__.py` file in that
folder.

## 9. Minimal Python Test Class

Create:

```text
C:\TestMasterData\System\Python\vision_recognition\image_step.py
```

Example:

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

Then use:

```text
Interpreter = Python_310
Module      = vision_recognition.image_step
Class       = ImageRecognitionStep
```

Run this first with fixed input JSON and no machine motion.

## 10. Important Checks

Check which Python YASE will use:

```powershell
C:\TestMasterData\System\Python\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
```

Check that required packages are installed into the same Python:

```powershell
C:\TestMasterData\System\Python\.venv\Scripts\python.exe -m pip list
```

Check imports:

```powershell
C:\TestMasterData\System\Python\.venv\Scripts\python.exe -c "import cv2, numpy, PIL; print('ok')"
```

If a package works in PowerShell but fails in YASE, verify that
`TMPython.ini` points to the same `python.exe` used in the PowerShell commands.
