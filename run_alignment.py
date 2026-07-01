#!/usr/bin/env python3
"""Run alignment algorithms from the terminal without the Tk UI.

Examples:

    python run_alignment.py --algorithm walk_beam --seed 42
    python run_alignment.py --algorithm walk_beam --scramble full --seed 42
    python run_alignment.py --algorithm manual --scramble lab --seed 42 --report
"""

from __future__ import annotations

import argparse
import sys

from alignment_algorithms import available_algorithms
from alignment_session import (
    apply_full_scramble,
    apply_lab_scramble,
    apply_laser_taper_scramble,
    default_alignment_layout,
    evaluate_alignment_layout,
    run_alignment_session,
)
from interactive_setup import format_simulation_report, simulate_layout


def _format_poses_um(poses: tuple[tuple[float, float, float], ...]) -> str:
    if not poses:
        return "none"
    parts = []
    for index, (x_offset, y_offset, position) in enumerate(poses, start=1):
        parts.append(
            f"B{index} (x {x_offset * 1e6:.4g}, y {y_offset * 1e6:.4g}, z {position * 1e6:.4g}) um"
        )
    return "; ".join(parts)


def _simulate_report(layout) -> str:
    results = simulate_layout(
        [layout.source],
        [],
        [],
        layout.final_z,
        balls=layout.balls,
        tapers=layout.tapers,
    )
    return format_simulation_report(results)


def _build_parser() -> argparse.ArgumentParser:
    algorithm_names = ", ".join(sorted(available_algorithms()))
    parser = argparse.ArgumentParser(
        description=(
            "Run ball-lens alignment algorithms headlessly. "
            "Only the two ball lenses are moved; source and taper stay at their scrambled poses."
        ),
    )
    parser.add_argument(
        "--algorithm",
        default="walk_beam",
        choices=sorted(available_algorithms()),
        help=f"Alignment algorithm to run (default: walk_beam). Choices: {algorithm_names}",
    )
    parser.add_argument(
        "--scramble",
        default="laser",
        choices=("laser", "full", "lab", "lab-laser"),
        help=(
            "Starting misalignment (default: laser). "
            "'laser' keeps ball lenses at nominal alignment and scrambles only the "
            "source and taper x/y (same as the UI 'Scramble laser/fibre' button). "
            "'full' also scrambles ball lens poses. "
            "'lab' applies symmetric seeded errors to all elements. "
            "'lab-laser' applies symmetric seeded errors to source/taper only."
        ),
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for scramble (default: 42)")
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print format_simulation_report before and after alignment",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print each move from the algorithm move history",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    layout = default_alignment_layout()

    if args.scramble == "full":
        apply_full_scramble(layout, seed=args.seed)
    elif args.scramble == "lab":
        apply_lab_scramble(layout, seed=args.seed)
    elif args.scramble == "lab-laser":
        apply_lab_scramble(layout, seed=args.seed, scramble_balls=False)
    else:
        apply_laser_taper_scramble(layout, seed=args.seed)

    initial_metrics = evaluate_alignment_layout(layout)
    if args.report:
        print("=== Before alignment ===")
        print(_simulate_report(layout))
        print()

    algorithm, result, device = run_alignment_session(args.algorithm, layout)
    final_metrics = evaluate_alignment_layout(layout)

    if args.verbose:
        for index, move in enumerate(device.move_history(), start=1):
            reading = move.reading
            print(
                f"Move {index}: lens {move.lens_index + 1} "
                f"dx={move.dx * 1e6:.4g} dy={move.dy * 1e6:.4g} dz={move.dz * 1e6:.4g} um "
                f"-> {reading.received_power * 1e3:.6g} mW"
            )
        if device.move_history():
            print()

    print(f"Algorithm: {algorithm.display_name}")
    print(f"Scramble: {args.scramble} (seed {args.seed})")
    print(f"Initial power: {initial_metrics.received_power * 1e3:.6g} mW")
    print(f"Final power:   {final_metrics.received_power * 1e3:.6g} mW")
    print(f"Coupling:      {final_metrics.total_efficiency * 100:.6g}%")
    print(f"Mode match:    {final_metrics.mode_efficiency * 100:.6g}%")
    print(f"Moves:         {result.move_count}")
    print(f"Evaluations:   {result.evaluations}")
    print(f"Final poses:   {_format_poses_um(result.final_poses)}")
    if result.message:
        print(result.message)

    if args.report:
        print()
        print("=== After alignment ===")
        print(_simulate_report(layout))

    return 0


if __name__ == "__main__":
    sys.exit(main())
