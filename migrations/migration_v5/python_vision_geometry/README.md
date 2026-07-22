# python_vision_geometry

`position_bias_planner.py` compares a live gross `2.1` or `4.1` capture against
the reviewed official gross baseline and returns biased close-position targets
for the later focus captures.
Each returned plan includes `bias_mapping_evidence`. Unless a future caller
supplies a validated calibration with `use_for_motion=true`, the biased
positions are marked as read-only/operator-review proposals.

For the fixed v4 standard gross images, `position_bias_planner.py` supports
`"auto_detect_gross_sessions": true`. That mode detects the coarse ball center
inside capture-specific ROIs for `2.1.1` and `4.1.1`, then computes the bounded
tower-only offset for the close positions. A live candidate image can be passed
as `candidate_image_path`; otherwise the standard image is used, producing a
zero-bias standard-image baseline.

`vision_geometry_solver.py` fuses reviewed vision-session JSON from the top and
mirror/side captures into `machine_coordinates_um`.

`macro_alignment_simulator.py` runs the v5 machine-coordinate workflow as one read-only
command: gross ball detection, bounded close-position bias planning, focused
feature memory, and final `machine_coordinates_um`. By default it auto-detects
the fixed v4 standard images and uses the 300 um trench model for
`machine_y_um`.

`standard_capture_evidence.py` is the audit view of the same pipeline. It runs
the standard v4 images through the gross and focused detectors, then reports
the detected gross ball centers/radii, 500 um ball scale, 500 um rectangle
scale, gross-to-close machine-position deltas, focus-plane memory, and final
`machine_coordinates_um`. It also reports same-camera gross motion samples
where reviewed ball detections exist. A full tower-to-pixel transform is marked
usable only when those samples have enough rank; the current v4 evidence is
diagnostic, not a motion-approved calibration.

`sequence_memory_workflow.py` is the durable capture-memory layer for the same
workflow. It creates a skeleton memory file for the required `2.1.1`, `2.4.1`,
`2.5.1`, `2.6.1`, `4.1.1`, `4.4.1`, `4.5.1`, and `4.6.2` captures, records
saved vision-lab session JSON/image paths/queried `machine_positions_um`, and
then builds the solver payload. If a record includes live `machine_positions_um`,
those positions override the standard JSON for that capture before solving.
Recorded focused captures also build `focus_plane_memory`: top laser/reference
focus, top ball focus, and side ball focus camera `y` values. `next_action`
returns this as remembered same-height focus guidance for later captures; it
only overwrites the returned camera `y` when the memory was initialized with
`apply_remembered_focus_planes`.
The same module also has a read-only `next_action` command that reports the
next gross/focused capture, a missing official gross baseline, or `solve_ready`.
When a gross capture has been reviewed, the next focused capture response
includes the bounded biased `machine_positions_um` target for that position.
For the final machine loop, the module also exposes
`next_motion_or_capture`. It compares live YASE-queried positions against the
next capture target and returns one flat action: `move_to_next_capture` with
`stage1`/`target1_um`/`confirm_text1`, `capture_review_record_required`, or
`solve_ready`. This keeps Python responsible for the hard workflow decision and
keeps actual movement in a YASE guarded apply sequence.

The desktop vision recognition lab can write the same memory file for offline
standard-image work. After selecting detected shapes with `Use selected`, click
`Save v5`; for v4 standard images this writes
`Standard position images\v4\v5_sequence_memory\v5_sequence_memory.json` with
the reviewed session and the notebook `machine_positions_um`. Clicking
`Save official` still writes the score baseline and also attempts to register
that reviewed session as the v5 official gross baseline. Empty selections are
rejected so they cannot satisfy `next_action`.

