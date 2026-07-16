# Yase Two-Lens Auto-Alignment Function Reference

This is a working reference for programming a two-ball-lens auto-alignment process in this codebase. It is not a final machine recipe. The goal is to make the function names, parameters, variable namespaces, and hardware handoff points explicit before writing production alignment logic.

The final process will likely need to:

1. Hold two ball lenses with vacuum tweezers or grippers.
2. Find the laser, waveguide, lenses, or fiducials with camera/vision.
3. Convert measured offsets into safe stage moves.
4. Optimize optical power using TIA/power readings.
5. Stop safely on missing fiducials, bad vacuum, axis errors, overload, or low confidence vision.

## Important Yase Concepts

### Sequence

A `.xseq` file is a Yase sequence. It is a table of statements. Each row has a statement name, parameters, labels, and sometimes comments or folded visual blocks.

In code terms, a sequence is closest to a function or script.

Example:

```text
SUB_Positioning/SUB_Test_DrawCircle_AlignX1Z1.xseq
SUB_Alignment/SUB_MainAlignmentHFA.xseq
SUB_DataHandling/SUB_ReadFinalPower.xseq
```

### Subsequence

A subsequence is just a sequence that another sequence can call.

It is not automatically executed just because it exists in the project. It runs only when:

1. The user selects and runs it in Yase, or
2. Another sequence calls it using its prototype name, for example `SEQ::SUB_SysReadAveragePower`.

### Prototype

`prototypes.xml` is the signature list. It tells Yase:

- The statement/function name.
- Which library owns it.
- Which parameters exist.
- Which parameters are inputs and outputs.
- The value type of each parameter.
- Some enum/dropdown values.

In code terms, this is closest to a function declaration or type signature.

If the prototype does not match the row in a `.xseq` file, Yase can show parameter/signature errors.

### Library

A library is the implementation location for a statement.

Examples:

```text
Library="Stage"
Library="IO"
Library="VariableIO"
Library="PowerMeter"
Library="system\HELPER"
Library="product_modules\Functions\Alignment\AdvancedAlignment\AdvancedAlignment"
Library="customer_modules\Nanosystec\Functions\Imaging\VisionAssistantIntegrator\VB"
```

For many machine functions, the `.xseq` does not contain the implementation. The sequence hands the parameters to the library, and the library talks to the machine, IO, motion controller, camera, or product module.

### INI Section

Yase process/system variables are grouped into INI sections.

In code terms:

```text
file:    processvar.ini or systemvar.ini
section: group name inside that file
name:    key inside that section
value:   stored value
```

Example:

```text
System / MainVelocity / VelocityAlignXSlow -> d_Vel_Align_XSlow
Process / Alignment / TIA_Tx               -> TIA_Tx
Process / PosPickUp_BallLens... / Camera_X -> d_Pos_Camera_X
```

The section name is often built dynamically using `SetString`, for example:

```text
s_Pos_Section = "PosPickUp_BallLens" + s_Process_Name
```

That does not store a value by itself. It only builds the section name that later `GetNumVar` or `SetNumVar` will use.

## Most Important Rule

Yase statements look like ordinary function calls, but some are direct machine handoffs.

These functions can move axes, change IO, open/close grippers, change vacuum, trigger cameras, or run hidden product modules:

```text
MoveStage
SetDigOut
SetAnalogOut
Grab
AdvAlign_SpiralScan
MetrologyLineScan
SUB_SYS_DMS_Touchdown_Universal_Alignment
SUB_SYS_Gripper_OpenClose
```

For those statements, a syntactically valid row is not enough. The parameter values, axis names, signs, units, clearances, and current machine state must also be correct.

## Motion And Stage Functions

### StageCheckAllFiducialed

Library:

```text
Stage
```

Purpose:

Checks whether all required stages have been homed/referenced/fiducialed.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| Out | `Fiducialed?` | Boolean | `1` means OK, `0` means not referenced/fiducialed. |

Typical use:

```text
StageCheckAllFiducialed -> StatusStages
ifnum StatusStages = 0.0
BEGIN
DisplayStatus "Attention! Stages are not fiducialed!"
Goto L_Error_Fiducial
END
```

Why this matters:

If a stage is not referenced, an absolute position may be meaningless. For a precision alignment process, the sequence should abort before motion if this check fails.

Machine handoff:

Yes. The sequence asks the stage system for machine state.

### MoveStage

Library:

```text
Stage
```

Purpose:

