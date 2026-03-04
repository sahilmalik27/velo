use parking_lot::RwLock;
use pyo3::prelude::*;
use std::sync::Arc;

const SMALL_THRESHOLD: usize = 4096; // 4KB threshold
const ARENA_SIZE: usize = 64 * 1024 * 1024; // 64MB arena

/// Shared memory arena for zero-copy large payloads
#[pyclass]
pub struct PyArena {
    inner: Arc<ArenaInner>,
}

struct ArenaInner {
    arena: RwLock<Vec<u8>>,
    offset: RwLock<usize>,
    size: usize,
}

#[pymethods]
impl PyArena {
    #[new]
    fn new(size: Option<usize>) -> PyResult<Self> {
        let size = size.unwrap_or(ARENA_SIZE);
        Ok(Self {
            inner: Arc::new(ArenaInner {
                arena: RwLock::new(vec![0; size]),
                offset: RwLock::new(0),
                size,
            }),
        })
    }

    /// Allocate space in arena (returns offset if successful)
    fn allocate(&self, size: usize) -> PyResult<Option<usize>> {
        let mut offset = self.inner.offset.write();
        if *offset + size > self.inner.size {
            // Arena full, return None to signal fallback to heap
            Ok(None)
        } else {
            let current = *offset;
            *offset += size;
            Ok(Some(current))
        }
    }

    /// Write data to arena at offset
    fn write(&self, offset: usize, data: &[u8]) -> PyResult<()> {
        let mut arena = self.inner.arena.write();
        if offset + data.len() > self.inner.size {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Write exceeds arena bounds",
            ));
        }
        arena[offset..offset + data.len()].copy_from_slice(data);
        Ok(())
    }

    /// Read data from arena at offset
    fn read(&self, offset: usize, size: usize) -> PyResult<Vec<u8>> {
        let arena = self.inner.arena.read();
        if offset + size > self.inner.size {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Read exceeds arena bounds",
            ));
        }
        Ok(arena[offset..offset + size].to_vec())
    }

    /// Reset arena (clear all allocations)
    fn reset(&self) {
        let mut offset = self.inner.offset.write();
        *offset = 0;
    }

    /// Get current usage
    fn usage(&self) -> usize {
        *self.inner.offset.read()
    }

    /// Get total size
    fn size(&self) -> usize {
        self.inner.size
    }
}

/// Internal arena manager for Rust-side usage
pub struct Arena {
    inner: Arc<ArenaInner>,
}

impl Arena {
    pub fn new(size: usize) -> Self {
        Self {
            inner: Arc::new(ArenaInner {
                arena: RwLock::new(vec![0; size]),
                offset: RwLock::new(0),
                size,
            }),
        }
    }

    pub fn should_use_arena(size: usize) -> bool {
        size >= SMALL_THRESHOLD
    }

    pub fn allocate(&self, size: usize) -> Option<usize> {
        let mut offset = self.inner.offset.write();
        if *offset + size > self.inner.size {
            None
        } else {
            let current = *offset;
            *offset += size;
            Some(current)
        }
    }

    pub fn write(&self, offset: usize, data: &[u8]) -> Result<(), &'static str> {
        let mut arena = self.inner.arena.write();
        if offset + data.len() > self.inner.size {
            Err("Write exceeds arena bounds")
        } else {
            arena[offset..offset + data.len()].copy_from_slice(data);
            Ok(())
        }
    }

    pub fn read(&self, offset: usize, size: usize) -> Result<Vec<u8>, &'static str> {
        let arena = self.inner.arena.read();
        if offset + size > self.inner.size {
            Err("Read exceeds arena bounds")
        } else {
            Ok(arena[offset..offset + size].to_vec())
        }
    }

    pub fn reset(&self) {
        let mut offset = self.inner.offset.write();
        *offset = 0;
    }
}

impl Clone for Arena {
    fn clone(&self) -> Self {
        Self {
            inner: Arc::clone(&self.inner),
        }
    }
}
