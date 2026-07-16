# Data Collected From Checkout

Values extracted from the repository where full machine exports are unavailable. **Not machine-validated** — use for development and sim defaults only.

Source: `Processvar.ini`, `examples/yase_sim_config.json`, sequence inventory, `.set` panel files (binary).

## MainVelocity (development placeholders)

From `examples/yase_sim_config.json` until `systemvar.ini` is exported:

| Key | Placeholder (um/s or deg/s) |
|---|---|
| VelocityAlignXSlow | 50 |
| VelocityAlignSlow | 100 |
| VelocityAlignMedium | 500 |
| VelocityAlignFast | 1000 |
| VelocityAlignXFast | 2000 |
| VelocityAlignXXFast | 5000 |
| VelocityCameraXSlow | 50 |
| VelocityCameraSlow | 100 |
| VelocityCameraMedium | 500 |
| VelocityCameraFast | 1000 |
| VelocityCameraXFast | 2000 |
| VelocityRotSlow | 1 (deg/s assumed) |
| VelocityRotMedium | 5 |
| VelocityRotFast | 10 |
| VelocityZoom | 100 |

Rotary keys are treated as **deg/s** per `YASE_MACHINE_CONVENTIONS.md`.

## TIA initialization (`[MainInitEquipment]`)

| Parameter | TIA1 | TIA2 |
|---|---|---|
| Offset | 0 | 0 |
| Range | 5 | 5 |
| Wavelength (nm) | 1550 | 1500 |
| Polarity | Non-Inverting | Inverting |
| CalibrationFactor | 1000 (shared) | |

Overload check uses `SwitchLimitAnalog = 5.0` in TIA init subsequences.

## Vision scaling

| Key | Value | Section |
|---|---|---|
| HFA_Chip_YawAlignment | 2.2678 | `[Scaling]` |

## Vision routines (names only — scripts not in repo)

| Sequence | Routine | Axes corrected |
|---|---|---|
| SUB_Fix_BallLens_Correction | FixingPos1_12032026 | Align_X1, Align_Z1 |
| SUB_Pick_Top_Correction | VIS_TM20_PickTopHFA_Gripper_24112023 | Align_X2, Align_Z2 |
| SUB_Chip_Top_Correction | (chip top VA) | Align_Yaw2, Align_Z2, Align_X2 |
| SUB_Chip_MirrorFront_Correction | | Align_Roll2 |
| SUB_Chip_MirrorSide_Correction | | Align_Pitch2 |

Pixel-to-µm calibration must be exported from TestMaster / Vision Assistant.

## Alignment scan setups (`.set` files — inspect in TestMaster)

| File | Likely content |
|---|---|
| RollAlign.set | Roll alignment / line scan parameters |
| AxesSet.set | Axis scan configuration |
| MultiXIOPanel.set | Multi-axis IO panel settings |

Named setups referenced in sequences:

- `TIA1_RoughSpiralScan`
- `TIA1_FineSpiralScan`
- Metrology line scans on `Align_Yaw2`, `Align_Roll2`, `Align_Pitch2`

Export panel screenshots or serialized settings from TestMaster before reusing in production.

## Axis limits

Not available in checkout. Per-axis soft limits, safe positions, and `max_relative_step_um` remain `null` in `examples/machine_site_data.template.json` until `Hardware.ini` or controller export is added.

## Alignment TIA routing (development)

Matches `examples/yase_sim_config.json`:

```text
TIA_Lo  = TIA1
TIA_Tx  = TIA1
TIA_Rx  = TIA2
OpticSwitchLo = 0, OpticSwitchTx = 0, OpticSwitchRx = 1
```

Verify on machine before marking `verified: true` in site data.
