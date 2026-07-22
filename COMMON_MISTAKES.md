# Common YASE, TMPython, JSON, and Image-Bridge Mistakes

Last updated: 2026-07-17

Record every confirmed integration mistake here when it is discovered. Include
the visible error, actual cause, correction, and a prevention check. Machine
paths and full bridge details remain in `MACHINE_CONFIGURATION.md`.

## 1. Goto and destination labels must match exactly

Observed error:

```text
50002: Parse error in sequence
<Goto> Label <L_Error_Fiducial> not found
```

Cause: generated default-position sequences used `Goto` targets such as
`L_Error_Fiducial` while declaring the destination statement label as
`@L_Error_Fiducial`. YASE treats those as different names. The sequence can
fail before any query, popup, or hardware motion.

Correction for new files: every label that can be reached by `Goto` is written
as the exact plain `L_...` target. Do not add a leading `@` to a destination
label unless the `Goto` target also includes it.

Prevention:

- Compare every constant `Goto` target with the complete set of statement
  labels.
- Matching is exact, including a leading `@`, spelling, spaces, and case.
- Audit generated `.xseq` files before loading them in TestMaster.
- A successful XML parse does not authorize or prove a safe machine move.

## 2. Saved files, open editor buffers, and runtime copies can differ

YASE/TestMaster may execute `$$tmp$$.xseq` and may hold a `.lock` file. An open
editor can retain old values after the source file was changed externally.

Prevention:

1. Stop the sequence.
2. Save and close the editor that owns the file.
3. Reopen the source `.xseq` from disk.
4. Verify the changed fields in YASE.
5. Rebuild only after the lock owner has released the file.

Never delete a live lock file. The owning editor can overwrite the repair with
its older in-memory copy.

## 3. The TMPython interpreter name is exact

Use `Python_310_PYTHON_AUTOMATION_INTERPRETER`. Do not use the historical
`Python_37_PYTHON_AUTOMATION_INTERPRETER` or `Python_310_ALIGNMENT_TEST`.
Error `5001` means the YASE value does not match a section in global
`D:\TestMasterData\config\TMPython.ini`.

## 4. ParamIn must contain JSON and ParamOut must be a variable

This installation uses `ParamIn` and `ParamOut`. An empty `ParamIn` produces
`10500: Expecting value: line 1 column 1 (char 0)`. Build valid, nonempty JSON
in a YASE string variable and connect a second string variable to `ParamOut`.

## 5. Use forward slashes for paths inside JSON

Single Windows backslashes can produce `10500: Invalid \escape`. Use:

```json
{"image_path":"D:/TestMasterData/data/Python_Automation/image.bmp"}
```

Inside an XSEQ XML attribute, encode JSON quotation marks as `&quot;`.

## 6. The Python module name follows the working directory

Modules directly in `python_env` use the bare filename without `.py`, for
example `vision_recognition_lab`. Do not invent a package prefix unless that
importable package actually exists below the configured working directory.

## 7. JSON writes and IMAQ image writes use different path rules

Ordinary `WriteToFile` accepts a verified absolute path under `python_env\log`,
but its parent directory must already exist.

The installed `IMAQWriteFile` wrapper works with a relative filename such as
`python_vision_input.bmp`; TestMaster resolves it below
`D:\TestMasterData\data\Python_Automation`. An absolute `python_env\log`
destination produced error `50003`.

Do not use `SaveImageToSpreadsheetFile` for the Python bridge. It treated the
`.tsv` path as a directory and attempted to create an `.imgcorr` sidecar.

## 8. An IMAQ image reference is not a filename

`r_Image_Ref` is an opaque NI/LabVIEW in-memory reference. Save the existing
reference through `IMAQWriteFile`, then pass the resolved image path to Python.

## 9. Static validation must not execute a motion sequence

For movement XSEQ changes, validate XML, labels, `Goto` targets, parameters,
and referenced files without running `MoveStage` or `SetAnalogOut`. A real run
requires fiducial checks, allowlists, bounds, operator confirmation, controller
state, and all normal machine interlocks.

## 10. Movement duration must fit the system wait timeout

Observed symptom: a system error/abort popup appeared while `Camera_X` was
still visibly moving during default position `1.0.0`.

Cause confirmed on 2026-07-17:

- `SUB_ApplyDefaultPositionMove.xseq` selected `VelocityCameraXSlow` for
  `Camera_X` and `Camera_Z`.
- Global `[MainVelocity]` defines `VelocityCameraXSlow = 500 um/s`.
- `D:\TestMasterData\System\SUB_SYS_AxisWaitFinish.xseq` has a hard-coded
  movement timeout of `45000 ms`.
- At `500 um/s`, only about `22500 um` can be travelled in 45 seconds, before
  allowing for acceleration.
- Default position `1.0.0` targets `Camera_X = -74997 um`; a delta greater
  than `22500 um` therefore times out even though the controller is still
  executing a valid move.

The timeout enters `SUB_SYS_ErrorHandler`. Aborting the YASE sequence did not
stop the already-issued stage command on this machine, so the stage continued
moving while the popup was visible. Never interpret a sequence-abort popup as
proof that physical motion has stopped.

The working Microcombsys absolute camera-position sequences use
`VelocityCameraXFast = 20000 um/s` for `Camera_X`/`Camera_Z` and
`VelocityCameraFast = 10000 um/s` for `Camera_Y`.

Prevention:

- Compute `abs(delta_um) / velocity_um_per_s` before issuing a move.
- Require predicted movement time plus margin to remain below the active wait
  timeout, or use a reviewed timeout appropriate for the planned move.
- Use the machine-approved fast/slow velocity for the operation rather than
  selecting slow velocity unconditionally.
- Keep one reviewed completion mechanism. Do not stack
  `SUB_SYS_AxisWaitFinishList` and `SUB_SysCheckAxisMove` without a documented
  reason and compatible timeout behavior.
- A sequence abort must invoke an explicit, verified controller stop if the
  safety requirement is to halt physical motion.

## 11. Do not use fast speed for close-to-chip correction moves

Migration v4 proved the direct hardcoded-position XML shape, but it used fast
stage velocities. Fast approach can be appropriate for reviewed long camera
moves, but it is too aggressive once the camera, towers, chip, ball, trench, or
mirror are already close.

Prevention:

- Use medium speed for reviewed approach moves between nearby standard vision
  positions.
- Use slow speed for image-derived offset corrections.
- Never use `VelocityAlignFast`, `VelocityCameraFast`, or
  `VelocityCameraXFast` in a close-to-chip correction subsequence.
- Still check movement duration against the active wait timeout. Slow speed can
  time out on long moves, so do not blindly replace every approach move with
  slow speed.

## 12. Do not rebase a multi-step transition after every one-axis move

Observed during migration v6 audit:

- A transition helper can be called repeatedly by YASE, applying one returned
  move per loop.
- If Python recalculates `target = current + standard_delta` on every loop,
  the target drifts after the first axis moves. The same standard delta can be
  applied repeatedly even though the final coordinate looked correct on the
  first call.
- If tower X/Z motion is requested while the tower is still low, the tower can
  move laterally near the trench before reaching the final safe coordinate.

Prevention:

- Anchor the transition target once at the start of the transition and store
  it in sequence memory until the transition completes.
- Before tower X/Z motion, raise the active tower Y to a reviewed clearance
  coordinate; only lower Y after lateral axes are at target.
- Treat direct hardcoded-position subsequences the same way, because operators
  can run them independently from an unknown current position.
