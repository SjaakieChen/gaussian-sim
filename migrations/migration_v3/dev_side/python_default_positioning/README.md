# dev_side/python_default_positioning

`default_position_move_planner.py` is the local planner used to validate and
regenerate the migration v3 numbered default-position YASE sequences.

The normal machine runtime path does not call this Python file. Operators run
the direct YASE wrappers in:

```text
D:\TestMasterData\Process\Python_Automation\SUB_default_positioning\
```

Those wrappers pass hard-coded values into the guarded apply sequences. Each
child call moves one stage or sets one camera value only, with a popup before
the hardware operation.

For local checks, the planner input is intentionally small:

```json
{
  "schema_version": 3,
  "target_id": "3.0.0",
  "default_positions_path": "migrations/migration_v3/dev_side/python_default_positioning/default_positions.json",
  "limits": {
    "max_single_move_um": 200000,
    "max_exposure": 500000
  },
  "algorithm": {
    "name": "default_position_move_planner",
    "tolerance_um": 0.05
  }
}
```

The example file under `examples\` uses a repository-relative path so it can run
from this checkout during smoke tests. Do not copy that relative path into YASE.

The planner returns a full `planned_actions` list and exposes the next action
in compatibility flat fields:

```text
action_type1
stage1
target1_um
max_single_move_um
analog_line1
analog_value1
max_exposure1
confirm_text1
```

Python never calls hardware. The apply `.xseq` files are responsible for
fiducials, stage/analog allowlists, delta/range checks, popup confirmation,
the single hardware operation, wait, and error check.
