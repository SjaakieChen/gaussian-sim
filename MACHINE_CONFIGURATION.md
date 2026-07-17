# Python Automation Machine Configuration

Last verified: 2026-07-10 16:54 local time

This document records the local TestMaster, YASE, and TMPython configuration
used by the Python Automation process. Use it as the reference when creating
new Python-backed YASE sequences or reproducing the setup on another device.

This is the authoritative handoff document for future agents. Before changing
a sequence, compare its live YASE fields and its on-disk `.xseq` content with
this guide. A successful runtime test does not guarantee that an unsaved YASE
editor buffer was written back to disk.

This root file is the repository source of truth. Do not depend on copies under
`migration\...` for current machine configuration.

The official TestMaster/YASE PDFs are kept in:

```text
testmaster documentation\Yase_TM_HB_Sep_2018.pdf
testmaster documentation\TestMaster Documentation 2020.1.10 (1).pdf
```

For recurring failure signatures and prevention checks, also read:

```text
COMMON_MISTAKES.md
```

## Current authoritative status

| Item | Status |
| --- | --- |
| TMPython interpreter section | `Python_310_PYTHON_AUTOMATION_INTERPRETER` is correct and verified |
| Python working directory | `D:\TestMasterData\Process\Python_Automation\python_env` |
| Fixed-Z read-only JSON bridge | Successful end-to-end checkout |
| Camera image bridge | Successful end-to-end checkout with a real `CAM_12` frame |
| Python-saved camera copy | Byte-for-byte verified by matching SHA-256 hashes |
| Hardware motion from Python | Not approved or proven |
| Simple JSON test on disk | Repository copy now uses the Python 3.10 interpreter name; verify after copying/saving in YASE |
| Default-positioning migration v4 | Static XML, label, and copy-layout validation only; not yet machine-run verified |

Never use `Python_37_PYTHON_AUTOMATION_INTERPRETER` or
`Python_310_ALIGNMENT_TEST`. They are historical mistakes, not aliases.

## Verified computer environment

| Component | Installed configuration |
| --- | --- |
| Operating system | Microsoft Windows 10 IoT Enterprise LTSC, 64-bit |
| Windows version | 10.0.19044 |
| PowerShell | 5.1.19041.3031 |
| Python | 3.10.4 |
| YASE editor | V 3.1.0 Build 103281600 |
| TMPython package | `testmaster_pyexec 2020.1.10+7804` for Python 3.10 |
| TestMaster data root | `D:\TestMasterData` |

## Important directories

```text
TestMaster root data: D:\TestMasterData
Process:              D:\TestMasterData\Process\Python_Automation
Python working folder:D:\TestMasterData\Process\Python_Automation\python_env
Python interpreter:   D:\TestMasterData\Process\Python_Automation\python_env\.venv\Scripts\python.exe
TMPython logs:        D:\TestMasterData\Process\Python_Automation\python_env\log
TMPython config:      D:\TestMasterData\config\TMPython.ini
Process variables:    D:\TestMasterData\Process\Python_Automation\Processvar.ini
YASE test sequences:  D:\TestMasterData\Process\Python_Automation\SUB_Testing
Alignment sequences:  D:\TestMasterData\Process\Python_Automation\SUB_alignment_solving
Default positioning:  D:\TestMasterData\Process\Python_Automation\SUB_default_positioning
```

Python source modules that TMPython must import are placed directly in
`python_env`. YASE `.xseq` files remain in the appropriate process subfolder.

## TestMaster and project folder layout

