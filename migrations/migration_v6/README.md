# Migration V6 Reviewed Vision Workflow

Read these repository-root files before copying or editing machine-facing files:

```text
MACHINE_CONFIGURATION.md
COMMON_MISTAKES.md
```

Also read `MOTION_SAFETY_AUDIT.md` in this folder.

V6 combines the v4 hardcoded standard positions with a schema-2 reviewed
vision workflow. Python records observations and proposes bounded moves. Only
the guarded YASE sequences call `MoveStage`.

The active V6 machine interface is
`python_vision_geometry.v6_offset_workflow`. Older copied analysis helpers in
that package are retained only as internal compatibility code; no V6 YASE
sequence calls their schema-1 entry points.

## Operator Entry Point

For a normal complete run, start:

```text
SUB_v6_vision_workflow\SUB_V6MainWorkflow_Guarded.xseq
```

The main workflow initializes memory once, approaches the standard positions,
captures and reviews each required view, iterates each correction from a fresh
reviewed image, and ends with read-only geometry verification.

Do not rerun the memory initializer between normal steps. Reinitializing clears
the active records, history, convergence state, and anchored transition plans.

## Independent Subsequence Types

- `SUB_V6MoveToPosition_*.xseq`: move to one hardcoded v4 position and apply
  its zoom/exposure plus `Illu_Coax = 0.9`, `Illu_1 = 0.9`, and
  `Illu_2 = 0.9`.
- `SUB_V6CaptureReviewRecord_*_ReadOnly.xseq`: allow one operator focus/image
  adjustment, query every camera/zoom/tower axis, grab the image, query every
  axis again, and open the editable Tkinter review.
- `SUB_V6OffsetCorrection_*_Guarded.xseq`: calculate one bounded correction
  from the latest active reviewed capture and apply only operator-confirmed
  slow tower moves.
- `SUB_V6Converge_*_Guarded.xseq`: repeat fresh capture, review, and one
  correction until tolerance is met, divergence is detected, or eight
  correction attempts have been used. A ninth capture is allowed only to
  verify the eighth move; it cannot authorize a ninth move.
- `SUB_V6TransitionMove_*_Guarded.xseq`: apply a standard position-to-position
  delta from one stored live anchor. The target remains fixed while its axes
  are completed.
- `SUB_V6FinalVerification_ReadOnly.xseq`: verify both final ball centers and
  their spacing without moving hardware.

These pieces are independently runnable, but not context-free. Before running
a capture or convergence wrapper alone, move to its expected standard/view
position and retain the memory for the same physical run. A transition requires
its source capture record at the current pose; corrected source views also
require the matching capture revision to be converged. Running steps out of
order either uses the active record for that capture ID or fails closed when a
prerequisite is missing.

The enforced order is:

```text
ball 1 coarse X/Z -> ball 1 fine X/Z -> ball 1 side Y
ball 2 coarse X/Z -> ball 2 fine X/Z -> ball 2 side Y
final read-only verification
```

Each fine step requires its recorded reference transitions. Each side step
requires the active fine-top capture to be converged and the top-to-side
transition to be complete. Ball 2 cannot start until ball 1 side alignment is
converged. Once any ball 2 capture is active, V6 rejects backward re-entry into
ball 1 correction or transition motion. To restart ball 1, physically reset the
setup and initialize new memory.

## Capture Review And Memory

Before every grab, the capture sequence reapplies the position's standard
exposure and all three light values, then presents one operator gate for focus
and framing adjustment with camera/tower pose controls. Do not change exposure,
lights, or zoom at this gate. After confirmation, V6 queries the full pose,
grabs, queries the pose again, and rejects an unstable capture.

The review UI preloads the proposed ROIs and detections. The operator can:

- replace a detection;
- redraw an ROI;
- assign a feature role from the capture-specific list;
- save the reviewed result; or
- cancel without changing memory.

The role selector names the required view explicitly. Top captures show only
their laser or ball-circle role plus `Ignore`; mirror captures show only the
side-mirror ball circle, trench-top line, trench-floor line, and `Ignore`.
These readable labels are converted back to the existing canonical role keys
before the reviewed result is stored.

