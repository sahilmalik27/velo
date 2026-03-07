use crossbeam::channel::{bounded, Receiver, Sender};
use dashmap::DashMap;
use pyo3::prelude::*;
use std::sync::Arc;
use std::time::Duration;
use tracing::info;

/// Channel endpoints for a single stream.
/// Input: scheduler (Sender) → worker (Receiver)
/// Output: worker (Sender) → caller (Receiver)
pub struct StreamChannels {
    pub input_tx: Sender<Vec<u8>>,
    pub input_rx: Receiver<Vec<u8>>,
    pub output_tx: Sender<Vec<u8>>,
    pub output_rx: Receiver<Vec<u8>>,
}

/// Main scheduler that manages stream channel lifecycle.
pub struct StreamScheduler {
    streams: DashMap<String, StreamChannels>,
    active_count: parking_lot::Mutex<usize>,
    max_concurrent: usize,
    buffer_size: usize,
}

impl StreamScheduler {
    pub fn new(max_concurrent: usize, buffer_size: usize) -> Self {
        Self {
            streams: DashMap::new(),
            active_count: parking_lot::Mutex::new(0),
            max_concurrent,
            buffer_size,
        }
    }

    /// Open a new stream — create channels, insert into DashMap, enforce max_concurrent.
    pub fn open_stream(&self, stream_id: String) -> Result<String, String> {
        let current_count = *self.active_count.lock();
        if current_count >= self.max_concurrent {
            return Err(format!(
                "Max concurrent streams ({}) reached",
                self.max_concurrent
            ));
        }

        if self.streams.contains_key(&stream_id) {
            return Err(format!("Stream {} already exists", stream_id));
        }

        let (input_tx, input_rx) = bounded(self.buffer_size);
        let (output_tx, output_rx) = bounded(self.buffer_size);

        self.streams.insert(
            stream_id.clone(),
            StreamChannels {
                input_tx,
                input_rx,
                output_tx,
                output_rx,
            },
        );

        let mut count = self.active_count.lock();
        *count += 1;

        info!("Opened stream: {} (active: {})", stream_id, *count);
        Ok(stream_id)
    }

    /// Close a stream — remove from DashMap. Dropping input_tx signals Disconnected to worker.
    pub fn close_stream(&self, stream_id: &str) -> Result<(), String> {
        self.streams
            .remove(stream_id)
            .ok_or_else(|| format!("Stream {} not found", stream_id))?;
        // Dropping StreamChannels here drops input_tx → worker sees Disconnected
        let mut count = self.active_count.lock();
        *count = count.saturating_sub(1);
        info!("Closed stream: {} (active: {})", stream_id, *count);
        Ok(())
    }

    /// Send event bytes to a stream's input channel (caller → worker).
    /// Clones the Sender out of DashMap first so the read lock is NOT held
    /// during the blocking send_timeout (prevents deadlock with close_stream).
    pub fn send_input(&self, stream_id: &str, data: Vec<u8>) -> Result<(), String> {
        let tx = {
            let entry = self
                .streams
                .get(stream_id)
                .ok_or_else(|| format!("Stream {} not found", stream_id))?;
            entry.input_tx.clone()
        }; // DashMap read lock released here
        tx.send_timeout(data, Duration::from_millis(5000))
            .map_err(|e| format!("Failed to send to input: {:?}", e))
    }

    /// Worker pulls next event from input channel (blocking with timeout, GIL released).
    /// Clones the Receiver out of DashMap first so the read lock is NOT held
    /// during recv_timeout — this prevents close_stream (write lock) from blocking
    /// for the full timeout duration.
    /// Returns None on both Timeout and Disconnected.
    pub fn recv_input(&self, stream_id: &str, timeout_ms: u64) -> Result<Option<Vec<u8>>, String> {
        let rx = {
            let entry = self
                .streams
                .get(stream_id)
                .ok_or_else(|| format!("Stream {} not found", stream_id))?;
            entry.input_rx.clone()
        }; // DashMap read lock released here
        match rx.recv_timeout(Duration::from_millis(timeout_ms)) {
            Ok(data) => Ok(Some(data)),
            Err(crossbeam::channel::RecvTimeoutError::Timeout) => Ok(None),
            Err(crossbeam::channel::RecvTimeoutError::Disconnected) => Ok(None),
        }
    }

    /// Non-blocking push from worker to output channel.
    pub fn send_output_nowait(&self, stream_id: &str, data: Vec<u8>) -> Result<(), String> {
        let tx = {
            let entry = self
                .streams
                .get(stream_id)
                .ok_or_else(|| format!("Stream {} not found", stream_id))?;
            entry.output_tx.clone()
        }; // DashMap read lock released here
        tx.try_send(data)
            .map_err(|e| format!("Output channel error: {:?}", e))
    }

    /// Caller reads result from output channel (blocking with timeout, GIL released).
    /// Clones Receiver out of DashMap first — same pattern as recv_input.
    pub fn recv_output(&self, stream_id: &str, timeout_ms: u64) -> Result<Option<Vec<u8>>, String> {
        let rx = {
            let entry = self
                .streams
                .get(stream_id)
                .ok_or_else(|| format!("Stream {} not found", stream_id))?;
            entry.output_rx.clone()
        }; // DashMap read lock released here
        match rx.recv_timeout(Duration::from_millis(timeout_ms)) {
            Ok(data) => Ok(Some(data)),
            Err(crossbeam::channel::RecvTimeoutError::Timeout) => Ok(None),
            Err(crossbeam::channel::RecvTimeoutError::Disconnected) => Ok(None),
        }
    }

    /// Get count of active streams.
    pub fn active_streams(&self) -> usize {
        *self.active_count.lock()
    }

