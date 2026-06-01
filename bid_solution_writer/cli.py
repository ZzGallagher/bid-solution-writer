from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import generate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bid_solution_writer", description="Generate the UAV inspection proposal document.")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="Generate proposal docx and records.")
    gen.add_argument("--input", type=Path, default=Path("input/高压线路无人机巡检方案技术要求.md"))
    gen.add_argument("--template", type=Path, default=Path("output/示例输出.docx"))
    gen.add_argument("--output", type=Path, default=Path("output/高压线路无人机巡检方案设计方案.docx"))
    gen.add_argument("--records-dir", type=Path, default=Path("output/records"))
    gen.add_argument("--renderer-command", default=None)
    gen.add_argument("--allow-local-draft", action="store_true", help="Use deterministic local draft text instead of calling the external API.")
    args = parser.parse_args(argv)

    if args.command == "generate":
        result = generate(
            input_path=args.input,
            template_path=args.template,
            output_path=args.output,
            records_dir=args.records_dir,
            renderer_command=args.renderer_command,
            allow_local_draft=args.allow_local_draft,
        )
        print(f"generated: {result['output']}")
        print(f"records: {result['records_dir']}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
