"""Capture or check a selected revocation head; reference trust is external."""
import argparse,json,os,stat
from pathlib import Path
from sqlalchemy import create_engine
from apps.api.settings import Settings
from core.paper import revocation_checkpoint as checkpoint
from core.paper.fault_adapter import encoded
from core.exchange.private_spot import unique_object


def reject_constant(_):raise ValueError('Nonfinite JSON')


def read(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Regular protected reference required')
        raw=stream.read(checkpoint.MAX_BYTES+1)
    if len(raw)>checkpoint.MAX_BYTES:raise ValueError('Oversize selected checkpoint')
    return checkpoint.verify(json.loads(raw,object_pairs_hook=unique_object,parse_constant=reject_constant))


def write(path,value):
    value=checkpoint.verify(value)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(encoded(value).encode());stream.flush();os.fsync(stream.fileno())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    capture=sub.add_parser('capture');capture.add_argument('--output',type=Path,required=True)
    check=sub.add_parser('check');check.add_argument('--expected',type=Path,required=True)
    args=parser.parse_args()
    try:
        expected=read(args.expected) if args.command=='check' else None
        engine=create_engine(Settings().database_url.get_secret_value(),hide_parameters=True)
        try:
            if args.command=='capture':
                value=checkpoint.capture(engine);write(args.output,value)
                result={'version':checkpoint.VERSION,'reference_captured':True,'revision':value['revision'],
                        'currentness_independently_verified':False,'external_submission_allowed':False}
            else:result=checkpoint.check(engine,expected)
        finally:engine.dispose()
    except Exception:
        parser.exit(1,'Revocation checkpoint operation failed; inspect trusted reference and complete database evidence.\n')
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
