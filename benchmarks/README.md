# streamfn Benchmarks

Performance evaluation framework for streamfn.

## Running Benchmarks

```bash
# Run all scenarios
python benchmarks/runner.py --scenario all

# Run specific scenarios
python benchmarks/runner.py --scenario throughput latency

# Run with comparisons
python benchmarks/runner.py --scenario all --compare all

# CI mode (fails if regression > 10%)
python benchmarks/runner.py --scenario all --ci

# Save results
python benchmarks/runner.py --scenario all --output results/bench.json
```

## Scenarios

1. **Throughput** - Events/sec at varying stream sizes and payload sizes
2. **Latency** - P50/P95/P99 inter-event latency
3. **Startup** - Stream open-to-first-event latency
4. **Concurrency** - Scaling with N concurrent streams
5. **Memory** - Memory footprint vs N streams, scale-to-zero verification

## Comparisons

- **vs asyncio naive** - Baseline pure Python asyncio queue-based streaming
- **vs Faust** - Kafka-backed Python streaming (optional, skipped if not installed)
- **vs Bytewax** - Rust-backed Python dataflow (optional, skipped if not installed)

## Performance Targets

| Metric | Target | Stretch |
|--------|--------|---------|
| Stream startup | < 500μs | < 100μs |
| Inter-event P99 latency | < 500μs | < 100μs |
| Throughput (1KB payload) | > 500K events/sec | > 1M events/sec |
| Concurrent streams | 1000 (< 1GB) | 10000 |
| vs asyncio naive | 5× faster | 10× faster |
| vs Faust | 20× faster | 50× faster |

## Output

- JSON results: machine-readable benchmark data
- Markdown table: human-readable summary
- Plots: matplotlib charts in `results/plots/`
