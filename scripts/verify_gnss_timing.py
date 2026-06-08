#!/usr/bin/env python3
"""
verify_gnss_timing.py — Check gnss_raw.csv for timestamp quality.

Usage:
  python verify_gnss_timing.py --file logs/<session>/gnss_raw.csv --expected-rate 5
  python verify_gnss_timing.py --file logs/<session>/gnss_raw.csv --expected-rate 1
  python verify_gnss_timing.py --file logs/<session>/gnss_raw.csv --expected-rate 10

Reports:
  - Row count and session duration
  - Configured / expected GNSS rate (from --expected-rate or gnss_raw.csv if logged)
  - Measured rate
  - Duplicate timestamp count
  - Delta statistics: mean, median, min, max
  - First 20 timestamp deltas
  - Pass/fail validation for the expected rate

Validation fails if:
  - Timestamps repeat in groups (e.g. five identical rows, then a 1 s jump)
  - Duplicate timestamp ratio exceeds 5 %
  - Mean delta deviates more than 30 % from the expected period
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path


def _find_column(header: list[str], *candidates: str) -> int | None:
    for name in candidates:
        if name in header:
            return header.index(name)
    return None


def run(csv_path: Path, expected_rate_hz: int) -> bool:
    if not csv_path.exists():
        print(f"ERROR: file not found: {csv_path}")
        return False

    with open(csv_path, newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            print("ERROR: empty file")
            return False

        ts_col      = _find_column(header, "timestamp_monotonic")
        proto_col   = _find_column(header, "source_protocol")
        utc_col     = _find_column(header, "gnss_utc_time")
        rate_cfg_col = None  # not in CSV, from --expected-rate

        if ts_col is None:
            print(f"ERROR: no 'timestamp_monotonic' column in {csv_path.name}")
            print(f"  Found columns: {header}")
            return False

        timestamps: list[float] = []
        protocols:  list[str]   = []
        utc_times:  list[str]   = []

        for row in reader:
            try:
                ts = float(row[ts_col])
                timestamps.append(ts)
                if proto_col is not None and proto_col < len(row):
                    protocols.append(row[proto_col])
                if utc_col is not None and utc_col < len(row):
                    utc_times.append(row[utc_col])
            except (ValueError, IndexError):
                pass

    n = len(timestamps)
    if n < 2:
        print(f"ERROR: only {n} valid rows — need at least 2 to compute deltas")
        return False

    duration_s = timestamps[-1] - timestamps[0]
    measured_hz = (n - 1) / duration_s if duration_s > 0 else 0.0

    # ── Compute deltas ────────────────────────────────────────────────────
    deltas: list[float] = [timestamps[i + 1] - timestamps[i] for i in range(n - 1)]
    dup_count = sum(1 for d in deltas if d < 1e-6)
    valid_deltas = [d for d in deltas if d >= 1e-6]

    mean_dt   = statistics.mean(valid_deltas)   if valid_deltas else 0.0
    median_dt = statistics.median(valid_deltas) if valid_deltas else 0.0
    min_dt    = min(valid_deltas)               if valid_deltas else 0.0
    max_dt    = max(valid_deltas)               if valid_deltas else 0.0

    expected_dt = 1.0 / expected_rate_hz

    # ── Protocol summary ──────────────────────────────────────────────────
    proto_counts: dict[str, int] = {}
    for p in protocols:
        proto_counts[p] = proto_counts.get(p, 0) + 1

    # ── Print report ──────────────────────────────────────────────────────
    print("=" * 60)
    print(f"  File:              {csv_path}")
    print(f"  Rows:              {n}")
    print(f"  Duration:          {duration_s:.2f} s")
    print(f"  Expected rate:     {expected_rate_hz} Hz  (dt = {expected_dt:.4f} s)")
    print(f"  Measured rate:     {measured_hz:.2f} Hz")
    if proto_counts:
        proto_str = ", ".join(f"{k}={v}" for k, v in proto_counts.items())
        print(f"  Protocol(s):       {proto_str}")
    print()
    print(f"  Duplicate timestamps (dt < 1 µs):  {dup_count} / {len(deltas)}")
    print(f"  Mean dt:           {mean_dt:.4f} s  ({1/mean_dt:.2f} Hz)")
    print(f"  Median dt:         {median_dt:.4f} s")
    print(f"  Min dt:            {min_dt:.4f} s")
    print(f"  Max dt:            {max_dt:.4f} s")
    print()
    print("  First 20 deltas (s):")
    for i, d in enumerate(deltas[:20]):
        flag = " *** DUPLICATE" if d < 1e-6 else (" ** LARGE" if d > expected_dt * 3 else "")
        print(f"    [{i:3d}]  {d:.6f}{flag}")
    print()

    # ── Validation ────────────────────────────────────────────────────────
    dup_ratio      = dup_count / len(deltas)
    rate_deviation = abs(mean_dt - expected_dt) / expected_dt if mean_dt > 0 else 1.0

    checks = [
        ("Row count >= 5",                    n >= 5,                      True),
        (f"Measured rate within 30% of {expected_rate_hz} Hz",
                                              rate_deviation < 0.30,       True),
        ("Duplicate timestamp ratio < 5%",    dup_ratio < 0.05,            True),
        ("Mean dt > 0",                       mean_dt > 0,                 True),
    ]

    all_pass = True
    for label, result, required in checks:
        icon = "PASS" if result else ("FAIL" if required else "WARN")
        print(f"  [{icon}]  {label}")
        if not result and required:
            all_pass = False

    print()
    if all_pass:
        print(f"  RESULT: PASS — timestamps look correct for {expected_rate_hz} Hz")
    else:
        print(f"  RESULT: FAIL — timestamp quality issues detected")
        if dup_ratio >= 0.05:
            print(
                f"  NOTE: {dup_count} duplicate timestamps detected.\n"
                "  This usually means the logger is using a single ser.read() timestamp\n"
                "  for multiple epochs batched in one read call.  Ensure gps_service.py\n"
                "  uses NMEA UTC time (field[1] of GGA) for per-epoch timestamps."
            )
    print("=" * 60)

    return all_pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate GNSS timestamp quality in gnss_raw.csv"
    )
    parser.add_argument("--file",          required=True, type=Path)
    parser.add_argument("--expected-rate", required=True, type=int,
                        choices=[1, 5, 10],
                        help="Expected GNSS update rate in Hz (1, 5, or 10)")
    args = parser.parse_args()
    ok = run(args.file, args.expected_rate)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
