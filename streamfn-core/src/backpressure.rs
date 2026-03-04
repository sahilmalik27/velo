use parking_lot::Mutex;
use std::time::{Duration, Instant};

/// Token bucket rate limiter for backpressure control
pub struct TokenBucket {
    rate: f64,           // tokens per second
    burst: f64,          // max burst size
    tokens: Mutex<f64>,  // current tokens
    last_update: Mutex<Instant>,
}

impl TokenBucket {
    pub fn new(rate: f64, burst: f64) -> Self {
        Self {
            rate,
            burst,
            tokens: Mutex::new(burst),
            last_update: Mutex::new(Instant::now()),
        }
    }

    /// Try to consume n tokens, returns true if successful
    pub fn try_consume(&self, n: f64) -> bool {
        self.refill();
        let mut tokens = self.tokens.lock();
        if *tokens >= n {
            *tokens -= n;
            true
        } else {
            false
        }
    }

    /// Wait until n tokens are available (async)
    pub async fn consume(&self, n: f64) {
        loop {
            if self.try_consume(n) {
                return;
            }
            // Sleep for the time it takes to accumulate needed tokens
            let tokens = *self.tokens.lock();
            let deficit = n - tokens;
            let wait_ms = ((deficit / self.rate) * 1000.0).ceil() as u64;
            tokio::time::sleep(Duration::from_millis(wait_ms.min(100))).await;
        }
    }

    /// Refill tokens based on elapsed time
    fn refill(&self) {
        let now = Instant::now();
        let mut last_update = self.last_update.lock();
        let elapsed = now.duration_since(*last_update).as_secs_f64();
        *last_update = now;

        let mut tokens = self.tokens.lock();
        let new_tokens = elapsed * self.rate;
        *tokens = (*tokens + new_tokens).min(self.burst);
    }

    /// Get current token count
    pub fn available_tokens(&self) -> f64 {
        self.refill();
        *self.tokens.lock()
    }

    /// Check if we can consume n tokens without waiting
    pub fn can_consume(&self, n: f64) -> bool {
        self.refill();
        *self.tokens.lock() >= n
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_token_bucket_basic() {
        let bucket = TokenBucket::new(100.0, 100.0);
        assert!(bucket.try_consume(50.0));
        assert!(bucket.try_consume(50.0));
        assert!(!bucket.try_consume(1.0)); // Should be empty
    }

    #[tokio::test]
    async fn test_token_bucket_refill() {
        let bucket = TokenBucket::new(1000.0, 100.0);
        assert!(bucket.try_consume(100.0));
        tokio::time::sleep(Duration::from_millis(50)).await;
        // Should have ~50 tokens after 50ms at 1000/sec
        assert!(bucket.try_consume(40.0));
    }
}
