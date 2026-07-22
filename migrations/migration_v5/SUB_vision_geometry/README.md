# SUB_vision_geometry v5

This folder contains the YASE sequences for the v5 vision-geometry bridge.
The helper sequences are read-only. The final workflow wrapper is
motion-capable only by delegating a parsed Python action to the existing guarded
default-positioning move sequence.

## Implemented

`SUB_V5MacroAlignmentSolve_ReadOnly.xseq` calls TMPython with:

```text
Interpreter = Python_310_PYTHON_AUTOMATION_INTERPRETER
Module      = python_vision_geometry.sequence_memory_workflow
Class       = VisionSequenceMemoryWorkflowStep
ParamIn     = s_PythonInputJson
ParamOut    = s_PythonResultJson
```

It builds a `solve_macro` JSON request, writes the request to:

```text
D:\TestMasterData\Process\Python_Automation\python_env\log\v5_macro_alignment_input.json
```

and writes the returned JSON to:

```text
D:\TestMasterData\Process\Python_Automation\python_env\log\v5_macro_alignment_result.json
```

The returned payload is displayed and stored as a string only. It is not parsed
into motion commands.

`SUB_V5SequenceMemoryInit_ReadOnly.xseq` creates the durable memory file:

```text
D:\TestMasterData\Process\Python_Automation\python_env\log\v5_sequence_memory.json
```

It uses the same TMPython module/class and passes:

```json
{"command":"init"}
```

with the v4 standard-position path, 500 um laser rectangle, 500 um ball
diameter, 300 um trench depth, and `apply_remembered_focus_planes=false`.

`SUB_V5SequenceMemoryNextAction_ReadOnly.xseq` reads that memory file and asks
Python for:

```json
{"command":"next_action"}
```

It writes the next-action response to:

```text
D:\TestMasterData\Process\Python_Automation\python_env\log\v5_sequence_next_action.json
```

That response can include a proposed `next_capture.machine_positions_um`, but
this sequence only displays and stores the JSON string. It does not parse it
into stage commands.

`SUB_V5CaptureReviewRecord_ReadOnly.xseq` is the live capture/review bridge.
It keeps YASE limited to hardware IO:

1. check that stages are fiducialed;
2. grab one `CAM_12` frame;
3. save `python_vision_input.bmp` with `IMAQWriteFile`;
4. query absolute positions for `Camera_X/Y/Z`, `Align_X/Y/Z1`, and
   `Align_X/Y/Z2`;
5. call TMPython class `VisionSequenceReviewRecordStep`.

That Python class asks the v5 memory for the next capture, opens the existing
Tkinter vision lab on the saved frame, lets the operator correct the selected
circle/rectangle features, and records the reviewed session plus live
`machine_positions_um`. If the UI closes with no selected shapes, the memory is
not updated.

The sequence expects the v4 standard-position bundle to be copied to:

```text
D:\TestMasterData\Process\Python_Automation\python_env\standard_positions_v4\
```

with `standard_positions.json` at that folder root and the image subfolder
layout preserved.

`SUB_V5MacroAlignmentFinalWorkflow_Guarded.xseq` is the final thin operator
wrapper. It keeps the difficult logic in Python and keeps YASE focused on
machine IO:

1. check that all stages are fiducialed;
2. query absolute positions for `Camera_X/Y/Z`, `Align_X/Y/Z1`, and
   `Align_X/Y/Z2`;
3. call Python command `next_motion_or_capture`;
4. parse only `ok`, `schema_version`, `action`, `stage1`, `target1_um`, and
   `confirm_text1`;
5. call `process\SUB_default_positioning\SUB_ApplyDefaultPositionMove` for one
   guarded move, or call `SUB_V5CaptureReviewRecord_ReadOnly.xseq` for one
   picture/UI/save step, or call `SUB_V5MacroAlignmentSolve_ReadOnly.xseq`.

The final wrapper has no direct `MoveStage`, no direct camera grab, and no
vision logic. Every image measurement goes through the Tkinter review UI before
it can be recorded into memory.

## Planned Capture Sequence

Current machine order:

1. run `SUB_V5SequenceMemoryInit_ReadOnly.xseq`;
2. run `SUB_V5MacroAlignmentFinalWorkflow_Guarded.xseq`;
3. the final wrapper repeats the Python/action loop internally until it reaches
   `solve_ready` or a guarded error path;
4. inspect `v5_final_workflow_result_from_yase.json` and the displayed final
   result/status if the wrapper stops early.

The final-step Python command is intentionally flat so a short YASE wrapper can
reuse the existing parse/apply pattern:

```json
{
  "command": "next_motion_or_capture",
  "stage1": "Camera_X",
  "target1_um": -38997.0,
  "distance1_um": -38997.0,
  "move_mode1": "Absolute",
  "confirm_text1": "..."
}
```

The checked-in final workflow performs that parse/apply handoff through
`SUB_ApplyDefaultPositionMove`. It is XML/test verified in the repo, but it
still needs a careful machine-side checkout before it should be treated as a
trusted operator procedure.

Do not add `MoveStage` to the vision-recognition sequence itself. Motion
belongs in separately reviewed default-position/apply sequences with explicit
fiducial checks, bounds checks, wait handling, and operator confirmation.

## Documentation Evidence

Before creating these sequences, these sources were checked:

- `MACHINE_CONFIGURATION.md`: verified TMPython interpreter section, `ParamIn`
  / `ParamOut` names, process paths, and read-only JSON bridge rules.
- `testmaster documentation\TestMaster Documentation 2020.1.10 (1).pdf`:
  pages 183-187 for TMPython JSON/module/class execution, page 656 for
  `IMAQWriteFile`, page 836 for `WriteToFile`, and pages 1017-1020 for stage
  library / `QueryStage` behavior used by later recording/apply work.
- `testmaster documentation\Yase_TM_HB_Sep_2018.pdf`: YASE sequence editor,
  statement, parameter, variable, and XML sequence-file behavior.
- Existing checked-in sequences:
  `migration_v2/SUB_alignment_solving/SUB_FixedZStagedBallPlacement_ReadOnly.xseq`
  and
  `migration_v4/SUB_vision_recognition/SUB_OpenVisionRecognitionLab_ReadOnly.xseq`.
