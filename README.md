# BeatCanvas

**BeatCanvas** is a beat-synchronized photo transition video generator. It detects note onsets in any song using spectral flux analysis and renders photo transitions (pendulum, pulse, slide, whip, drift, tile reveals) locked precisely to the music. Originally reverse-engineered from CapCut desktop draft templates.

## Features
- **Any-music beat detection**: Zero dependencies beyond NumPy.
- **10+ transition styles**: Rich visual effects synchronized perfectly to the beat.
- **Interactive waveform beat editor**: Fine-tune your beats visually.
- **Drag & drop**: Easily import photos and music.
- **Sub-minute render times**: Highly optimized, CPU-based rendering.

## Requirements
- Python 3.10+
- FFmpeg (auto-discovered from PATH or WinGet)

## Quick Start
```bash
pip install -r requirements.txt
python run.py
```
This opens a browser at http://127.0.0.1:8772 with a single-page studio.

## Template Styles
- **Beat Cut**: Fast, snappy cuts directly on the beat.
- **Beat Pulse**: A subtle scale-up and fade effect synchronized with the kick drum.
- **Beat Pendulum**: A smooth swinging motion back and forth to the rhythm.
- **Beat Slide**: Photos slide in from different directions on each beat.
- **Beat Whip**: A fast panning whip transition between images.
- **Slow Drift**: Continuous slow camera movement with beat-synced color/exposure flashes.
- **Reveal Tiles**: A grid-based transition where image parts reveal sequentially.

## Project Structure
- `beatcanvas/`: Core engine (audio analysis, rendering, utilities)
- `webapp/`: Multi-user server layer and REST API
- `tools/`: Developer utilities and test scripts
- `templates/`: JSON definitions for transition styles
