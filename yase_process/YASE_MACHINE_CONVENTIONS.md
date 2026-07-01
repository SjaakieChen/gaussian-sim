# YASE Machine Conventions

User-confirmed conventions for programming, simulation, and site-data collection. This file is the single reference for numbering and coordinate frames. IO polarity and machine-export values still require on-machine verification.

## Linear Axes

| Machine axis | Physical meaning | Simulation axis |
|---|---|---|
| X | Transverse | `x` |
| Z | Transverse (orthogonal to X in the stage plane) | `y` |
| Y | Optical / laser propagation direction | `z` |

Mapping used by `yase_sim`:

```text
machine X -> simulation x
machine Z -> simulation y
machine Y -> simulation z
```

Linear axis signs are confirmed correct. No sign flips are applied in the simulator.

Holder stages:

| Holder | Lens side | Stage prefix |
|---|---|---|
| 1 | Left (left-most ball lens) | `Align_*1` → actor `Align1` |
| 2 | Right (right-most ball lens) | `Align_*2` → actor `Align2` |

## Rotation Frame (Yaw, Pitch, Roll)

Body frame for both holders:

- **+Z is the nose** — forward direction along the optical / laser axis.
- In machine coordinates, nose = **+Y** (same as simulation **+z**).
- Transverse body **+X** = machine **+X** (simulation **+x**).
- Transverse body **+Y** = machine **+Z** (simulation **+y**).

Positive rotations follow the right-hand rule about each body axis.

| Yase axis | Body rotation | Rotation axis | Typical vision / alignment use |
|---|---|---|---|
| `Align_Rolln` | Roll | Body **+Z** (nose / optical) | Mirror-front view (`Align_Roll2` in chip mirror correction) |
| `Align_Pitchn` | Pitch | Body **+X** | Mirror-side view (`Align_Pitch2`) |
| `Align_Yawn` | Yaw | Body **+Y** | Chip top view (`Align_Yaw2`); scaled by `HFA_Chip_YawAlignment` in `[Scaling]` |

**Units:** degrees. Rotary velocity keys (`VelocityRotSlow`, `VelocityRotMedium`, `VelocityRotFast`, `VelocityRotXFast`) are treated as **deg/s** until `systemvar.ini` from the machine proves otherwise.

The simulator stores rotary positions but does not yet apply them to the optical power model.

## Numbering: 1 = Left, 2 = Right

Applies to lenses, grippers, vacuum channels, force sensors, and any numbered add-on.

| Item | Holder 1 (left) | Holder 2 (right) |
|---|---|---|
| Stage actor | `Align1` | `Align2` |
| Linear axes | `Align_X1`, `Align_Y1`, `Align_Z1` | `Align_X2`, `Align_Y2`, `Align_Z2` |
| Rotary axes | `Align_Roll1`, `Align_Pitch1`, `Align_Yaw1` | `Align_Roll2`, `Align_Pitch2`, `Align_Yaw2` |
| Gripper helper (`SUB_SYS_Gripper_OpenClose`) | `Gripper1` | `Gripper2` |
| Vacuum helper (`SUB_SYS_Vacuum_OnOff`) | `Gripper` | `CH2` |
| Gripper pressure (analog) | `Gripper1_Pressure` | `Gripper2_Pressure` |
| Gripper open sensor (digital) | `Gripper1_Open` | `Gripper2_Open` |
| DMS force (analog) | `Force1` on `Align_Y1` | `Force2` on `Align_Y2` |
| Sim config gripper / vacuum keys | `Gripper1` / `Vacuum1` | `Gripper2` / `Vacuum2` |

### Auxiliary channels (not holder 1 or 2)

| Name | Role |
|---|---|
| `CH3` | Auxiliary vacuum channel (used in `SUB_MainPickupLA` with `off`); not mapped to holder 1 or 2 |
| `CH1` | Tray vacuum channel per prototype enum; verify on machine before use |
| `VacuumDevice` | Master vacuum enable digital output (`SUB_MainDeviceHandling`) |
| `Gripper2OpenClose` | Direct digital output for gripper 2 mechanism; polarity not yet verified |

### Legacy sequence naming

Some checked-in sequences call `Gripper2` or `CH3` in contexts that target the left lens workflow (`SUB_MainPickupLA`). Treat those as **sequence bugs or legacy aliases** until proven on the machine. New two-lens code must use the table above.

## TIA (Power Received)

- A **TIA reading is optical power received** at the detector (mW after calibration).
- Used as the alignment objective (`SUB_SysReadAveragePower`, `AdvAlign_SpiralScan`) and for final acceptance (`SUB_ReadFinalPower`).
- Init parameters in `[MainInitEquipment]` (`Processvar.ini`): offset, range, wavelength, polarity per meter.
- Channel routing for Lo / Tx / Rx is manual on the machine and stored in `[Alignment]` (`TIA_Lo`, `TIA_Tx`, `TIA_Rx`, `OpticSwitch*`).

Development simulator defaults (override in `examples/yase_sim_config.json`):

```text
TIA_Lo  -> TIA1
TIA_Tx  -> TIA1
TIA_Rx  -> TIA2
```

## Still Required From Machine

These conventions do not replace machine exports. See `YASE_MACHINE_DATA_COLLECTION_CHECKLIST.md` and `examples/machine_exports/README.md`.

- IO polarity (`On` = vacuum on vs vent, gripper open vs close)
- Pressure thresholds for held / missing / leak
- Real `[Alignment]` and `[MainVelocity]` from `systemvar.ini`
- Full `IOPointDB.txt`, `Hardware.ini`
- Vision pixel-to-µm calibration and `.set` scan parameters
- Collision limits and abort behavior