Moves one stage axis.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `Stage` | String enum | `Align_X1`, `Align_Z1`, `Align_X2`, `Align_Z2`, `Camera_X`, `Camera_Y`, `Camera_Z`, `Zoom` | Which axis to move. |
| In | `Velocity [um/s]` | DBL | `d_Vel_Align_XSlow`, `d_Vel_Align_Slow` | Motion speed in um/s. |
| In | `Distance [um]` | DBL | `5.0`, `-1.464466`, calculated offset | Relative distance or absolute target, depending on mode. |
| In | `Sync` | Enum Word | `No sync`, `Sync` | Whether this move starts without waiting or synchronously. |
| In | `rel/abs` | Enum Word | `Relative`, `Absolute` | Whether `Distance` is a relative step or absolute target. |

Known stage names from the prototype:

```text
Align_Pitch1
Align_Pitch2
Align_Roll1
Align_Roll2
Align_X1
Align_X2
Align_Y1
Align_Y2
Align_Yaw1
Align_Yaw2
Align_Z1
Align_Z2
Camera_X
Camera_Y
Camera_Z
CursorX
CursorY
Zoom
```

Typical relative move:

```text
MoveStage Align_X1 d_Vel_Align_XSlow 5.0 No sync Relative
MoveStage Align_Z1 d_Vel_Align_XSlow 0.0 No sync Relative
SEQ::SUB_SYS_AxisWaitFinishList "Align_X1,Align_Z1"
SEQ::SUB_SysCheckAxisMove "Align_X1" "Align_Z1" "" "" "" "" -> Error, S_ErrorMessage
```

Machine handoff:

Yes. This is a direct motion command.

Safety notes:

- Confirm the axis name maps to the physical thing you intend to move.
- Confirm sign convention before using calculated offsets.
- Use small relative steps near the device.
- Wait and check axis errors after every move group.
- Do not use absolute moves unless the coordinate frame and fiducial state are known.

### QueryStage

Library:

```text
Stage
```

Purpose:

Reads a stage position.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `Stage` | String enum | `Align_X1`, `Align_Z1`, `Camera_X` | Which axis to query. |
| In | `Query` | Enum Word | `Absolute`, `User offset` | Which position frame to read. |
| Out | `Position [um]` | DBL | output variable | Current position. |
| Out | `Message` | String | output variable | Status/message from stage system. |

Typical use:

```text
QueryStage Align_X1 Absolute d_pos_Align_X1
QueryStage Align_Z1 Absolute d_pos_Align_Z1
```

Machine handoff:

Yes. It reads the motion controller/stage system.

### SEQ::SUB_SYS_AxisWaitFinishList

Library:

```text
system..
```

Purpose:

Waits until one or more axes have finished moving.

Parameters:

| Direction | Name | Type | Example | Meaning |
|---|---|---|---|---|
| In | `AxisList [CSV Format]` | String | `Align_X1,Align_Z1` | Comma-separated list of axes to wait for. |

Typical use:

```text
SEQ::SUB_SYS_AxisWaitFinishList "Align_X1,Align_Z1"
```

Machine handoff:

Yes. It waits on machine axis state.

### SEQ::SUB_SysCheckAxisMove

Library:

```text
system..
```

Purpose:

