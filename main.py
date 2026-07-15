import os
import sys

from tanuki_tools.app import find_game_folder, run


def self_test() -> None:
    from tanuki_tools.csv_tools import discover_scripts
    from tanuki_tools.tac_tools import INDEX_KEY, TacArchive, _crypt_le, resource_path

    game = find_game_folder()
    sample = b"TanukiToolsTest!"
    encrypted = _crypt_le(sample, INDEX_KEY, encrypt=True)
    if _crypt_le(encrypted, INDEX_KEY, encrypt=False) != sample:
        raise RuntimeError("Échec de l'auto-test Blowfish.")
    if not resource_path("resources/tanuki.lst").is_file():
        raise RuntimeError("La liste de noms TanukiSoft est absente.")
    if (game / "datascn.tac").is_dir() and not discover_scripts(game / "datascn.tac"):
        raise RuntimeError("Aucun script CSV détecté.")
    if (game / "datapic.tac").is_file() and not TacArchive(game / "datapic.tac").entries:
        raise RuntimeError("Archive TAC vide.")


if __name__ == "__main__":
    if (
        "--self-test" in sys.argv
        or os.environ.get("TANUKI_TOOLS_SELFTEST") == "1"
        or os.environ.get("T07_TOOLKIT_SELFTEST") == "1"
    ):
        self_test()
    else:
        run()
