"""Verify a bounded completed Paper intent export without DB/network access."""
import argparse,json
from pathlib import Path
from core.paper import intents

def main():
    parser=argparse.ArgumentParser();parser.add_argument('file',type=Path);args=parser.parse_args()
    with args.file.open('rb') as stream:raw=stream.read(intents.MAX_BYTES+1)
    if len(raw)>intents.MAX_BYTES:raise ValueError('Intent export exceeds 32 MiB')
    print('Verified Paper intents:',intents.verify(json.loads(raw)))

if __name__=='__main__':main()