Checks whether a recent axis move produced an error.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Axis1` | String | First axis to check. |
| In | `Axis2` | String | Second axis to check. Empty if unused. |
| In | `Axis3` | String | Third axis to check. Empty if unused. |
| In | `Axis4` | String | Fourth axis to check. Empty if unused. |
| In | `Axis5` | String | Fifth axis to check. Empty if unused. |
| In | `Axis6` | String | Sixth axis to check. Empty if unused. |
| Out | `Error` | DBL | Axis error code. Usually `0` means no error. |
| Out | `S_ErrorMessage` | String | Human-readable error message. |

Typical use:

```text
SEQ::SUB_SysCheckAxisMove Align_X1 Align_Z1 "" "" "" "" -> ErrorType, ErrorMessage
```

Machine handoff:

Yes. It reads machine/controller error state.

## Optical Power And TIA Functions

### GetPower

Library:

```text
PowerMeter
```

Purpose:

Reads one power meter/TIA value.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `PowerMeter` | String enum | `TIA1`, `TIA2` | Which meter to read. |
| Out | `Power` | DBL | output variable | Current power value. |

Machine handoff:

Yes. It reads a physical meter.

### SEQ::SUB_SysReadAveragePower

Library:

```text
system..
```

Purpose:

Reads a power meter multiple times and returns averaged power values.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `PowerMeter` | String | `TIA1`, `TIA2`, or variable like `TIA_Tx` | Which meter/channel to read. |
| In | `Delay` | DBL | `10.0`, `50.0` | Delay between readings or read timing parameter, as used by existing code. |
| In | `Number of measurements` | DBL | `10.0`, `20.0` | Number of samples to average. |
| Out | `Average power [mW]` | DBL | output variable | Average power in mW. |
| Out | `Average power [dBm]` | DBL | output variable | Average power in dBm. |
| Out | `Average power [mA]` | DBL | output variable | Average current/power equivalent in mA. |

Typical use:

```text
SEQ::SUB_SysReadAveragePower TIA_Tx 50.0 20.0 -> PowerTx_Final
```

Machine handoff:

Yes. It reads meter hardware through a system helper.

### TIARange

Library:

```text
product_modules\Devices\Sources_Meter\TIA\TIA
```

Purpose:

Gets or sets the TIA gain/range.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `Meter` | String enum | `TIA1`, `TIA2` | Which TIA to configure/read. |
| In | `Function` | Enum Word | `Get`, `Set` | Read current range or set a new range. |
| In | `GainIn` | DBL | likely `0` to `6` | Requested gain/range for `Set`. |
| Out | `GainOut` | DBL | output variable | Current or resulting gain/range. |

Machine handoff:

Yes. This affects measurement range and overload behavior.

Device-specific data needed:

- Which TIA channel measures which optical path.
- Safe default gain/range.
- Overload threshold and recovery behavior.
- Whether the sequence should auto-range before alignment.

### SetDigOut For Optical Switch

Library:

```text
IO
```

Purpose:

Sets a digital output. Existing code uses this for optical switch state as well as gripper/vacuum outputs.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `Digital Line` | String | `OpticSwitch`, `Gripper2OpenClose`, `VacuumDevice` | Digital output name. |
| In | `State` | Enum Word | `Off`, `On` | Output state. |
| Out | `LastChangeTime` | DBL | output variable | Timestamp/change time from IO system. |

Known project pattern:

```text
SetDigOut OpticSwitch On   -> switch to Rx in SUB_ReadFinalPower.xseq
SetDigOut OpticSwitch Off  -> switch to Tx in SUB_ReadFinalPower.xseq
```

Machine handoff:

Yes. This changes physical IO.

Warning:

The prototype enum and the project examples do not always list exactly the same digital line strings. The actual accepted IO names are device/project-specific and must be verified in Yase on the real machine.

## IO, Vacuum, And Gripper Functions

### SetDigOut

Library:

```text
IO
```

Purpose:

Sets a digital output line.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Digital Line` | String | Output line name. |
| In | `State` | Enum Word | `Off` or `On`. |
| Out | `LastChangeTime` | DBL | Change timestamp/status value. |

Examples of output names found in the project/prototypes:

```text
Gripper1OpenClose
Gripper2OpenClose
GripperVacuumOpenClose
VacuumDevice
OpticSwitch
TrayVacuum_CH1
TrayVacuum_CH2
TrayVacuum_CH3
TrayVacuum_CH4
UV_On
```

Machine handoff:

Yes. This can physically actuate hardware.

Safety notes:

- Do not assume `On` means open, closed, vacuum enabled, or vacuum disabled without confirming the wiring/logic.
- For a two-lens process, every vacuum and gripper line needs a confirmed physical mapping.
- Prefer a system helper like `SUB_SYS_Gripper_OpenClose` if it already contains timing and error checks.

### GetDigOut

Library:

```text
IO
```

Purpose:

Reads the current state of a digital output.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Digital Line` | String | Output line name. |
| Out | `State` | U32 | Current output state. |
| Out | `LastChangeTime` | DBL | Last change timestamp/status. |

Typical use:

Read mode/status outputs such as `AdjustMode`, `TeachMode`, or actuator state before branching.

Machine handoff:

Yes. It reads IO state.

### GetDigIn

Library:

```text
IO
```

Purpose:

Reads a digital input, typically a sensor/interlock.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Digital Line` | String | Input line name. |
| Out | `State` | U32 | Current input state. |
| Out | `ReadOutTime` | DBL | Read timestamp/status. |

Useful input names from the prototype:

```text
Gripper1_Open
Gripper2_Open
Vacuum_OK
VaccumGripper_Open
Input_1
Input_2
Input_3
Input_4
TIA1
TIA2
```

Machine handoff:

Yes. It reads physical IO.

### GetAnalogIn

Library:

```text
IO
```

Purpose:

Reads an analog input.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `Analog Line` | String enum | `Force1`, `Force2`, `Gripper1_Pressure`, `Vacuum_Pressure`, `TIA1`, `TIA2` | Analog channel to read. |
| In | `LifeTime(ms)` | I32 | project-specific | Read/cache lifetime parameter. |
| Out | `Value` | DBL | output variable | Measured analog value. |
| Out | `ReadOutTime` | DBL | output variable | Read timestamp/status. |

Machine handoff:

Yes. It reads physical analog hardware.

### SetAnalogOut

Library:

```text
IO
```

Purpose:

