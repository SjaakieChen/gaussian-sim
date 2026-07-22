# SUB_vision_geometry v5

This folder contains read-only YASE sequences for the v5 vision-geometry
bridge.

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

The sequence expects the v4 standard-position bundle to be copied to:

```text
D:\TestMasterData\Process\Python_Automation\python_env\standard_positions_v4\
```

with `standard_positions.json` at that folder root and the image subfolder
layout preserved.

## Planned Capture Sequence

Later read-only capture sequences will:

1. move to a reviewed default position through existing default-position
   sequences;
2. grab one `CAM_12` frame;
3. save the same image reference with `IMAQWriteFile`;
4. open `vision_recognition_lab.VisionRecognitionLabStep`;
5. write the returned JSON to `python_env\log`;
6. record the session and queried `machine_positions_um` into v5 sequence
   memory.

Do not add `MoveStage` to the vision-recognition sequence itself. Motion
belongs in separately reviewed default-position/apply sequences with explicit
fiducial checks, bounds checks, wait handling, and operator confirmation.

## Documentation Evidence

Before creating this sequence, these sources were checked:

- `MACHINE_CONFIGURATION.md`: verified TMPython interpreter section, `ParamIn`
  / `ParamOut` names, process paths, and read-only JSON bridge rules.
- `testmaster documentation\Yase_TM_HB_Sep_2018.pdf`: YASE sequence editor,
  statement, parameter, variable, and XML sequence-file behavior.
- `testmaster documentation\TestMaster Documentation 2020.1.10 (1).pdf`:
  statement-library references for file, stage, and parameter behavior.
- Existing checked-in sequences:
  `migration_v2/SUB_alignment_solving/SUB_FixedZStagedBallPlacement_ReadOnly.xseq`
  and
  `migration_v4/SUB_vision_recognition/SUB_OpenVisionRecognitionLab_ReadOnly.xseq`.
