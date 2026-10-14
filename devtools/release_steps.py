"""Original release operations; explicit isolated destinations, no production writer."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation",choices=("migration","installed-wheel"))
    parser.add_argument("--work",type=Path,required=True)
    parser.add_argument("--wheel-receipt",type=Path)
    args=parser.parse_args()
    target=args.work.resolve()
    assert not target.exists() and not any((p/".git").exists() for p in (target,*target.parents))
    if args.operation=="migration":
        from alembic import command
        from alembic.config import Config
        target.mkdir(parents=True)
        config=Config(str(ROOT/"alembic.ini"))
        config.set_main_option("script_location",str(ROOT/"migrations"))
        config.set_main_option("sqlalchemy.url",("sqlite:///"+(target/"fresh.sqlite").as_posix()).replace("%","%%"))
        command.upgrade(config,"head")
        command.check(config)
        print("FULL_RELEASE_FRESH_MIGRATION_PASS")
    else:
        assert args.wheel_receipt is not None
        value=json.loads(args.wheel_receipt.read_bytes())
        subprocess.run([sys.executable,"-I","-u",str(ROOT/"scripts/wheel_e2e.py"),value["wheel"],"--work-dir",str(target)],cwd=ROOT,check=True)


if __name__=="__main__":
    main()