Sets an analog output.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `Analog Line` | String enum | `Illu_1`, `Illu_2`, `Illu_Coax`, `Laser_Offset_X`, `Lens_Offset_X`, `cam_12_ExpTime` | Analog output channel. |
| In | `Value` | DBL | device-specific | Output value. |
| Out | `LastChangeTime` | DBL | output variable | Timestamp/status value. |

Machine handoff:

Yes. This changes physical analog outputs such as illumination, exposure, or offsets.

### SEQ::SUB_SYS_Gripper_OpenClose

Library:

```text
system..
```

Purpose:

Opens or closes a named gripper through a system helper.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `Gripper Name [Gripper1,Gripper2]` | String | `Gripper1`, `Gripper2` | Which gripper to control. |
| In | `Open/Close[open/close]` | String | `open`, `close` | Command. |
| Out | `ErrorType` | DBL | output variable | Error code. |
| Out | `ErrorMessage` | String | output variable | Error message. |
| Out | `SequenceName` | String | output variable | Called sequence name. |

Machine handoff:

Yes. This can move gripper hardware.

For the two-lens process:

Use this only after confirming whether the vacuum tweezers are controlled by these gripper helpers or by separate digital outputs.

## Vision And Camera Functions

### Grab

Library:

```text
AdvancedIMAQ
```

Purpose:

Captures an image from a camera.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `Camera` | String enum | `CAM_12` | Camera to acquire from. |
| Out | `Image Out` | String | image reference variable | Captured image reference. |

Machine handoff:

Yes. It triggers/reads camera hardware.

### Vision Assistant VB Modules

Library:

```text
customer_modules\Nanosystec\Functions\Imaging\VisionAssistantIntegrator\VB
```

Purpose:

Runs a predefined vision script/module on an image.

Examples found in the codebase:

```text
FixingPos1_12032026
VIS_TM20_PickTopHFA_Fiber_16022024
VIS_TM20_PickTopHFA_Gripper_24112023
```

Example: `FixingPos1_12032026`

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Image In` | String | Image reference from `Grab`. |
| Out | `Fitted Circle (Balllens) Reference` | U32 | Reference to ball-lens circle result. |
| Out | `Image Out` | String | Annotated/result image. |
| Out | `Caliper Results (laser_Mid_Right_edge) Reference` | U32 | Reference to laser edge result. |
| Out | `AllDocRefs` | String | Document references to free later. |

Machine handoff:

Partly. It runs external vision code whose internals are not visible in the `.xseq`.

Critical warning:

The exact output names are vision-script-specific. A spelling mismatch in statement name, control name, element name, or array element can silently give the wrong value or fail.

### VA_TM_GetValue

Library:

```text
customer_modules\Nanosystec\Functions\Imaging\VisionAssistantIntegrator\XML
```

Purpose:

Extracts one value from a vision result reference.

Parameters:

| Direction | Name | Type | Example | Meaning |
|---|---|---|---|---|
| In | `XMLReference` | U32 | result reference from vision module | Which vision result object to read. |
| In | `Statement Name` | String | `FixingPos1_12032026` | Which vision script produced the result. |
| In | `Control Name` | String | `Fitted Circle _Balllens_` | Which control/result group to read. |
| In | `Element Name` | String | `X.Center_Pixels_` | Which value to extract. |
| In | `Array Elements String` | String | `0,0` | Index into result array. |
| Out | `String Value` | String | output variable | Value as string. |
| Out | `Numeric Value` | DBL | output variable | Value as number. |

Known examples from ball-lens correction:

```text
Control: Fitted Circle _Balllens_
Element: X.Center_Pixels_
Index:   0,0

Control: Fitted Circle _Balllens_
Element: Y.Center_Pixels_
Index:   0,1

Control: Fitted Circle _Balllens_
Element: Radius_Pixels_
Index:   0,2

Control: Caliper Results _laser_Mid_Right_edge_
Element: outputstring.CaliperResults_laser_Mid_Right_edge_0
Index:   0,0 or 0,1
```

Machine handoff:

No direct motion, but it depends on external vision module output.

### VA_TM_FreeAllDocs

Library:

```text
customer_modules\Nanosystec\Functions\Imaging\VisionAssistantIntegrator\XML
```

Purpose:

Frees vision document references after reading results.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `AllDocRefs` | String | Document references returned by the vision module. |

Use this after finishing `VA_TM_GetValue` calls for one image.

### IMAQWind_ShowImage

Library:

```text
IMAQWind
```

Purpose:

Displays an image in a Yase image window.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Image In` | String | Image reference to display. |
| In | `Title` | String | Window title. |
| In | `Window Number (0...15)` | I32 | Display window number. |

Useful for debugging vision during development.

## Alignment And Scan Functions

### AdvAlign_SpiralScan

Library:

```text
product_modules\Functions\Alignment\AdvancedAlignment\AdvancedAlignment
```

