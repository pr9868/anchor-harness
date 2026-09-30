"""Compile a public JSON workflow manifest without executing or writing state."""
import argparse
import json
from pathlib import Path
from . import WorkflowManifest, compile_workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--mode", choices=("quick", "full", "deep"), default="full")
    args = parser.parse_args()
    try:
        value = json.loads(args.manifest.read_text())
        plan = compile_workflow(WorkflowManifest.from_dict(value), mode=args.mode)
    except (OSError, ValueError, TypeError) as error:
        parser.exit(2, f"Invalid workflow: {error}\n")
    print(plan.canonical_json())


if __name__ == "__main__":
    main()
