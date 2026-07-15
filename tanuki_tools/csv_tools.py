from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


MANIFEST_NAME = "_tanuki_tools_manifest.json"
LEGACY_MANIFEST_NAME = "_t07_manifest.json"
MANIFEST_FORMAT = 1


class CsvToolError(RuntimeError):
    pass


@dataclass(slots=True)
class ScriptInfo:
    path: Path
    filename: str
    encoding: str
    rows: int
    text_rows: int
    project_script: bool
    project_order: int | None


@dataclass(slots=True)
class ExportItem:
    token: str
    text_file: str
    source_file: str
    row_index: int
    line_id: str
    original_sha256: str


@dataclass(slots=True)
class ExportReport:
    output_dir: Path
    files: int
    lines: int


@dataclass(slots=True)
class ImportReport:
    output_dir: Path
    files_written: int
    translated: int
    unchanged: int
    empty: int
    simplified_characters: int


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def detect_encoding(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    try:
        raw.decode("cp932")
        return "cp932"
    except UnicodeDecodeError:
        try:
            raw.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError as exc:
            raise CsvToolError("Encodage CSV non reconnu (CP932/UTF-8 attendus).") from exc


def read_csv(path: Path) -> tuple[list[list[str]], str]:
    raw = path.read_bytes()
    if not raw:
        return [], "utf-8"
    encoding = detect_encoding(raw)
    text = raw.decode(encoding)
    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        raise CsvToolError(f"CSV illisible : {path.name} ({exc})") from exc
    return rows, encoding


def _project_names(folder: Path) -> list[str]:
    project = folder / "_project.csv"
    if not project.exists() or project.stat().st_size == 0:
        return []
    rows, _ = read_csv(project)
    result: list[str] = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        is_macro = len(row) > 1 and row[1].strip().upper() == "TRUE"
        if not is_macro:
            result.append(row[0].strip() + ".csv")
    return result


def discover_scripts(folder: str | Path) -> list[ScriptInfo]:
    root = Path(folder)
    if not root.is_dir():
        raise CsvToolError("Le dossier CSV n'existe pas.")

    project_names = _project_names(root)
    project_lookup = {name.casefold(): index for index, name in enumerate(project_names)}
    scripts: list[ScriptInfo] = []
    for path in root.glob("*.csv"):
        if path.stat().st_size == 0:
            continue
        rows, encoding = read_csv(path)
        if not rows or "%text%" not in rows[0]:
            continue
        text_index = rows[0].index("%text%")
        text_rows = sum(
            1 for row in rows[1:] if len(row) > text_index and bool(row[text_index].strip())
        )
        project_order = project_lookup.get(path.name.casefold())
        scripts.append(
            ScriptInfo(
                path=path,
                filename=path.name,
                encoding=encoding,
                rows=max(0, len(rows) - 1),
                text_rows=text_rows,
                project_script=project_order is not None,
                project_order=project_order,
            )
        )
    scripts.sort(
        key=lambda item: (
            0 if item.project_script else 1,
            item.project_order if item.project_order is not None else 999999,
            item.filename.casefold(),
        )
    )
    if not scripts:
        raise CsvToolError("Aucun CSV contenant une colonne %text% n'a été trouvé.")
    return scripts


def _clean_meta(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").strip() or "—"


def export_dialogues(
    source_folder: str | Path,
    output_folder: str | Path,
    selected_files: Iterable[str],
    *,
    prefill_translation: bool = True,
) -> ExportReport:
    source = Path(source_folder).resolve()
    output = Path(output_folder).resolve()
    selected = list(dict.fromkeys(selected_files))
    if not selected:
        raise CsvToolError("Aucun script n'est sélectionné.")
    if source == output:
        raise CsvToolError("Le dossier TXT doit être différent du dossier CSV.")
    output.mkdir(parents=True, exist_ok=True)

    export_id = uuid.uuid4().hex[:12].upper()
    items: list[ExportItem] = []
    total = 0

    for filename in selected:
        path = source / filename
        if not path.is_file():
            raise CsvToolError(f"Script introuvable : {filename}")
        rows, encoding = read_csv(path)
        if not rows or "%text%" not in rows[0]:
            raise CsvToolError(f"La colonne %text% manque dans {filename}.")
        header = rows[0]
        indexes = {name: i for i, name in enumerate(header)}
        text_index = indexes["%text%"]
        text_name = path.with_suffix(".txt").name
        blocks: list[str] = [
            "# Tanuki Tools",
            f"# Source : {filename} ({encoding})",
            "# Traduisez uniquement le contenu placé après le marqueur TRADUCTION.",
            "# Ne modifiez pas les marqueurs <<<TANUKI:...>>>.",
            "",
        ]
        local_number = 0
        for row_index, row in enumerate(rows[1:], start=1):
            if len(row) <= text_index or not row[text_index].strip():
                continue
            local_number += 1
            total += 1
            token = f"{local_number:06d}"
            line_id = row[indexes.get("%line%", 0)].strip() if row else ""
            speaker = row[indexes["%name%"]] if "%name%" in indexes and len(row) > indexes["%name%"] else ""
            voice = row[indexes["%voice%"]] if "%voice%" in indexes and len(row) > indexes["%voice%"] else ""
            original = row[text_index].replace("\r\n", "\n").replace("\r", "\n")
            translation = original if prefill_translation else ""
            prefix = f"TANUKI:{export_id}:{path.stem}:{token}"
            blocks.extend(
                [
                    f"<<<{prefix}:DEBUT>>>",
                    f"Fichier     : {filename}",
                    f"Ligne       : {_clean_meta(line_id)}",
                    f"Personnage  : {_clean_meta(speaker)}",
                    f"Voix        : {_clean_meta(voice)}",
                    f"<<<{prefix}:ORIGINAL>>>",
                    original,
                    f"<<<{prefix}:TRADUCTION>>>",
                    translation,
                    f"<<<{prefix}:FIN>>>",
                    "",
                ]
            )
            items.append(
                ExportItem(
                    token=prefix,
                    text_file=text_name,
                    source_file=filename,
                    row_index=row_index,
                    line_id=line_id,
                    original_sha256=_sha_text(original),
                )
            )
        (output / text_name).write_text("\n".join(blocks), encoding="utf-8-sig", newline="\n")

    manifest = {
        "format": MANIFEST_FORMAT,
        "export_id": export_id,
        "source_folder": source.name,
        "source_files": selected,
        "prefilled": prefill_translation,
        "items": [asdict(item) for item in items],
    }
    (output / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    return ExportReport(output, len(selected), total)


def _extract_translation(document: str, token: str) -> str:
    marker = f"<<<{token}:TRADUCTION>>>"
    end_marker = f"<<<{token}:FIN>>>"
    start = document.find(marker)
    if start < 0:
        raise CsvToolError(f"Marqueur TRADUCTION manquant : {token}")
    start += len(marker)
    if document.startswith("\r\n", start):
        start += 2
    elif document.startswith("\n", start):
        start += 1
    end = document.find(end_marker, start)
    if end < 0:
        raise CsvToolError(f"Marqueur FIN manquant : {token}")
    value = document[start:end]
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    return value.replace("\r\n", "\n").replace("\r", "\n")


_TYPOGRAPHY = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "«": '"',
        "»": '"',
        "–": "-",
        "—": "-",
        "…": "...",
        " ": " ",
        "œ": "oe",
        "Œ": "OE",
        "æ": "ae",
        "Æ": "AE",
    }
)


def cp932_safe(value: str) -> tuple[str, int]:
    original = value
    value = value.translate(_TYPOGRAPHY)
    output: list[str] = []
    for char in value:
        try:
            char.encode("cp932")
            output.append(char)
        except UnicodeEncodeError:
            # Decompose only characters CP932 cannot represent. Normalizing the
            # complete string would also split Japanese dakuten (が -> か + mark)
            # and silently damage otherwise valid source text.
            replacement: list[str] = []
            for part in unicodedata.normalize("NFKD", char):
                if unicodedata.combining(part):
                    continue
                try:
                    part.encode("cp932")
                    replacement.append(part)
                except UnicodeEncodeError:
                    replacement.append("?")
            output.extend(replacement or ["?"])
    result = "".join(output)
    changed = sum(1 for a, b in zip(original, result) if a != b) + abs(len(original) - len(result))
    return result, changed


def _load_manifest(folder: Path) -> dict:
    path = folder / MANIFEST_NAME
    if not path.is_file():
        path = folder / LEGACY_MANIFEST_NAME
    if not path.is_file():
        raise CsvToolError(
            f"{MANIFEST_NAME} est introuvable dans le dossier TXT "
            f"(l'ancien {LEGACY_MANIFEST_NAME} est également accepté)."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CsvToolError("Le manifeste TXT est illisible.") from exc
    if data.get("format") != MANIFEST_FORMAT or not isinstance(data.get("items"), list):
        raise CsvToolError("Version de manifeste TXT non prise en charge.")
    return data


def import_dialogues(
    source_folder: str | Path,
    translation_folder: str | Path,
    output_folder: str | Path,
    *,
    encoding_mode: str = "cp932_safe",
) -> ImportReport:
    source = Path(source_folder).resolve()
    translations = Path(translation_folder).resolve()
    output = Path(output_folder).resolve()
    if not source.is_dir() or not translations.is_dir():
        raise CsvToolError("Le dossier CSV source ou le dossier TXT n'existe pas.")
    if output in (source, translations):
        raise CsvToolError("Le dossier de sortie doit être une copie distincte.")
    if encoding_mode not in {"cp932_safe", "cp932_strict", "utf8_bom"}:
        raise CsvToolError("Mode d'encodage inconnu.")

    manifest = _load_manifest(translations)
    documents: dict[str, str] = {}
    by_file: dict[str, list[dict]] = {}
    for item in manifest["items"]:
        by_file.setdefault(item["source_file"], []).append(item)
        text_file = item["text_file"]
        if text_file not in documents:
            path = translations / text_file
            if not path.is_file():
                raise CsvToolError(f"Fichier TXT manquant : {text_file}")
            documents[text_file] = path.read_text(encoding="utf-8-sig")

    if output.exists() and any(output.iterdir()):
        raise CsvToolError("Le dossier de sortie existe déjà et n'est pas vide.")
    shutil.copytree(source, output, dirs_exist_ok=True)

    translated = unchanged = empty = simplified = files_written = 0
    expected_values: dict[tuple[str, int], str] = {}
    try:
        for filename, items in by_file.items():
            source_path = source / filename
            if not source_path.is_file():
                raise CsvToolError(f"CSV source manquant : {filename}")
            rows, source_encoding = read_csv(source_path)
            if not rows or "%text%" not in rows[0]:
                raise CsvToolError(f"Colonne %text% absente : {filename}")
            text_index = rows[0].index("%text%")
            changed_file = False
            for item in items:
                row_index = int(item["row_index"])
                if row_index >= len(rows) or len(rows[row_index]) <= text_index:
                    raise CsvToolError(f"Ligne source déplacée ou supprimée : {filename} / {item['token']}")
                current = rows[row_index][text_index].replace("\r\n", "\n").replace("\r", "\n")
                if _sha_text(current) != item["original_sha256"]:
                    raise CsvToolError(
                        f"Le texte source a changé depuis l'export : {filename}, ligne {item['line_id']}"
                    )
                value = _extract_translation(documents[item["text_file"]], item["token"])
                if not value.strip():
                    empty += 1
                    continue
                if value == current:
                    unchanged += 1
                    continue
                if encoding_mode == "cp932_safe":
                    value, changed_count = cp932_safe(value)
                    simplified += changed_count
                elif encoding_mode == "cp932_strict":
                    try:
                        value.encode("cp932")
                    except UnicodeEncodeError as exc:
                        raise CsvToolError(
                            f"Caractère incompatible CP932 dans {filename}, ligne {item['line_id']}. "
                            "Choisissez le mode compatible sans accents."
                        ) from exc
                rows[row_index][text_index] = value
                expected_values[(filename, row_index)] = value
                translated += 1
                changed_file = True

            if changed_file:
                destination = output / filename
                output_encoding = "utf-8-sig" if encoding_mode == "utf8_bom" else source_encoding
                if output_encoding == "utf-8":
                    output_encoding = "utf-8-sig"
                try:
                    with destination.open("w", encoding=output_encoding, newline="") as stream:
                        csv.writer(stream, lineterminator="\r\n").writerows(rows)
                except UnicodeEncodeError as exc:
                    raise CsvToolError(
                        f"Impossible d'écrire {filename} en {output_encoding}. "
                        "Utilisez le mode compatible CP932."
                    ) from exc
                files_written += 1

        for (filename, row_index), expected in expected_values.items():
            rows, _ = read_csv(output / filename)
            text_index = rows[0].index("%text%")
            if rows[row_index][text_index] != expected:
                raise CsvToolError(f"Échec de vérification après écriture : {filename}")
    except Exception:
        # The incomplete copy is intentionally kept for diagnosis; originals are never touched.
        raise

    return ImportReport(output, files_written, translated, unchanged, empty, simplified)
