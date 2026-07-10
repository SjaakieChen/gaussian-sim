# migration_v2 python_alignment_solving

`fixed_z_staged_ball_placement.py` is the TMPython-side planner for the v2
fixed-Z ball-placement handoff.

YASE call:

```text
Interpreter = Python_310_ALIGNMENT_TEST
Module      = python_alignment_solving.fixed_z_staged_ball_placement
Class       = FixedZStagedBallPlacementStep
Input JSON  = s_PythonInputJson
Result JSON = s_PythonResultJson
```

The input uses machine coordinates in micrometres:

```text
machine X -> simulation z, optical propagation axis
machine Z -> simulation x
machine Y -> simulation y, vertical/transverse no-go axis
```

The default staging axis is `Align_Y*`, because the checked-in no-go/trench
collision model is expressed as a machine-Y vertical clearance problem. If the
real machine commissioning data proves a different safe approach axis is needed,
set `staging.clearance_stage_axis` to `Align_Z` or `Align_X` explicitly.

The result returns one next absolute move plus the complete remaining plan:

```text
action
stage1
target1_um
distance1_um       same value as target1_um for MoveStage Absolute
delta1_um
move_mode1         Absolute
confirm_text1
planned_moves[]
state.target_positions_um
state.axis_mapping
state.no_go_zones_um
```

Python only plans and checks the geometry. YASE must still confirm every move,
check fiducials, validate the stage/target/delta, call `MoveStage`, wait, and
check the axis error result.