Purpose:

Runs a predefined spiral scan alignment routine.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `Setup` | String enum | `TIA1_RoughSpiralScan`, `TIA1_FineSpiralScan` | Preconfigured scan setup. |
| In | `SpiralScanMode` | Enum Word | `SpiralRough`, `SpiralFine` | Coarse or fine scan mode. |
| In | `Threshold optional` | DBL | process-specific | Optional power/signal threshold. |
| In | `Display` | Enum Word | `hide`, `show` | Whether to show scan UI/results. |
| Out | `Max Int` | DBL | output variable | Maximum intensity/power found. |
| Out | `Max Pos1` | DBL | output variable | Best position coordinate 1. |
| Out | `Max Pos2` | DBL | output variable | Best position coordinate 2. |
| Out | `Threshld Found` | Boolean | output variable | Whether threshold was reached. |

Known setup names from prototype:

```text
SpiralInitial
TIA1_FineSpiralScan
TIA1_RoughSpiralScan
TIA1_X2Y2_20
TIA1_X2Y2_40
TIA1_X2Y2_60
TIA2_X2Y2_20
TIA2_X2Y2_40
TIA2_X2Y2_60
```

Machine handoff:

Yes. This external module can move stages and read meters according to its hidden setup.

Critical unknowns:

- Which axes each setup moves.
- Scan range and step size.
- Which power channel it reads.
- Whether it returns to start or stays at max.
- Whether it is safe for two-lens holding geometry.

### MetrologyLineScan

Library:

```text
product_modules\Functions\OpticalMetrology\Metrology
```

Purpose:

Runs a line scan over one stage axis while reading a meter/signal.

Important parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `Setup` | String enum | `Align_Pitch2`, `Align_Roll2`, `Align_Yaw2` | Predefined scan setup. |
| In | `ShowScan?` | Boolean | `0`, `1` | Whether to show scan. |
| In | `Timeout(ms)` | U32 | process-specific | Timeout. |
| In | `%` | DBL | process-specific | Percent/threshold setting. |
| In | `Meter` | String enum | `TIA1`, `TIA2` | Which meter to read. |
| In | `Stage` | String enum | `Align_Yaw2`, `Align_Roll2`, etc. | Which stage to scan. |
| In | `Step width (um)` | DBL | process-specific | Step size. |
| In | `Scan range (um)` | DBL | process-specific | Total scan range. |
| In | `Speed` | DBL | process-specific | Stage speed. |
| In | `Return to` | Enum Word | `Max`, `Min`, `Start` | Where to leave axis after scan. |
| In | `Initial move?` | Boolean | `0`, `1` | Whether to make an initial move. |
| In | `ScanDirection` | Boolean | process-specific | Scan direction flag. |
| In | `GaussFit` | Boolean | `0`, `1` | Whether to fit a Gaussian. |
| In | `Polynomial order` | I32 | process-specific | Fit order. |
| In | `Save?` | Boolean | `0`, `1` | Save scan data. |
| In | `FileName` | String | process-specific | Save file name. |

Machine handoff:

Yes. This can move stages and read optical power.

Critical unknowns:

- Full output signature should be checked before use.
- The selected setup may encode hidden axis/power behavior.
- The return mode must be chosen deliberately.

### SEQ::SUB_SYS_DMS_Touchdown_Universal_Alignment

Library:

```text
system\HELPER
```

Purpose:

Runs a force-sensor touchdown/alignment helper.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `Stage Name for TD [Any Stage]` | String | stage name | Axis used for touchdown. |
| In | `DMS Name for TD [Force1,Force2]` | String | `Force1`, `Force2` | Force sensor. |
| In | `Stepup distance after TD [um]` | DBL | process-specific | Retraction distance after touchdown. |
| Out | `ErrorType` | DBL | output variable | Error code. |
| Out | `ErrorMessage` | String | output variable | Error message. |
| Out | `SequenceName` | String | output variable | Called sequence name. |

Machine handoff:

Yes. This can intentionally move into contact. Treat as high-risk until all axis, force, and material limits are known.

## Variable And INI Functions

### GetNumVar

Library:

```text
VariableIO
```

Purpose:

Reads a numeric variable from `processvar.ini`, `systemvar.ini`, or a custom path.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `from` | Enum Word | `Process`, `System`, `Path` | Which INI source to read. |
| In | `Path` | String | usually blank unless `Path` mode | Custom INI path. |
| In | `Section` | String | `MainVelocity`, `Alignment`, `s_Pos_Section` | INI section name. |
| In | `Name` | String | `VelocityAlignXSlow`, `Camera_X` | Key name inside section. |
| Out | `VarValueOut` | DBL | output variable | Numeric value read. |

Example:

