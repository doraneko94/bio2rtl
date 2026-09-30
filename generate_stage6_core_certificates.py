from __future__ import annotations
from pathlib import Path
import json
import os

from bio2rtl.stage6_generic_certificates import generate_stage6_generic

ROOT = Path(__file__).resolve().parent
SEM = ROOT / 'build/semantic'
OUT = Path(os.environ.get('BIO2RTL_CERT_OUT', ROOT / 'build/generated_certificates'))
OUT.mkdir(parents = True, exist_ok = True)

report = generate_stage6_generic(SEM, OUT)
(ROOT / 'build/stage6_core_certificate_report.json').write_text(json.dumps(report, indent = 2, sort_keys = True) + '\n')
print(json.dumps(report, indent = 2, sort_keys = True))
