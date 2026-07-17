# Python Vision Recognition Lab Runtime

This folder is the copy-ready Python side of the migration v3 vision
recognition lab.

Copy the bundled UI module into the machine TMPython working directory:

```text
migrations\migration_v3\dev_side\python_vision_recognition\vision_recognition_lab.py
  -> D:\TestMasterData\Process\Python_Automation\python_env\vision_recognition_lab.py
```

Copy the requirements file next to it:

```text
migrations\migration_v3\dev_side\requirements.txt
  -> D:\TestMasterData\Process\Python_Automation\python_env\requirements_vision_recognition.txt
```

The YASE sequence calls:

```text
Module = vision_recognition_lab
Class  = VisionRecognitionLabStep
```

The step expects JSON with:

```json
{
  "schema_version": 3,
  "image_path": "D:/TestMasterData/data/Python_Automation/python_vision_input.bmp",
  "roi_output_path": "D:/TestMasterData/Process/Python_Automation/python_env/log/vision_recognition_rois.json",
  "result_output_path": "D:/TestMasterData/Process/Python_Automation/python_env/log/vision_recognition_result.json"
}
```

Install these public packages into the machine venv:

```powershell
D:\TestMasterData\Process\Python_Automation\python_env\.venv\Scripts\python.exe -m pip install -r D:\TestMasterData\Process\Python_Automation\python_env\requirements_vision_recognition.txt
```

Do not install `testmaster_pyexec` from this file. Use the vendor package that
matches the machine TMPython setup.

After copying the module, verify the import from inside `python_env`:

```powershell
cd D:\TestMasterData\Process\Python_Automation\python_env
.\.venv\Scripts\python.exe -c "import vision_recognition_lab as v; print(v.VisionRecognitionLabStep.__name__)"
```

The expected output is:

```text
VisionRecognitionLabStep
```
