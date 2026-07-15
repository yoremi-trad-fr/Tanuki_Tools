from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import struct
import sys
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Event
from typing import Callable, Iterable

try:
    from Crypto.Cipher import Blowfish
except ImportError as exc:  # pragma: no cover - handled in packaged application
    Blowfish = None
    _CRYPTO_IMPORT_ERROR = exc
else:
    _CRYPTO_IMPORT_ERROR = None

try:
    from PIL import Image
except ImportError:  # dimensions are optional
    Image = None


INDEX_KEY = b"TLibArchiveData"
IMAGE_MANIFEST = ".tanuki_tools_images.json"
LEGACY_IMAGE_MANIFEST = ".t07_images.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
ProgressCallback = Callable[[int, int, str], None]


class TacError(RuntimeError):
    pass


class OperationCancelled(TacError):
    pass


@dataclass(slots=True)
class TacBucket:
    hash_low: int
    count: int
    index: int


@dataclass(slots=True)
class TacEntry:
    index: int
    hash_value: int
    packed: bool
    unpacked_size: int
    offset: int
    stored_size: int
    name: str
    encrypted_size: int

    @property
    def extension(self) -> str:
        return Path(self.name).suffix.lower()

    @property
    def is_image(self) -> bool:
        return self.extension in IMAGE_EXTENSIONS


@dataclass(slots=True)
class ArchiveSummary:
    entries: int
    named_entries: int
    images: int
    folders: dict[str, int]
    extensions: dict[str, int]


@dataclass(slots=True)
class ExtractReport:
    output_dir: Path
    extracted: int
    bytes_written: int


@dataclass(slots=True)
class RebuildReport:
    output_path: Path
    replaced: int
    copied: int
    output_size: int


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def _require_crypto() -> None:
    if Blowfish is None:
        raise TacError(
            "Le module PyCryptodome est requis pour lire les archives TAC. "
            "Installez les dépendances avec : pip install -r requirements.txt"
        ) from _CRYPTO_IMPORT_ERROR


def _swap_u32_words(data: bytes) -> bytes:
    output = bytearray(len(data))
    for offset in range(0, len(data), 4):
        output[offset : offset + 4] = data[offset : offset + 4][::-1]
    return bytes(output)


def _crypt_le(data: bytes, key: bytes, *, encrypt: bool) -> bytes:
    _require_crypto()
    block_length = len(data) & ~7
    if not block_length:
        return data
    cipher = Blowfish.new(key, Blowfish.MODE_ECB)
    swapped = _swap_u32_words(data[:block_length])
    transformed = cipher.encrypt(swapped) if encrypt else cipher.decrypt(swapped)
    return _swap_u32_words(transformed) + data[block_length:]


def hash_name_ascii(name: str, seed: int) -> int:
    value = 0
    for char in name.replace("\\", "/").upper():
        value = (ord(char) + 0x19919 * value + seed) & 0xFFFFFFFFFFFFFFFF
    return value


def entry_key(hash_value: int) -> bytes:
    return f"{hash_value}_tlib_secure_".encode("ascii")


def _safe_relative(name: str) -> Path:
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise TacError(f"Chemin dangereux dans l'archive : {name}")
    if any(":" in part for part in pure.parts):
        raise TacError(f"Chemin non portable dans l'archive : {name}")
    return Path(*pure.parts)


def _image_dimensions(raw: bytes) -> tuple[int | None, int | None]:
    if Image is None:
        return None, None
    try:
        with Image.open(io.BytesIO(raw)) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None, None


