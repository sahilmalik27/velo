"""
Benchmark runner for streamfn.

Usage:
    python benchmarks/runner.py --scenario all
    python benchmarks/runner.py --scenario throughput latency --compare all
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# System info
import platform
import psutil


def get_system_info() -> Dict[str, Any]:
    """Collect system information."""
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "memory_total_gb": psutil.virtual_memory().total / (1024 ** 3),
        "timestamp": time.time(),
    }


def run_scenario(name: str) -> Dict[str, Any]:
    """Run a benchmark scenario."""
    import importlib

    print(f"Running scenario: {name}...")

    try:
        module = importlib.import_module(f"benchmarks.scenarios.{name}")
        result = module.run()
        print(f"  ✓ {name} completed")
        return {"scenario": name, "success": True, "results": result}
    except Exception as e:
        print(f"  ✗ {name} failed: {e}")
        return {"scenario": name, "success": False, "error": str(e)}


def run_comparison(name: str) -> Dict[str, Any]:
    """Run a comparison benchmark."""
    import importlib

    print(f"Running comparison: {name}...")

    try:
        module = importlib.import_module(f"benchmarks.compare.{name}")
        result = module.run()
        print(f"  ✓ {name} completed")
        return {"comparison": name, "success": True, "results": result}
    except ImportError as e:
        print(f"  ⊘ {name} skipped (dependency not installed): {e}")
        return {"comparison": name, "success": False, "skipped": True, "reason": str(e)}
    except Exception as e:
        print(f"  ✗ {name} failed: {e}")
        return {"comparison": name, "success": False, "error": str(e)}


def format_results_markdown(results: Dict[str, Any]) -> str:
    """Format results as Markdown table."""
    lines = ["# streamfn Benchmark Results\n"]

    # System info
    lines.append("## System Information\n")
    sys_info = results["system_info"]
    lines.append(f"- **Python**: {sys_info['python_version']}")
    lines.append(f"- **Platform**: {sys_info['platform']}")
    lines.append(f"- **CPU**: {sys_info['processor']} ({sys_info['cpu_count']} cores)")
    lines.append(f"- **Memory**: {sys_info['memory_total_gb']:.1f} GB\n")

    # Scenarios
    if results.get("scenarios"):
        lines.append("## Scenario Results\n")
        lines.append("| Scenario | Status | Key Metrics |")
        lines.append("|----------|--------|-------------|")

        for scenario in results["scenarios"]:
            name = scenario["scenario"]
            status = "✓" if scenario["success"] else "✗"

            # Extract key metric
            if scenario["success"]:
                r = scenario["results"]
                if name == "throughput":
                    metrics = f"{r.get('events_per_sec', 0):.0f} events/sec"
                elif name == "latency":
                    metrics = f"P99: {r.get('p99_us', 0):.0f}μs"
                elif name == "startup":
                    metrics = f"Mean: {r.get('mean_us', 0):.0f}μs"
                elif name == "concurrency":
                    metrics = f"{r.get('max_streams', 0)} streams"
                elif name == "memory":
                    metrics = f"{r.get('peak_mb', 0):.1f} MB"
                else:
                    metrics = "N/A"
            else:
                metrics = scenario.get("error", "Failed")

            lines.append(f"| {name} | {status} | {metrics} |")

        lines.append("")

    # Comparisons
    if results.get("comparisons"):
        lines.append("## Comparison Results\n")
        lines.append("| Comparison | Status | Speedup |")
        lines.append("|------------|--------|---------|")

        for comp in results["comparisons"]:
            name = comp["comparison"]
            if comp.get("skipped"):
                status = "⊘"
                speedup = "N/A (skipped)"
            elif comp["success"]:
                status = "✓"
                speedup = f"{comp['results'].get('speedup', 1.0):.1f}×"
            else:
                status = "✗"
                speedup = comp.get("error", "Failed")

            lines.append(f"| {name} | {status} | {speedup} |")

        lines.append("")

    return "\n".join(lines)


def check_regression(current: Dict[str, Any], baseline: Dict[str, Any], threshold: float = 0.1) -> bool:
    """Check if current results regressed vs baseline."""
    regressions = []

    for scenario in current.get("scenarios", []):
        if not scenario["success"]:
            continue

        name = scenario["scenario"]

        # Find matching baseline scenario
        baseline_scenario = next(
            (s for s in baseline.get("scenarios", []) if s["scenario"] == name),
            None
        )

        if not baseline_scenario or not baseline_scenario["success"]:
            continue

        # Check key metrics
        current_val = scenario["results"].get("events_per_sec") or scenario["results"].get("mean_us")
        baseline_val = baseline_scenario["results"].get("events_per_sec") or baseline_scenario["results"].get("mean_us")

        if current_val and baseline_val:
            # For throughput (higher is better)
            if "events_per_sec" in scenario["results"]:
                regression = (baseline_val - current_val) / baseline_val
                if regression > threshold:
                    regressions.append(f"{name}: {regression*100:.1f}% slower")
            # For latency (lower is better)
            else:
                regression = (current_val - baseline_val) / baseline_val
                if regression > threshold:
                    regressions.append(f"{name}: {regression*100:.1f}% slower")

    if regressions:
        print("\n❌ Performance regressions detected:")
        for r in regressions:
            print(f"  - {r}")
        return True

    print("\n✓ No performance regressions detected")
    return False


def main():
    parser = argparse.ArgumentParser(description="streamfn benchmark runner")
    parser.add_argument(
        "--scenario",
        nargs="+",
        default=["all"],
        help="Scenarios to run (throughput, latency, startup, concurrency, memory, all)",
    )
    parser.add_argument(
        "--compare",
        nargs="*",
        help="Comparisons to run (asyncio_naive, faust, bytewax, all)",
    )
    parser.add_argument(
        "--output",
        help="Output file for JSON results",
    )
    parser.add_argument(
        "--baseline",
        help="Baseline JSON file for regression check",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: fail if regression > 10%%",
    )

    args = parser.parse_args()

    # Collect system info
    results = {
        "system_info": get_system_info(),
        "scenarios": [],
        "comparisons": [],
    }

    # Determine scenarios to run
    all_scenarios = ["throughput", "latency", "startup", "concurrency", "memory"]
    if "all" in args.scenario:
        scenarios = all_scenarios
    else:
        scenarios = args.scenario

    # Run scenarios
    for scenario in scenarios:
        result = run_scenario(scenario)
        results["scenarios"].append(result)

    # Run comparisons
    if args.compare is not None:
        all_comparisons = ["vs_asyncio_naive", "vs_faust", "vs_bytewax"]
        if "all" in args.compare:
            comparisons = all_comparisons
        else:
            comparisons = [f"vs_{c}" if not c.startswith("vs_") else c for c in args.compare]

        for comparison in comparisons:
            result = run_comparison(comparison)
            results["comparisons"].append(result)

    # Output results
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n✓ Results saved to {args.output}")

    # Print markdown summary
    print("\n" + format_results_markdown(results))

    # Check for regressions
    if args.ci or args.baseline:
        if args.baseline:
            with open(args.baseline) as f:
                baseline = json.load(f)
        else:
            baseline = {}

        if check_regression(results, baseline):
            sys.exit(1)


if __name__ == "__main__":
    main()
