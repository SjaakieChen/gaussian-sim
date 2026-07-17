# SUB_vision_recognition v4

Read-only YASE launcher for the Python vision recognition lab.

Copy this folder to:

```text
D:\TestMasterData\Process\Python_Automation\SUB_vision_recognition\
```

Copy `migration_v4\vision_recognition_lab.py` directly into:

```text
D:\TestMasterData\Process\Python_Automation\python_env
```

The launcher captures a `CAM_12` BMP through TestMaster/YASE, then calls:

```text
TMPython_ExecuteScript
Interpreter = Python_310_PYTHON_AUTOMATION_INTERPRETER
Module      = vision_recognition_lab
Class       = VisionRecognitionLabStep
ParamIn     = s_PythonInputJson
ParamOut    = s_PythonResultJson
```

It does not move stages.
