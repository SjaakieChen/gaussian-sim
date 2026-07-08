# Migration YASE Files

This folder contains YASE-side files created for the Python migration.

## Files

| File | Purpose |
| --- | --- |
| `SUB_TMPython_MovementCommand_ReadOnly.xseq` | Read-only YASE template that calls the Python movement checkout class and displays the returned JSON. It does not move hardware. |
| `SUB_TMPython_JsonInOut_StoreExample.xseq` | Read-only YASE template that builds input JSON, calls Python, stores input/output JSON in process variables, and writes optional JSON files under `#SM_PROCESS#\Python\log`. |

## Important

The checked-in machine export did not contain any existing `.xseq` file that
calls `TMPython_ExecuteScript`. This template is therefore a starting point.

Before running it on the machine:

1. Confirm `TMPython_ExecuteScript` exists after importing TestMaster
   prototypes.
2. Open this file in YASE.
3. Replace the placeholder TMPython parameter names if the installed prototype
   uses different labels.
4. Run read-only first and verify the returned JSON.
5. Add motion only after the read-only call works.

Expected Python call:

```text
Interpreter = Python_310_MICROCOMBSYS_INTERPRETER
Module      = testmaster_alignment.movement_command_test_step
Class       = MovementCommandTestStep
```

## JSON Storage Example

`SUB_TMPython_JsonInOut_StoreExample.xseq` shows the recommended first storage
pattern:

```text
s_PythonInputJson   -> JSON sent into Python
s_PythonResultJson  -> JSON returned by Python
```

It stores both strings into `processvar.ini`:

```text
[PythonMigration]
LastInputJson  = ...
LastResultJson = ...
```

It also writes optional inspection files:

```text
#SM_PROCESS#\Python\log\tmpython_last_input.json
#SM_PROCESS#\Python\log\tmpython_last_result.json
```

Create `#SM_PROCESS#\Python\log` before running that file-write path.
