# Migration v1 Alignment Solving Bundle

This bundle contains the first fixed-Z theoretical alignment solve handoff for
TestMaster/YASE.

Do not use v1 as a machine-motion coordinate reference. Its handoff predates
the corrected universal mapping (`Align_X` -> simulation z, `Align_Z` ->
simulation x, `Align_Y` -> simulation y). Use `migration_v2` for staged
machine motion.

```text
migration\migration_v1\
  SUB_alignment_solving\
    SUB_FixedZAlignmentSolving_ReadOnly.xseq
    SUB_ApplyFixedZAlignmentSolveMove.xseq
    README.md
  python_alignment_solving\
    fixed_z_alignment_solver.py
    examples\fixed_z_alignment_input.json
    examples\fixed_z_alignment_output.json
    README.md
```

The Python solver takes the measured laser, fiber/waveguide, and two ball-lens
coordinates in micrometres. It holds machine `Align_Y*` fixed, solves machine
`Align_X*` and `Align_Z*` target positions with the same fixed-Z transverse
model used by the simulator, and returns a YASE-friendly JSON move contract.

The returned JSON includes:

```text
action
stage1
distance1_um
target_Align_X1_um
target_Align_Z1_um
target_Align_X2_um
target_Align_Z2_um
state.target_positions_um
state.path_um
```

The generated path is checked against strict ball-lens axial clearance and
no-go zones before a move is emitted. YASE/TestMaster must still parse,
validate, and execute each move.

## References Used

- `yase_process\YASE_MACHINE_CONVENTIONS.md` for machine-axis to simulation-axis mapping.
- `yase_process\YASE_PYTHON_INTEGRATION_README.md` for the TMPython JSON move contract.
- `yase_process\YASE_PROGRAMMING_GUIDE.md` for safe subsequence structure and motion checks.
- `yase_process\SUB_Positioning\SUB_Test_DrawCircle_AlignX1Z1.xseq` for the guarded `MoveStage` / wait / axis-check pattern.
- `alignment_algorithms\position_solve.py` for the simulation fixed-Z transverse response solve.
