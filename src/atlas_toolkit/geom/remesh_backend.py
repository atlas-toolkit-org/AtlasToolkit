"""Cancellable native array transport; helpers die with the Atlas worker."""
from pathlib import Path
import queue
import struct
import subprocess
import tempfile
import threading
import numpy as np


def find_backend():
    from atlas_toolkit.core.runtimes import find_helper
    return find_helper('atlas-remesh-core', 'ATLAS_TOOLKIT_REMESH', 'remesh_exe')


def remesh(vertices, faces, weights, settings, ctx):
    exe = find_backend()
    if exe is None:
        raise RuntimeError('Remesh backend missing. Build it with native/build.py --only remesh or locate atlas-remesh-core.')
    from atlas_toolkit.core import procs
    from atlas_toolkit.core.runtimes import check_helper
    check_helper(exe, 'atlas-remesh-core', 1)
    with tempfile.TemporaryDirectory(prefix='atlas-remesh-') as folder:
        src, dst = Path(folder)/'in.bin', Path(folder)/'out.bin'
        with src.open('wb') as stream:
            stream.write(struct.pack('<III', 1, len(vertices), len(faces)))
            stream.write(np.asarray(vertices, dtype='<f8').tobytes())
            stream.write(np.asarray(faces, dtype='<u4').tobytes())
            stream.write(np.asarray(weights, dtype='<f8').tobytes())
        keys = ('method', 'target', 'length', 'minimum', 'maximum', 'error', 'normal', 'angle',
                'boundary', 'iterations', 'density', 'project')
        args = [str(exe), str(src), str(dst)] + [str(settings[k]) for k in keys]
        ctx.check_cancel()
        proc = procs.spawn(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace')
        lines, tail = queue.Queue(), []
        def pump():
            for line in proc.stdout:
                lines.put(line.strip())
            lines.put(None)
        thread = threading.Thread(target=pump, daemon=True)
        thread.start()
        try:
            while True:
                ctx.check_cancel()
                try:
                    line = lines.get(timeout=0.05)
                except queue.Empty:
                    continue
                if line is None:
                    break
                if line.startswith('PROGRESS '):
                    _, fraction, message = line.split(' ', 2)
                    ctx.progress(float(fraction), message)
                elif line.startswith('PEAK_MB '):
                    ctx.native_peak_mb = float(line.split()[1])
                elif line:
                    tail = (tail + [line])[-20:]
            code = proc.wait()
            ctx.check_cancel()
            if code:
                raise ValueError('Native remeshing failed: ' + '\n'.join(tail))
            with dst.open('rb') as stream:
                version, nv, nf = struct.unpack('<III', stream.read(12))
                if version != 1 or not nv or not nf:
                    raise ValueError('Incompatible or empty remesh helper output.')
                v = np.fromfile(stream, dtype='<f8', count=nv*3).reshape(-1, 3)
                f = np.fromfile(stream, dtype='<u4', count=nf*3).reshape(-1, 3).astype(np.int64)
                if len(v) != nv or len(f) != nf or f.max() >= nv or not np.isfinite(v).all():
                    raise ValueError('Invalid remesh helper arrays.')
            return v, f
        finally:
            procs.finish(proc)
            thread.join(timeout=2)
            proc.stdout.close()