```text
D:\TestMasterData\
|-- config\                         Global machine configuration
|   |-- Hardware.ini                Hardware devices, including CAM_12
|   |-- Systemvar.ini               Global/runtime system variables
|   |-- TMPython.ini                TMPython interpreter definitions
|   `-- prototypes.xml              Global YASE statement prototypes
|-- data\                           Per-process runtime data
|   `-- Python_Automation\
|       `-- python_vision_input.bmp  CAM_12 frame written by IMAQWriteFile
|-- log\                            TestMaster logs
|   |-- PLEIADES\error_2.log        May contain Python_Automation errors when PLEIADES is loaded
|   `-- Python_Automation\          Python_Automation process logs
|-- Process\                        Process definitions and sequences
|   |-- Python_Automation\          This development process
|   `-- Microcombsys\               Existing production/reference process
|-- System\                         Shared TestMaster system sequences
|   `-- HELPER\SUB_SYS_GrabAndSaveImage.xseq
|-- Project_Modules\
|-- Project_Specific\
`-- user\
```

Python Automation process layout:

```text
D:\TestMasterData\Process\Python_Automation\
|-- MAIN_PROCESS.xseq
|-- Process.ini
|-- Processvar.ini
|-- Sequencer.ini
|-- prototypes.xml
|-- VisionAssistantIntegrator.ini   Present but currently empty
|-- python_env\
|   |-- .venv\
|   |   `-- Scripts\python.exe
|   |-- log\
|   |   |-- tmpython_*.log
|   |   |-- json_write_extract_input.json
|   |   |-- json_write_extract_output.json
|   |   |-- fixed_z_staged_ball_placement_input.json
|   |   |-- fixed_z_staged_ball_placement_result.json
|   |   |-- python_vision_request.json
|   |   |-- python_vision_result.json
|   |   `-- python_vision_saved.bmp
|   |-- fixed_z_staged_ball_placement.py
|   |-- json_write_extract.py
|   |-- vision_image_file_check.py
|   |-- README.md
|   `-- MACHINE_CONFIGURATION.md
|-- SUB_Testing\
|   |-- SUB_JSON_WriteExtract.xseq
|   |-- SUB_TMPython_MovementCommand_ReadOnly.xseq
|   `-- SUB_PythonVisionImageFileCheck_ReadOnly.xseq
|-- SUB_alignment_solving\
|   |-- SUB_FixedZStagedBallPlacement_ReadOnly.xseq
|   |-- SUB_ApplyFixedZStagedBallMove.xseq
|   `-- README.md
`-- SUB_default_positioning\
    |-- SUB_ApplyDefaultPositionMove.xseq
    |-- SUB_ApplyDefaultPositionExposure.xseq
    |-- SUB_DefaultPosition_1.0.0_pick_ball_1.xseq
    |-- SUB_DefaultPosition_2.0.0_pick_ball_2.xseq
    |-- SUB_DefaultPosition_3.0.0_cam_view_1_wide.xseq
    |-- SUB_DefaultPosition_4.0.0_cam_view_1_side.xseq
    |-- SUB_DefaultPosition_5.0.0_back_view_after_trench.xseq
    `-- SUB_DefaultPosition_6.0.0_full_above_trench.xseq
```

Microcombsys reference vision layout, read-only for this work:

```text
D:\TestMasterData\Process\Microcombsys\
|-- Processvar.ini
|-- prototypes.xml
`-- SUB_MachineVision\
    |-- SUB_Chip_MirrorFront_Correction.xseq
    |-- SUB_Chip_MirrorSide_Correction.xseq
    |-- SUB_Chip_Top_Correction.xseq
    |-- SUB_Fix_BallLens_Correction.xseq
    |-- SUB_Fix_BallLens_Correction1.xseq
    |-- SUB_Pick_Top_Correction.xseq
    `-- SUB_Scan Area.xseq
```

Do not modify Microcombsys reference files while developing the Python bridge.
Copy behavior into the Python_Automation process and validate it there.

## Configuration-file ownership

| File | Scope and purpose | Guidance |
| --- | --- | --- |
| `D:\TestMasterData\config\TMPython.ini` | Global TMPython interpreter registry | The section name must exactly match the YASE `Interpreter` field |
| `D:\TestMasterData\config\Hardware.ini` | Global hardware/device configuration | `CAM_12` uses `IMAQdx_HW_Interface.vi`; do not change acquisition format casually |
| `D:\TestMasterData\config\Systemvar.ini` | Global and runtime system variables | Contains panel/reference state, including opaque camera references |
| `D:\TestMasterData\config\prototypes.xml` | Global YASE statement definitions | Useful for discovering exact statement and parameter names |
| `Python_Automation\prototypes.xml` | Process-local prototype snapshot | YASE uses `ParamIn`/`ParamOut` for the installed TMPython statement |
| `Python_Automation\Processvar.ini` | Python_Automation persistent process variables | Stores `LastInputJson` and `LastResultJson` for the fixed-Z checkout |
| `Python_Automation\Process.ini` | Process menus and process path settings | Empty path settings defer to TestMaster environment paths |
| `Python_Automation\Sequencer.ini` | Sequence/subsequence search paths | Search-path fields are currently empty |
| `Python_Automation\VisionAssistantIntegrator.ini` | Process Vision Assistant integration settings | File currently has no content |
| `Microcombsys\Processvar.ini` | Microcombsys vision settings and scale | Reference only; contains illumination, exposure, and `HFA_Top_1` |

Relevant global camera configuration:

```ini
[CAM_12]
InstrPath = "IMAQdx_HW_Interface.vi"
DeviceType = "Camera"
CameraName = CAM_12
CalibrateX = 1
CalibrateY = 1
Rotation = 0
GetRAWData = False
ContinuousAcquisition = True
TriggeredAcquisition = True
```

