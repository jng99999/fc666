"""Reproduce sampled equity and observed-point drawdown from exported inputs."""
import json
import sys
from pathlib import Path
from core.portfolio.sampled import verify
from core.portfolio.continuity import MAX_BYTES

if __name__=='__main__':
    path=Path(sys.argv[1])
    if path.stat().st_size>MAX_BYTES:raise ValueError('Export exceeds 32 MiB')
    print('Verified Paper sampled observations:',verify(json.loads(path.read_text())))
