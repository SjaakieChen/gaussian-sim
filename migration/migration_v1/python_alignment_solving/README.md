# python_alignment_solving

This is the legacy migration v1 solver. Do not copy its coordinate convention
into machine-motion code. The corrected universal mapping is `Align_X` ->
simulation z, `Align_Z` -> simulation x, and `Align_Y` -> simulation y; use
`migration_v2` for staged machine motion.

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

`FixedZAlignmentSolveStep` uses the legacy v1 fixed-Z transverse model:
machine `Y` positions are held fixed, then the target machine `X` and `Z`
coordinates for the two ball lenses are solved so the modeled beam reaches the
fiber coordinate with zero outgoing transverse angle.

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

## Legacy Coordinate Convention

All distances are micrometres. This table documents v1 behavior only; it is not
the current machine convention.

| Machine axis | Meaning in this solver |
| --- | --- |
| `X` / `Align_X*` | transverse x |
| `Z` / `Align_Z*` | legacy second transverse coordinate |
| `Y` / `Align_Y*` | legacy held propagation coordinate |

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
