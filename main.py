import pymupdf
import pyttsx3
import sys

if len(sys.argv) != 2:
    print("Usage: python3 to_audio.py <path_to_pdf>")
    sys.exit(1)

file_name = sys.argv[1]
doc = pymupdf.open(file_name)

full_text = ""

for page_num, page in enumerate(doc):
    text = page.get_text()

    if not text.strip():
        print(f"OCRing page {page_num + 1}...")

        text_page = page.get_textpage_ocr(
            language="eng",
            dpi=300,
            full=True
        )

        text = page.get_text(textpage=text_page)

    print(f"Processed page {page_num + 1}")
    full_text += text + "\n"

print("Generating audio...")

engine = pyttsx3.init()
engine.setProperty("rate", 180)

engine.save_to_file(full_text, "output.mp3")
engine.runAndWait()

print("Done: output.mp3")