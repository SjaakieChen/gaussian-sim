from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json

from .interpreter import YaseInterpreter
from .machine import SimulationMachine, parse_number


def parse_parameter(text: str) -> tuple[str, Any]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("parameters must be NAME=VALUE")
    name, value = text.split("=", 1)
    value = value.strip()
    try:
        parsed: Any = parse_number(value)
    except Exception:
        parsed = value
    if value and not _looks_numeric(value):
        parsed = value
    return name.strip(), parsed


def _looks_numeric(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Yase .xseq file against the local simulation interpreter.")
    parser.add_argument("sequence", help="Path to the .xseq file to run.")
    parser.add_argument("--root", default=".", help="Repository/process root. Defaults to current directory.")
    parser.add_argument("--config", help="JSON simulation config.")
    parser.add_argument("--param", action="append", default=[], type=parse_parameter, help="Input parameter NAME=VALUE.")
    parser.add_argument("--max-steps", type=int, default=10000)
    parser.add_argument("--strict-unknown", action="store_true", help="Fail on unsupported statements instead of warning.")
    parser.add_argument("--trace", action="store_true", help="Include executed statement trace.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    config = Path(args.config).resolve() if args.config else None
    machine = SimulationMachine.from_config(root, config)
    interpreter = YaseInterpreter(machine, strict_unknown=args.strict_unknown, trace_enabled=args.trace)
    result = interpreter.run(args.sequence, parameters=dict(args.param), max_steps=args.max_steps)
    payload = {
        "sequence": str(result.sequence),
        "steps": result.steps,
        "halted": result.halted,
        "return_parameters": result.return_parameters,
        "machine": result.machine_snapshot,
        "move_count": len(machine.move_log),
        "power_reads": [event.__dict__ for event in machine.power_log],
        "warnings": result.warnings,
    }
    if args.trace:
        payload["trace"] = result.trace
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Sequence: {payload['sequence']}")
        print(f"Steps: {payload['steps']}")
        print(f"Moves: {payload['move_count']}")
        print("Stage positions:")
        for stage, position in payload["machine"]["stage_positions"].items():
            print(f"  {stage}: {position}")
        print("Simulation actors:")
        for actor, coords in payload["machine"]["simulation_actors"].items():
            print(f"  {actor}: {coords}")
        if payload["warnings"]:
            print("Warnings:")
            for warning in payload["warnings"]:
                print(f"  - {warning}")
        missing = payload["machine"]["missing_information"]
        if missing:
            print("Missing information:")
            for item in missing:
                print(f"  - {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

