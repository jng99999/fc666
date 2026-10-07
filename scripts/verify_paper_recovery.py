"""Reproduce a read-only Paper recovery export, without a database."""
import json
import sys
from pathlib import Path
from core.paper.recovery import verify,MAX_BYTES

if __name__=='__main__':
    path=Path(sys.argv[1])
    if path.stat().st_size>MAX_BYTES:raise ValueError('Export exceeds 32 MiB')
    print('Verified Paper recovery:',verify(json.loads(path.read_text())))