`sequence_geometry_memory.py` handles the actual multi-focus capture pattern:
it remembers the selected laser rectangle from the plank/reference focus image,
remembers the selected ball circle from the ball-focus image, and compares them
through the unchanged top-view camera `x/z` frame. It uses the 500 um laser
rectangle short edge for top-view pixel scale, the 500 um ball diameter for
scale checks, and the current 300 um trench-depth assumption for the fallback
vertical coordinate.
Saved vision-lab sessions may include `feature_role` and `selection_index` on
selected shapes. The solver prefers explicit roles such as `laser_reference`,
`ball_1_top_ball`, `ball_2_top_ball`, and `side_reference` before falling back
to the older first rectangle / first circle ordering. Current lab exports add
default roles automatically and allow operator overrides in the detected-shapes
table, so old sessions and new reviewed sessions both remain accepted.
The solver now returns this as explicit `focus_memory`: the reference camera
`y`, ball-focus camera `y`, the unchanged top-view camera `x/z` registration,
and the physical model `ball_center_y_um = 250 - 300 = -50`.

For the fixed v4 standard images, `sequence_geometry_memory.py` also supports
`"auto_detect_missing_sessions": true`. That mode fills missing standard
sessions from known capture-specific ROIs, for example auto-detecting the
missing `2.5.1` top ball circle. Use this for offline standard-image reconstruction and
vision-script development; machine runs should still prefer reviewed saved
session JSON.

Side-view captures now contribute side-reference memory as well. The solver
detects a horizontal bright-to-dark edge in the side image and reports a
`side_height_candidate` by scaling the ball center-to-edge pixel distance with
the 500 um ball diameter. By default this is diagnostic only and
`machine_y_um` stays on the 300 um trench model. To explicitly use the detected
side-reference candidate, pass:

```json
{"machine_y_source": "side_reference"}
```

Keep that opt-in mode read-only until the chosen side edge is reviewed as the
right physical reference plane.

The module is intentionally read-only. It has no TestMaster or YASE motion
calls. When used through TMPython, the caller should write its JSON result to
disk and review it before any later movement sequence consumes the coordinates.

Local smoke commands:

```powershell
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.position_bias_planner migrations\migration_v5\python_vision_geometry\examples\position_bias_input.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.position_bias_planner migrations\migration_v5\python_vision_geometry\examples\position_bias_auto_input.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.standard_capture_evidence --standard-positions "Standard position images\v4\standard_positions.json"
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.macro_alignment_simulator migrations\migration_v5\python_vision_geometry\examples\macro_alignment_auto_input.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_memory_workflow migrations\migration_v5\python_vision_geometry\examples\sequence_memory_auto_input.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.vision_geometry_solver migrations\migration_v5\python_vision_geometry\examples\vision_geometry_input.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_geometry_memory migrations\migration_v5\python_vision_geometry\examples\sequence_geometry_memory_input.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_geometry_memory migrations\migration_v5\python_vision_geometry\examples\sequence_geometry_memory_auto_input.json
```

Typical capture-memory loop:

```powershell
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_memory_workflow init --output migrations\migration_v5\python_vision_geometry\examples\current_sequence_memory.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_memory_workflow next migrations\migration_v5\python_vision_geometry\examples\current_sequence_memory.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_memory_workflow record migrations\migration_v5\python_vision_geometry\examples\current_sequence_memory.json --capture-id 2.4.1 --session path\to\2.4.1.session.json --image path\to\2.4.1.bmp --camera-x -38997 --camera-y -45395 --camera-z -93995 --tower-1-x 5331 --tower-1-y 12291 --tower-1-z 15198
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_memory_workflow next migrations\migration_v5\python_vision_geometry\examples\current_sequence_memory.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_memory_workflow next-motion migrations\migration_v5\python_vision_geometry\examples\current_sequence_memory.json --camera-x -38997 --camera-y -45996 --camera-z -97694 --tower-1-x 5331 --tower-1-y 13290 --tower-1-z 12998
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_memory_workflow solve migrations\migration_v5\python_vision_geometry\examples\current_sequence_memory.json --output migrations\migration_v5\python_vision_geometry\examples\current_sequence_result.json
```

The `record` command accepts `--camera-x/y/z`, `--tower-1-x/y/z`, and
`--tower-2-x/y/z` in micrometres. Use the current queried machine coordinates
for the capture; those values feed both standard-position overrides and
same-height `focus_plane_memory`.

To make `next_action` apply remembered camera `y` values to returned capture
targets, initialize with:

```powershell
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_memory_workflow init --apply-remembered-focus-planes --output migrations\migration_v5\python_vision_geometry\examples\current_sequence_memory.json
```
