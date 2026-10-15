"""Prevent concurrent workers from owning the same node-local persistent state."""

import os


class RuntimeGuard:
    def __init__(self, data):
        self.path = data / "runtime.lock"
        self.file = None

    def acquire(self):
        if self.file:
            return
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        file = os.fdopen(descriptor, "r+b")
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(descriptor).st_size == 0:
                    file.write(b"\0")
                    file.flush()
                file.seek(0)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file.close()
            raise RuntimeError("Looprd state is already owned by another worker") from None
        self.file = file

    def close(self):
        if self.file:
            self.file.close()
            self.file = None
