# Migration v3 Vision Recognition Lab

This read-only sequence opens the Python vision recognition lab on a freshly
captured `CAM_12` frame.

Before copying to the machine, read the repository-level
[`MACHINE_CONFIGURATION.md`](../../MACHINE_CONFIGURATION.md). It is the source
of truth for the Python Automation process paths, TMPython interpreter name,
and camera-image bridge.

## Machine Deployment

The device-side folder layout should be:

```text
D:\TestMasterData\Process\Python_Automation\
|-- SUB_vision_recognition\
|   `-- SUB_OpenVisionRecognitionLab_ReadOnly.xseq
`-- python_env\
    |-- .venv\
    |   `-- Scripts\python.exe
    |-- log\
    |-- vision_recognition_lab.py
    `-- requirements_vision_recognition.txt
```

Copy the YASE sequence to:

```text
D:\TestMasterData\Process\Python_Automation\SUB_vision_recognition\
```

Copy the Python UI module from the migration v3 bundle to the TMPython working
directory:

```text
migrations\migration_v3\dev_side\python_vision_recognition\vision_recognition_lab.py
  -> D:\TestMasterData\Process\Python_Automation\python_env\vision_recognition_lab.py
```

Copy or place the requirements file where you can run `pip` from it:

```text
migrations\migration_v3\dev_side\requirements.txt
  -> D:\TestMasterData\Process\Python_Automation\python_env\requirements_vision_recognition.txt
```

Then install the public Python requirements with the machine venv:

```powershell
D:\TestMasterData\Process\Python_Automation\python_env\.venv\Scripts\python.exe -m pip install -r D:\TestMasterData\Process\Python_Automation\python_env\requirements_vision_recognition.txt
```

The vendor `testmaster_pyexec` / `tmpython` package is installed separately by
the machine setup and is not listed in that public requirements file.

## Runtime Flow

`SUB_OpenVisionRecognitionLab_ReadOnly.xseq` performs:

```text
AdvancedIMAQ Grab CAM_12 -> r_Image_Ref
AdvancedIMAQ IMAQWriteFile r_Image_Ref -> python_vision_input.bmp
TMPython_ExecuteScript vision_recognition_lab.VisionRecognitionLabStep
```

The BMP path passed into Python is:

```text
D:/TestMasterData/data/Python_Automation/python_vision_input.bmp
```

The Python UI is the same `VisionRecognitionLab` used locally. It opens in
captured-image mode with a single image, lets the operator draw ROIs and run
recognition, then saves JSON when the window is closed or `Save + Close` is
pressed.

The returned files are:

```text
D:\TestMasterData\Process\Python_Automation\python_env\log\vision_recognition_rois.json
D:\TestMasterData\Process\Python_Automation\python_env\log\vision_recognition_result.json
D:\TestMasterData\Process\Python_Automation\python_env\log\vision_recognition_result_from_yase.json
```

This sequence does not move hardware.

## Documentation Evidence

The sequence shape is based on the checked-in documentation and the current
machine configuration:

- `MACHINE_CONFIGURATION.md` identifies `Python_310_PYTHON_AUTOMATION_INTERPRETER`,
  `D:\TestMasterData\Process\Python_Automation\python_env`, and the verified
  `CAM_12` BMP bridge.
- `testmaster documentation\TestMaster Documentation 2020.1.10 (1).pdf`,
  pages 650-656, documents `AdvancedIMAQ` and `IMAQWriteFile` with image
  reference input, filename, and file type.
- The same TestMaster manual page 836 documents `WriteToFile` with `File Path`,
  `Mode`, `Data`, and `success`.
- The same TestMaster manual page 1035 documents `DisplayStatus`.
- `testmaster documentation\Yase_TM_HB_Sep_2018.pdf`, pages 6-7 and 39,
  documents YASE sequences as statement-based programs and the need to import
  project prototypes/statements.
