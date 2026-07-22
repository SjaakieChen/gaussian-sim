# Migration v5 Vision Geometry

Migration v5 is the read-only geometry bridge for turning reviewed vision-lab
measurements into one machine-coordinate frame for:

- the laser middle/reference;
- ball lens 1;
- ball lens 2.

No v5 file in this scaffold issues hardware motion. YASE remains responsible
for moving to default positions, acquiring `CAM_12`, saving the same image
reference with `IMAQWriteFile`, and opening the Python vision recognition lab.
Python receives saved image/session JSON and returns geometry JSON.

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

The gross `2.1` and `4.1` views are not final coordinate evidence. They are
used to keep the next close-focus moves from blindly assuming that pickup
landed exactly like the standard captures. `position_bias_planner.py` computes
a bounded tower-only bias from the selected ball-center pixel shift. It leaves
the reviewed camera, zoom, exposure, and focus settings unchanged.

For offline standard-image reconstruction, `python_vision_geometry/macro_alignment_simulator.py`
runs the whole read-only v5 chain from the standard images: gross ball
detection, close-position bias planning, focused rectangle/ball memory, side
diagnostics, and final `machine_coordinates_um`.
It is the current single-command simulator for testing the vision script against
the saved v4 picture set.

For an actual repeated sequence, `python_vision_geometry/sequence_memory_workflow.py`
is the capture-memory layer in front of the simulator. Each capture record can
store the saved vision-lab session, the saved image path, and the queried
`machine_positions_um` from YASE. When present, those live machine positions
override the standard v4 JSON before solving, which is the bridge between the
standard image set and a current machine run.

`SUB_vision_geometry/SUB_V5MacroAlignmentSolve_ReadOnly.xseq` is the first
read-only YASE bridge for this layer. It calls TMPython, writes request/result
JSON under `python_env\log`, and displays the returned
`machine_coordinates_um`. It does not grab images, record live sessions, parse
coordinates into stages, or move hardware.

## Current known gaps

- The vision lab still needs semantic selected-shape roles such as
  `machine_reference`, `ball_1`, `ball_2`, `chip_reference`, and `side_reference`.
  The v5 sequence memory now supplies capture-level roles, but the selected
  shapes inside each vision-lab session are still inferred from type/order.
- The default view transforms in `measurement_plan.json` are placeholders. The
  signs and scale must be verified with repeated machine captures before any
  motion-enabled sequence consumes v5 output.
- The gross-position bias transform is also a placeholder until reviewed. It is
  useful for read-only planning and YASE output, not for automatic motion.
- The side-view vertical coordinate currently has a trench-depth fallback, not
  a reviewed side-reference measurement. Keep it read-only until side-view
  reference selection is validated.
- Live YASE capture recording into sequence memory is not implemented yet. The
  current v5 YASE file only solves a read-only macro payload.
- The detector/fiber coordinate is not solved by this scaffold. It can be added
  to the same JSON contract when the matching chip/fiber reference measurement
  is reviewed.
- Python motion is still not approved. A later motion-capable v5 must reuse the
  existing read-only/parse/apply pattern: Python proposes JSON, YASE validates
  stage allowlists/bounds/deltas/interlocks, and the operator confirms the move.