The active capture record stores the exact post-grab pose, pre/post stability
evidence, view, zoom, commanded camera settings, image dimensions, selected
features, scale source, revision, and timestamp. Exposure and illumination are
recorded as the standard values reapplied before the operator gate; the
repository has no verified analog-output readback statement. Re-recording the
same capture ID moves the old active record into history and invalidates
all downstream correction plans, convergence states, transitions, and final
verification. A changed fine-top reference therefore cannot leave a later
side or second-ball convergence marker usable.

Memory stays current when the operator changes a camera or tower position
before the grab because the queried post-adjustment pose, not the hardcoded
standard pose, is recorded.

## Canonical Axes

V6 uses machine-axis names in every schema-2 output:

```text
image right                 -> positive machine_x_um -> Align_X*
image up                    -> positive machine_z_um -> Align_Z*
mirror-corrected vertical   -> machine_y_um          -> Align_Y*
```

Image Y increases downward, so top-view pixel Y has the opposite sign from
`machine_z_um`. Legacy `x`, `y`, and `z` keys are accepted only at input
normalization boundaries. Diagnostics and outputs use the canonical names.

## Coarse And Fine Geometry

The coarse views `2.1.1` and `4.1.1` only bring the relevant ball into the
expected frame. They compare the live reviewed ball center with the saved
standard reviewed ball center. A ball displaced right is corrected with
negative `machine_x_um`; a ball displaced down is corrected with positive
`machine_z_um`.

Both coarse captures use the ball in the upper direct camera image. The lower
mirror image is excluded from their automatic search ROIs and must not be
assigned a coarse top-ball role. The lower mirror is used only by the dedicated
side captures `2.6.1` and `4.6.2`.

Fine top geometry uses the reviewed laser rectangle from `2.4.1`/`4.4.1` and
the reviewed ball from `2.5.1`/`4.5.1`:

```text
measured machine_x_um =
    camera-ball machine_x_um - camera-reference machine_x_um
    + pixel-x(ball - rectangle) * um_per_pixel

measured machine_z_um =
    camera-ball machine_z_um - camera-reference machine_z_um
    - pixel-y(ball - rectangle) * um_per_pixel
```

This compensates for recorded camera X/Z motion between the two captures. The
reference and ball records must have the same fine-top view, zoom, and image
dimensions. Calibration is not reused after a view or zoom change.

The final fine-top targets relative to the rectangle center are:

```text
ball 1: machine_x_um = 289, machine_z_um = 0
ball 2: machine_x_um = 989, machine_z_um = 0
```

## Mirror Side Geometry

The side view is the mirror region at the bottom of the image, not a direct
camera view. Review must contain exactly one mirror ROI, one side-ball center,
one `trench_top_surface` line, and one `trench_bottom_floor` line.

For a full-image Y coordinate:

```text
mirror_flipped_y_px = mirror_roi_bottom_y_px - full_image_y_px
um_per_pixel = 300 um / abs(trench_floor_y_px - trench_top_y_px)
```

The 300 um trench height is therefore the ruler for that reviewed side view.
The code validates line order, ROI containment, uniqueness, and plausible
separation before proposing `Align_Y*`. The ruler is valid only for that view
and zoom. Missing or ambiguous mirror evidence produces no Y move.

Both final side targets place the reviewed ball center at the physical trench
top, represented as relative `machine_y_um = 0`.

## Collision-Aware Move Order

Every hardcoded position first raises every tower that has an X/Z target.
Camera and zoom motion follows, then tower `machine_z_um`, then tower
`machine_x_um`. A tower is lowered to its final `machine_y_um` only after all
lateral targets have been reached. Standard and transition moves use medium
speed; image-derived corrections use slow speed. The standalone hardcoded
position files do not read V6 memory, so their operator gate must not be
accepted unless the chip, trench, both balls, towers, and camera are clear of
the complete move.

Top-view lateral corrections are rejected when the active tower is below the
reviewed Y boundary for that view. Before ball 2 moves in X/Z, Python projects
both possible bounded axis orders into the final rectangle-relative X/Z frame.
It selects the order with the larger minimum ball-to-ball clearance and fails
closed if neither order keeps a strictly positive 500 um sphere separation.
The check deliberately ignores the real raised Y separation, making it
conservative.

Before either top-to-side transition starts lowering a tower, Python validates
the reviewed top geometry against the strict source, ball, taper, and
trench-floor model. The side correction repeats that gate after the transition
before proposing any additional `machine_y_um` move.

