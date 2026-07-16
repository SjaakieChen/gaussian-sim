# Machine Export Placeholders

The repository checkout does not include a full live TestMaster export. Copy machine files here after export, then re-run validation.

## Required exports

| File | Status in checkout | Export from |
|---|---|---|
| `systemvar.ini` | **Missing** | `#SM_SYSTEM#` on TestMaster machine |
| `Hardware.ini` | **Missing** | `#SM_CONFIG#` or system config folder |
| `IOPointDB.txt` | **Partial** — only 2 camera exposure lines in repo root | Full IO database from TestMaster IO configuration |
| `Processvar.ini` | Present at repo root | Re-export after machine recipe changes |
| `Process.ini` | Present but may be incomplete | Re-export from process folder |

## Export procedure

1. On the TestMaster PC, locate `#SM_SYSTEM#`, `#SM_CONFIG#`, and `#SM_PROCESS#`.
2. Copy `systemvar.ini` and `Hardware.ini` into this folder.
3. Export the full `IOPointDB.txt` (all digital/analog lines, not only camera exposure).
4. Update `manifest.json` in this folder with export date and operator name.
5. Merge `[MainVelocity]` from `systemvar.ini` into `examples/machine_site_data.template.json` → `main_velocity`.
6. Run validation:

```powershell
python tools\validate_machine_site_data.py --inventory YASE_MACHINE_INTERFACE_INVENTORY.json --site examples\machine_site_data.template.json
```

## Partial IOPointDB in checkout

The repo root [`IOPointDB.txt`](../IOPointDB.txt) contains only:

- `cam_12_ExposureTime`
- `camera_swir_ExposureTime`

Production sequences reference many more lines (`Vacuum_OK`, `Gripper2OpenClose`, `TIA1`, etc.). The full database is required before IO polarity and sensor mapping can be marked `verified: true`.

## Conventions already recorded

User-confirmed numbering and coordinate conventions are in [`YASE_MACHINE_CONVENTIONS.md`](../YASE_MACHINE_CONVENTIONS.md). Machine exports still needed for velocities, limits, IO polarity, and approval gate.
