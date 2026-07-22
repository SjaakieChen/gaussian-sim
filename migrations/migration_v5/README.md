# Migration v5 Vision Geometry

Migration v5 is the geometry bridge for turning reviewed vision-lab
measurements into one machine-coordinate frame for:

- the laser middle/reference;
- ball lens 1;
- ball lens 2.

Most v5 files are read-only bridges. The motion-capable entry point is
`SUB_V5MacroAlignmentFinalWorkflow_Guarded.xseq`, and it stays intentionally
thin: Python decides the next flat action, YASE performs only a guarded move
through `SUB_ApplyDefaultPositionMove`, or runs the picture/review and solve
subsequences. Python does not move hardware.

## Coordinate convention

Use the same machine-axis convention as `migration_v2`:

- `machine_x_um` / `Align_X*`: optical propagation axis;
- `machine_z_um` / `Align_Z*`: horizontal transverse axis in the top view;
- `machine_y_um` / `Align_Y*`: vertical transverse / clearance axis.

The v5 output is always `machine_coordinates_um`. The zero entry is
`machine_reference`, defined by the laser rectangle center feature. Ball entries
are machine-axis offsets from that reference in micrometres.

## Measurement sequence

The sequence is defined in `measurement_plan.json`.

For ball 1:

1. Move to `2.1`, grab/open vision lab for gross dual-view confirmation.
2. Use the reviewed offset to bias the planned close-view positions.
3. Move to `2.4`, grab/open vision lab for chip/laser reference focus.
4. Move to `2.5`, grab/open vision lab for ball top focus.
5. Move to `2.6`, grab/open vision lab for mirror/side height focus.

For ball 2:

1. Move to `4.1`, grab/open vision lab for gross dual-view confirmation.
2. Use the reviewed offset to bias the planned close-view positions.
3. Move to `4.4`, grab/open vision lab for chip/laser reference focus.
4. Move to `4.5`, grab/open vision lab for ball top focus.
5. Move to `4.6.2`, grab/open vision lab for mirror/side height focus.

The top/direct views provide `machine_x_um` and `machine_z_um`. The mirror/side
views provide `machine_x_um` and `machine_y_um`. The repeated `machine_x_um`
measurement is a consistency check between top and side views.

The current practical sequence can also solve from remembered features when the
laser rectangle and ball are not focused in the same image:

1. Save the selected laser/reference rectangle from `2.4.1` or `4.4.1`.
2. Save the selected ball circle from `2.5.1` or `4.5.1`.
3. Use the unchanged top-view camera `x/z` coordinates to compare those pixel
   features in one image frame.
4. Convert pixels using the laser rectangle's 500 um short edge.
5. Estimate `machine_y_um` with the current 300 um trench-depth assumption:
   ball center = 250 um radius - 300 um trench depth = -50 um relative to the
   laser rectangle/chip-top plane.
The solver returns the supporting `focus_memory` as well: the reference and
ball-focus camera `y` values, the unchanged camera `x/z` registration check,
and the physical ball/trench height model used for `machine_y_um`.
The sequence memory also stores recorded camera `y` values as
`focus_plane_memory` keyed by feature height. Later `next_action` responses use
that memory as same-height focus guidance, and only apply it to the returned
target when `apply_remembered_focus_planes` is explicitly enabled.

The gross `2.1` and `4.1` views are not final coordinate evidence. They are
used to keep the next close-focus moves from blindly assuming that pickup
landed exactly like the standard captures. `position_bias_planner.py` computes
a bounded tower-only bias from the selected ball-center pixel shift. It leaves
the reviewed camera, zoom, exposure, and focus settings unchanged.
The current bias output is explicitly marked as a read-only/operator-review
proposal through `bias_mapping_evidence`; it is not motion-approved unless a
later validated gross-view calibration is supplied.

For offline standard-image reconstruction, `python_vision_geometry/macro_alignment_simulator.py`
runs the whole read-only v5 chain from the standard images: gross ball
detection, close-position bias planning, focused rectangle/ball memory, side
diagnostics, and final `machine_coordinates_um`.
It is the current single-command simulator for testing the vision script against
the saved v4 picture set.
`python_vision_geometry/standard_capture_evidence.py` is the companion audit
command: it reports the actual standard-image detections, 500 um ball/rectangle
scales, focus-plane memory, and gross-to-close machine-position deltas used by
the simulator. It also reports empirical same-camera gross motion samples and
their matrix rank; underconstrained samples remain diagnostic and are not
motion-approved calibration.

