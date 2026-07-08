# TestMaster Python Migration Bundle

This folder groups the files needed to migrate the Python alignment work onto
the TestMaster/YASE machine.

## Folder Layout

```text
migration\
  TMPython.ini
  yase_files\
    SUB_TMPython_MovementCommand_ReadOnly.xseq
    SUB_TMPython_JsonInOut_StoreExample.xseq
    README.md
  migration_files\
    testmaster_alignment\
    testmaster_vision\
    output_examples\
    docs\
    requirements.txt
    CALL_GUIDE.md
```

Use `yase_files` for YASE sequences or YASE-side templates we create.

Use `migration_files` for the Python/runtime files that get copied to the
machine Python working directory.

## Recommended Machine Copy

Copy the Python packages from:

```text
migration\migration_files\testmaster_alignment\
migration\migration_files\testmaster_vision\
```

to the active process-owned Python folder on the machine:

```text
D:\TestMasterData\<active-process>\Python\
```

For the self-contained process layout, create the venv and logs in that same
folder:

```text
D:\TestMasterData\<active-process>\Python\.venv\
D:\TestMasterData\<active-process>\Python\log\
```

Then configure a process-specific TMPython interpreter section such as
`Python_310_ALIGNMENT_TEST`.

On a shared machine, do not make every user edit the same `TMPython.ini`
section. Keep one central `TMPython.ini`, but add one named section per
process/test. Each YASE file then selects the right section with its
`Interpreter` field.

A loose editable template is included at:

```text
migration\TMPython.ini
```

Copy it to `#SM_CONFIG#\TMPython.ini` on the machine and replace the placeholder
process paths before running YASE.

The detailed call guide is:

```text
migration\migration_files\CALL_GUIDE.md
```
