from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class YaseParameter:
    name: str
    type_name: str
    direction: str
    value_type: str
    numeric_value: str | None
    string_value: str | None
    variable_name: str | None
    description: str = ""

    @classmethod
    def from_xml(cls, element: ET.Element) -> "YaseParameter":
        return cls(
            name=element.attrib.get("Name", ""),
            type_name=element.attrib.get("Type", ""),
            direction=element.attrib.get("Direction", ""),
            value_type=element.attrib.get("ValueType", ""),
            numeric_value=element.attrib.get("NumericValue"),
            string_value=element.attrib.get("StringValue"),
            variable_name=element.attrib.get("VariableName"),
            description=element.attrib.get("Description", ""),
        )


@dataclass(frozen=True)
class YaseStatement:
    index: int
    label: str
    name: str
    library: str
    editable: str
    parameters: tuple[YaseParameter, ...]

    @classmethod
    def from_xml(cls, index: int, element: ET.Element) -> "YaseStatement":
        return cls(
            index=index,
            label=element.attrib.get("Label", ""),
            name=element.attrib.get("Name", ""),
            library=element.attrib.get("Library", ""),
            editable=element.attrib.get("Editable", ""),
            parameters=tuple(YaseParameter.from_xml(p) for p in element.findall("Parameter")),
        )

    def param(self, name: str) -> YaseParameter:
        for parameter in self.parameters:
            if parameter.name == name:
                return parameter
        raise KeyError(f"statement {self.name!r} has no parameter {name!r}")


@dataclass(frozen=True)
class YaseSequence:
    path: Path
    comments: tuple[str, ...]
    statements: tuple[YaseStatement, ...]
    labels: dict[str, int]

    def statement_names(self) -> list[str]:
        return [statement.name for statement in self.statements]


def normalize_label(label: str) -> str:
    return label.lstrip("@").strip()


def iter_sequence_statements(root: ET.Element) -> Iterable[ET.Element]:
    for child in root:
        if child.tag == "Statement":
            yield child


def load_xseq(path: str | Path) -> YaseSequence:
    sequence_path = Path(path)
    tree = ET.parse(sequence_path)
    root = tree.getroot()
    statements = tuple(
        YaseStatement.from_xml(index, element)
        for index, element in enumerate(iter_sequence_statements(root))
    )
    labels: dict[str, int] = {}
    for statement in statements:
        label = normalize_label(statement.label)
        if label and label != "*":
            labels[label] = statement.index
    comments = tuple((element.text or "") for element in root.findall("Comment"))
    return YaseSequence(
        path=sequence_path,
        comments=comments,
        statements=statements,
        labels=labels,
    )

