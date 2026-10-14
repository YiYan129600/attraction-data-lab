import argparse
import json
import sys
import sqlite3
from pathlib import Path

from .core import SELECTION_POLICIES, analyze, import_records, init_db, read_input, sample_users, select_candidates, validate_db


def main(argv=None):
    parser=argparse.ArgumentParser(description="Offline evidence-aware Xiaohongshu research")
    commands=parser.add_subparsers(dest="command",required=True)
    for name in ("init","import","analyze","select","sample","validate"):
        cmd=commands.add_parser(name)
        cmd.add_argument("--db",required=True)
        cmd.add_argument("--output")
        if name in {"analyze","select","sample","validate"}:
            cmd.add_argument("--study",required=name!="validate")
            cmd.add_argument("--source-mode",choices=("real","synthetic"),default="real")
            cmd.add_argument("--owner")
        if name=="import":
            cmd.add_argument("--input",required=True)
        if name=="analyze":
            cmd.add_argument("--selection-policy",choices=sorted(SELECTION_POLICIES))
            cmd.add_argument("--minimum-platform-comments",type=int)
        if name in {"select","sample"}:
            cmd.add_argument("--limit",type=int,default=24 if name=="select" else 30)
            cmd.add_argument("--seed",default="initial-v1")
        if name=="sample":
            cmd.add_argument("--note",required=True)
    args=parser.parse_args(argv)
    try:
        if getattr(args,"limit",0)<0:
            raise ValueError("limit must be nonnegative")
        if args.command=="init": result=init_db(args.db)
        elif args.command=="import": result=import_records(args.db,read_input(args.input))
        elif args.command=="analyze": result=analyze(args.db,args.study,args.source_mode,args.owner,args.selection_policy,args.minimum_platform_comments)
        elif args.command=="select": result=select_candidates(args.db,args.study,args.source_mode,args.limit,args.seed,args.owner)
        elif args.command=="sample": result=sample_users(args.db,args.study,args.note,args.source_mode,args.limit,args.seed,args.owner)
        else: result=validate_db(args.db,args.study,args.source_mode)
        rendered=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
        if args.output:
            Path(args.output).parent.mkdir(parents=True,exist_ok=True)
            Path(args.output).write_text(rendered+"\n",encoding="utf-8")
        else:
            print(rendered)
        return 1 if args.command=="validate" and not result["valid"] else 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({"error":str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
