from pathlib import Path
from ebookmetafile.models import BookRecord

# Case 1: output_pattern that doesn't include directory (filename only)
rec = BookRecord(id=1, filepath=Path(r"S:\ebooks\scifi\Card - Ender 06 - book.mobi"))
rec.metadata_file = {"author": "Card, Orson Scott", "title": "Shadow of the Hegemon"}
rec.chosen_metadata = {"author": "Card, Orson Scott", "title": "Shadow of the Hegemon"}
rec.output_pattern = "{author} - {title}{ext}"
rec.recompute_new_filepath()
print("output_pattern:", rec.output_pattern)
print("new_filepath:", rec.new_filepath)
print("new_filepath.parent:", rec.new_filepath.parent)

# Case 2: output_pattern includes an absolute path
rec2 = BookRecord(id=2, filepath=Path(r"S:\ebooks\scifi\file.epub"))
rec2.metadata_file = {"author": "A", "title": "B"}
rec2.chosen_metadata = {"author": "A", "title": "B"}
rec2.output_pattern = r"D:\outdir\{author} - {title}{ext}"
rec2.recompute_new_filepath()
print("output_pattern:", rec2.output_pattern)
print("new_filepath:", rec2.new_filepath)
print("new_filepath.parent:", rec2.new_filepath.parent)