Relevant Microcombsys vision values:

```ini
[Vision_Ball_Corr]
Illu_Coax = 0.8
Zoom_ExpTime = 4.04E+4
Illu_1 = 0.0
Illu_2 = 0.0

[Scaling]
HFA_Top_1 = 4.15
```

`HFA_Top_1 = 4.15` is interpreted by the existing top-camera workflow as
approximately `4.15 pixels/um`, or `0.240963855 um/pixel`, only when the same
camera, zoom, optical plane, and calibration are active. It is not a universal
machine accuracy guarantee.

## Required TMPython.ini section

The active configuration is:

```ini
[Python_310_PYTHON_AUTOMATION_INTERPRETER]
WorkingDirectory = "D:\TestMasterData\Process\Python_Automation\python_env"
PythonInterpreter = "D:\TestMasterData\Process\Python_Automation\python_env\.venv\Scripts\python.exe"
LogDirectory = "D:\TestMasterData\Process\Python_Automation\python_env\log"
```

The interpreter name used by YASE is the section name without brackets:

```text
Python_310_PYTHON_AUTOMATION_INTERPRETER
```

Do not use these old or nonexistent names:

```text
Python_37_PYTHON_AUTOMATION_INTERPRETER
Python_310_ALIGNMENT_TEST
```

When moving the project to another computer, update all three paths in
`TMPython.ini`. The working directory and Python module location must still
agree.

## Required YASE TMPython statement wiring

This installed TMPython prototype uses the parameter names `ParamIn` and
`ParamOut`. It does not use `Input JSON` and `Result JSON`.

Use the following pattern:

```xml
<Statement Label="" Editable="FALSE" Name="TMPython_ExecuteScript" Library="">
   <Parameter Name="Interpreter" Description="" Type="String" Direction="Input"
      ValueType="Constant" NumericValue="0.0"
      StringValue="Python_310_PYTHON_AUTOMATION_INTERPRETER" />
   <Parameter Name="Module" Description="" Type="String" Direction="Input"
      ValueType="Constant" NumericValue="0.0"
      StringValue="json_write_extract" />
   <Parameter Name="Class" Description="" Type="String" Direction="Input"
      ValueType="Constant" NumericValue="0.0"
      StringValue="JsonWriteExtract" />
   <Parameter Name="ParamIn" Description="" Type="String" Direction="Input"
      ValueType="Variable" VariableName="s_JsonInput" />
   <Parameter Name="ParamOut" Description="" Type="String" Direction="Output"
      ValueType="Variable" VariableName="s_JsonOutput" />
</Statement>
```

Critical rules:

- `ParamIn` must be a YASE variable containing valid, nonempty JSON text.
- `ParamOut` must be a YASE variable so the returned JSON is captured.
- Do not configure either parameter as an empty constant.
- The module name has no `.py` extension.
- A file directly under `python_env` uses its bare module name. For example,
  `fixed_z_staged_ball_placement.py` becomes `fixed_z_staged_ball_placement`.
- Do not add a package prefix unless that package directory and its
  `__init__.py` actually exist under the working directory or on `sys.path`.

## Python statement contract

Python classes called by YASE inherit from `TMPythonStatementJ`:

```python
from tmpython.statement import TMPythonStatementJ


class ExampleStatement(TMPythonStatementJ):
    def run(self, params_in):
        value = params_in.get("value")
        return {"value": value, "status": "ok"}
```

TMPython performs JSON conversion automatically:

1. YASE provides JSON text through `ParamIn`.
2. TMPython decodes it and passes a Python dictionary to `run`.
3. `run` returns a Python dictionary.
4. TMPython encodes the dictionary and writes JSON text to `ParamOut`.

Do not call `json.loads(params_in)` inside `run`; `params_in` is already a
dictionary.

## JSON bridge rules

### YASE input construction

In YASE, build the complete JSON text with `SetString` and write its output to
a string variable such as `s_PythonInputJson`. Then configure:

```text
ParamIn  -> ValueType Variable -> s_PythonInputJson
ParamOut -> ValueType Variable -> s_PythonResultJson
```

The diagnostic JSON file written by `WriteToFile` is not automatically read by
TMPython. Python receives the value connected to `ParamIn`. Editing a file in
`python_env\log` does nothing unless the YASE sequence explicitly reads that
file and passes its content.

### JSON inside XSEQ XML

Escape JSON double quotes as `&quot;` inside an XML `StringValue` attribute:

```xml
StringValue="{&quot;schema_version&quot;:1,&quot;value&quot;:123}"
```

