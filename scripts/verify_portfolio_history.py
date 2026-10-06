"""Reproduce an exported immutable history envelope without a database."""
import json
import sys
from pathlib import Path
from core.portfolio.history import verify_export as verify


if __name__=='__main__':
    path=Path(sys.argv[1])
    if path.stat().st_size>32*1024*1024:raise ValueError('Export exceeds 32 MiB')
    print('Verified Paper history:',verify(json.loads(path.read_text())))
