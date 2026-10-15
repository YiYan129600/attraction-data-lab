import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import re
import sqlite3
import sys
import uuid
from pathlib import Path

from .config import load_config
from .providers import ReplayProvider, RestProvider, ProviderError
from .runner import Runner, report
from .state import State, service_lock


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline-first, budgeted content research collector")
    parser.add_argument("command", choices=("doctor", "plan", "run", "resume", "report"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--provider", choices=("replay", "xhs-rest"))
    parser.add_argument("--captures")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--run")
    parser.add_argument("--max-candidates", type=int)
    parser.add_argument("--retry-blocked", action="store_true")
    args = parser.parse_args(argv)
    state = None
    try:
        config = load_config(args.config)
        if args.provider:
            config.provider = args.provider
        if args.captures:
            config.captures = args.captures
        if args.max_candidates is not None:
            config.max_candidates = args.max_candidates
        config.validate()
        if args.retry_blocked and args.command != "resume":
            raise ValueError("--retry-blocked is only for explicit resume after conditions change")
        if args.command in {"doctor", "plan"} and not args.live:
            result = {"offline": True, "scope": config.scope, "provider": config.provider,
                      "queries": list(config.queries), "max_candidates": config.max_candidates,
                      "credential_read": False, "network_calls": 0}
            if config.provider == "replay" and config.captures and Path(config.captures).exists():
                result["replay"] = ReplayProvider(config, config.captures).probe()
            elif config.provider == "replay":
                result["captures_status"] = "not_found"
            if args.command == "plan":
                count = min(config.max_candidates, result.get("replay", {}).get("notes", config.max_candidates))
                result["upper_bounds"] = {"search_operations": len(config.queries), "unique_candidates": count,
                                          "screen_profile_slots": count * 8, "deep_notes": min(config.max_deep_notes, count),
                                          "total_deep_profile_slots": min(config.max_deep_notes, count) * 30}
                result["counts_are_capacity_not_completed_work"] = True
        elif args.command == "doctor":
            if config.provider != "xhs-rest":
                raise ValueError("live doctor requires xhs-rest")
            with service_lock(config):
                provider = RestProvider(config, live=True)
                health = provider.request("/health")
                result = {"health_response_received": True, "probe": provider.probe(), "network_calls": 2}
        else:
            if args.command in {"resume", "report"} and not args.run:
                raise ValueError("resume/report requires --run")
            run_id = args.run or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", run_id):
                raise ValueError("invalid run identifier")
            with service_lock(config):
                state = State(config, run_id, create=args.command == "run")
                if args.command == "report":
                    result = report(config, state)
                else:
                    if config.provider == "replay":
                        if args.live or not config.captures:
                            raise ValueError("replay requires captures and cannot use --live")
                        provider = ReplayProvider(config, config.captures)
                    else:
                        provider = RestProvider(config, live=args.live)
                    result = Runner(config, state, provider, retry_blocked=args.retry_blocked).run()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if args.command in {"run", "resume"} and result.get("stop_reason"):
            return 4 if result["stop_reason"] in {"max_requests", "max_profile_visits", "max_seconds"} else 3
        return 0
    except ProviderError as exc:
        print(json.dumps({"error": exc.category, "http_status": exc.status, "input_contents_disclosed": False}), file=sys.stderr)
        return 3
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
        print('{"error":"collector configuration, source schema or local output failed","input_contents_disclosed":false}', file=sys.stderr)
        return 2
    finally:
        if state:
            state.close()


if __name__ == "__main__":
    raise SystemExit(main())
