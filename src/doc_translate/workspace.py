"""Single-writer exclusion for a workspace on macOS (POSIX flock)."""

import fcntl
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


class WorkspaceLockedError(RuntimeError):
    """Another writer owns the workspace's OS lock."""


@contextmanager
def workspace_lock(workspace: Path) -> Generator[None]:
    """Take a nonblocking OS lock; file existence does not imply ownership.

    Locks are not reentrant. save_source_snapshot acquires this lock itself.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / ".writer.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkspaceLockedError(
                "Workspace already has an active writer"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
