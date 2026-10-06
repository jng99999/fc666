"""Offline internal-consistency check; does not establish source authenticity."""
import json
from pathlib import Path
import sys
from core.portfolio.valuation import evaluate


def verify(value):
    if value!=evaluate(value['inputs']):raise ValueError('Portfolio report differs from reproduced snapshot')
    return {'status':value['status'],'accounts':len(value['rows']),'alerts':len(value['alerts'])}


if __name__=='__main__':
    path=Path(sys.argv[1])
    if path.stat().st_size>32*1024*1024:raise ValueError('Export exceeds 32 MiB')
    print('Verified Paper scenario:',verify(json.loads(path.read_text())))
