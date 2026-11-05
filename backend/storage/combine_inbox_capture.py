"""Create a separate authenticated staging baseline; never modify sources."""
import argparse,sys
from backend.storage.migrate import key_from_env
from backend.storage.project_inbox import combine_initial_capture

def main(argv=None):
    parser=argparse.ArgumentParser(description='Combine source and marked inbox capture for staging only')
    parser.add_argument('--source',required=True);parser.add_argument('--capture',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:
        source,capture=combine_initial_capture(args.source,args.capture,args.output,key_from_env())
        print(f'Encrypted staging baseline prepared: {source} source and {capture} marked capture records.')
    except Exception:
        print('Staging baseline could not be prepared; review private inputs and configuration.',file=sys.stderr);return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
