import hashlib
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 1024 * 1024

PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Extension -> (canonical content type, leading magic bytes)
ALLOWED_TYPES: dict[str, tuple[str, bytes]] = {
    ".pdf": (PDF_CONTENT_TYPE, b"%PDF-"),
    ".docx": (DOCX_CONTENT_TYPE, b"PK\x03\x04"),
}


class UploadError(Exception):
    """Base class for rejected uploads."""


class EmptyFileError(UploadError):
    pass


class UnsupportedFileTypeError(UploadError):
    pass


class FileTooLargeError(UploadError):
    pass


@dataclass(frozen=True)
class StoredFile:
    storage_key: str
    content_type: str
    size_bytes: int
    sha256: str


class LocalFileStorage:
    """Stores uploads under a local directory. Swapped for S3 on deploy."""

    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root
        self.max_bytes = max_bytes

    def path_for(self, storage_key: str) -> Path:
        return self.root / storage_key

    def save_contract(self, filename: str, stream: BinaryIO) -> StoredFile:
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_TYPES:
            raise UnsupportedFileTypeError("Only .pdf and .docx files are accepted")
        content_type, magic = ALLOWED_TYPES[extension]

        head = stream.read(len(magic))
        if not head:
            raise EmptyFileError("Uploaded file is empty")
        if head != magic:
            raise UnsupportedFileTypeError(f"File content does not match {extension}")

        # Never use the client filename as a path: generate the key server-side.
        storage_key = f"{uuid.uuid4()}{extension}"
        destination = self.path_for(storage_key)
        self.root.mkdir(parents=True, exist_ok=True)

        digest = hashlib.sha256()
        size = 0
        try:
            with destination.open("wb") as out:
                chunk = head
                while chunk:
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise FileTooLargeError(
                            f"File exceeds the {self.max_bytes // (1024 * 1024)} MB limit"
                        )
                    digest.update(chunk)
                    out.write(chunk)
                    chunk = stream.read(CHUNK_SIZE)
            if extension == ".docx":
                _check_docx(destination)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

        return StoredFile(
            storage_key=storage_key,
            content_type=content_type,
            size_bytes=size,
            sha256=digest.hexdigest(),
        )

    def delete(self, storage_key: str) -> None:
        self.path_for(storage_key).unlink(missing_ok=True)


def _check_docx(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            if "word/document.xml" not in archive.namelist():
                raise UnsupportedFileTypeError("ZIP archive is not a Word document")
    except zipfile.BadZipFile as exc:
        raise UnsupportedFileTypeError("File is not a valid .docx") from exc
