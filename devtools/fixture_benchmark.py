"""Compare real cold schema initialization with fresh copies of immutable empty images."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from football_system.infrastructure.database.session import create_database_engine,create_schema  # noqa: E402
from football_system.infrastructure.database.migrations import upgrade_database  # noqa: E402
from tests.support.empty_database import fresh_empty_database,_image  # noqa: E402
from devtools.measure import hardware,environment  # noqa: E402


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--repetitions",type=int,default=5)
    args=parser.parse_args()
    assert args.work.parent.is_dir() and args.output.parent.is_dir() and args.repetitions>=3
    assert not any((p/".git").exists() for p in (args.work.resolve(),*args.work.resolve().parents))
    args.work.mkdir(exist_ok=False)
    results=[]
    for kind in ("metadata","migrated"):
        _image.cache_clear()
        cold=[]
        for index in range(args.repetitions):
            path=args.work/(kind+"-cold-"+str(index)+".sqlite")
            start=time.perf_counter()
            if kind=="migrated":
                upgrade_database("sqlite:///"+path.as_posix(),ROOT/"alembic.ini")
            else:
                engine=create_database_engine("sqlite:///"+path.as_posix())
                try:
                    create_schema(engine)
                finally:
                    engine.dispose()
            cold.append(time.perf_counter()-start)
        warm=[]
        for index in range(args.repetitions+1):
            start=time.perf_counter()
            assert fresh_empty_database(args.work/(kind+"-clone-"+str(index)+".sqlite"),kind=kind)
            warm.append(time.perf_counter()-start)
        results.append(dict(kind=kind,cold_seconds=cold,cache_population_seconds=warm[0],fresh_clone_seconds=warm[1:],
            cold_median_seconds=statistics.median(cold),clone_median_seconds=statistics.median(warm[1:]),
            scope="EMPTY_INITIALIZATION_ONLY_NOT_FULL_TEST_SPEEDUP"))
    result=dict(status="PASS_EMPTY_FIXTURE_INITIALIZATION_BENCHMARK",at_utc=datetime.now(timezone.utc).isoformat(),
        hardware=hardware(),packages=environment(),results=results,mutable_database_shared=False,populated_model_or_source_cached=False)
    with args.output.open("x",encoding="utf-8") as stream:
        json.dump(result,stream,indent=2)
    print(json.dumps(results,indent=2))


if __name__=="__main__":
    main()