```text
GetNumVar System "" MainVelocity VelocityAlignXSlow -> d_Vel_Align_XSlow
GetNumVar Process "" s_Pos_Section Camera_X         -> d_Pos_Camera_X
```

This does not associate the output variable with the section permanently. It only copies the INI value into the local sequence variable at that moment.

### SetNumVar

Library:

```text
VariableIO
```

Purpose:

Writes a numeric variable to `processvar.ini`, `systemvar.ini`, or a custom path.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `valid for` | Enum Word | `Process`, `System`, or `Path`. |
| In | `Path` | String | Custom INI path if using `Path`. |
| In | `Section` | String | INI section name. |
| In | `Name` | String | Key name inside section. |
| In | `VarValueIn` | DBL | Numeric value to write. |

Example:

```text
SetNumVar Process "" s_Pos_Section Camera_X d_Pos_Camera_X
```

This is the row that stores the number back into the INI key.

### GetStringVar

Library:

```text
VariableIO
```

Purpose:

Reads a string from an INI variable.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `from` | Enum Word | `Process`, `System`, or `Path`. |
| In | `Path` | String | Custom path if needed. |
| In | `Section` | String | INI section. |
| In | `Name` | String | Key name. |
| Out | `VarStringOut` | String | String value read. |

Known examples:

```text
GetStringVar Process "" Alignment TIA_Lo -> TIA_Lo
GetStringVar Process "" Alignment TIA_Tx -> TIA_Tx
GetStringVar Process "" Alignment TIA_Rx -> TIA_Rx
```

### SetStringVar

Library:

```text
VariableIO
```

Purpose:

Writes a string to an INI variable.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `valid for` | Enum Word | `Process`, `System`, or `Path`. |
| In | `Path` | String | Custom path if needed. |
| In | `Section` | String | INI section. |
| In | `Name` | String | Key name. |
| In | `VarStringIn` | String | String value to write. |

### KeyAvailable

Library:

```text
VariableIO
```

Purpose:

Checks whether an INI key exists before reading it.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `from` | Enum Word | `Process`, `System`, or `Path`. |
| In | `Path` | String | Custom path if needed. |
| In | `Section` | String | INI section. |
| In | `Name` | String | Key name. |
| Out | `found?` | Boolean | Whether the key exists. |

Use this when missing variables could cause unsafe default behavior.

## Standard Logic And Calculation Functions

### SetString

Library:

```text
Standard
```

Purpose:

Combines or assigns strings.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `String 1` | String | First string. |
| In | `String 2` | String | Second string, often blank or suffix. |
| Out | `String out` | String | Result string. |

Examples:

```text
SetString SUB_SavePickUP_ballLens "" -> S_SequenceName
SetString PosPickUp_BallLens s_Process_Name -> s_Pos_Section
```

The second example builds a dynamic INI section name. It does not read or write the INI by itself.

### set

Library:

```text
Standard
```

Purpose:

Assigns a numeric value to a numeric output variable.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Value` | DBL | Number to assign. |
| Out | `Number out` | DBL | Output variable. |

### calc

Library:

```text
Standard
```

Purpose:

Performs a basic numeric calculation.

Parameters:

| Direction | Name | Type | Typical values | Meaning |
|---|---|---|---|---|
| In | `Number 1 in` | DBL | variable or number | Left operand. |
| In | `Operation` | Enum Word | `+`, `--`, `*`, `/` | Operation. `--` means subtraction. |
| In | `Number 2 in` | DBL | variable or number | Right operand. |
| Out | `Number out` | DBL | output variable | Result. |

Examples:

```text
calc X_Center_Pixels -- X_Target_Pixels -> X_Error_Pixels
calc X_Error_Pixels * PixelScale_um_per_px -> X_Error_um
```

### ifnum

Library:

```text
Standard
```

Purpose:

Numeric conditional branch.

Parameters:

| Direction | Name | Type | Values | Meaning |
|---|---|---|---|---|
| In | `Num1` | DBL | variable or number | Left side. |
| In | `Comp` | Enum Word | `<`, `<=`, `<>`, `=`, `>`, `>=` | Comparison. |
| In | `Num2` | DBL | variable or number | Right side. |

Yase control behavior:

If true, the next statement/block runs. If false, the next statement/block is skipped.

Common pattern:

```text
ifnum ErrorType <> 0.0
BEGIN
DisplayStatus ErrorMessage
Goto L_Error
END
```

### InRange

Library:

```text
Standard
```

Purpose:

Checks whether a number is between a min and max.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Value` | DBL | Value to check. |
| In | `Max` | DBL | Maximum allowed value. |
| In | `Min` | DBL | Minimum allowed value. |
| Out | `InRange` | Boolean | Whether the value is in range. |

