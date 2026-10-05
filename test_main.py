import argparse
import csv
import io
import unittest
from unittest.mock import patch

import main


class NarrationTests(unittest.TestCase):
    def test_wraps_proper_names_and_preserves_paragraphs(self):
        raw = "The expedition reached\nOregon and returned.\n\nThe trans-\nMississippi West.\n168 UNITED STATES HISTORY: AP EDITION"
        self.assertEqual(
            main.clean_text(raw),
            "The expedition reached Oregon and returned.\n\nThe trans-Mississippi West.",
        )
        self.assertEqual(main.clean_text("The settle-\nment grew."), "The settlement grew.")

    def test_map_noise_is_omitted_but_nearby_heading_is_retained(self):
        stream = io.StringIO()
        fields = "level block_num par_num line_num text conf top height".split()
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        blocks = [
            ("1", "RTH NN/ / 55 Ww scribbles from a map and random mixed up text", 30, 100),
            ("2", "PACIFIC OCEAN", 95, 200),
            ("3", "Consequences", 95, 580),
            ("4", "The Louisiana Purchase more than doubled the size of the United States and extended the western frontier.", 95, 620),
        ]
        for block, text, confidence, top in blocks:
            for word in text.split():
                writer.writerow(dict(level=5, block_num=block, par_num=1, line_num=1,
                                     text=word, conf=confidence, top=top, height=20))
        clean = main.narration_from_tsv(stream.getvalue())
        self.assertIn("Consequences", clean)
        self.assertIn("extended the western frontier.", clean)
        self.assertNotIn("PACIFIC", clean)
        self.assertNotIn("scribbles", clean)
        self.assertIn("scribbles", main.narration_from_tsv(stream.getvalue(), keep_noise=True))

    def test_selected_pages_only_and_embedded_text_skips_ocr(self):
        document = main.pymupdf.open()
        for _ in range(3):
            document.new_page()
        document[1].insert_text((50, 50), "The Louisiana Purchase expanded the United States. " * 6, fontsize=5)
        args = argparse.Namespace(pdf="unused.pdf", start_page=2, end_page=3,
                                  no_split_spreads=False, no_ocr=False, keep_ocr_noise=False)
        with patch.object(main.pymupdf, "open", return_value=document), patch.object(
            main, "ocr_side", return_value="The expedition traveled west. " * 12
        ) as ocr:
            pages = main.extract_pages(args)
        self.assertEqual([p.pdf_page for p in pages], [2, 3])
        self.assertEqual(ocr.call_count, 1)


if __name__ == "__main__":
    unittest.main()
