"""Pinned native xatlas array protocol and cancellable, content-addressed UV cache."""
from pathlib import Path
import hashlib
import os
import queue
import struct
import subprocess
import tempfile
import threading
import numpy as np


def find_backend():
    from atlas_toolkit.core.runtimes import find_helper
    return find_helper('atlas-texture-core','ATLAS_TOOLKIT_TEXTURE','texture_exe')


def cache_root():
    from atlas_toolkit.core.paths import cache_dir
    return cache_dir()/'surface-texture-v2'


def unwrap(vertices,faces,ctx):
    exe=find_backend()
    if exe is None:
        raise ValueError('Automatic wrapping needs bundled atlas-texture-core; rebuild it with native/build.py --only texture or choose cylindrical/triplanar wrapping.')
    from atlas_toolkit.core import procs
    from atlas_toolkit.core.runtimes import check_helper
    check_helper(exe,'atlas-texture-core',2)
    vertices=np.asarray(vertices,dtype='<f8')
    faces=np.asarray(faces,dtype='<u4')
    if len(vertices)>2000000 or len(faces)>2000000:
        raise ValueError('Automatic unwrapping supports up to two million source faces/vertices. Use Remesh/Decimate first.')
    key=hashlib.sha256(vertices.tobytes()+faces.tobytes()+Path(exe).read_bytes()).hexdigest()
    cache=cache_root()
    cache.mkdir(parents=True,exist_ok=True)
    path=cache/(key+'.npz')
    if path.exists():
        try:
            with np.load(path,allow_pickle=False) as data:
                result=tuple(data[k] for k in ('uv','chart'))
            if result[0].shape==(len(faces),3,2) and result[1].shape==(len(faces),):
                ctx.log('Reusing cached surface charts')
                return *result,True
        except (OSError,ValueError,KeyError,EOFError):
            pass
    with tempfile.TemporaryDirectory(prefix='atlas-texture-uv-') as tmp:
        src,dst=Path(tmp)/'in.bin',Path(tmp)/'out.bin'
        with src.open('wb') as stream:
            stream.write(struct.pack('<III',2,len(vertices),len(faces)))
            stream.write(vertices.tobytes()); stream.write(faces.tobytes())
        proc=procs.spawn([str(exe),str(src),str(dst)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
        lines=queue.Queue()
        def pump():
            for line in proc.stdout:
                lines.put(line.strip())
        reader=threading.Thread(target=pump,daemon=True)
        reader.start()
        tail=[]
        try:
            while proc.poll() is None:
                ctx.check_cancel()
                try:
                    line=lines.get(timeout=.1)
                except queue.Empty:
                    continue
                tail.append(line)
                tail=tail[-10:]
                if line.startswith('PROGRESS '):
                    _,phase,percent=line.split()
                    ctx.progress(.06+.13*(min(int(phase),3)+int(percent)/100)/4,'Unwrapping surface charts')
            reader.join(timeout=2)
            ctx.check_cancel()
            if proc.returncode or not dst.exists():
                raise ValueError('xatlas unwrap failed: '+'; '.join(tail))
            with dst.open('rb') as stream:
                protocol,nv,nf=struct.unpack('<III',stream.read(12))
                if protocol!=2 or nf!=len(faces) or nv>len(faces)*3:
                    raise ValueError('Invalid xatlas output counts')
                mapping=np.frombuffer(stream.read(nv*4),dtype='<u4')
                coords=np.frombuffer(stream.read(nv*16),dtype='<f8').reshape(nv,2)
                indices=np.frombuffer(stream.read(nf*12),dtype='<u4').reshape(nf,3)
                charts=np.frombuffer(stream.read(nv*4),dtype='<i4')
            if len(charts)!=nv or (indices>=nv).any() or not np.array_equal(mapping[indices],faces) or not np.isfinite(coords).all():
                raise ValueError('xatlas output changed the source face mapping')
            uv,chart=coords[indices].copy(),charts[indices[:,0]].copy()
            if (chart<0).any():
                raise ValueError(f'{int((chart<0).sum())} extremely thin faces could not be unwrapped; remesh the input, or choose triplanar wrapping.')
            # Translation does not affect physical tile size, and avoids unstable
            # chart packing offsets changing texture phase from one part to another.
            for c in np.unique(chart):
                mask=chart==c
                uv[mask]-=uv[mask].reshape(-1,2).min(axis=0)
            with tempfile.NamedTemporaryFile(dir=cache,suffix='.npz',delete=False) as staged:
                staging=Path(staged.name)
                np.savez_compressed(staged,uv=uv,chart=chart)
            try:
                os.replace(staging,path)
            finally:
                staging.unlink(missing_ok=True)
            return uv,chart,False
        finally:
            procs.finish(proc)
            proc.stdout.close()
