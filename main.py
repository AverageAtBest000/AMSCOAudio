#!/usr/bin/env python3
"""Turn a scanned AMSCO-style textbook PDF into sectioned audio files.

The supplied books are scanned as landscape two-page spreads. Processing each
half independently keeps the narration in the same order as the printed book.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import pymupdf
except ImportError:
    pymupdf = None

MIN_NATIVE_TEXT = 80
MIN_NARRATION_TEXT = 200
HEADER_OR_FOOTER = re.compile(
    r"^(?:\d+\s*[|/]\s*)?(?:HOW TO READ .* PROFESSOR|INTRODUCTION|"
    r"THE STRUCTURE OF NONFICTION INFORMATION|CLASSROOM USE ONLY)"
    r"(?:\s*[|/]\s*\d+)?$",
    re.IGNORECASE,
)
PAGE_NUMBER = re.compile(r"^\s*\d+\s*$")
WHITESPACE = re.compile(r"[ \t]+")
VOICE_DIR = Path(__file__).resolve().parent / "voices"
DEFAULT_VOICE = "en_US-lessac-high"


@dataclass
class LogicalPage:
    pdf_page: int
    side: str
    text: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a scanned, two-page-spread textbook PDF to sectioned MP3 audio."
    )
    parser.add_argument("pdf", type=Path, nargs="?", help="source PDF")
    parser.add_argument(
        "-o", "--output-dir", type=Path, default=Path("audio"),
        help="directory for generated MP3 files (default: ./audio)",
    )
    parser.add_argument("--engine", choices=("piper", "espeak"), default="piper", help="speech engine (default: offline neural Piper)")
    parser.add_argument("--rate", type=int, default=180, help="speech rate; Piper uses relative speed (180 = natural pace), eSpeak uses words/minute")
    parser.add_argument("--voice", help="Piper model name/path, or eSpeak voice name with --engine espeak")
    parser.add_argument("--list-voices", action="store_true", help="list installed voices and exit")
    parser.add_argument("--language", default="eng", help="Tesseract OCR language (default: eng)")
    parser.add_argument("--start-page", type=int, default=1, help="first PDF page to process")
    parser.add_argument("--end-page", type=int, help="last PDF page to process")
    parser.add_argument("--no-ocr", action="store_true", help="do not OCR pages without embedded text")
    parser.add_argument("--keep-ocr-noise", action="store_true", help="retain low-confidence OCR and detached diagram labels for review")
    parser.add_argument(
        "--no-split-spreads", action="store_true",
        help="treat every PDF page as one page instead of splitting landscape spreads",
    )
    parser.add_argument("--keep-text", action="store_true", help="also save cleaned narration as .txt files")
    return parser.parse_args()


def require_dependencies(engine: str = "piper") -> None:
    missing = []
    if pymupdf is None:
        missing.append("PyMuPDF")
    if missing:
        packages = ", ".join(missing)
        raise RuntimeError(f"Missing package(s): {packages}. Install with: pip install -r requirements.txt")
    if engine == "piper":
        try:
            import piper  # noqa: F401
        except ImportError as error:
            raise RuntimeError("Piper is required. Install with: python -m pip install -r requirements.txt") from error
    if engine == "espeak" and not (shutil.which("espeak-ng") or shutil.which("espeak")):
        raise RuntimeError("eSpeak or eSpeak NG is required to create speech audio.")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is required to encode MP3 audio.")


def clean_text(raw: str) -> str:
    """Remove spread artefacts while retaining paragraph and heading boundaries."""
    lines: list[str] = []
    for line in raw.splitlines():
        line = WHITESPACE.sub(" ", line).strip()
        if not line:
            lines.append("")
            continue
        if PAGE_NUMBER.match(line) or HEADER_OR_FOOTER.match(line):
            continue
        if re.match(r"^\d+\s+UNITED STATES HISTORY\b", line, re.I) or re.match(r"^TOPIC\s+\d+\.\d+\s+.*\s+\d+$", line, re.I):
            continue
        if re.fullmatch(r"CLASSROOM(?:\s+USE)?|ONLY", line, re.IGNORECASE):
            continue
        if len(re.sub(r"[^A-Za-z0-9]", "", line)) < 2:
            continue
        line = re.sub(r"^[•«+]\s+", "", line)
        if line.isupper() and len(line) <= 70:
            lines.extend(["", line, ""])
        else:
            lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r"(?<=[A-Za-z])-\n(?=[a-z])", "", text)
    text = re.sub(r"(?<=[A-Za-z])-\n(?=[A-Z])", "-", text)
    # Printed line wraps are not speech pauses, including before proper nouns.
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def narration_from_tsv(tsv: str, keep_noise: bool = False) -> str:
    """Keep confident prose and nearby headings, omitting detached map labels."""
    blocks = defaultdict(list)
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] == "5" and row["text"].strip():
            blocks[row["block_num"]].append(row)
    candidates = []
    for words in blocks.values():
        size = sum(len(w["text"]) for w in words)
        confidence = sum(float(w["conf"]) * len(w["text"]) for w in words) / size
        top = min(int(w["top"]) for w in words)
        bottom = max(int(w["top"]) + int(w["height"]) for w in words)
        height = sorted(int(w["height"]) for w in words)[len(words) // 2]
        candidates.append((words, confidence, top, bottom, height))
    prose = [b for b in candidates if len(b[0]) >= 12 and b[1] >= 70]
    paragraphs = []
    for block in candidates:
        words, confidence, top, bottom, height = block
        nearby = any(max(p[2] - bottom, top - p[3], 0) <= 3 * max(height, p[4]) for p in prose)
        if not keep_noise and not (block in prose or (confidence >= 80 and nearby)):
            continue
        groups = defaultdict(list)
        for word in words:
            groups[(word["par_num"], word["line_num"])].append(word["text"])
        previous_par = None
        lines = []
        for (paragraph, _), tokens in groups.items():
            if previous_par is not None and previous_par != paragraph:
                lines.append("")
            lines.append(" ".join(tokens))
            previous_par = paragraph
        paragraphs.append("\n".join(lines))
    return "\n\n".join(paragraphs)


def ocr_side(page, clip, args: argparse.Namespace) -> str:
    if not shutil.which("tesseract"):
        raise RuntimeError("Tesseract OCR is required for scanned pages.")
    with tempfile.TemporaryDirectory(prefix="amsco-ocr-") as directory:
        image_path = Path(directory) / "page.png"
        page.get_pixmap(clip=clip, dpi=300).save(image_path)
        result = subprocess.run(
            ["tesseract", str(image_path), "stdout", "-l", args.language, "tsv"],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError(f"OCR failed: {result.stderr.strip()}")
        return narration_from_tsv(result.stdout, args.keep_ocr_noise)


def page_halves(page: "pymupdf.Page", split_spreads: bool) -> Iterable[tuple[str, "pymupdf.Rect"]]:
    rect = page.rect
    if not split_spreads or rect.width <= rect.height:
        yield "full", rect
        return
    midpoint = rect.x0 + rect.width / 2
    gutter = rect.width * 0.012
    yield "left", pymupdf.Rect(rect.x0, rect.y0, midpoint - gutter, rect.y1)
    yield "right", pymupdf.Rect(midpoint + gutter, rect.y0, rect.x1, rect.y1)


def has_enough_text(text: str) -> bool:
    return len(re.sub(r"\s+", "", text)) >= MIN_NATIVE_TEXT


def extract_pages(args: argparse.Namespace) -> list[LogicalPage]:
    document = pymupdf.open(args.pdf)
    last_page = args.end_page or len(document)
    if args.start_page < 1 or last_page < args.start_page or last_page > len(document):
        raise ValueError(f"Choose pages between 1 and {len(document)}.")

    results: list[LogicalPage] = []
    try:
        for pdf_number in range(args.start_page, last_page + 1):
            print(f"Reading PDF page {pdf_number}/{last_page}…")
            page = document[pdf_number - 1]
            sides = list(page_halves(page, not args.no_split_spreads))
            native_text = {
                side: page.get_text("text", clip=clip, sort=True).strip() for side, clip in sides
            }
            for side, clip in sides:
                text = native_text[side]
                if not args.no_ocr and not has_enough_text(text):
                    print(f"  OCRing {side} side…")
                    text = ocr_side(page, clip, args)
                text = clean_text(text)
                if len(re.sub(r"\s+", "", text)) >= MIN_NARRATION_TEXT:
                    results.append(LogicalPage(pdf_number, side, text))
                else:
                    print(f"  Skipped non-narrative {side} side.")
    finally:
        document.close()
    return results


def section_title(text: str, fallback: str) -> str:
    """Use a short, title-like first line for a human-readable file name."""
    first_line = text.splitlines()[0] if text else ""
    title = first_line if 3 <= len(first_line) <= 70 and not first_line.endswith((".", ",")) else fallback
    title = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").lower()
    return title[:48] or fallback


def espeak_command() -> str:
    return shutil.which("espeak-ng") or shutil.which("espeak") or "espeak"


def list_voices(args: argparse.Namespace) -> None:
    if args.engine == "espeak":
        subprocess.run([espeak_command(), "--voices"], check=True)
    else:
        for model in sorted(VOICE_DIR.glob("*.onnx")):
            print(model.stem)
        print("Download another model: python -m piper.download_voices <voice-name> --download-dir", VOICE_DIR)


def load_voice(args: argparse.Namespace):
    if args.engine == "espeak":
        return None
    from piper import PiperVoice
    name = args.voice or DEFAULT_VOICE
    model = Path(name)
    if not model.is_file():
        model = VOICE_DIR / (name if name.endswith(".onnx") else f"{name}.onnx")
    if not model.is_file() or not Path(f"{model}.json").is_file():
        raise RuntimeError(f"Piper voice not found: {model}. Download with: python -m piper.download_voices {DEFAULT_VOICE} --download-dir {VOICE_DIR}")
    print(f"Loading Piper voice: {model.stem}…")
    return PiperVoice.load(str(model))


def make_mp3(text: str, destination: Path, args: argparse.Namespace, voice=None) -> None:
    """Synthesize a WAV, then encode it as a standards-compliant MP3."""
    with tempfile.TemporaryDirectory(prefix="amscoaudio-") as temporary_directory:
        wav_path = Path(temporary_directory) / "speech.wav"
        try:
            if args.engine == "piper":
                from piper import SynthesisConfig
                with wave.open(str(wav_path), "wb") as wav_file:
                    voice.synthesize_wav(text, wav_file, syn_config=SynthesisConfig(length_scale=180 / args.rate))
            else:
                speech_command = [espeak_command(), "-s", str(args.rate), "-w", str(wav_path), "--stdin"]
                if args.voice:
                    speech_command.extend(["-v", args.voice])
                subprocess.run(speech_command, input=text, check=True, capture_output=True, text=True)
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path), "-codec:a", "libmp3lame", "-q:a", "3", str(destination)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as error:
            details = error.stderr.strip() or error.stdout.strip() or "no diagnostic output"
            raise RuntimeError(f"Audio generation failed: {details}") from error


def write_audio(pages: list[LogicalPage], args: argparse.Namespace, voice=None) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for index, logical_page in enumerate(pages, start=1):
        prefix = f"{index:03d}_pdf{logical_page.pdf_page:03d}_{logical_page.side}"
        name = section_title(logical_page.text, prefix)
        audio_path = args.output_dir / f"{prefix}_{name}.mp3"
        print(f"Writing {audio_path.name}…")
        make_mp3(logical_page.text, audio_path, args, voice)
        if args.keep_text:
            audio_path.with_suffix(".txt").write_text(logical_page.text + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    try:
        if args.rate <= 0:
            raise ValueError("--rate must be greater than zero.")
        require_dependencies(args.engine)
        if args.list_voices:
            list_voices(args)
            return 0
        if args.pdf is None:
            raise ValueError("Provide a source PDF.")
        if not args.pdf.is_file():
            raise FileNotFoundError(f"PDF not found: {args.pdf}")
        voice = load_voice(args)
        pages = extract_pages(args)
        if not pages:
            raise RuntimeError("No readable text was found. Check the OCR language or remove --no-ocr.")
        write_audio(pages, args, voice)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Done: wrote {len(pages)} audio file(s) to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