The final geometry verifier also requires these nominal surface clearances:

```text
source to ball 1 = 39 um
ball 1 to ball 2 = 200 um
ball 2 to taper = 39 um
each ball to trench floor = 50 um
```

This proves the reviewed ball-center layout, not the entire physical swept
volume. Raw stage-to-object transforms and the collision volumes of grippers,
the trench, chip, camera, and mirror are not calibrated in this repository and
must be commissioned on the machine.

## Convergence And Final Proof

All image-derived moves are bounded and use slow alignment velocities. After
every move, the convergence wrapper requires a new grab and a new reviewed
record. It stops when:

- every residual is within tolerance;
- the residual increases, indicating a possible sign, scale, or feature error;
- a bound is rejected; or
- eight reviewed correction attempts are exhausted; the last move is still
  followed by one fresh read-only convergence check.

Final verification is read-only and requires:

```text
ball 1 = (289, 0, 0) um
ball 2 = (989, 0, 0) um
ball 2 X - ball 1 X = 700 um
```

Coordinates above are ordered as
`(machine_x_um, machine_y_um, machine_z_um)`.

## Motion Policy

- Standard approaches and transitions use medium velocities.
- Vision-derived corrections use slow velocities.
- No close-to-chip V6 sequence uses a fast velocity.
- Every real image-derived move remains operator-confirmed in YASE.
- Python does not directly move hardware.

## Standard-Image Simulator

The simulator is offline and changes only an in-memory pose:

```powershell
.\.venv\Scripts\python.exe migrations\migration_v6\tools\simulate_v6_standard_workflow.py --target all
```

By default it opens only the editable vision-review UI. Add
`--popup-scope yase` to preview the YASE operator gates around the same
standard-image review UI. Add `--popup-scope all` to include non-operational
diagnostics as well:

```powershell
.\.venv\Scripts\python.exe migrations\migration_v6\tools\simulate_v6_standard_workflow.py --target all --popup-scope yase
```

A headless geometry replay is:

```powershell
.\.venv\Scripts\python.exe migrations\migration_v6\tools\simulate_v6_standard_workflow.py --headless --target all --output tmp\v6_all_trace.json
```

Injected pixel errors can be supplied with `--coarse-shift-x-px`,
`--coarse-shift-y-px`, `--fine-shift-x-px`, `--fine-shift-y-px`, and
`--side-shift-y-px`.

A simulator baseline is replaced only through an explicit option:

```powershell
.\.venv\Scripts\python.exe migrations\migration_v6\tools\simulate_v6_standard_workflow.py --headless --replace-baseline 2.1.1=tmp\reviewed_2.1.1.json
```

The previous file is first copied under
`tmp\v6_baseline_backups\<timestamp>\`. A missing baseline is never silently
created or overwritten.

## Copy Layout

Copy the YASE folders under the configured Python Automation process:

```text
SUB_v6_standard_positions\
SUB_v6_vision_workflow\
```

Copy `python_vision_geometry\`, `vision_recognition_lab.py`,
`requirements.txt`, and `standard_positions_v4\` into the configured
`python_env` locations documented in `MACHINE_CONFIGURATION.md`.

Before running the workflow in the cleanroom, run this from
`D:\TestMasterData\Process\Python_Automation\python_env`:

```powershell
.\.venv\Scripts\python.exe -m python_vision_geometry.cleanroom_runtime_check --require-tmpython --json-output log\v6_cleanroom_runtime_check.json
```

This is read-only. It verifies that the copied Python environment can import
the V6 TMPython entrypoints, the real `vision_recognition_lab.py`, the OpenCV
and scikit-image recognizers, the side trench ruler algorithm, Tkinter, and
all required reviewed baseline JSON files. A failed check means do not start
`SUB_V6MainWorkflow_Guarded.xseq`.

## Commissioning Boundary

Repository validation covers Python tests, simulator behavior, XML parsing,
Goto/static audits, configured settings, and velocity selection. It is not
physical machine validation. Actual correction signs, soft limits, collision
clearance Y, gripper/trench/camera swept volumes, wait timing, camera stability
tolerance, and mirror Y behavior must be commissioned with guarded small moves
on the target machine before operational use.
