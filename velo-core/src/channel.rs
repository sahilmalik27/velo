use crossbeam::channel::{bounded, Receiver, Sender, TryRecvError, TrySendError};
use pyo3::prelude::*;
use std::time::Duration;

/// Lock-free SPSC channel wrapper for event passing between Rust and Python
#[pyclass]
pub struct PyChannel {
    sender: Sender<Vec<u8>>,
    receiver: Receiver<Vec<u8>>,
    capacity: usize,
}

#[pymethods]
impl PyChannel {
    #[new]
    fn new(capacity: usize) -> PyResult<Self> {
        let (sender, receiver) = bounded(capacity);
        Ok(Self {
            sender,
            receiver,
            capacity,
        })
    }

    /// Send an item to the channel (non-blocking)
    fn send(&self, data: Vec<u8>) -> PyResult<bool> {
        match self.sender.try_send(data) {
            Ok(()) => Ok(true),
            Err(TrySendError::Full(_)) => Ok(false),
            Err(TrySendError::Disconnected(_)) => {
                Err(pyo3::exceptions::PyRuntimeError::new_err(
                    "Channel disconnected",
                ))
            }
        }
    }

    /// Receive an item from the channel with timeout
    fn recv_timeout(&self, timeout_ms: u64) -> PyResult<Option<Vec<u8>>> {
        match self.receiver.recv_timeout(Duration::from_millis(timeout_ms)) {
            Ok(data) => Ok(Some(data)),
            Err(crossbeam::channel::RecvTimeoutError::Timeout) => Ok(None),
            Err(crossbeam::channel::RecvTimeoutError::Disconnected) => {
                Err(pyo3::exceptions::PyRuntimeError::new_err(
                    "Channel disconnected",
                ))
            }
        }
    }

    /// Try to receive an item (non-blocking)
    fn try_recv(&self) -> PyResult<Option<Vec<u8>>> {
        match self.receiver.try_recv() {
            Ok(data) => Ok(Some(data)),
            Err(TryRecvError::Empty) => Ok(None),
            Err(TryRecvError::Disconnected) => {
                Err(pyo3::exceptions::PyRuntimeError::new_err(
                    "Channel disconnected",
                ))
            }
        }
    }

    /// Get current length of channel
    fn len(&self) -> usize {
        self.receiver.len()
    }

    /// Check if channel is empty
    fn is_empty(&self) -> bool {
        self.receiver.is_empty()
    }

    /// Get channel capacity
    fn capacity(&self) -> usize {
        self.capacity
    }
}

/// Internal Rust-only channel for worker communication
pub struct StreamChannel {
    sender: Sender<Vec<u8>>,
    receiver: Receiver<Vec<u8>>,
}

impl StreamChannel {
    pub fn new(capacity: usize) -> Self {
        let (sender, receiver) = bounded(capacity);
        Self { sender, receiver }
    }

    pub fn sender(&self) -> Sender<Vec<u8>> {
        self.sender.clone()
    }

    pub fn receiver(&self) -> Receiver<Vec<u8>> {
        self.receiver.clone()
    }

    pub fn try_send(&self, data: Vec<u8>) -> Result<(), TrySendError<Vec<u8>>> {
        self.sender.try_send(data)
    }

    pub fn recv_timeout(&self, timeout: Duration) -> Result<Vec<u8>, crossbeam::channel::RecvTimeoutError> {
        self.receiver.recv_timeout(timeout)
    }

    pub fn len(&self) -> usize {
        self.receiver.len()
    }

    pub fn is_empty(&self) -> bool {
        self.receiver.is_empty()
    }
}
