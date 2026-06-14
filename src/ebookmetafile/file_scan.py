from pathlib import Path
from typing import List

EBOOK_EXTENSIONS = {".epub", ".mobi", ".azw3", ".azw", ".prc", ".pdf"}


def scan_ebooks(dir_in: Path) -> List[Path]:
    """
    Recursively scan dir_in for ebook files with supported extensions.
    """
    files: List[Path] = []
    for path in dir_in.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in EBOOK_EXTENSIONS:
            files.append(path)
    return files
