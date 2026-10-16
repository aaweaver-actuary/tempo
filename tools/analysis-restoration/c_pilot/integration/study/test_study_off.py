"""One actual F2 review/save/reload after verified installed Intelligence shutdown."""
import json
import os
from pathlib import Path
from test_study_ui import test_production_study_learning_native as _prove_study


def test_C_optional_off_study(case,tmp_path):
    receipt=json.loads(Path(os.environ['TEMPO_C_OPTIONAL_STOP_RECEIPT']).read_text())
    assert receipt['component']=='Intelligence' and receipt['installed'] and receipt['report_read'] and receipt['server_stopped']
    _prove_study(case,tmp_path,'normal')
