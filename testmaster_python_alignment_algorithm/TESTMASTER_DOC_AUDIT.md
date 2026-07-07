# TestMaster/Yase Documentation Audit

This audit records the manual points used to shape the Python package and the
recommended `SUB_PY_*` subprocess structure.

Sources checked:

- `machine_documentation/Yase_TM_HB_Sep_2018.pdf`
- `machine_documentation/TestMaster Documentation 2020.1.10 (1).pdf`

## Findings Applied

1. Yase sequences are built from predefined statements and are the correct
   place to orchestrate machine actions. The Python package therefore returns
   requested moves only; it does not call hardware APIs.
   Source: Yase manual p.6.

2. Yase editing depends on imported prototypes. After adding TMPython or new
   `SUB_PY_*` subsequences, prototypes must be imported again before trusting
   the editor view.
   Source: Yase manual p.39 and TestMaster manual pp.172-174.

3. AutoCheck can update sequences after statement changes, but it should be
   treated as a review step. The package documentation therefore recommends
   small subprocesses and no-motion tests before enabling motion.
   Source: Yase manual p.60.

4. TestMaster uses symbolic paths such as `#SM_ROOT#`, `#SM_CONFIG#`, and
   `#SM_PROCESS#`. The package README uses those path conventions and avoids
   hard-coding the process folder.
   Source: TestMaster manual pp.43-45.

5. Callable subsequences are defined through sequence parameter declarations
   and return statements. The recommended `SUB_PY_*` interface keeps only a few
   stable parameters and carries algorithm details in JSON.
   Source: TestMaster manual pp.164-167.

6. The Sequencer Configuration panel controls which statements and
   subsequences are available in Yase. The README instructs adding/importing
   the TMPython statement library and process-local subprocesses there.
   Source: TestMaster manual pp.172-174.

7. Stage statements are provided by the Stage library under the TestMaster root.
   YASE must validate and execute `MoveStage`, then wait/check movement after
   Python returns a request.
   Source: TestMaster manual p.1017 and local `YASE_2_LENS_AUTO_ALIGNMENT_FUNCTION_REFERENCE.md`.

8. TestMaster includes JSON statements with JSONPath access. The chosen output
   keeps flat fields (`stage1`, `distance1_um`) for early parsing and also adds
   a structured `moves` array for cleaner JSON logging.
   Source: TestMaster manual pp.1174-1179.

## Package Readiness Conclusion

The Python package is designed to be copied as a folder under:

```text
#SM_SYSTEM#\Python\testmaster_python_alignment_algorithm\
```

It is copy-ready at the Python level once the machine has:

- the TestMaster TMPython support package installed into the configured Python;
- `TMPython.ini` pointing to the selected interpreter;
- the TMPython statement library added in `Sequencer -> Config Statements...`;
- one process-local `SUB_PY_*` `.xseq` subprocess per algorithm;
- imported Yase prototypes after the above configuration;
- a no-motion test proving JSON can pass in and out.

The package is not a hardware-validated operating procedure. YASE must still
own interlocks, rooms, limits, waits, move checks, error handling, and logging.
