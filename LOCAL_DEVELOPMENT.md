# Local Development Environment

Use the repo-local virtual environment for simulator, vision lab, and migration
tests:

```powershell
.\.venv\Scripts\Activate.ps1
python --version
```

The expected local version is:

```text
Python 3.10.4
```

That matches the verified Python Automation machine configuration more closely
than the shared MiniConda Python 3.11 environment.

Install or refresh the local packages with:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade `
  "numpy>=2.2,<2.3" `
  "matplotlib>=3.10,<3.11" `
  "opencv-python-headless>=4.10,<5" `
  "scikit-image>=0.25,<0.26" `
  pytest
```

Check the environment:

```powershell
.\.venv\Scripts\python.exe -m pip check
```

Run the current vision and migration smoke tests:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_migration_v2_staged_ball_placement.py `
  tests/test_migration_v3_vision_recognition.py `
  tests/test_vision_recognition_lab.py `
  tests/test_migration_v4_default_positioning.py `
  -q
```

Do not use the shared `C:\Main\Coding\DevTools\MiniConda\python.exe` for this
repo's vision stack. It contains unrelated packages with conflicting NumPy
requirements.
