"""Verify both raster tools and shared heightfield code match the staged release. Works on one file (the code, notices and
native parts all inside it) and on a folder or .app (the code in the program, the rest beside it)."""
import argparse
import sys
from pathlib import Path
from types import CodeType
from PyInstaller.archive.readers import CArchiveReader

ROOT=Path(__file__).resolve().parents[1]


def normalize(code):
    if not isinstance(code,CodeType):return code
    return code.replace(co_filename='',co_consts=tuple(normalize(x) for x in code.co_consts))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True)
    args=parser.parse_args()
    archive=CArchiveReader(str(args.exe))
    pyz=archive.open_embedded_archive(next(k for k in archive.toc if k.endswith('.pyz')))
    paths=[ROOT/'src/atlas_toolkit/version.py',ROOT/'src/atlas_toolkit/ui/widgets/preview.py',
           ROOT/'src/atlas_toolkit/core/job.py',ROOT/'src/atlas_toolkit/ui/tool_page.py',
           ROOT/'src/atlas_toolkit/ui/widgets/heightfield_placement.py',
           ROOT/'src/atlas_toolkit/core/tool.py',ROOT/'src/atlas_toolkit/core/presets.py',
           ROOT/'src/atlas_toolkit/ui/widgets/presets.py',
           ROOT/'src/atlas_toolkit/ui/workflow/inspector.py',
           *sorted((ROOT/'src/atlas_toolkit/tools/raster_dem_to_mesh').glob('*.py')),
           *sorted((ROOT/'src/atlas_toolkit/tools/image_to_mesh').glob('*.py')),
           *sorted((ROOT/'src/atlas_toolkit/geom/heightfield').glob('*.py'))]
    for path in paths:
        relative=path.relative_to(ROOT/'src').with_suffix('')
        name='.'.join(relative.parts)
        if name.endswith('.__init__'):name=name[:-9]
        expected=compile(path.read_bytes(),str(path),'exec',dont_inherit=True,optimize=0)
        if normalize(pyz.extract(name))!=normalize(expected):
            raise RuntimeError(f'Frozen source mismatch: {name}')
        print('matched',name,flush=True)
    exe=args.exe.resolve()
    # A bundle keeps its files in Contents/ on macOS, in the program's folder (_internal) elsewhere.
    beside=exe.parents[1] if sys.platform=='darwin' else exe.parent
    files=[str(p.relative_to(beside)).replace('\\','/') for p in beside.rglob('*') if p.is_file()]
    for name in ('pydelatin.txt','hmm.txt','glm.txt','remesh-decimate.txt'):
        key='atlas_toolkit/licenses/'+name
        packed=next((item for item in archive.toc if item.replace('\\','/')==key),None)
        if packed is not None:
            content=archive.extract(packed)
        else:
            content=(beside/next(f for f in files if f.endswith(key))).read_bytes()
        # Line endings depend on the checkout; the notice is the same text.
        if content.replace(b'\r\n',b'\n')!=(ROOT/'src'/key).read_bytes().replace(b'\r\n',b'\n'):
            raise RuntimeError(f'Frozen dependency notice mismatch: {name}')
        print('matched notice',name,flush=True)
    native=[*archive.toc,*files]
    if not any('pydelatin' in name and name.endswith(('.pyd','.so','.dylib')) for name in native):
        raise RuntimeError('Native pydelatin extension missing from frozen app.')
    return 0


if __name__=='__main__':raise SystemExit(main())
