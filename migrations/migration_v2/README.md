# Migration v2 Staged Ball Placement Bundle

Before editing or copying these files to the machine, read the repository-level
[`MACHINE_CONFIGURATION.md`](../../MACHINE_CONFIGURATION.md). It is the
authoritative machine configuration and evidence log for verified Python
Automation process paths, TMPython interpreter section, module layout, and log
directory.

This bundle extends the v1 fixed-Z theoretical solve into a staged machine
handoff:

1. use laser and detector coordinates to solve the two ball-lens target poses;
2. move the balls to a safe clearance coordinate;
3. move the non-clearance axes to the solved coordinates;
4. lower the clearance axis to the solved coordinate.

The bundle deliberately keeps Python and YASE responsibilities separate:

```text
Python:
  - parse measured laser, detector, and ball coordinates
  - solve the fixed-Z two-ball target
  - build a collision-checked staged absolute-move plan
  - return the next move and full plan as JSON

YASE/TestMaster:
  - check fiducialized stages
  - show an operator confirmation popup before every MoveStage
  - validate stage names, absolute targets, and deltas
  - call MoveStage, wait, and check axis errors
  - own all real hardware motion
```

Files:

```text
migrations\migration_v2\
  python_alignment_solving\
    fixed_z_staged_ball_placement.py
    examples\fixed_z_staged_ball_placement_input.json
    README.md
  SUB_alignment_solving\
    SUB_FixedZStagedBallPlacement_ReadOnly.xseq
    SUB_ApplyFixedZStagedBallMove.xseq
    README.md
```

Machine deployment for the current Python Automation checkout:

```text
Copy Python source:
migrations\migration_v2\python_alignment_solving\fixed_z_staged_ball_placement.py

to:
D:\TestMasterData\Process\Python_Automation\python_env\fixed_z_staged_ball_placement.py

Copy YASE files to:
D:\TestMasterData\Process\Python_Automation\SUB_alignment_solving\

Diagnostic JSON is written under:
D:\TestMasterData\Process\Python_Automation\python_env\log\
```

The machine `TMPython_ExecuteScript` field values must be:

```text
Interpreter = Python_310_PYTHON_AUTOMATION_INTERPRETER
Module      = fixed_z_staged_ball_placement
Class       = FixedZStagedBallPlacementStep
ParamIn     = s_PythonInputJson
ParamOut    = s_PythonResultJson
```

Axis mapping is explicit and must not be changed silently:

```text
machine X -> simulation z / optical propagation
machine Z -> simulation x
machine Y -> simulation y
```

The default clearance axis is `Align_Y*` because the simulator/Tkinter no-go
logic treats the trench/floor clearance as simulation `y`, which maps to
machine `Y`. If machine commissioning proves a different approach axis is
required, change `staging.clearance_stage_axis` in the input JSON and re-run the
planner plus the YASE dry run.

Relevant checked sources:

- `yase_example_processes\YASE_MACHINE_CONVENTIONS.md`
- `yase_example_processes\YASE_PROGRAMMING_GUIDE.md`
- `yase_example_processes\SUB_Positioning\SUB_Test_DrawCircle_AlignX1Z1.xseq`
- `yase_example_processes\SUB_Positioning\SUB_SYS_MoveToPos_Predispense.xseq`
- `testmaster documentation\Yase_TM_HB_Sep_2018.pdf`
- `testmaster documentation\TestMaster Documentation 2020.1.10 (1).pdf`
