mod arena;
mod backpressure;
mod channel;
mod scheduler;
mod worker;

use pyo3::prelude::*;
use tracing_subscriber::{fmt, EnvFilter};

/// Initialize tracing (called from Python)
#[pyfunction]
fn init_tracing(level: Option<String>) -> PyResult<()> {
    let filter = EnvFilter::try_from_default_env()
        .or_else(|_| EnvFilter::try_new(level.unwrap_or_else(|| "info".to_string())))
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Invalid log level: {}", e)))?;

    fmt()
        .with_env_filter(filter)
        .with_target(false)
        .with_thread_ids(true)
        .try_init()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to init tracing: {}", e)))?;

    Ok(())
}

/// Python module - streamfn._core
#[pymodule]
fn streamfn_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<channel::PyChannel>()?;
    m.add_class::<arena::PyArena>()?;
    m.add_class::<scheduler::PyStreamScheduler>()?;
    m.add_function(wrap_pyfunction!(init_tracing, m)?)?;

    // Add version
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;

    Ok(())
}
