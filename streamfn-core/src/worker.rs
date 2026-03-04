use crate::backpressure::TokenBucket;
use crate::channel::StreamChannel;
use crossbeam::channel::{Receiver, Sender};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::oneshot;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkerState {
    Created,
    Running,
    Draining,
    Dead,
}

/// Configuration for a stream worker
#[derive(Clone)]
pub struct WorkerConfig {
    pub buffer_size: usize,
    pub backpressure_rate: f64,
    pub backpressure_burst: f64,
    pub idle_timeout_ms: u64,
}

impl Default for WorkerConfig {
    fn default() -> Self {
        Self {
            buffer_size: 256,
            backpressure_rate: 1000.0,
            backpressure_burst: 1000.0,
            idle_timeout_ms: 5000,
        }
    }
}

/// A worker that processes events for a single stream
pub struct StreamWorker {
    pub id: String,
    pub state: WorkerState,
    pub input_channel: StreamChannel,
    pub output_channel: StreamChannel,
    pub token_bucket: Arc<TokenBucket>,
    pub config: WorkerConfig,
    shutdown_tx: Option<oneshot::Sender<()>>,
}

impl StreamWorker {
    pub fn new(id: String, config: WorkerConfig) -> Self {
        let input_channel = StreamChannel::new(config.buffer_size);
        let output_channel = StreamChannel::new(config.buffer_size);
        let token_bucket = Arc::new(TokenBucket::new(
            config.backpressure_rate,
            config.backpressure_burst,
        ));

        Self {
            id,
            state: WorkerState::Created,
            input_channel,
            output_channel,
            token_bucket,
            config,
            shutdown_tx: None,
        }
    }

    pub fn input_sender(&self) -> Sender<Vec<u8>> {
        self.input_channel.sender()
    }

    pub fn output_receiver(&self) -> Receiver<Vec<u8>> {
        self.output_channel.receiver()
    }

    /// Start the worker (spawn tokio task)
    pub fn start(&mut self) -> oneshot::Receiver<()> {
        let (tx, rx) = oneshot::channel();
        self.shutdown_tx = Some(tx);
        self.state = WorkerState::Running;
        rx
    }

    /// Request worker shutdown
    pub fn shutdown(&mut self) {
        self.state = WorkerState::Draining;
        if let Some(tx) = self.shutdown_tx.take() {
            let _ = tx.send(());
        }
    }

    /// Check if worker is idle (no events for idle_timeout_ms)
    pub fn is_idle(&self) -> bool {
        self.input_channel.is_empty() && self.output_channel.is_empty()
    }

    /// Process events (called by runtime loop)
    pub async fn process_event(&mut self, event: Vec<u8>) -> Result<Vec<u8>, String> {
        // Apply backpressure
        self.token_bucket.consume(1.0).await;

        // Forward to output (in real implementation, this would invoke Python generator)
        self.output_channel
            .try_send(event.clone())
            .map_err(|e| format!("Failed to send to output: {:?}", e))?;

        Ok(event)
    }

    /// Drain remaining events before shutdown
    pub async fn drain(&mut self) {
        self.state = WorkerState::Draining;
        let timeout = Duration::from_millis(self.config.idle_timeout_ms);

        loop {
            match self.input_channel.recv_timeout(timeout) {
                Ok(event) => {
                    let _ = self.process_event(event).await;
                }
                Err(_) => break,
            }
        }

        self.state = WorkerState::Dead;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_worker_creation() {
        let config = WorkerConfig::default();
        let worker = StreamWorker::new("test-stream".to_string(), config);
        assert_eq!(worker.state, WorkerState::Created);
        assert_eq!(worker.id, "test-stream");
    }

    #[tokio::test]
    async fn test_worker_lifecycle() {
        let config = WorkerConfig::default();
        let mut worker = StreamWorker::new("test-stream".to_string(), config);

        let _shutdown_rx = worker.start();
        assert_eq!(worker.state, WorkerState::Running);

        worker.shutdown();
        assert_eq!(worker.state, WorkerState::Draining);
    }
}