For Windows paths inside JSON, use forward slashes:

```json
{"image_path":"D:/TestMasterData/data/Python_Automation/python_vision_input.bmp"}
```

Do not rely on doubled backslashes in a YASE `SetString`. On this machine YASE
converted the doubled backslashes to single backslashes at runtime, producing
invalid JSON such as `D:\TestMasterData` and the error:

```text
10500: Invalid \escape: line 1 column 37
```

Forward slashes are valid for Windows paths in Python and avoid this problem.

### JSON output handling

TMPython returns one JSON string through `ParamOut`. If YASE needs individual
numeric values for motion, it must parse and validate the fields before using
them. A whole returned JSON string must never be connected directly to a stage
command.

Validate at least:

- schema version;
- success/action status;
- expected stage name from an allowlist;
- finite numeric target and delta;
- configured motion limits;
- current stage position and sign convention;
- required operator confirmation;
- all machine interlocks and controller state.

### Module naming

With `WorkingDirectory` set to `python_env`, use:

```text
File:   python_env\vision_image_file_check.py
Module: vision_image_file_check
```

Do not include `.py`. Do not use a package prefix such as
`python_alignment_solving.` unless that package directory exists and contains
an importable package.

## Simple JSON test

Files:

```text
Python: D:\TestMasterData\Process\Python_Automation\python_env\json_write_extract.py
YASE:   D:\TestMasterData\Process\Python_Automation\SUB_Testing\SUB_JSON_WriteExtract.xseq
```

The test sends:

```json
{"item":{"name":"ball_1","x_um":123.45,"enabled":true}}
```

Expected returned JSON:

```json
{"name":"ball_1","x_um":123.45,"enabled":true,"status":"JSON extracted successfully"}
```

Diagnostic copies are written to:

```text
D:\TestMasterData\Process\Python_Automation\python_env\log\json_write_extract_input.json
D:\TestMasterData\Process\Python_Automation\python_env\log\json_write_extract_output.json
```

The JSON round trip succeeded at runtime on 2026-07-10. The repository copy of
`SUB_JSON_WriteExtract.xseq` now uses:

```text
Python_310_PYTHON_AUTOMATION_INTERPRETER
```

After copying to the machine or editing in YASE, close and reopen the sequence
and verify that the saved on-disk `Interpreter` field still matches that value.

## Fixed-Z staged placement configuration

Files:

```text
Python: D:\TestMasterData\Process\Python_Automation\python_env\fixed_z_staged_ball_placement.py
YASE:   D:\TestMasterData\Process\Python_Automation\SUB_alignment_solving\SUB_FixedZStagedBallPlacement_ReadOnly.xseq
Input:  D:\TestMasterData\Process\Python_Automation\python_env\log\fixed_z_staged_ball_placement_input.json
Output: D:\TestMasterData\Process\Python_Automation\python_env\log\fixed_z_staged_ball_placement_result.json
```

Required TMPython fields:

```text
Interpreter: Python_310_PYTHON_AUTOMATION_INTERPRETER
Module:      fixed_z_staged_ball_placement
Class:       FixedZStagedBallPlacementStep
ParamIn:     variable s_PythonInputJson
ParamOut:    variable s_PythonResultJson
```

The diagnostic input file is not automatically read by the Python class.
Python receives the JSON held in `s_PythonInputJson`. The file is only a copy
that makes troubleshooting easier.

The current read-only sequence also checks whether all stages are fiducialed.
If that check is false, the sequence intentionally stops before calling
Python, even though it does not perform motion.

## Successful fixed-Z read-only checkout

The complete fixed-Z read-only YASE-to-Python round trip ran successfully on
this machine on 2026-07-10 at approximately 16:24 local time.

Evidence from that run:

```text
Input JSON written:  2026-07-10 16:24:24
TMPython call:        FixedZStagedBallPlacementStep
Returned action:     move
Returned first stage:Align_Y1
First absolute target: 505.0 um
First delta:          +513.0 um (from -8.0 um to 505.0 um)
Generated plan size:  6 operator-confirmed absolute moves
Result JSON written: 2026-07-10 16:24:42
TMPython session end: 2026-07-10 16:24:45
```

The solver generated this planned order from the fixed commissioning payload:

1. Raise `Align_Y1` to the safe-clearance coordinate.
2. Raise `Align_Y2` to the safe-clearance coordinate.
3. Move `Align_Z1` to its solved transverse coordinate.
4. Move `Align_Z2` to its solved transverse coordinate.
5. Lower `Align_Y1` to its solved coordinate.
6. Lower `Align_Y2` to its solved coordinate.

