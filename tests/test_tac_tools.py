from __future__ import annotations

import os
import unittest
from pathlib import Path

from tanuki_tools.tac_tools import TacArchive, _crypt_le


class TacToolsTests(unittest.TestCase):
    def test_crypto_round_trip(self):
        original = b"12345678ABCDEFGHtail"
        encrypted = _crypt_le(original, b"TLibArchiveData", encrypt=True)
        self.assertEqual(_crypt_le(encrypted, b"TLibArchiveData", encrypt=False), original)

    def test_real_datapic_index_and_png(self):
        archive_path = Path(os.environ.get("TANUKI_TEST_DATAPIC", "datapic.tac"))
        if not archive_path.is_file():
            self.skipTest("datapic.tac absent (définir TANUKI_TEST_DATAPIC pour ce test)")
        archive = TacArchive(archive_path)
        self.assertEqual(len(archive.entries), 1552)
        entry = next(item for item in archive.entries if item.name.casefold() == "bg/b.png")
        self.assertTrue(archive.read_entry(entry).startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
