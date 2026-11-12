"""Conservative RAM preflight, without an extra runtime dependency."""
import os
import sys


def available_memory():
    if os.name=='nt':
        import ctypes
        class Status(ctypes.Structure):
            _fields_ = [('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(name,ctypes.c_ulonglong) for name in
                ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
        status = Status()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.available
    if sys.platform.startswith('linux'):
        try:          # counts what the system can reclaim from its caches, unlike the free pages below
            with open('/proc/meminfo',encoding='ascii') as stream:
                for line in stream:
                    if line.startswith('MemAvailable:'):
                        return int(line.split()[1])*1024
        except (OSError,ValueError,IndexError):
            pass
    try:
        if sys.platform=='darwin':   # no free-page count there, and macOS keeps most memory busy with caches anyway
            return os.sysconf('SC_PHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')//2
        return os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
    except (ValueError,AttributeError,OSError):
        return 4*1024**3


def preflight(cells, faces):
    estimated = int(cells*64+faces*768+128*1024**2)
    available = available_memory()
    if estimated > available*.7:
        raise ValueError(f'Estimated peak RAM {estimated/1024**3:.2f} GiB exceeds the safe share of {available/1024**3:.2f} GiB available. '
                         'Increase pitch/tolerance, reduce the triangle budget, close other applications, or resample the source image or DEM.')
    return estimated
