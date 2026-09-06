"""BeatCanvas - render CapCut-authored photo templates with your own images."""
import os

# OpenCV's OpenEXR support is opt-in and the flag must be set before cv2 loads.
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

__version__ = "0.1.0"
