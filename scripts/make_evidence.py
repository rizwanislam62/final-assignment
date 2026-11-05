"""Regenerate the command-line output examples referenced in the report.

    python scripts/make_evidence.py

Each command's console output is saved to ``output/evidence/<name>.txt``.
Bookings made by the demo go to ``output/evidence/demo_bookings.json`` (via a
temporary settings file), so the real ``state/bookings.json`` is not touched.
"""

from __future__ import annotations

import builtins
import contextlib
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from busbidder.cli import main  # noqa: E402

OUT = ROOT / "output" / "evidence"


def run(name: str, args: list[str], inputs: list[str] | None = None) -> str:
    """Run one CLI command in-process, answering prompts from ``inputs``, and save the transcript."""
    buffer = io.StringIO()
    feed = iter(inputs or [])

    def fake_input(prompt=""):
        answer = next(feed, "")
        buffer.write(f"{prompt}{answer}\n")
        return answer

    original_input = builtins.input
    builtins.input = fake_input
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main(args)
    finally:
        builtins.input = original_input
    shown = " ".join(a.replace(str(ROOT) + os.sep, "").replace(os.sep, "/") for a in args)
    text = f"$ python main.py {shown}\n{buffer.getvalue()}\n[exit code {code}]\n"
    (OUT / f"{name}.txt").write_text(text, encoding="utf-8")
    print(f"wrote {name}.txt (exit {code})")
    return text


def main_evidence() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    settings["files"]["bookings"] = str(OUT / "demo_bookings.json")
    demo_cfg = OUT / "settings_demo.json"
    demo_cfg.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    (OUT / "demo_bookings.json").unlink(missing_ok=True)
    cfg = ["--config", str(demo_cfg)]

    journey = ["--to", "Malaga", "--from-date", "2027-07-12", "--to-date", "2027-07-14"]
    run("01_validate", cfg + ["validate"])
    run("02_criteria", cfg + ["criteria"])
    run("03_example_search", cfg + ["search", *journey, "--budget", "50", "--depart", "morning",
                                    "--max-transfers", "1", "--changeable",
                                    "--weights", "price=40,duration=25,departure=15,flexibility=20",
                                    "--show-removed", "10", "--top", "10",
                                    "--export", str(OUT / "ranking_example.csv")])
    run("04_compare_profiles_malaga", cfg + ["compare", *journey, "--explain",
                                             "--export-dir", str(OUT / "rankings_malaga")])
    run("05_compare_profiles_barcelona", cfg + ["compare", "--to", "Barcelona", "--from-date", "2027-07-12",
                                                "--to-date", "2027-07-14"])
    run("06_search_and_book", cfg + ["search", *journey, "--profile", "example", "--top", "5", "--seats", "2",
                                     "--book"], ["1", "Ana Lopez", "y"])
    booking = json.loads((OUT / "demo_bookings.json").read_text(encoding="utf-8"))["bookings"][0]
    run("07_bookings_list", cfg + ["bookings"])
    run("08_cancel", cfg + ["cancel", booking["reference"]])
    run("09_overbooking_refused", cfg + ["book", "--offer", "SOL-BAR-0001", "--name", "Ola Nordmann"])
    run("10_invalid_weights", cfg + ["search", *journey, "--weights", "price=50,duration=25,departure=15,flexibility=20"])
    run("11_unknown_destination", cfg + ["search", "--to", "Malga", "--from-date", "2027-07-12"])
    run("12_no_acceptable_offer", cfg + ["search", *journey, "--budget", "10", "--show-removed", "5"])


if __name__ == "__main__":
    main_evidence()