Each returned move has `confirm_required: true`. The response exposes the
first proposed move in the flat YASE-friendly fields `stage1`, `target1_um`,
`delta1_um`, `move_mode1`, `phase1`, and `confirm_text1`, and returns the full
plan in `planned_moves`.

### What the successful run proves

- The stage-fiducial gate allowed this particular run to reach the Python
  call.
- YASE successfully created and populated `s_PythonInputJson`.
- YASE successfully wrote the input diagnostic JSON file.
- The `Python_310_PYTHON_AUTOMATION_INTERPRETER` section launched the correct
  Python environment.
- TMPython found and imported the `fixed_z_staged_ball_placement` module and
  `FixedZStagedBallPlacementStep` class.
- `ParamIn` transferred valid JSON from YASE to Python as a dictionary.
- The Python solver accepted the commissioning schema, calculated target
  positions, checked its configured no-go zones, and built a staged plan.
- `ParamOut` transferred the returned dictionary back to YASE as JSON.
- YASE populated `s_PythonResultJson`, displayed it, stored it as
  `LastResultJson`, and wrote the result diagnostic file.
- The end-to-end software data path works on this computer:

```text
YASE SetString
  -> s_PythonInputJson
  -> TMPython ParamIn
  -> Python solver
  -> TMPython ParamOut
  -> s_PythonResultJson
  -> YASE display, process variable, and result file
```

### What the successful run does not prove

- It does not prove any stage was moved. The sequence is deliberately
  read-only and contains no call that applies the returned move.
- It does not prove the returned coordinates are physically correct for the
  current machine setup. The sequence still uses fixed commissioning/example
  coordinates rather than current `QueryStage` and camera measurements.
- It does not validate the real laser position, detector position, ball-lens
  positions, camera calibration, optical power, or focus quality.
- It does not prove the configured no-go-zone dimensions match the physical
  machine.
- It does not exercise operator confirmation, stage limits, velocity limits,
  motion completion, controller faults, or collision interlocks during a real
  move.
- It does not prove that repeatedly applying the returned plan will converge
  to a successful optical alignment.

Treat this run as a successful software integration checkout, not as approval
for automatic hardware motion. A motion-enabled sequence must replace the
fixed input with measured machine data and independently validate every
returned stage, target, delta, limit, interlock, and confirmation before a
move is issued.

## Machine-vision reference workflow

The Microcombsys files were inspected read-only to understand the existing
camera and Vision Assistant flow. No Microcombsys file was modified.

### Legacy in-memory image flow

The relevant workflow in
`Microcombsys\SUB_MachineVision\SUB_Fix_BallLens_Correction1.xseq` is:

```text
CAM_12
  -> AdvancedIMAQ Grab
  -> r_Image_Ref
  -> FixingPos1Topview_22042026_edit Vision VI
  -> r_ImageResult_Ref plus XML/document references
  -> VA_TM_GetValue
  -> d_Distance_X, d_Distance_Z, d_ScaleFactor
  -> YASE scaling and offsets
  -> relative Align_X1 and Align_Z1 moves
```

`r_Image_Ref` is a TestMaster string containing a flattened NI IMAQ image
reference. It is an opaque in-memory reference, not a filename and not pixel
JSON. It is meaningful to NI/LabVIEW code in the owning process. Do not pass it
through TMPython and expect NumPy to receive pixels.

`r_ImageResult_Ref` is the processed/annotated result image reference returned
by the Vision VI. The legacy sequence displays it with `IMAQWind_ShowImage` but
does not save the raw frame as a normal image file.

The Vision Assistant document references are later released by
`VA_TM_FreeAllDocs`; they are not durable image storage.

### Existing Vision VI outputs

`FixingPos1Topview_22042026_edit` exposes three measurements through
`VA_TM_GetValue`:

```text
d_Distance_X
d_Distance_Z
d_ScaleFactor
```

The YASE sequence then performs:

```text
scale_um_per_pixel = 500 / d_ScaleFactor
move_X_um = d_Distance_X * scale_um_per_pixel + 500
move_Z_um = -(d_Distance_Z * scale_um_per_pixel + 250)
```

and sends those results to relative stage moves. Any Python replacement must
match the exact units, origin, direction, ROI, edge polarity, failure behavior,
and sign convention before it is allowed to influence motion.

The older `microb_topview` VI exposes:

```text
ball-lens center X in pixels
ball-lens center Y in pixels
ball-lens radius in pixels
laser midpoint X in pixels
laser midpoint Y in pixels
```

The compiled Vision VIs are under:

