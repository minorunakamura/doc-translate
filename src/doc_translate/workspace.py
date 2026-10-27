"""Single-writer exclusion for a workspace on macOS (POSIX flock)."""

import fcntl
import os
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

_workflow_owner = threading.local()


class WorkspaceLockedError(RuntimeError):
    """Another writer owns the workspace's OS lock."""


@contextmanager
def workspace_lock(workspace: Path) -> Generator[None]:
    """Take a nonblocking OS lock; file existence does not imply ownership.

    Manual locks are not reentrant; only orchestration may lend its lock to a stage.
    """
    owner = (os.getpid(), workspace.resolve())
    if getattr(_workflow_owner, "owner", None) == owner and not _workflow_owner.active:
        _workflow_owner.active = True
        try:
            yield
        finally:
            _workflow_owner.active = False
        return
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


@contextmanager
def _workflow_lock(workspace: Path) -> Generator[None]:
    """Run/resume alone lend their OS lock to one same-thread public stage at a time."""
    with workspace_lock(workspace):
        previous = getattr(_workflow_owner, "owner", None)
        _workflow_owner.owner = (os.getpid(), workspace.resolve())
        _workflow_owner.active = False
        try:
            yield
        finally:
            _workflow_owner.owner = previous
            _workflow_owner.active = False
