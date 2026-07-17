import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
YASE_VISION_SEQUENCE = (
    ROOT
    / "migrations"
    / "migration_v3"
    / "SUB_vision_recognition"
    / "SUB_OpenVisionRecognitionLab_ReadOnly.xseq"
)
SOURCE_RUNTIME = ROOT / "vision recognition lab" / "vision_recognition_lab.py"
COPY_READY_RUNTIME = (
    ROOT
    / "migrations"
    / "migration_v3"
    / "dev_side"
    / "python_vision_recognition"
    / "vision_recognition_lab.py"
)
RUNTIME_REQUIREMENTS = ROOT / "migrations" / "migration_v3" / "dev_side" / "requirements.txt"


def _root():
    return ET.parse(YASE_VISION_SEQUENCE).getroot()


def _statement_names():
    return [statement.attrib["Name"] for statement in _root().findall("Statement")]


def _string_values():
    return [
        parameter.attrib.get("StringValue", "")
        for parameter in _root().iter("Parameter")
        if "StringValue" in parameter.attrib
    ]


def _parameter_names():
    return [parameter.attrib["Name"] for parameter in _root().iter("Parameter")]


def test_v3_vision_recognition_sequence_opens_lab_on_fresh_camera_bmp():
    names = _statement_names()
    string_values = _string_values()
    parameter_names = _parameter_names()

    assert "Grab" in names
    assert "IMAQWriteFile" in names
    assert "TMPython_ExecuteScript" in names
    assert "MoveStage" not in names
    assert "SetAnalogOut" not in names
    assert "CAM_12" in string_values
    assert "python_vision_input.bmp" in string_values
    assert "BMP" in string_values
    assert "Python_310_PYTHON_AUTOMATION_INTERPRETER" in string_values
    assert "vision_recognition_lab" in string_values
    assert "VisionRecognitionLabStep" in string_values
    assert "ParamIn" in parameter_names
    assert "ParamOut" in parameter_names
    assert "Input JSON" not in parameter_names
    assert "Result JSON" not in parameter_names
    assert "Python_310_ALIGNMENT_TEST" not in string_values
    assert "Python_37_PYTHON_AUTOMATION_INTERPRETER" not in string_values
    assert "#SM_PROCESS#" not in "\n".join(string_values)


def test_v3_vision_recognition_json_payload_uses_machine_paths_with_forward_slashes():
    payload_text = next(value for value in _string_values() if '"image_path"' in value)
    payload = json.loads(payload_text)

    assert payload == {
        "schema_version": 3,
        "image_path": "D:/TestMasterData/data/Python_Automation/python_vision_input.bmp",
        "roi_output_path": "D:/TestMasterData/Process/Python_Automation/python_env/log/vision_recognition_rois.json",
        "result_output_path": "D:/TestMasterData/Process/Python_Automation/python_env/log/vision_recognition_result.json",
    }
    assert "\\" not in payload["image_path"]
    assert "\\" not in payload["roi_output_path"]
    assert "\\" not in payload["result_output_path"]


def test_v3_vision_recognition_copy_ready_runtime_matches_lab_source():
    assert COPY_READY_RUNTIME.read_text(encoding="utf-8") == SOURCE_RUNTIME.read_text(encoding="utf-8")


def test_v3_vision_recognition_copy_ready_runtime_imports_as_direct_python_env_module():
    module_name = "vision_recognition_lab_copy_for_test"
    spec = importlib.util.spec_from_file_location(module_name, COPY_READY_RUNTIME)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)

    assert module.VisionRecognitionLabStep.__name__ == "VisionRecognitionLabStep"


def test_v3_vision_recognition_requirements_are_public_python310_runtime_packages():
    requirements = RUNTIME_REQUIREMENTS.read_text(encoding="utf-8").splitlines()

    assert requirements == [
        "numpy>=2.2,<2.3",
        "matplotlib>=3.10,<3.11",
        "opencv-python-headless>=4.8,<5",
        "scikit-image>=0.25,<0.26",
    ]
    assert not any("tmpython" in line.lower() or "testmaster" in line.lower() for line in requirements)
    assert not any("pypdf" in line.lower() for line in requirements)
