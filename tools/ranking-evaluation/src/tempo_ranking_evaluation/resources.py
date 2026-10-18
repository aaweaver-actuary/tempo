"""Resources adjacent to this installed module, independent of checkout layout."""
from pathlib import Path


def installed_corpus_directory() -> Path:
    # The supported wheel installation is unpacked. No dynamic importer is needed.
    path=Path(__file__).with_name('corpus')
    if not path.is_dir():raise ValueError('Packaged corpus directory is unavailable')
    return path
