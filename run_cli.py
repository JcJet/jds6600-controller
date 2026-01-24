from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jds_controller.commands import parse_csv_commands
from jds_controller.ports import find_first_jds6600, list_serial_ports
from jds_controller.runner import RunnerState, run_sequence
from jds_controller.util import KeyReader, KeyHelp, fmt_seconds


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jds6600_controller",
        description="Execute JDS6600 CSV commands. Keys: P pause/resume, Q quit, H help."
    )
    p.add_argument("--list-ports", action="store_true", help="List serial ports and exit.")
    p.add_argument("-p", "--port", default=None, help="Serial port like COM3 or /dev/ttyUSB0. If omitted, auto-detect.")
    p.add_argument("-f", "--csv", dest="csv_path", default=None, help="CSV file path. Default: commands.csv near script.")
    p.add_argument("--channel", choices=["1", "2", "both"], default="both", help="Default channel for freq steps.")
    p.add_argument("--dry-run", action="store_true", help="Do not connect to device; just print steps.")
    p.add_argument("--no-interactive", action="store_true", help="Disable key controls.")
    return p


def _print_ports() -> int:
    ports = list_serial_ports()
    if not ports:
        print("No serial ports found.")
        return 1

    # Linux: stable by-id symlinks are best for end users
    try:
        from jds_controller.ports import list_linux_by_id_ports  # type: ignore
        by_id = list_linux_by_id_ports()
    except Exception:
        by_id = []

    if by_id:
        print("Recommended (stable) ports (/dev/serial/by-id):")
        for p in by_id:
            print(f" * {p}")
        print("")

    print("All detected serial ports:")
    for p in ports:
        extra = []
        if p.vid is not None and p.pid is not None:
            extra.append(f"VID:PID={p.vid:04x}:{p.pid:04x}")
        if p.manufacturer:
            extra.append(p.manufacturer)
        if p.hwid and ("VID" not in p.hwid.upper()):
            extra.append(p.hwid)
        extras = (" | " + ", ".join(extra)) if extra else ""
        print(f" - {p.device} ({p.description}){extras}")

    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_ports:
        return _print_ports()

    script_dir = Path(__file__).resolve().parent
    csv_path = Path(args.csv_path) if args.csv_path else (script_dir / "commands.csv")

    try:
        steps = parse_csv_commands(csv_path)
    except Exception as e:
        print(f"CSV error: {e}", file=sys.stderr)
        return 2

    port = ""
    if not args.dry_run:
        try:
            port = find_first_jds6600(args.port)
        except Exception as e:
            print(str(e), file=sys.stderr)
            return 3

    state = RunnerState()

    def on_status(msg: str) -> None:
        print(msg)

    def on_progress(i: int, total: int, est_remaining: float, step) -> None:
        remaining_steps = (total - 1) - i
        print(
            f"Progress: {i+1}/{total} | remaining steps: {remaining_steps} | "
            f"est remaining wait time: {fmt_seconds(est_remaining)}"
        )

    interactive = not args.no_interactive
    help_keys = KeyHelp()

    if interactive:
        print(f"Interactive keys: {help_keys.pause}=pause/resume, {help_keys.quit}=quit, {help_keys.help}=help")

    if interactive:
        with KeyReader() as kr:

            def status_with_keys(msg: str) -> None:
                k = kr.get_key()
                if k:
                    kk = k.lower()
                    if kk == help_keys.pause.lower():
                        state.paused = not state.paused
                        print("== PAUSED ==" if state.paused else "== RESUMED ==")
                    elif kk == help_keys.quit.lower():
                        state.stopped = True
                        print("== STOP REQUESTED ==")
                    elif kk == help_keys.help.lower():
                        print(f"Keys: {help_keys.pause}=pause/resume, {help_keys.quit}=quit, {help_keys.help}=help")
                on_status(msg)

            return run_sequence(
                steps,
                port=port,
                dry_run=bool(args.dry_run),
                default_channel=args.channel,
                state=state,
                on_status=status_with_keys,
                on_progress=on_progress,
                tick_wait_updates=True,
            )

    return run_sequence(
        steps,
        port=port,
        dry_run=bool(args.dry_run),
        default_channel=args.channel,
        state=state,
        on_status=on_status,
        on_progress=on_progress,
        tick_wait_updates=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
