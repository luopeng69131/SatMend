"""Command-line interface for scenario preparation and simulation."""

from argparse import ArgumentParser
from dataclasses import asdict
import json
from pathlib import Path

from .method import Action, plan_sessions
from .scenario import load_scenario, scenario_to_dict
from .simulator import simulate


def main(argv=None):
    parser = ArgumentParser(prog="satmend", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="normalize a scenario and its optional contact CSV")
    run = commands.add_parser("run", help="simulate a scenario with the reference planner or a supplied plan")
    for command in (prepare, run):
        command.add_argument("scenario", type=Path, help="scenario JSON path")
        command.add_argument("--contacts", type=Path, help="CSV overriding the ground contact calendar")
        command.add_argument("--output", type=Path, help="write JSON to this path instead of stdout")
    run.add_argument("--actions", type=Path, help="JSON array of Action objects")
    run.add_argument("--slots", type=int, default=2, help="number of ground repair slots (default: 2)")
    run.add_argument("--diagnosis-s", type=float, default=2.0)
    run.add_argument("--reuse-diagnosis-s", type=float, default=1.0)
    run.add_argument("--validation-s", type=float, default=1.0)
    run.add_argument("--state-recovery-s", type=float, default=0.5)
    args = parser.parse_args(argv)

    try:
        scenario = load_scenario(args.scenario, args.contacts)
        if args.command == "prepare":
            result = scenario_to_dict(scenario)
        else:
            if args.actions:
                rows = json.loads(args.actions.read_text(encoding="utf-8"))
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ValueError("Actions must be a JSON array of objects")
                actions = tuple(Action(**row) for row in rows)
            else:
                actions = plan_sessions(
                    scenario, slots=args.slots, diagnosis_s=args.diagnosis_s,
                    reuse_diagnosis_s=args.reuse_diagnosis_s, validation_s=args.validation_s,
                    state_recovery_s=args.state_recovery_s,
                )
            result = simulate(scenario, actions)
            result["planned_actions"] = [asdict(action) for action in actions]
        output = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"satmend: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
