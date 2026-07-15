from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tanuki_tools.csv_tools import (
    MANIFEST_NAME,
    cp932_safe,
    discover_scripts,
    export_dialogues,
    import_dialogues,
)


HEADER = [
    "%line%", "%seq%", "%effect%", "%music%", "%bg%", "%cg%", "%ov%", "%truename%",
    "%name%", "%voice%", "%st0_name%", "%st0_pos%", "%st0_face%", "%st1_name%", "%st1_pos%",
    "%st1_face%", "%st2_name%", "%st2_pos%", "%st2_face%", "%pageflag%", "%text%",
]


class CsvToolsTests(unittest.TestCase):
    def _write_csv(self, path: Path) -> None:
        rows = [
            HEADER,
            ["100", "", "", "", "", "", "", "", "Chie", "v001.wav"] + [""] * 10 + ["Bonjour, monde\\nDeuxieme ligne"],
            ["200", "", "", "", "", "", "", "", "", ""] + [""] * 10 + ["Texte avec \"guillemets\""],
            ["", ""],
        ]
        with path.open("w", encoding="cp932", newline="") as stream:
            csv.writer(stream, lineterminator="\r\n").writerows(rows)

    def test_round_trip_changes_only_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, txt, output = root / "datascn.tac", root / "txt", root / "out"
            source.mkdir()
            self._write_csv(source / "01commonA.csv")
            with (source / "_project.csv").open("w", encoding="cp932", newline="") as stream:
                csv.writer(stream).writerows([["%filename%", "%macro%"], ["01commonA", "FALSE"]])
            infos = discover_scripts(source)
            self.assertEqual([x.filename for x in infos], ["01commonA.csv"])
            export_dialogues(source, txt, ["01commonA.csv"], prefill_translation=True)
            manifest = json.loads((txt / MANIFEST_NAME).read_text(encoding="utf-8"))
            item = manifest["items"][0]
            document_path = txt / item["text_file"]
            document = document_path.read_text(encoding="utf-8-sig")
            document = document.replace("Bonjour, monde\\nDeuxieme ligne", "Déjà traduit, ça marche !", 1)
            # The first occurrence is ORIGINAL; replace the second one in the translation slot.
            document = document.replace("Bonjour, monde\\nDeuxieme ligne", "Déjà traduit, ça marche !", 1)
            document_path.write_text(document, encoding="utf-8-sig")
            report = import_dialogues(source, txt, output, encoding_mode="cp932_safe")
            self.assertEqual(report.translated, 1)
            rows = list(csv.reader((output / "01commonA.csv").read_text(encoding="cp932").splitlines()))
            self.assertEqual(rows[1][-1], "Deja traduit, ca marche !")
            self.assertEqual(rows[1][8], "Chie")
            self.assertEqual(rows[2][-1], 'Texte avec "guillemets"')

    def test_cp932_safe(self):
        value, changed = cp932_safe("Élève déjà prêt — cœur…")
        self.assertEqual(value, "Eleve deja pret - coeur...")
        self.assertGreater(changed, 0)
        japanese, _ = cp932_safe("が é")
        self.assertEqual(japanese, "が e")


if __name__ == "__main__":
    unittest.main()