Use this before motion to clamp or reject unsafe calculated moves.

### DisplayExtdSelectionDialog

Library:

```text
Dialog
```

Purpose:

Shows a dialog with two buttons.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Dialog text` | String | Message to show. |
| In | `LeftPos` | DBL | Dialog position. |
| In | `TopPos` | DBL | Dialog position. |
| In | `Window Title` | String | Dialog title. |
| In | `Button 1 (OK) text` | String | Text for OK button. |
| In | `Button 2 (Skip) text` | String | Text for skip button. |

Known control behavior:

Button 1 executes the next row. Button 2 skips the next row.

Abort/Move pattern:

```text
DisplayExtdSelectionDialog "Move 5 um test path?" 0.0 0.0 S_SequenceName Abort Move
Goto L_End
```

If the user presses `Abort`, the `Goto L_End` row runs. If the user presses `Move`, the `Goto L_End` row is skipped and motion continues.

### Delay

Library:

```text
XSEQFlowControl
```

Purpose:

Waits for a fixed time.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Wait time [ms]` | U32 | Delay in milliseconds. |

Use sparingly. Prefer explicit wait/check functions for axes and IO where possible.

## Sequence Input And Output Parameters

### DeclareNumParam

Purpose:

Declares a numeric input parameter for a subsequence.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Name` | String | Parameter name. |
| In | `Default value` | DBL | Default if caller does not pass a value. |
| Out | `Value` | DBL | Local variable receiving the parameter value. |

### DeclareStrParam

Purpose:

Declares a string input parameter for a subsequence.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Name` | String | Parameter name. |
| In | `Default value` | String | Default if caller does not pass a value. |
| Out | `Value` | String | Local variable receiving the parameter value. |

### ReturnNumParam

Purpose:

