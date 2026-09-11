"""CLI handlers for ``argus policy``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.policy.operator_policy import (
    load_operator_policy,
    write_operator_policy_effective_artifact,
    write_operator_policy_effective_markdown,
)


def run_policy_command(args: Any) -> int:
    root = repo_root()
    if args.policy_command == "show-operator":
        pol = load_operator_policy(root)
        if args.json:
            print(dumps_json(pol))
            return 0
        json_path = write_operator_policy_effective_artifact(root)
        md_path = write_operator_policy_effective_markdown(root)
        print(f"Effective operator policy written:\n  {json_path}\n  {md_path}")
        return 0
    if args.policy_command == "experiment":
        from argus.policy.experiment import render_policy_experiment_markdown, run_policy_experiment

        paths = [Path(p) for p in getattr(args, "experiment_profiles", None) or []]
        payload = run_policy_experiment(
            root,
            profile_yaml_paths=paths or None,
            include_effective=not bool(getattr(args, "no_effective", False)),
            write_artifacts=not bool(getattr(args, "no_save", False)),
        )
        if args.json:
            print(dumps_json(payload))
            return 0
        if not getattr(args, "no_save", False):
            d = root / "runs" / "policy" / "experiments"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_policy_experiment_markdown(payload))
        return 0
    if args.policy_command == "feedback":
        from argus.policy.feedback import (
            render_operator_policy_feedback_markdown,
            run_operator_policy_feedback,
        )

        payload = run_operator_policy_feedback(
            root,
            limit_history=int(getattr(args, "limit_history", 50)),
            write_artifacts=not bool(getattr(args, "no_save", False)),
        )
        if args.json:
            print(dumps_json(payload))
            return 0
        if not getattr(args, "no_save", False):
            d = root / "runs" / "policy" / "feedback"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_operator_policy_feedback_markdown(payload))
        return 0
    if args.policy_command == "effectiveness":
        from argus.policy.effectiveness import (
            render_operator_policy_effectiveness_markdown,
            run_operator_policy_effectiveness,
        )

        payload = run_operator_policy_effectiveness(
            root,
            limit_history=int(getattr(args, "limit_history", 50)),
            write_artifacts=not bool(getattr(args, "no_save", False)),
        )
        if args.json:
            print(dumps_json(payload))
            return 0
        if not getattr(args, "no_save", False):
            d = root / "runs" / "policy" / "effectiveness"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_operator_policy_effectiveness_markdown(payload))
        return 0
    if args.policy_command == "recommend":
        from argus.policy.recommendations import (
            render_operator_policy_recommendations_markdown,
            run_operator_policy_recommendations,
        )

        pd = getattr(args, "products_dir", None)
        pd = pd.resolve() if pd is not None else None
        payload = run_operator_policy_recommendations(
            root,
            limit_history=int(getattr(args, "limit_history", 50)),
            products_dir=pd,
            write_artifacts=not bool(getattr(args, "no_save", False)),
        )
        if args.json:
            print(dumps_json(payload))
            return 0
        if not getattr(args, "no_save", False):
            d = root / "runs" / "policy" / "recommendations"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_operator_policy_recommendations_markdown(payload))
        return 0
    if args.policy_command == "learning-synthesis":
        from argus.policy.learning_synthesis import (
            render_operator_learning_synthesis_markdown,
            run_operator_learning_synthesis,
        )

        pd = getattr(args, "products_dir", None)
        pd = pd.resolve() if pd is not None else None
        payload = run_operator_learning_synthesis(
            root,
            limit_history=int(getattr(args, "limit_history", 50)),
            products_dir=pd,
            write_artifacts=not bool(getattr(args, "no_save", False)),
        )
        if args.json:
            print(dumps_json(payload))
            return 0
        if not getattr(args, "no_save", False):
            d = root / "runs" / "policy" / "learning_synthesis"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_operator_learning_synthesis_markdown(payload))
        return 0
    print("Unknown policy subcommand", file=sys.stderr)
    return 2
