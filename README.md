# AMSCOAudio

Convert scanned textbook PDFs into listenable MP3 files. The converter is designed for the AMSCO-style layout used by the supplied material: each landscape PDF page contains a left and right printed book page. It splits those spreads first, so narration follows the book from the left page to the right page.

It uses embedded PDF text when it exists and automatically OCRs scanned sides with Tesseract when it does not. Only the selected PDF pages are processed. OCR uses confidence and layout to omit noisy graphics and detached map labels. Wrapped lines are joined for smoother narration, and common running headers and page numbers are removed.

Speech uses [Piper](https://github.com/OHF-Voice/piper1-gpl), an offline neural speech engine, with the Lessac high-quality English voice by default. No textbook text is sent to a speech service.

## Install

Python 3.10+, [Tesseract OCR](https://github.com/tesseract-ocr/tesseract), and FFmpeg must be installed on the computer. eSpeak is only required if you explicitly select the older `--engine espeak` option.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m piper.download_voices en_US-lessac-high --download-dir voices
```

## Convert a book

```bash
.venv/bin/python main.py AMSCO45.pdf --keep-text
```

This writes ordered MP3 tracks to `./audio`. Each track corresponds to one logical book page. Voice installation requires an internet connection once; conversion then works offline. Models are loaded from the `voices` directory beside `main.py`.

Useful options:

```bash
# Preserve the cleaned text beside each MP3 for review
.venv/bin/python main.py AMSCO45.pdf --keep-text

# Process a small range while checking OCR quality
.venv/bin/python main.py AMSCO45.pdf --start-page 3 --end-page 5 --keep-text -o audio-piper

# Choose a speech voice and rate
.venv/bin/python main.py --list-voices
.venv/bin/python main.py AMSCO45.pdf --voice en_US-lessac-high --rate 165
```

Piper's default `--rate 180` uses the voice's natural pace; smaller values slow it down and larger values speed it up proportionally. This is approximate, not a guaranteed words-per-minute rate. `--voice` also accepts a local `.onnx` path with its adjacent `.onnx.json` configuration.

OCR filtering is heuristic: review the saved text if narration seems incomplete, especially on diagrams, tables, and short lists. `--keep-ocr-noise` retains all recognized blocks for comparison. It may narrate garbled graphics again. Page ranges refer to PDF pages, not printed book page numbers. Landscape spreads are split left-to-right.

Use `.venv/bin/python main.py --help` for the full command reference. Existing recordings are not upgraded automatically; rerun conversion to generate Piper recordings, using a new output folder to compare versions.