The vision lab exports additive selected-feature metadata for this workflow:
rectangles default to `feature_role=laser_reference`, circle/fitted silhouette
features default to `feature_role=ball_candidate`, and every selected item gets
`selection_index`. Operators can override the selected role in the detected
shape table for captures that need a more specific value. The v5 solver
prefers explicit roles such as `laser_reference` and `ball_1_top_ball` before
falling back to legacy shape order, so reviewed sessions no longer have to rely
only on first rectangle / first circle ordering.
The lab now also has a `Save v5` action. For standard v4 images it records the
reviewed selected-shape session plus the position's `machine_positions_um` into
`Standard position images\v4\v5_sequence_memory\v5_sequence_memory.json`.
Using `Save official` from the UI still writes the score baseline JSON, and it
also attempts to register that same reviewed session as the v5 official gross
baseline. The v5 save requires at least one `Use selected` shape so an empty
session cannot accidentally satisfy `next_action`.

For an actual repeated sequence, `python_vision_geometry/sequence_memory_workflow.py`
is the capture-memory layer in front of the simulator. Each capture record can
store the saved vision-lab session, the saved image path, and the queried
`machine_positions_um` from YASE. When present, those live machine positions
override the standard v4 JSON before solving, which is the bridge between the
standard image set and a current machine run.
The workflow can also be asked for `next_action`; it returns the next required
capture/baseline step with the target `machine_positions_um`, or `solve_ready`
when the recorded memory can produce `machine_coordinates_um`.

`SUB_vision_geometry` now contains YASE bridges for this layer:

- `SUB_V5SequenceMemoryInit_ReadOnly.xseq` initializes
  `python_env\log\v5_sequence_memory.json`;
- `SUB_V5SequenceMemoryNextAction_ReadOnly.xseq` asks Python for the next
  capture/baseline/solve action and writes
  `python_env\log\v5_sequence_next_action.json`;
- `SUB_V5CaptureReviewRecord_ReadOnly.xseq` grabs `CAM_12`, saves
  `python_vision_input.bmp`, queries live machine positions, opens the Tkinter
  vision lab, and records only the operator-approved selected shapes into
  `v5_sequence_memory.json`;
- `SUB_V5MacroAlignmentSolve_ReadOnly.xseq` solves the current memory and
  displays the returned `machine_coordinates_um`.
- `SUB_V5MacroAlignmentFinalWorkflow_Guarded.xseq` is the final operator
  wrapper: it queries live stage positions, asks Python for one
  `next_motion_or_capture` action, then either calls the guarded default move
  sequence, calls the capture/review UI bridge, or calls the read-only solve.

These files call TMPython, write request/result JSON under `python_env\log`,
and display returned JSON strings. Only the capture/review sequence grabs and
saves a camera frame. Only the final workflow wrapper can cause motion, and it
does that by delegating to `process\SUB_default_positioning\SUB_ApplyDefaultPositionMove`.

For the final motion workflow, Python now exposes `next_motion_or_capture`.
Given the current YASE-queried stage positions, it returns one flat action:
`move_to_next_capture` with `stage1`, `target1_um`, `distance1_um`,
`delta1_um`, and `confirm_text1`; `capture_review_record_required`; or
`solve_ready`. That is the intended short YASE loop boundary: YASE performs one
guarded move or picture step, and Python handles the decision logic and UI
recording. The checked-in final wrapper parses only the flat fields it needs:
`ok`, `schema_version`, `action`, `stage1`, `target1_um`, and
`confirm_text1`.

## Current known gaps

- The vision lab now exports selected-shape `feature_role` and
  `selection_index` metadata. Default roles are automatic, and the detected
  shape table can override roles for unusual captures. Older sessions still
  fall back to type/order.
- The default view transforms in `measurement_plan.json` are placeholders. The
  signs and scale must be verified with repeated machine captures before any
  motion-enabled sequence consumes v5 output.
- The gross-position bias transform is also a placeholder until reviewed. It is
  useful for read-only planning and YASE output, not for automatic motion.
- The side-view vertical coordinate currently has a trench-depth fallback, not
  a reviewed side-reference measurement. Keep it read-only until side-view
  reference selection is validated.
- The current v5 YASE files can initialize memory, query `next_action`, grab a
  live image, open the UI, record the reviewed session with queried machine
  coordinates, run the guarded final loop, and solve a macro payload. The final
  wrapper is statically XML/test verified in this repo; it still needs a careful
  machine-side checkout before it should be trusted as an operator procedure.
- The detector/fiber coordinate is not solved by this scaffold. It can be added
  to the same JSON contract when the matching chip/fiber reference measurement
  is reviewed.
- Python motion is still not approved. The motion-capable v5 wrapper keeps that
  boundary: Python proposes JSON, YASE validates through the existing guarded
  apply subsequence, and the operator confirms the move.
