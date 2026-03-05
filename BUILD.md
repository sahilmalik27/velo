# Build Instructions

## Prerequisites

1. **Rust** (latest stable)
   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   ```

2. **Python** 3.8+

3. **Maturin** (Rust-to-Python build tool)
   ```bash
   pip install maturin
   ```

## Development Build

```bash
# Build and install in development mode
maturin develop --release

# Verify installation
python -c "import velo; print(velo.__version__)"
```

## Run Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=velo --cov-report=html
```

## Run Benchmarks

```bash
# Install benchmark dependencies
pip install -e ".[benchmark]"

# Run all benchmarks
python benchmarks/runner.py --scenario all

# Run specific scenarios
python benchmarks/runner.py --scenario throughput latency

# With comparisons
python benchmarks/runner.py --scenario all --compare all

# Save results
python benchmarks/runner.py --scenario all --output results/bench.json
```

## Run Examples

```bash
# Video pipeline
python examples/video_pipeline.py

# LLM token stream
python examples/llm_token_stream.py

# IoT sensor burst
python examples/iot_sensor_burst.py

# Webhook correlator
python examples/webhook_correlator.py
```

## Type Checking

```bash
# Install mypy
pip install mypy

# Run type checker
mypy velo/
```

## Production Build

```bash
# Build wheel
maturin build --release

# Install wheel
pip install target/wheels/velo-*.whl
```

## Clean

```bash
# Clean Rust build artifacts
cargo clean

# Clean Python artifacts
rm -rf build/ dist/ *.egg-info/
find . -type d -name __pycache__ -exec rm -rf {} +
```

## Troubleshooting

### Rust not found
Make sure Rust is in your PATH:
```bash
source $HOME/.cargo/env
```

### PyO3 version mismatch
Update dependencies:
```bash
cargo update
```

### Import error
Rebuild the extension:
```bash
maturin develop --release
```
