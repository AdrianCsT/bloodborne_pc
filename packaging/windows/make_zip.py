"""Writes the release zip from the staged folder with forward slashes in every entry name, as the zip
format requires. Windows PowerShell's Compress-Archive stored backslashes, which some extractors
take as part of the file name.

    python make_zip.py STAGE_DIR FOLDER OUT_ZIP    (FOLDER inside STAGE_DIR is the zip's top folder)
"""
import sys
import zipfile
from pathlib import Path

stage, folder, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
root = stage / folder
if not root.is_dir():
    sys.exit(f'{root} is not a folder')
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(root.rglob('*')):
        name = path.relative_to(stage).as_posix()
        if path.is_dir():
            if not any(path.iterdir()):  # an empty folder needs its own entry; others come with their files
                archive.writestr(name + '/', b'')
        else:
            archive.write(path, name)
print(f'{out}: {len(archive.namelist())} entries')
