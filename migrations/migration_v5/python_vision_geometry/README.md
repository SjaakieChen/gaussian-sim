# python_vision_geometry

`position_bias_planner.py` compares a live gross `2.1` or `4.1` capture against
the reviewed official gross baseline and returns biased close-position targets
for the later focus captures.

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

`sequence_memory_workflow.py` is the durable capture-memory layer for the same
workflow. It creates a skeleton memory file for the required `2.1.1`, `2.4.1`,
`2.5.1`, `2.6.1`, `4.1.1`, `4.4.1`, `4.5.1`, and `4.6.2` captures, records
saved vision-lab session JSON/image paths/queried `machine_positions_um`, and
then builds the solver payload. If a record includes live `machine_positions_um`,
those positions override the standard JSON for that capture before solving.

`sequence_geometry_memory.py` handles the actual multi-focus capture pattern:
it remembers the selected laser rectangle from the plank/reference focus image,
remembers the selected ball circle from the ball-focus image, and compares them
through the unchanged top-view camera `x/z` frame. It uses the 500 um laser
rectangle short edge for top-view pixel scale, the 500 um ball diameter for
scale checks, and the current 300 um trench-depth assumption for the fallback
vertical coordinate.

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
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.macro_alignment_simulator migrations\migration_v5\python_vision_geometry\examples\macro_alignment_auto_input.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_memory_workflow migrations\migration_v5\python_vision_geometry\examples\sequence_memory_auto_input.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.vision_geometry_solver migrations\migration_v5\python_vision_geometry\examples\vision_geometry_input.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_geometry_memory migrations\migration_v5\python_vision_geometry\examples\sequence_geometry_memory_input.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_geometry_memory migrations\migration_v5\python_vision_geometry\examples\sequence_geometry_memory_auto_input.json
```

Typical capture-memory loop:

```powershell
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_memory_workflow init --output migrations\migration_v5\python_vision_geometry\examples\current_sequence_memory.json
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_memory_workflow record migrations\migration_v5\python_vision_geometry\examples\current_sequence_memory.json --capture-id 2.4.1 --session path\to\2.4.1.session.json --image path\to\2.4.1.bmp
.\.venv\Scripts\python.exe -m migrations.migration_v5.python_vision_geometry.sequence_memory_workflow solve migrations\migration_v5\python_vision_geometry\examples\current_sequence_memory.json --output migrations\migration_v5\python_vision_geometry\examples\current_sequence_result.json
```
