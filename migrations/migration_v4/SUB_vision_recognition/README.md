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

When the operator marks exactly one rectangle/edge detection and one or more
circle/blob detections as `Use`, the Python `ParamOut` JSON includes:

- `relative_measurement`: structured micron coordinates using the first used
  circle/blob center as `(0, 0)`;
- a fixed short-edge calibration of `500 um`; the operator cannot edit this
  conversion length in the UI;
- `relative_measurement.measure_edge.midpoint_relative_um`: the selected short
  edge midpoint relative to that origin;
- `relative_measurement.circles`: every used circle/blob center relative to the
  first used circle/blob, using the same `um_per_pixel` conversion;
- `yase_display`: a compact text summary for the YASE status display.

The sequence displays the full returned JSON through the verified
`DisplayStatus(s_PythonResultJson)` pattern. The `status` and `yase_display`
fields are placed in `ParamOut` for the operator-readable relative measurement
summary.