    /// List all stream IDs.
    pub fn list_streams(&self) -> Vec<String> {
        self.streams.iter().map(|r| r.key().clone()).collect()
    }
}

#[pyclass]
pub struct PyStreamScheduler {
    inner: Arc<StreamScheduler>,
}

#[pymethods]
impl PyStreamScheduler {
    #[new]
    #[pyo3(signature = (max_concurrent=None))]
    fn new(max_concurrent: Option<usize>) -> PyResult<Self> {
        let max_concurrent = max_concurrent.unwrap_or(1000);
        Ok(Self {
            inner: Arc::new(StreamScheduler::new(max_concurrent, 256)),
        })
    }

    /// Open a new stream
    fn open_stream(&self, stream_id: String) -> PyResult<String> {
        self.inner
            .open_stream(stream_id)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)
    }

    /// Send event bytes to stream's input channel (caller → worker, GIL released)
    fn send_input(&self, py: Python, stream_id: String, data: Vec<u8>) -> PyResult<()> {
        let inner = Arc::clone(&self.inner);
        py.allow_threads(|| {
            inner
                .send_input(&stream_id, data)
                .map_err(pyo3::exceptions::PyRuntimeError::new_err)
        })
    }

    /// Worker pulls next event from input channel (blocking, GIL released)
    fn recv_input(
        &self,
        py: Python,
        stream_id: String,
        timeout_ms: u64,
    ) -> PyResult<Option<Py<pyo3::types::PyBytes>>> {
        let inner = Arc::clone(&self.inner);
        let result = py.allow_threads(|| {
            inner
                .recv_input(&stream_id, timeout_ms)
                .map_err(pyo3::exceptions::PyRuntimeError::new_err)
        })?;

        Ok(result.map(|data| pyo3::types::PyBytes::new_bound(py, &data).unbind()))
    }

    /// Worker pushes result to output channel (non-blocking, sync)
    fn send_output_nowait(&self, stream_id: String, data: Vec<u8>) -> PyResult<()> {
        self.inner
            .send_output_nowait(&stream_id, data)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)
    }

    /// Caller reads result from output channel (blocking, GIL released)
    fn recv_output(
        &self,
        py: Python,
        stream_id: String,
        timeout_ms: u64,
    ) -> PyResult<Option<Py<pyo3::types::PyBytes>>> {
        let inner = Arc::clone(&self.inner);
        let result = py.allow_threads(|| {
            inner
                .recv_output(&stream_id, timeout_ms)
                .map_err(pyo3::exceptions::PyRuntimeError::new_err)
        })?;

        Ok(result.map(|data| pyo3::types::PyBytes::new_bound(py, &data).unbind()))
    }

    /// Close a stream (sync, GIL released)
    fn close_stream(&self, py: Python, stream_id: String) -> PyResult<()> {
        let inner = Arc::clone(&self.inner);
        py.allow_threads(|| {
            inner
                .close_stream(&stream_id)
                .map_err(pyo3::exceptions::PyRuntimeError::new_err)
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
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_scheduler_creation() {
        let scheduler = StreamScheduler::new(100, 256);
        assert_eq!(scheduler.active_streams(), 0);
    }

    #[test]
    fn test_open_stream() {
        let scheduler = StreamScheduler::new(100, 256);
        let result = scheduler.open_stream("test-1".to_string());
        assert!(result.is_ok());
        assert_eq!(scheduler.active_streams(), 1);
    }

    #[test]
    fn test_close_stream() {
        let scheduler = StreamScheduler::new(100, 256);
        scheduler.open_stream("test-1".to_string()).unwrap();
        assert_eq!(scheduler.active_streams(), 1);

        scheduler.close_stream("test-1").unwrap();
        assert_eq!(scheduler.active_streams(), 0);
    }

    #[test]
    fn test_send_recv_input() {
        let scheduler = StreamScheduler::new(100, 256);
        scheduler.open_stream("test-1".to_string()).unwrap();

        scheduler.send_input("test-1", vec![1, 2, 3]).unwrap();

        let result = scheduler.recv_input("test-1", 100).unwrap();
        assert_eq!(result, Some(vec![1, 2, 3]));
    }

    #[test]
    fn test_send_recv_output() {
        let scheduler = StreamScheduler::new(100, 256);
        scheduler.open_stream("test-1".to_string()).unwrap();

        scheduler
            .send_output_nowait("test-1", vec![4, 5, 6])
            .unwrap();

        let result = scheduler.recv_output("test-1", 100).unwrap();
        assert_eq!(result, Some(vec![4, 5, 6]));
    }

    #[test]
    fn test_recv_input_timeout() {
        let scheduler = StreamScheduler::new(100, 256);
        scheduler.open_stream("test-1".to_string()).unwrap();

        let result = scheduler.recv_input("test-1", 10).unwrap();
        assert_eq!(result, None);
    }

    #[test]
    fn test_close_disconnects_input() {
        let scheduler = StreamScheduler::new(100, 256);
        scheduler.open_stream("test-1".to_string()).unwrap();

        // Grab a clone of the input_rx before closing
        let input_rx = scheduler.streams.get("test-1").unwrap().input_rx.clone();

        scheduler.close_stream("test-1").unwrap();

        // After close, recv on the cloned rx should return Disconnected
        match input_rx.recv_timeout(Duration::from_millis(10)) {
            Err(crossbeam::channel::RecvTimeoutError::Disconnected) => {} // expected
            other => panic!("Expected Disconnected, got {:?}", other),
        }
    }

    #[test]
    fn test_max_concurrent() {
        let scheduler = StreamScheduler::new(2, 256);
        scheduler.open_stream("s1".to_string()).unwrap();
        scheduler.open_stream("s2".to_string()).unwrap();

        let result = scheduler.open_stream("s3".to_string());
        assert!(result.is_err());
    }
}
