"""Small OS memory snapshots; no monitoring dependency is required."""
from __future__ import annotations

import os


def memory_snapshot():
    if os.name=="nt":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[("cb",wintypes.DWORD),("PageFaultCount",wintypes.DWORD),
                      *[(x,ctypes.c_size_t) for x in ("PeakWorkingSetSize","WorkingSetSize","QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage","QuotaPeakNonPagedPoolUsage","QuotaNonPagedPoolUsage",
                        "PagefileUsage","PeakPagefileUsage")]]
        value=Counters(); value.cb=ctypes.sizeof(value)
        get=ctypes.windll.psapi.GetProcessMemoryInfo
        get.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
        if get(wintypes.HANDLE(-1),ctypes.byref(value),value.cb):
            return {"working_set_mb":value.WorkingSetSize/1024**2,"peak_working_set_mb":value.PeakWorkingSetSize/1024**2}
    else:
        try:
            import resource,sys
            peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=="darwin" else 1024)
            return {"peak_working_set_mb":peak}
        except ImportError:
            pass
    return {"unavailable":True}

