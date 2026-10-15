"""Worker resource bounds; these are fault controls, not a hostile-code sandbox."""

import os
import math

WINDOWS_JOB = None


def apply(memory_mb, seconds):
    seconds = max(1, math.ceil(seconds))
    if os.name != "nt":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1048576, memory_mb * 1048576))
        resource.setrlimit(resource.RLIMIT_CPU, (seconds + 1, seconds + 1))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
        return
    import ctypes as c
    from ctypes import wintypes as w
    class Basic(c.Structure):
        _fields_ = [("processTime", c.c_longlong), ("jobTime", c.c_longlong), ("flags", w.DWORD),
            ("minWorking", c.c_size_t), ("maxWorking", c.c_size_t), ("processes", w.DWORD),
            ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
    class IO(c.Structure):
        _fields_ = [(name, c.c_ulonglong) for name in ("readOps", "writeOps", "otherOps", "readBytes", "writeBytes", "otherBytes")]
    class Extended(c.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("processMemory", c.c_size_t), ("jobMemory", c.c_size_t),
                    ("peakProcess", c.c_size_t), ("peakJob", c.c_size_t)]
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x100 | 0x2000 | 0x8
    limits.basic.processes = 1
    limits.processMemory = memory_mb * 1048576
    if not job or not kernel.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise OSError("could not enforce worker memory/process limits")
    global WINDOWS_JOB
    WINDOWS_JOB = job