Returns a numeric output parameter to the caller.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Name` | String | Output parameter name. |
| In | `Value` | DBL | Value to return. |

### ReturnStrParam

Purpose:

Returns a string output parameter to the caller.

Parameters:

| Direction | Name | Type | Meaning |
|---|---|---|---|
| In | `Name` | String | Output parameter name. |
| In | `Value` | String | Value to return. |

Standard output names used by many project subsequences:

```text
ErrorType
ErrorMessage
SequenceName
```

## How A Two-Lens Auto-Alignment Sequence Could Be Structured

This is a likely structure, not final code.

### 1. Entry And Safety

Likely functions:

```text
SetString
DisplayStatus
StageCheckAllFiducialed
GetDigIn
GetAnalogIn
GetNumVar
GetStringVar
DisplayExtdSelectionDialog
```

Purpose:

- Set `S_SequenceName`.
- Check all stages are referenced.
- Check vacuum/gripper state.
- Read velocities.
- Read TIA/channel names.
- Confirm operator wants to start.

### 2. Locate Lens/Waveguide/Laser By Vision

Likely functions:

```text
SetAnalogOut
Grab
FixingPos1_12032026 or another vision module
VA_TM_GetValue
IMAQWind_ShowImage
VA_TM_FreeAllDocs
```

Purpose:

- Set illumination and camera exposure.
- Acquire image.
- Run the correct vision script.
- Extract ball-lens center/radius, waveguide edge, laser edge, or fiducial positions.
- Convert pixel offsets into stage offsets.

### 3. Convert Measurements To Motion

Likely functions:

```text
calc
set
InRange
ifnum
DisplayStatus
```

Purpose:

- Calculate pixel error.
- Apply pixel-to-um scale.
- Apply sign and axis mapping.
- Clamp to max step size.
- Reject values outside safe range.

### 4. Execute Small Safe Moves

Likely functions:

```text
MoveStage
SEQ::SUB_SYS_AxisWaitFinishList
SEQ::SUB_SysCheckAxisMove
QueryStage
```

Purpose:

- Move only the intended axes.
- Wait for completion.
- Check axis errors.
- Optionally query final positions.

### 5. Coarse Optical Search

Likely functions:

```text
TIARange
SetDigOut
SEQ::SUB_SysReadAveragePower
AdvAlign_SpiralScan
MetrologyLineScan
```

Purpose:

- Set/verify TIA range.
- Select the correct optical path if using an optical switch.
- Run a controlled coarse scan or line scan.
- Find a first power maximum.

### 6. Fine Closed-Loop Optimization

Likely functions:

```text
SEQ::SUB_SysReadAveragePower
MoveStage
SEQ::SUB_SYS_AxisWaitFinishList
SEQ::SUB_SysCheckAxisMove
calc
ifnum
InRange
```

Purpose:

- Measure current power.
- Try small relative perturbations.
- Keep moves that improve the objective.
- Stop when improvement is below threshold or max iterations is reached.

### 7. Store Results

Likely functions:

```text
QueryStage
SetNumVar
SetStringVar
ReturnNumParam
ReturnStrParam
```

Purpose:

- Store final positions.
- Store final power values.
- Return error/status outputs to caller.

## Device-Specific Information Still Needed

Do not write final autonomous two-lens motion until these are known.

### Axis Mapping

Needed:

- Which physical axis controls lens 1 X/Y/Z?
- Which physical axis controls lens 2 X/Y/Z?
- Which axes are pitch, yaw, and roll for each lens?
- Which axis is optical propagation direction?
- Which axes are transverse to the waveguide/laser?
- Which stage signs move the lens up/down, left/right, toward/away?

Risk if unknown:

The code can move the correct named axis in the wrong physical direction.

### Vacuum And Gripper Mapping

Needed:

- Which output controls vacuum tweezer 1?
- Which output controls vacuum tweezer 2?
- Which sensor confirms lens 1 is held?
- Which sensor confirms lens 2 is held?
- Does `On` mean vacuum enabled or valve opened to atmosphere?
- What pressure threshold means lens present?
- What is the safe failure action if vacuum is lost?

Risk if unknown:

The sequence can drop or crash a lens while still being syntactically correct.

### Safe Positions And Clearances

Needed:

- Safe open position before starting alignment.
- Maximum relative step near waveguide.
- Maximum absolute travel limits for each involved axis.
- Safe Z height above chip/waveguide.
- Contact/no-contact rules.
- Whether each subsequence should return to a safe pose or preserve current pose for the next step.

Good practice:

Do not blindly move to a generic safe space at the start of every subsequence. Instead, each subsequence should declare its required entry state and exit state:

```text
Entry: lens held, above chip, stages fiducialed, vacuum OK
Exit on success: lens still held, aligned position preserved
Exit on error: motion stopped, vacuum unchanged, operator notified
```

### Camera Calibration

Needed:

- Pixel-to-um scale for each camera/view/zoom.
- Image coordinate sign convention.
- Rotation between camera X/Y and stage axes.
- Which camera view sees lens 1, lens 2, laser, and waveguide.
- Vision script names for the actual two-lens process.
- Exact `VA_TM_GetValue` control names, element names, and array indexes.

Risk if unknown:

The vision result can be real but converted into the wrong stage correction.

### Optical Signal Mapping

Needed:

- Which TIA reads laser input, Tx, Rx, Lo, or final transmitted power.
- Which `OpticSwitch` state selects which path.
- Whether the process should optimize one signal or multiple signals.
- What target power, threshold, and acceptable balance are.
- TIA gain/range strategy and overload behavior.

Risk if unknown:

The optimizer can maximize the wrong channel or drive into a saturated measurement.

### Existing Advanced Alignment Setups

Needed:

- What `TIA1_RoughSpiralScan`, `TIA1_FineSpiralScan`, and related setups actually move.
- Scan ranges and step sizes.
- Whether they return to max, start, or some configured position.
- Whether they are valid for two lenses held by tweezers.

Risk if unknown:

Calling `AdvAlign_SpiralScan` can run hidden motion that conflicts with the desired lens-holding geometry.

## Recommended Production Coding Rules

1. Always check `StageCheckAllFiducialed` before stage motion.
2. Always check vacuum/gripper sensors before moving held lenses.
3. Use `GetNumVar`/`GetStringVar` for configured values instead of hardcoding where possible.
4. Use small relative moves for search and correction.
5. After every move group, call `SUB_SYS_AxisWaitFinishList`.
6. After every move group, call `SUB_SysCheckAxisMove`.
7. Clamp calculated moves with `InRange` or explicit numeric checks.
8. Never assume a vision string or IO line name. Verify it in Yase descriptions/prototypes and with a no-motion test.
9. Store final positions and powers explicitly with `SetNumVar`.
10. Return `ErrorType`, `ErrorMessage`, and `SequenceName` from every reusable subsequence.

## Files To Inspect When Writing The Real Alignment Code

Useful examples already in this repository:

```text
SUB_Positioning/SUB_SavePickUP_ballLens.xseq
SUB_Positioning/SUB_Test_DrawCircle_AlignX1Z1.xseq
SUB_DataHandling/SUB_ReadFinalPower.xseq
SUB_Alignment/SUB_MainAlignmentHFA.xseq
SUB_MachineVision/SUB_Fix_BallLens_Correction.xseq
SUB_MachineVision/SUB_Pick_Top_Correction.xseq
SUB_Positioning/SUB_MoveFiberByOffset_FA.xseq
SUB_DispenseUV/SUB_ReleaseGripperAfterUV.xseq
prototypes.xml
```

Use `prototypes.xml` as the source of truth for parameter order and value type, then use existing `.xseq` files as examples of working parameter values.

