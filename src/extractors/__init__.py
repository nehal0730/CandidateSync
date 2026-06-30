"""extractors package — one module per source type."""
from .csv_extractor     import extract_from_csv
from .ats_extractor     import extract_from_ats_json
from .resume_extractor  import extract_from_resume
from .notes_extractor   import extract_from_notes

__all__ = [
    "extract_from_csv",
    "extract_from_ats_json",
    "extract_from_resume",
    "extract_from_notes",
]