```text
C:\TestMaster\Customer_Modules\Nanosystec\Functions\Imaging\
VisionAssistantIntegrator\VB_VIs.llb\
```

The `.xseq` files expose the interface, but not the internal Vision Assistant
thresholds, ROIs, caliper settings, filters, or rejection rules. Those must be
documented from LabVIEW/Vision Assistant or inferred and validated against a
representative image set before replacing the VI.

## Proven YASE-to-Python image bridge

Files:

```text
YASE:
D:\TestMasterData\Process\Python_Automation\SUB_Testing\
SUB_PythonVisionImageFileCheck_ReadOnly.xseq

Python:
D:\TestMasterData\Process\Python_Automation\python_env\
vision_image_file_check.py
```

Current read-only flow:

```text
CAM_12
  -> AdvancedIMAQ Grab
  -> r_Image_Ref
  -> IMAQWriteFile with FileName=python_vision_input.bmp and FileType=BMP
  -> D:\TestMasterData\data\Python_Automation\python_vision_input.bmp
  -> JSON request containing the forward-slash path
  -> TMPython ParamIn
  -> VisionImageFileCheck
  -> Python BMP decoder and NumPy statistics
  -> byte-for-byte Python copy under python_env\log
  -> TMPython ParamOut
  -> YASE display and python_vision_result.json
```

### Why the image is written under the process data directory

The installed `IMAQWriteFile` TestMaster wrapper resolves a relative filename
against the active process data directory. Use:

```text
FileName = python_vision_input.bmp
FileType = BMP
```

For the Python_Automation process this resolves to:

```text
D:\TestMasterData\data\Python_Automation\python_vision_input.bmp
```

Do not give this wrapper the absolute `python_env\log` path. That attempt
reached `STMT_IMAQWriteFile.vi` but failed with generic `IMAQ WriteFile` error
50003. This path behavior is different from the ordinary `WriteToFile`
statement used for JSON.

### JSON request for the image bridge

Use forward slashes in the JSON literal:

```json
{
  "schema_version": 1,
  "image_path": "D:/TestMasterData/data/Python_Automation/python_vision_input.bmp"
}
```

The request diagnostic is written to:

```text
D:\TestMasterData\Process\Python_Automation\python_env\log\python_vision_request.json
```

The returned diagnostic is written to:

```text
D:\TestMasterData\Process\Python_Automation\python_env\log\python_vision_result.json
```

### Python image handling

The current virtual environment contains only these application dependencies:

```text
numpy 2.2.6
testmaster-pyexec 2020.1.10+7804
```

OpenCV, Pillow, ImageIO, and scikit-image are not installed. The image-check
module therefore contains a local BMP decoder using the Python standard
library and NumPy.

Supported by the current decoder:

- Windows BMP signature `BM`;
- uncompressed BMP only;
- 8-bit indexed/grayscale;
- 24-bit BGR;
- 32-bit BGRA;
- bottom-up and top-down row order;
- row padding/stride;
- palette lookup for 8-bit indexed files.

For 24- and 32-bit BMP files, Python converts BGR to grayscale luminance for
the current scalar-statistics checkout. It does not preserve separate color
channels in the returned measurements.

Unsupported by the current decoder:

- compressed BMP variants;
- 1-, 4-, or 16-bit BMP;
- PNG, TIFF, JPEG, or AIPD;
- direct use of the opaque `r_Image_Ref`;
- direct acquisition from NI-IMAQdx in Python.

### Python-saved lossless image

Python atomically copies the source BMP to:

```text
D:\TestMasterData\Process\Python_Automation\python_env\log\python_vision_saved.bmp
```

It computes SHA-256 for the source and copy and refuses success if they differ.
For the verified frame, both hashes were:

```text
2d2a608e5f2c99a9ccfb8e289d058ab5740cdf3b58a2d3a35a4dea609021818f
```

Therefore, the YASE-to-file-to-Python path and Python save operation added zero
pixel loss for that frame.

### Successful real-frame checkout

The complete image bridge succeeded on 2026-07-10 at approximately 16:50:56.
Verified result:

```text
ok:                    true
format:                uncompressed BMP
width:                 2592 pixels
height:                1944 pixels
bit depth:             8 bits per pixel
source channels:       1
pixel count:           5,038,848
finite pixel count:    5,038,848
minimum:               15
maximum:               255
mean:                  90.8738292959
standard deviation:    31.4559586402
brightest coordinate:  (1188, 551)
darkest coordinate:    (17, 1833)
BMP row stride:        2592 bytes
BMP orientation:       bottom-up
```

Additional inspection of that frame:

