"""Actual Learning activity endpoint; no Study database or broker is needed."""
from pathlib import Path
import os
from tempo_learning_runtime.api import create_app
from tempo_learning_runtime.config import Configuration
app = create_app(Configuration('saturation-control', 'postgresql://unused/learning', 'postgresql://reader/learning',
    Path(os.environ['TEMPO_SATURATION_TOKEN_FILE']).read_text(), origins=(os.environ['TEMPO_SATURATION_ORIGIN'],),
    maintenance=False, host_activity_directory=Path(os.environ['TEMPO_STUDY_ACTIVITY_DIRECTORY'])))
