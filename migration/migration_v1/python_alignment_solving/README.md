# python_alignment_solving

Copy this folder to the process-local Python working directory used by
`TMPython.ini`, for example:

```text
#SM_PROCESS#\Python\python_alignment_solving\
```

The TMPython call is:

```text
Module = python_alignment_solving.fixed_z_alignment_solver
Class  = FixedZAlignmentSolveStep
```

## Example Files

| File | Purpose |
| --- | --- |
| `examples\fixed_z_alignment_input.json` | Example JSON passed into TMPython/YASE as the solver input. |
| `examples\fixed_z_alignment_output.json` | Expected JSON returned by the solver for that exact input. |

The output is a new result JSON, not the same object as the input JSON. YASE
stores/sends the input separately and receives this returned payload from
TMPython.

## What It Solves

`FixedZAlignmentSolveStep` uses the same fixed-Z transverse model as the
simulation: machine `Y` / optical propagation positions are held fixed, then the
target machine `X` and `Z` coordinates for the two ball lenses are solved so
the modeled beam reaches the fiber coordinate with zero outgoing transverse
angle.

The class returns:

- `action`, `stage1`, `distance1_um`, and `moves` for the existing YASE motion
  contract;
- flat absolute target fields such as `target_Align_X1_um`;
- `state.target_positions_um` and `state.path_um` for logging and repeated
  calls;
- generated no-go zones and any violations in `state`.

The output is deliberately JSON-first. The checked-in process `prototypes.xml`
does not currently contain JSON parsing statements, so import/verify the
machine JSON/TMPython prototypes before wiring `MoveStage`.

## Coordinate Convention

All distances are micrometres.

| Machine axis | Meaning in this solver |
| --- | --- |
| `X` / `Align_X*` | transverse x |
| `Z` / `Align_Z*` | transverse z, matching simulation y |
| `Y` / `Align_Y*` | optical propagation position, matching simulation z |

No sign flips are applied.

## First Local Smoke Test

From the repository root:

```powershell
Get-Content migration\migration_v1\python_alignment_solving\examples\fixed_z_alignment_input.json | python migration\migration_v1\python_alignment_solving\fixed_z_alignment_solver.py
```

To regenerate the checked-in example output shape:

```powershell
Get-Content migration\migration_v1\python_alignment_solving\examples\fixed_z_alignment_input.json | python migration\migration_v1\python_alignment_solving\fixed_z_alignment_solver.py | python -m json.tool
```