```text
1st percentile:        29
median:                86
99th percentile:       176
pixels at 255:         117 (about 0.00232 percent)
pixels at or above 250:146 (about 0.00290 percent)
```

This proves that Python can consume the real image captured by `CAM_12`
without calling a Vision Assistant processing VI. It does not prove that the
Python code reproduces the circle/caliper results or is accurate enough for
motion.

### Precision interpretation

The BMP bridge is lossless, but the acquired image is currently 8-bit. It has
256 possible intensity levels. File transport accuracy and physical
measurement accuracy are different:

- file transport: verified exact by SHA-256;
- spatial sampling: approximately `0.24096 um/pixel` if the Microcombsys
  `HFA_Top_1` calibration applies to the current zoom and plane;
- feature localization: may use subpixel fitting, but real accuracy depends on
  focus, noise, contrast, distortion, illumination, calibration, and repeated
  measurement jitter;
- absolute machine accuracy: not established by this checkout.

The verified image appeared somewhat soft/noisy, so focus and repeatability
are likely larger limitations than BMP storage. To pursue higher precision:

1. lock camera, zoom, exposure, illumination, and working distance;
2. calibrate pixel scale and lens distortion at the exact measurement plane;
3. capture repeated stationary frames and quantify fitted-coordinate jitter;
4. avoid JPEG and all lossy conversions;
5. verify whether `CAM_12` supports 10-, 12-, or 16-bit output;
6. if higher bit depth is enabled, use a compatible lossless PNG/TIFF bridge
   and extend or replace the Python decoder;
7. compare Python and Vision VI measurements in read-only shadow mode across
   representative good, marginal, and failure images.

Do not change `GetRAWData`, IMAQdx pixel format, exposure, or camera ownership
from Python without a separate camera-capability and compatibility checkout.

### Export methods that failed or are unsuitable

`SaveImageToSpreadsheetFile` was tested with an absolute `.tsv` path. The VI
treated that path as a directory and attempted to write:

```text
...\python_vision_input.tsv\python_vision_input.imgcorr
```

It failed with error 50003. Do not reuse that pattern.

`SUB_SYS_GrabAndSaveImage.xseq` can save a TIFF, but it performs its own camera
grab. It is unsuitable when Python must analyze the exact same `r_Image_Ref`
already acquired by the calling sequence. `IMAQWriteFile` on the existing
reference is the verified same-frame bridge.

### Safe path toward replacing the Vision VI

1. Keep TestMaster responsible for `CAM_12` acquisition.
2. Save the same `r_Image_Ref` with the verified BMP bridge.
3. Run the existing Vision VI and record its raw pixel measurements.
4. Run Python on the saved frame without connecting Python to motion.
5. Return explicit status, measurements, confidence, and failure reason.
6. Compare Python and VI results on the identical frame.
7. Establish tolerances in pixels and micrometres.
8. Validate failure cases, missing features, blur, saturation, and occlusion.
9. Add JSON-to-numeric YASE extraction and independent bounds checking.
10. Only after review, allow an operator-confirmed test move with conservative
    limits and full interlocks.

## Writing files from YASE

The currently proven approach is to use absolute paths under the existing
`python_env\log` directory, for example:

```text
D:\TestMasterData\Process\Python_Automation\python_env\log\example.json
```

`WriteToFile` creates or replaces a file but does not create missing parent
directories. Verify that the parent directory exists before running the
sequence.

Do not use this unverified path token:

```text
#SM_PROCESS#
```

Known TestMaster symbolic paths include `#SM_ROOT#` and `#SM_DATA#`, but resolve
them with the YASE `ResolvePath` statement before passing the result to a file
operation. Absolute paths are simpler for initial machine checkout.

## Logs and troubleshooting

TMPython creates one log per host session under:

```text
D:\TestMasterData\Process\Python_Automation\python_env\log\tmpython_*.log
```

The log records the actual interpreter, working directory, `sys.path`, module,
class, decoded input dictionary, returned output dictionary, and exceptions.

TestMaster errors for the currently active process may appear under a process
log folder such as:

```text
D:\TestMasterData\log\Python_Automation
D:\TestMasterData\log\PLEIADES
```

The PLEIADES folder can contain Python Automation sequence errors when
PLEIADES is the process currently loaded in TestMaster.

### Errors observed on this machine

