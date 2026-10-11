import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xv", description="Cross-vendor headless CLI jobs (codex / agy).")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.add_parser("start", help="start a new job (codex|agy)")
    sub.add_parser("ask", help="send a follow-up round to a job")
    sub.add_parser("wait", help="wait for the current round to finish")
    sub.add_parser("show", help="print a round's response")
    sub.add_parser("list", help="list jobs")
    sub.add_parser("stop", help="stop the current round (session kept)")
    sub.add_parser("close", help="close a finished job's pane")
    sub.add_parser("watch", help="follow the current round in this terminal")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_usage()
        raise SystemExit(2)
