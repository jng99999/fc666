"""Protected local read-only artifacts, with sanitized CLI failures."""
import argparse
from core.exchange.private_spot import ReadOnlySpot
from core.exchange.private_snapshot import credentials_file,capture,compare,read,write


def main():
    parser=argparse.ArgumentParser(description='Read-only private Spot snapshot/difference tool')
    sub=parser.add_subparsers(dest='command',required=True)
    cap=sub.add_parser('capture');cap.add_argument('--credentials-file',required=True);cap.add_argument('--scope',required=True);cap.add_argument('--symbol',choices=['BTCUSDT','ETHUSDT'],required=True);cap.add_argument('--from-id',type=int,default=0);cap.add_argument('--output',required=True)
    diff=sub.add_parser('compare');diff.add_argument('--before',required=True);diff.add_argument('--after',required=True);diff.add_argument('--output',required=True)
    args=parser.parse_args()
    try:
        value=capture(ReadOnlySpot(credentials_file(args.credentials_file)),args.scope,args.symbol,args.from_id) if args.command=='capture' else compare(read(args.before),read(args.after))
        write(args.output,value)
    except Exception:parser.exit(1,'Private operation failed; check file permissions, input coverage and destination access.\n')
    print('Protected read-only artifact saved; identity and full reconciliation remain unverified.')


if __name__=='__main__':main()