| Error/source | Meaning on this installation | Correction |
| --- | --- | --- |
| `5001: The interpreter <Python_310_ALIGNMENT_TEST> was not found` | The YASE `Interpreter` value does not exactly match a section in global `TMPython.ini` | Use `Python_310_PYTHON_AUTOMATION_INTERPRETER` |
| `10500: Expecting value: line 1 column 1 (char 0)` | `ParamIn` is empty, so TMPython has no JSON value to decode | Build nonempty JSON first and connect the populated string variable to `ParamIn` |
| `10500: Invalid \escape` | A Windows path reached the JSON decoder with single backslashes | Put forward slashes in JSON paths, for example `D:/TestMasterData/data/...` |
| `No module named ...` | The YASE `Module` value does not match the Python file location | For a module directly in `python_env`, use the bare filename without `.py` or a package prefix |
| `7: New File` from `STMT_WriteToFile.vi` | The normal file destination or its parent is invalid, missing, or based on an unsupported token | Use an existing absolute directory such as `python_env\log`; `WriteToFile` does not create its parent directory |
| `50003` from `STMT_SaveImageToSpreadsheetFile.vi`, ending in `.tsv\python_vision_input.imgcorr` | The spreadsheet exporter interpreted the supplied `.tsv` path as a directory and attempted to create a correction sidecar below it | Do not use this exporter for the Python bridge; use `IMAQWriteFile` on the existing image reference |
| `50003` from `STMT_IMAQWriteFile.vi` with an absolute `python_env\log` path | This installed image wrapper did not accept that absolute destination pattern | Give it the relative filename `python_vision_input.bmp`; it resolves under `D:\TestMasterData\data\Python_Automation` |
| Generic `50003` | This is a wrapper error, not one unique diagnosis | Read the nested LabVIEW source and statement name in the error text before changing the sequence |

The line number in `$$tmp$$.xseq` identifies the generated runtime copy, not
necessarily the same visible line number in the saved source sequence. Match
the reported statement name and parameters as well as the line number.

## YASE lock files and cached sequences

YASE/TestMaster can keep a sequence locked or execute a generated
`$$tmp$$.xseq`. If an error still reports an old interpreter, module, or
parameter value after the source `.xseq` was edited:

1. Stop the running sequence.
2. Close the sequence editor/debugger that owns the lock.
3. Close YASE/TestMaster if necessary so the lock is released.
4. Reopen the `.xseq` from disk.
5. Confirm the TMPython fields in the editor.
6. Recompile and run again.

Do not delete a lock file while the owning program is still running. The open
editor may overwrite the corrected file with its older in-memory copy.

## Reproducing this setup on another device

1. Install a compatible 64-bit Python 3.10 environment.
2. Create the virtual environment under the desired Python working folder.
3. Install the compatible TestMaster TMPython package into that environment.
4. Copy the Python modules into the configured working directory.
5. Add a matching section to `TMPython.ini` and update all absolute paths.
6. Ensure the configured log directory already exists and is writable.
7. Configure YASE with `ParamIn` and `ParamOut`, not the newer prototype names.
8. Run `SUB_JSON_WriteExtract.xseq` before testing an alignment algorithm.
9. Inspect the newest `tmpython_*.log` and confirm the actual interpreter and
   working directory.
10. Only proceed to motion-enabled sequences after the JSON round trip and all
    machine safety/interlock checks pass.

## Pre-flight checklist for a new YASE/Python bridge

Before the first run:

- Confirm the intended `.xseq` is under the correct process subfolder, while
  the importable `.py` file is directly under the configured working directory.
- Confirm global `TMPython.ini` contains the exact interpreter section and all
  three configured directories exist.
- In the TMPython statement, verify `Interpreter`, bare `Module`, `Class`,
  variable `ParamIn`, and variable `ParamOut` field by field.
- Ensure `ParamIn` is valid, nonempty JSON. Use XML `&quot;` when editing the
  `.xseq` source and forward slashes for every path inside JSON.
- Create diagnostic parent folders before `WriteToFile` runs.
- For an existing IMAQ image reference, use relative `IMAQWriteFile` output and
  pass Python the resolved process-data path.
- Keep image analysis read-only until output fields, units, coordinate axes,
  scale, uncertainty, failure behavior, and limits are independently checked.

After editing in YASE:

1. Save the sequence.
2. Close and reopen it so the on-disk version is reloaded.
3. Recheck the live TMPython and file-statement fields.
4. If a `$$tmp$$.xseq` error contains an old value, release the owning lock and
   rebuild the sequence; do not delete a live lock.
5. Inspect the newest TMPython log for the actual interpreter, module, input,
   output, and traceback.
6. Preserve the request, response, and source image from a successful checkout
   as reproducible evidence.

Useful local import check from `python_env`:

```powershell
.\.venv\Scripts\python.exe -c "from tmpython.statement import TMPythonStatementJ; print('TMPython import OK')"
```

This configuration guide contains machine paths and software versions, but no
credentials or secrets.
