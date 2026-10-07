"""Offline reproduction of hint-only Paper portfolio risk history."""
import json
import sys
from pathlib import Path
from core.portfolio.risk_history import verify
from core.portfolio.continuity import MAX_BYTES

if __name__=='__main__':
    path=Path(sys.argv[1])
    if path.stat().st_size>MAX_BYTES:raise ValueError('Export exceeds 32 MiB')
    print('Verified Paper risk history:',verify(json.loads(path.read_text())))
