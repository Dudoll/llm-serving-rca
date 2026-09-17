#!/usr/bin/env python3
"""Build a reproducible, paired open-loop experiment plan.

Each load-generator seed defines one block. Every offered rate appears once in
that block and uses the same seed, which makes comparisons across rates less
sensitive to a particular random request/arrival trace. For the three-rate
Phase 3b design, deterministic permutation blocks counterbalance both position
and directed predecessor effects.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import random
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Sequence, TextIO


FIELDNAMES = (
    "block",
    "order_in_block",
    "repetition",
    "loadgen_seed",
    "request_rate",
    "num_prompts",
    "arrival_window_seconds",
    "plan_shuffle_seed",
    "run_namespace",
    "input_len",
    "output_len",
    "max_concurrency",
    "num_warmups",
    "random_range_ratio",
    "ttft_slo_ms",
    "e2e_slo_ms",
    "tpot_slo_ms",
)


@dataclass(frozen=True)
class PlanRow:
    block: int
    order_in_block: int
    repetition: int
    loadgen_seed: int
    request_rate: Decimal
    num_prompts: int
    arrival_window_seconds: Decimal
    plan_shuffle_seed: int
    run_namespace: str = ""
    input_len: int | str | None = ""
    output_len: int | str | None = ""
    max_concurrency: int | str | None = ""
    num_warmups: int | str | None = ""
    random_range_ratio: str = ""
    ttft_slo_ms: str = ""
    e2e_slo_ms: str = ""
    tpot_slo_ms: str = ""

    def as_csv_row(self) -> dict[str, int | str]:
        return {
            "block": self.block,
            "order_in_block": self.order_in_block,
            "repetition": self.repetition,
            "loadgen_seed": self.loadgen_seed,
            "request_rate": format_decimal(self.request_rate),
            "num_prompts": self.num_prompts,
            "arrival_window_seconds": format_decimal(self.arrival_window_seconds),
            "plan_shuffle_seed": self.plan_shuffle_seed,
            "run_namespace": self.run_namespace,
            "input_len": self.input_len,
            "output_len": self.output_len,
            "max_concurrency": self.max_concurrency,
            "num_warmups": self.num_warmups,
            "random_range_ratio": self.random_range_ratio,
            "ttft_slo_ms": self.ttft_slo_ms,
            "e2e_slo_ms": self.e2e_slo_ms,
            "tpot_slo_ms": self.tpot_slo_ms,
        }


def format_decimal(value: Decimal) -> str:
    """Render a Decimal without exponent notation or redundant zeroes."""
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def positive_decimal(raw_value: str, name: str) -> Decimal:
    try:
        value = Decimal(raw_value)
    except InvalidOperation as error:
        raise ValueError(f"{name} must be numeric, got {raw_value!r}") from error
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be finite and positive, got {raw_value!r}")
    return value


def prompts_for_window(request_rate: Decimal, window_seconds: Decimal) -> int:
    """Return the exact integer request count for a nominal arrival window."""
    prompt_count = request_rate * window_seconds
    integral_count = prompt_count.to_integral_value()
    if prompt_count != integral_count:
        raise ValueError(
            "request_rate * arrival_window_seconds must be an integer, got "
            f"{format_decimal(request_rate)} * {format_decimal(window_seconds)} "
            f"= {format_decimal(prompt_count)}"
        )
    if integral_count <= 0:
        raise ValueError("the planned prompt count must be positive")
    return int(integral_count)


def _design_score(
    orders: Sequence[tuple[Decimal, ...]], rates: Sequence[Decimal]
) -> tuple[int, int]:
    position_counts = {
        (rate, position): sum(order[position] == rate for order in orders)
        for rate in rates
        for position in range(len(rates))
    }
    position_imbalance = max(
        max(position_counts[(rate, position)] for position in range(len(rates)))
        - min(position_counts[(rate, position)] for position in range(len(rates)))
        for rate in rates
    )
    predecessor_counts = {
        (left, right): sum(
            any(pair == (left, right) for pair in zip(order, order[1:]))
            for order in orders
        )
        for left in rates
        for right in rates
        if left != right
    }
    predecessor_imbalance = max(predecessor_counts.values()) - min(
        predecessor_counts.values()
    )
    return position_imbalance, predecessor_imbalance


def balanced_rate_orders(
    rates: Sequence[Decimal], block_count: int, rng: random.Random
) -> list[tuple[Decimal, ...]]:
    """Build deterministic orders with balanced position and carryover exposure."""
    if len(rates) != 3:
        # The checked-in experiment has three treatments. Keep a bounded,
        # position-balanced fallback for ad-hoc matrices of another size.
        base_rates = list(rates)
        rng.shuffle(base_rates)
        shifts = [index % len(base_rates) for index in range(block_count)]
        rng.shuffle(shifts)
        return [tuple(base_rates[shift:] + base_rates[:shift]) for shift in shifts]

    permutations = list(itertools.permutations(rates))
    cycle_count, remainder = divmod(block_count, len(permutations))
    orders: list[tuple[Decimal, ...]] = []
    for _ in range(cycle_count):
        cycle = list(permutations)
        rng.shuffle(cycle)
        orders.extend(cycle)
    if remainder:
        candidates = list(itertools.combinations(permutations, remainder))
        best_score = min(_design_score(candidate, rates) for candidate in candidates)
        best_candidates = [
            candidate
            for candidate in candidates
            if _design_score(candidate, rates) == best_score
        ]
        orders.extend(rng.choice(best_candidates))
    rng.shuffle(orders)
    return orders


def build_plan(
    rates: Sequence[Decimal],
    seeds: Sequence[int],
    arrival_window_seconds: Decimal,
    shuffle_seed: int,
    *,
    run_namespace: str = "",
    input_len: int | str | None = "",
    output_len: int | str | None = "",
    max_concurrency: int | str | None = "",
    num_warmups: int | str | None = "",
    random_range_ratio: str = "",
    ttft_slo_ms: str = "",
    e2e_slo_ms: str = "",
    tpot_slo_ms: str = "",
) -> list[PlanRow]:
    if not rates:
        raise ValueError("at least one request rate is required")
    if not seeds:
        raise ValueError("at least one load-generator seed is required")
    if len(set(rates)) != len(rates):
        raise ValueError("request rates must be unique within a block")
    if len(set(seeds)) != len(seeds):
        raise ValueError("load-generator seeds must be unique across blocks")
    if any(not rate.is_finite() or rate <= 0 for rate in rates):
        raise ValueError("all request rates must be finite and positive")
    if not arrival_window_seconds.is_finite() or arrival_window_seconds <= 0:
        raise ValueError("arrival_window_seconds must be finite and positive")
    if any(seed < 0 for seed in seeds):
        raise ValueError("load-generator seeds must be non-negative integers")

    rng = random.Random(str(shuffle_seed))
    rate_orders = balanced_rate_orders(rates, len(seeds), rng)

    plan: list[PlanRow] = []
    for block, (loadgen_seed, block_rates) in enumerate(
        zip(seeds, rate_orders), start=1
    ):
        for order_in_block, request_rate in enumerate(block_rates, start=1):
            plan.append(
                PlanRow(
                    block=block,
                    order_in_block=order_in_block,
                    repetition=block,
                    loadgen_seed=loadgen_seed,
                    request_rate=request_rate,
                    num_prompts=prompts_for_window(
                        request_rate, arrival_window_seconds
                    ),
                    arrival_window_seconds=arrival_window_seconds,
                    plan_shuffle_seed=shuffle_seed,
                    run_namespace=run_namespace,
                    input_len=input_len,
                    output_len=output_len,
                    max_concurrency=max_concurrency,
                    num_warmups=num_warmups,
                    random_range_ratio=random_range_ratio,
                    ttft_slo_ms=ttft_slo_ms,
                    e2e_slo_ms=e2e_slo_ms,
                    tpot_slo_ms=tpot_slo_ms,
                )
            )
    return plan


def write_plan(
    rows: Sequence[PlanRow],
    output_file: TextIO,
    *,
    delimiter: str = ",",
    include_header: bool = True,
) -> None:
    writer = csv.DictWriter(
        output_file,
        fieldnames=FIELDNAMES,
        delimiter=delimiter,
        lineterminator="\n",
    )
    if include_header:
        writer.writeheader()
    for row in rows:
        writer.writerow(row.as_csv_row())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build paired, deterministically shuffled open-loop blocks"
    )
    parser.add_argument("--rates", nargs="+", required=True)
    parser.add_argument("--seeds", nargs="+", required=True, type=int)
    parser.add_argument("--arrival-window-seconds", required=True)
    parser.add_argument("--shuffle-seed", type=int, default=0)
    parser.add_argument("--run-namespace", default="")
    parser.add_argument("--input-len", type=int)
    parser.add_argument("--output-len", type=int)
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--num-warmups", type=int)
    parser.add_argument("--random-range-ratio", default="")
    parser.add_argument("--ttft-slo-ms", default="")
    parser.add_argument("--e2e-slo-ms", default="")
    parser.add_argument("--tpot-slo-ms", default="")
    parser.add_argument(
        "--format", choices=("csv", "tsv"), default="csv", dest="output_format"
    )
    parser.add_argument("--no-header", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output path; omit to write the plan to stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        rates = [positive_decimal(rate, "request rate") for rate in args.rates]
        window_seconds = positive_decimal(args.arrival_window_seconds, "arrival window")
        for option_name, raw_value in (
            ("TTFT SLO", args.ttft_slo_ms),
            ("E2E SLO", args.e2e_slo_ms),
            ("TPOT SLO", args.tpot_slo_ms),
        ):
            if raw_value:
                positive_decimal(raw_value, option_name)
        for option_name, value in (
            ("input length", args.input_len),
            ("output length", args.output_len),
            ("max concurrency", args.max_concurrency),
            ("warmup count", args.num_warmups),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{option_name} must be positive")
        rows = build_plan(
            rates,
            args.seeds,
            window_seconds,
            args.shuffle_seed,
            run_namespace=args.run_namespace,
            input_len=args.input_len,
            output_len=args.output_len,
            max_concurrency=args.max_concurrency,
            num_warmups=args.num_warmups,
            random_range_ratio=args.random_range_ratio,
            ttft_slo_ms=args.ttft_slo_ms,
            e2e_slo_ms=args.e2e_slo_ms,
            tpot_slo_ms=args.tpot_slo_ms,
        )
    except ValueError as error:
        parser.error(str(error))

    delimiter = "\t" if args.output_format == "tsv" else ","
    if args.output is None:
        write_plan(
            rows,
            sys.stdout,
            delimiter=delimiter,
            include_header=not args.no_header,
        )
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="", encoding="utf-8") as output_file:
            write_plan(
                rows,
                output_file,
                delimiter=delimiter,
                include_header=not args.no_header,
            )
        print(f"Wrote {len(rows)} planned runs to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