def _guess_extension(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if raw.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if raw.startswith(b"BM"):
        return ".bmp"
    if raw.startswith(b"OggS"):
        return ".ogg"
    if raw.startswith(b"TSV\x00"):
        return ".bcs"
    if raw.startswith((b"{", b"[", b"#", b"//")):
        return ".txt"
    return ""


class TacArchive:
    def __init__(self, path: str | Path, names_file: str | Path | None = None):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise TacError("Archive TAC introuvable.")
        self.names_file = Path(names_file) if names_file else resource_path("resources/tanuki.lst")
        self.header = b""
        self.version = 0
        self.index_offset = 0
        self.index_size = 0
        self.base_offset = 0
        self.count = 0
        self.bucket_count = 0
        self.seed = 0
        self.buckets: list[TacBucket] = []
        self.entries: list[TacEntry] = []
        self._open()

    def _open(self) -> None:
        with self.path.open("rb") as stream:
            header = stream.read(0x2C)
            if len(header) < 0x24 or not header.startswith(b"TArc"):
                raise TacError("Ce fichier n'est pas une archive TanukiSoft TAC.")
            version_text = header[4:8]
            if version_text == b"1.10":
                self.version, self.index_offset = 110, 0x2C
            elif version_text == b"1.00":
                self.version, self.index_offset = 100, 0x24
                header = header[:0x24]
            else:
                raise TacError("Version TAC non prise en charge (1.00/1.10 attendue).")
            self.header = header
            self.count, self.bucket_count, self.index_size, self.seed = struct.unpack_from(
                "<4I", header, 0x14
            )
            if self.count <= 0 or self.count > 1_000_000 or self.bucket_count > 1_000_000:
                raise TacError("Index TAC incohérent.")
            self.base_offset = self.index_offset + self.index_size
            stream.seek(self.index_offset)
            encrypted_index = stream.read(self.index_size)
            if len(encrypted_index) != self.index_size:
                raise TacError("Index TAC tronqué.")
        try:
            packed_index = _crypt_le(encrypted_index, INDEX_KEY, encrypt=False)
            raw_index = zlib.decompress(packed_index)
        except Exception as exc:
            raise TacError("Impossible de déchiffrer ou décompresser l'index TAC.") from exc

        minimum = self.bucket_count * 8 + self.count * 24
        if len(raw_index) < minimum:
            raise TacError("Index TAC incomplet.")
        cursor = 0
        for _ in range(self.bucket_count):
            low, count, index = struct.unpack_from("<HHi", raw_index, cursor)
            self.buckets.append(TacBucket(low, count, index))
            cursor += 8

        partial: list[tuple[int, bool, int, int, int]] = []
        for _ in range(self.count):
            high, packed, unpacked, relative_offset, stored = struct.unpack_from(
                "<QiIII", raw_index, cursor
            )
            partial.append((high, bool(packed), unpacked, relative_offset, stored))
            cursor += 24

        full_hashes = [0] * self.count
        for bucket in self.buckets:
            if bucket.index < 0 or bucket.index + bucket.count > self.count:
                raise TacError("Table de hash TAC incohérente.")
            for index in range(bucket.index, bucket.index + bucket.count):
                full_hashes[index] = ((partial[index][0] << 16) | bucket.hash_low) & 0xFFFFFFFFFFFFFFFF

        names = self._resolve_names(set(full_hashes))
        file_size = self.path.stat().st_size
        for index, values in enumerate(partial):
            _, packed, unpacked, relative, stored = values
            offset = self.base_offset + relative
            if offset < self.base_offset or stored < 0 or offset + stored > file_size:
                raise TacError(f"Entrée TAC hors limites : index {index}")
            hash_value = full_hashes[index]
            name = names.get(hash_value, f"{hash_value:016X}")
            extension = Path(name).suffix.lower()
            encrypted_size = stored
            entry = TacEntry(index, hash_value, packed, unpacked, offset, stored, name, encrypted_size)
            if not packed and not extension:
                prefix = self._read_encrypted_prefix(entry, min(stored, 16))
                guessed = _guess_extension(prefix)
                if guessed:
                    entry.name += guessed
                    extension = guessed
            if not packed and extension in IMAGE_EXTENSIONS:
                entry.encrypted_size = min(10240, stored)
            self.entries.append(entry)

    def _resolve_names(self, wanted: set[int]) -> dict[int, str]:
        if not self.names_file.is_file():
            return {}
        result: dict[int, str] = {}
        try:
            with self.names_file.open("r", encoding="utf-8-sig", errors="replace") as stream:
                for raw_name in stream:
                    name = raw_name.strip()
                    if not name:
                        continue
                    value = hash_name_ascii(name, self.seed)
                    if value in wanted:
                        result[value] = name.replace("\\", "/")
                        if len(result) == len(wanted):
                            break
        except OSError as exc:
            raise TacError("Impossible de lire la liste de noms TanukiSoft.") from exc
        return result

    def _read_encrypted_prefix(self, entry: TacEntry, length: int) -> bytes:
        with self.path.open("rb") as stream:
            stream.seek(entry.offset)
            raw = stream.read(length)
        if entry.packed:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return raw
        return _crypt_le(raw, entry_key(entry.hash_value), encrypt=False)

    def read_entry(self, entry: TacEntry) -> bytes:
        with self.path.open("rb") as stream:
            stream.seek(entry.offset)
            stored = stream.read(entry.stored_size)
        if len(stored) != entry.stored_size:
            raise TacError(f"Entrée tronquée : {entry.name}")
        if entry.packed:
            try:
                raw = zlib.decompress(stored)
            except zlib.error as exc:
                raise TacError(f"Entrée compressée invalide : {entry.name}") from exc
        else:
            encrypted = min(entry.encrypted_size, len(stored))
            raw = _crypt_le(stored[:encrypted], entry_key(entry.hash_value), encrypt=False) + stored[encrypted:]
        # In TArc1.10 image entries this field is usually the constant 10240
        # (the encrypted prefix length), not the real file size. It is a true
        # unpacked size only for zlib-packed entries.
        if entry.packed and entry.unpacked_size and len(raw) != entry.unpacked_size:
            raise TacError(
                f"Taille décompressée incorrecte pour {entry.name} : {len(raw)} / {entry.unpacked_size}"
            )
        return raw

    def summary(self) -> ArchiveSummary:
        folders: Counter[str] = Counter()
        extensions: Counter[str] = Counter()
        named = images = 0
        for entry in self.entries:
            parts = entry.name.replace("\\", "/").split("/")
            folders[parts[0] if len(parts) > 1 else "(racine)"] += 1
            extensions[entry.extension or "(sans extension)"] += 1
            if not entry.name.startswith(f"{entry.hash_value:016X}"):
                named += 1
            if entry.is_image:
                images += 1
        return ArchiveSummary(
            len(self.entries), named, images, dict(folders.most_common()), dict(extensions.most_common())
        )

    def extract(
        self,
        output_folder: str | Path,
        *,
        images_only: bool = True,
        progress: ProgressCallback | None = None,
        cancel: Event | None = None,
    ) -> ExtractReport:
        output = Path(output_folder).resolve()
        output.mkdir(parents=True, exist_ok=True)
        chosen = [entry for entry in self.entries if entry.is_image or not images_only]
        manifest_entries: dict[str, dict] = {}
        bytes_written = 0
        for number, entry in enumerate(chosen, start=1):
            if cancel and cancel.is_set():
                raise OperationCancelled("Extraction annulée.")
            relative = _safe_relative(entry.name)
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            raw = self.read_entry(entry)
            destination.write_bytes(raw)
            width, height = _image_dimensions(raw) if entry.is_image else (None, None)
            manifest_entries[entry.name] = {
                "hash": f"{entry.hash_value:016X}",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
                "width": width,
                "height": height,
            }
            bytes_written += len(raw)
            if progress:
                progress(number, len(chosen), entry.name)

        stat = self.path.stat()
        manifest = {
            "format": 1,
            "archive_name": self.path.name,
            "archive_size": stat.st_size,
            "archive_mtime_ns": stat.st_mtime_ns,
            "seed": self.seed,
            "images_only": images_only,
            "entries": manifest_entries,
        }
        (output / IMAGE_MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )
        return ExtractReport(output, len(chosen), bytes_written)

    def _replacement_files(self, folder: Path) -> tuple[dict[str, Path], dict]:
        manifest: dict = {}
        manifest_path = folder / IMAGE_MANIFEST
        if not manifest_path.is_file():
            manifest_path = folder / LEGACY_IMAGE_MANIFEST
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise TacError("Le manifeste d'images est illisible.") from exc
        result: dict[str, Path] = {}
        for path in folder.rglob("*"):
            if not path.is_file() or path.name in {IMAGE_MANIFEST, LEGACY_IMAGE_MANIFEST}:
                continue
            relative = path.relative_to(folder).as_posix()
            result[relative.casefold()] = path
        return result, manifest

    def rebuild(
        self,
        replacement_folder: str | Path,
        output_path: str | Path,
        *,
        strict_dimensions: bool = True,
        progress: ProgressCallback | None = None,
        cancel: Event | None = None,
    ) -> RebuildReport:
        replacements_root = Path(replacement_folder).resolve()
        output = Path(output_path).resolve()
        if not replacements_root.is_dir():
            raise TacError("Le dossier de remplacements n'existe pas.")
        if output == self.path:
            raise TacError("Choisissez un nouveau fichier TAC : l'original ne sera jamais écrasé.")
        output.parent.mkdir(parents=True, exist_ok=True)
        files, manifest = self._replacement_files(replacements_root)
        by_name = {entry.name.replace("\\", "/").casefold(): entry for entry in self.entries}
        unknown = sorted(name for name in files if name not in by_name)
        if unknown:
            preview = ", ".join(unknown[:5])
            raise TacError(f"Fichier(s) absent(s) de l'archive : {preview}")

        old_manifest_entries = manifest.get("entries", {}) if isinstance(manifest, dict) else {}
        chosen: dict[int, Path] = {}
        hashes: dict[int, str] = {}
        dimensions: dict[int, tuple[int | None, int | None]] = {}
        for normalized, path in files.items():
            entry = by_name[normalized]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            old = old_manifest_entries.get(entry.name)
            if old and old.get("sha256") == digest:
                continue
            raw = path.read_bytes()
            if entry.is_image:
                width, height = _image_dimensions(raw)
                if width is None or height is None:
                    raise TacError(f"Image de remplacement invalide : {entry.name}")
                if strict_dimensions:
                    expected = (old or {}).get("width"), (old or {}).get("height")
                    if expected[0] is None or expected[1] is None:
                        original_width, original_height = _image_dimensions(self.read_entry(entry))
                        expected = original_width, original_height
                    if expected != (width, height):
                        raise TacError(
                            f"Dimensions différentes pour {entry.name} : {width}×{height}, "
                            f"attendu {expected[0]}×{expected[1]}."
                        )
                dimensions[entry.index] = (width, height)
            chosen[entry.index] = path
            hashes[entry.index] = digest
        if not chosen:
            raise TacError("Aucun fichier modifié n'a été détecté dans ce dossier.")

        packed_replacements: dict[int, bytes] = {}
        new_sizes: list[tuple[int, int]] = []
        for entry in self.entries:
            replacement = chosen.get(entry.index)
            if replacement is None:
                new_sizes.append((entry.unpacked_size, entry.stored_size))
                continue
            raw_size = replacement.stat().st_size
            if entry.packed:
                packed = zlib.compress(replacement.read_bytes())
                packed_replacements[entry.index] = packed
                new_sizes.append((raw_size, len(packed)))
            else:
                # Preserve the creator's 10240 marker for uncompressed images.
                new_sizes.append((entry.unpacked_size, raw_size))

        raw_index = bytearray()
        for bucket in self.buckets:
            raw_index += struct.pack("<HHi", bucket.hash_low, bucket.count, bucket.index)
        relative_offset = 0
        for entry, (unpacked_size, stored_size) in zip(self.entries, new_sizes):
            raw_index += struct.pack(
                "<QiIII",
                entry.hash_value >> 16,
                1 if entry.packed else 0,
                unpacked_size,
                relative_offset,
                stored_size,
            )
            relative_offset += stored_size
        compressed_index = zlib.compress(bytes(raw_index))
        encrypted_index = _crypt_le(compressed_index, INDEX_KEY, encrypt=True)
        header = bytearray(self.header)
        struct.pack_into("<I", header, 0x1C, len(encrypted_index))

        partial = output.with_name(output.name + ".partial")
        if partial.exists():
            partial.unlink()
        try:
            with self.path.open("rb") as source, partial.open("wb") as destination:
                destination.write(header)
                destination.write(encrypted_index)
                total = len(self.entries)
                for number, entry in enumerate(self.entries, start=1):
                    if cancel and cancel.is_set():
                        raise OperationCancelled("Reconstruction annulée.")
                    replacement = chosen.get(entry.index)
                    if replacement is None:
                        source.seek(entry.offset)
                        remaining = entry.stored_size
                        while remaining:
                            chunk = source.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise TacError(f"Archive source tronquée à {entry.name}.")
                            destination.write(chunk)
                            remaining -= len(chunk)
                    elif entry.packed:
                        destination.write(packed_replacements[entry.index])
                    else:
                        raw_size = replacement.stat().st_size
                        encrypted_size = min(10240, raw_size) if entry.is_image else raw_size
                        with replacement.open("rb") as repl:
                            prefix = repl.read(encrypted_size)
                            destination.write(_crypt_le(prefix, entry_key(entry.hash_value), encrypt=True))
                            shutil.copyfileobj(repl, destination, length=1024 * 1024)
                    if progress:
                        progress(number, total, entry.name)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(partial, output)
        except Exception:
            if partial.exists():
                partial.unlink()
            raise

        rebuilt = TacArchive(output, self.names_file)
        if len(rebuilt.entries) != len(self.entries):
            raise TacError("La vérification de l'archive reconstruite a échoué.")
        for index, replacement in chosen.items():
            actual = hashlib.sha256(rebuilt.read_entry(rebuilt.entries[index])).hexdigest()
            if actual != hashes[index]:
                raise TacError(f"Échec de vérification du remplacement : {self.entries[index].name}")
        return RebuildReport(output, len(chosen), len(self.entries) - len(chosen), output.stat().st_size)
