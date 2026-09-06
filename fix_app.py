import re

with open("C:/MyProjects/SnapBeatServer/beatcanvas/app.py", "r") as f:
    code = f.read()

# Fix photo_order
code = code.replace('"photo_order": "\\n".join(photo_paths),', '"photo_order": photo_paths,')

# Fix empty strings
code = code.replace('"beats_per_clip": "",', '')
code = code.replace('"max_seconds": "",', '')
code = code.replace('"reveal_tiles": "",', '')
code = code.replace('"audio_duration": "",', '')
code = code.replace('"output_dir": ""', '')

with open("C:/MyProjects/SnapBeatServer/beatcanvas/app.py", "w") as f:
    f.write(code)
