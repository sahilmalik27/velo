use crate::worker::{StreamWorker, WorkerConfig, WorkerState};
use dashmap::DashMap;
use parking_lot::RwLock;
use pyo3::prelude::*;
use std::sync::Arc;
use std::time::Duration;
use tokio::runtime::Runtime;
use tracing::{debug, error, info, warn};

/// Main scheduler that manages stream worker lifecycle
#[pyclass]
pub struct PyStreamScheduler {
    inner: Arc<StreamScheduler>,
    runtime: Arc<Runtime>,
}

pub struct StreamScheduler {
    workers: DashMap<String, Arc<RwLock<StreamWorker>>>,
    config: WorkerConfig,
    active_count: Arc<parking_lot::Mutex<usize>>,
    max_concurrent: usize,
}

impl StreamScheduler {
    pub fn new(max_concurrent: usize, config: WorkerConfig) -> Self {
        Self {
            workers: DashMap::new(),
            config,
            active_count: Arc::new(parking_lot::Mutex::new(0)),
            max_concurrent,
        }
    }

    /// Open a new stream (microsecond startup)
    pub fn open_stream(&self, stream_id: String) -> Result<String, String> {
        let current_count = *self.active_count.lock();
        if current_count >= self.max_concurrent {
            return Err(format!(
                "Max concurrent streams ({}) reached",
                self.max_concurrent
            ));
        }

        if self.workers.contains_key(&stream_id) {
            return Err(format!("Stream {} already exists", stream_id));
        }

        let mut worker = StreamWorker::new(stream_id.clone(), self.config.clone());
        let _shutdown_rx = worker.start();

        self.workers
            .insert(stream_id.clone(), Arc::new(RwLock::new(worker)));

        let mut count = self.active_count.lock();
        *count += 1;

        info!("Opened stream: {} (active: {})", stream_id, *count);
        Ok(stream_id)
    }

    /// Send event to a stream
    pub fn send_event(&self, stream_id: &str, event: Vec<u8>) -> Result<(), String> {
        let worker_ref = self
            .workers
            .get(stream_id)
            .ok_or_else(|| format!("Stream {} not found", stream_id))?;

        let worker = worker_ref.read();
        worker
            .input_sender()
            .try_send(event)
            .map_err(|e| format!("Failed to send event: {:?}", e))?;

        Ok(())
    }

    /// Receive event from a stream
    pub fn recv_event(&self, stream_id: &str, timeout_ms: u64) -> Result<Option<Vec<u8>>, String> {
        let worker_ref = self
            .workers
            .get(stream_id)
            .ok_or_else(|| format!("Stream {} not found", stream_id))?;

        let worker = worker_ref.read();
        match worker
            .output_receiver()
            .recv_timeout(Duration::from_millis(timeout_ms))
        {
            Ok(event) => Ok(Some(event)),
            Err(crossbeam::channel::RecvTimeoutError::Timeout) => Ok(None),
            Err(e) => Err(format!("Failed to receive event: {:?}", e)),
        }
    }

    /// Close a stream (drain and cleanup)
    pub async fn close_stream(&self, stream_id: &str) -> Result<(), String> {
        let worker_ref = self
            .workers
            .remove(stream_id)
            .ok_or_else(|| format!("Stream {} not found", stream_id))?
            .1;

        let mut worker = worker_ref.write();
        worker.drain().await;

        let mut count = self.active_count.lock();
        *count = count.saturating_sub(1);

        info!("Closed stream: {} (active: {})", stream_id, *count);
        Ok(())
    }

    /// Get count of active streams
    pub fn active_streams(&self) -> usize {
        *self.active_count.lock()
    }

    /// List all stream IDs
    pub fn list_streams(&self) -> Vec<String> {
        self.workers.iter().map(|r| r.key().clone()).collect()
    }

    /// Cleanup idle streams (scale-to-zero)
    pub async fn cleanup_idle_streams(&self) {
        let idle_streams: Vec<String> = self
            .workers
            .iter()
            .filter(|r| {
                let worker = r.value().read();
                worker.is_idle() && worker.state == WorkerState::Running
            })
            .map(|r| r.key().clone())
            .collect();

        for stream_id in idle_streams {
            if let Err(e) = self.close_stream(&stream_id).await {
                warn!("Failed to cleanup idle stream {}: {}", stream_id, e);
            } else {
                debug!("Cleaned up idle stream: {}", stream_id);
            }
        }
    }
}

#[pymethods]
impl PyStreamScheduler {
    #[new]
    fn new(max_concurrent: Option<usize>) -> PyResult<Self> {
        let max_concurrent = max_concurrent.unwrap_or(1000);
        let config = WorkerConfig::default();

        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(4)
            .enable_all()
            .build()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to create runtime: {}", e)))?;

        Ok(Self {
            inner: Arc::new(StreamScheduler::new(max_concurrent, config)),
            runtime: Arc::new(runtime),
        })
    }

    /// Open a new stream
    fn open_stream(&self, stream_id: String) -> PyResult<String> {
        self.inner
            .open_stream(stream_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
    }

    /// Send event to stream
    fn send_event(&self, stream_id: String, event: Vec<u8>) -> PyResult<()> {
        self.inner
            .send_event(&stream_id, event)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
    }

    /// Receive event from stream
    fn recv_event(&self, stream_id: String, timeout_ms: u64) -> PyResult<Option<Vec<u8>>> {
        self.inner
            .recv_event(&stream_id, timeout_ms)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
    }

    /// Close a stream
    fn close_stream(&self, py: Python, stream_id: String) -> PyResult<()> {
        let inner = Arc::clone(&self.inner);
        let runtime = Arc::clone(&self.runtime);

        py.allow_threads(|| {
            runtime.block_on(async {
                inner
                    .close_stream(&stream_id)
                    .await
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
            })
        })
    }

    /// Get active stream count
    fn active_streams(&self) -> usize {
        self.inner.active_streams()
    }

    /// List all streams
    fn list_streams(&self) -> Vec<String> {
        self.inner.list_streams()
    }

    /// Cleanup idle streams
    fn cleanup_idle_streams(&self, py: Python) -> PyResult<()> {
        let inner = Arc::clone(&self.inner);
        let runtime = Arc::clone(&self.runtime);

        py.allow_threads(|| {
            runtime.block_on(async {
                inner.cleanup_idle_streams().await;
                Ok(())
            })
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_scheduler_creation() {
        let config = WorkerConfig::default();
        let scheduler = StreamScheduler::new(100, config);
        assert_eq!(scheduler.active_streams(), 0);
    }

    #[test]
    fn test_open_stream() {
        let config = WorkerConfig::default();
        let scheduler = StreamScheduler::new(100, config);
        let result = scheduler.open_stream("test-1".to_string());
        assert!(result.is_ok());
        assert_eq!(scheduler.active_streams(), 1);
    }

    #[tokio::test]
    async fn test_close_stream() {
        let config = WorkerConfig::default();
        let scheduler = StreamScheduler::new(100, config);
        scheduler.open_stream("test-1".to_string()).unwrap();
        assert_eq!(scheduler.active_streams(), 1);

        scheduler.close_stream("test-1").await.unwrap();
        assert_eq!(scheduler.active_streams(), 0);
    }
}
