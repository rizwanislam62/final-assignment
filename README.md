# BusBidder Spain

A command-line Python application that filters and ranks competing bus offers from Madrid to fifteen Spanish
destinations, explains the recommendation, compares traveller profiles, and simulates bookings.

ACIT4420 Problem-solving with scripting, final project, OsloMet 2026.

## Requirements

| Item | Value |
|---|---|
| Python | 3.10 or newer (developed and tested on **Python 3.12.10**, Windows 11) |
| Application libraries | Python standard library only |
| Test library | `pytest` (developed with pytest 9.2.0) |
| Report / diagram tools (optional) | `matplotlib`, `reportlab` |

Install the dependencies from the project folder:

```
python -m pip install -r requirements.txt
```

## Running the program

The entry point is **`main.py`** in the project root. Run every command from the project folder.

```
python main.py --help
```

Running `python main.py` with no arguments starts a guided interactive search and booking.

### The specification example

This is the example request from the specification: Madrid to Malaga, 12 to 14 July, EUR 50, morning,
at most 1 transfer, changeable ticket, weights 40/25/15/20.

```
python main.py search --to Malaga --from-date 2027-07-12 --to-date 2027-07-14 --budget 50 --depart morning --max-transfers 1 --changeable --weights price=40,duration=25,departure=15,flexibility=20
```

The same request is stored as a profile, so this shorter command gives the same result:

```
python main.py search --to Malaga --from-date 2027-07-12 --to-date 2027-07-14 --profile example
```

Add `--book` to choose an offer and book it after viewing the ranking. Add `--show-removed` to list the offers
removed by hard constraints with their reasons. Add `--export file.csv` to save the full ranking.

### All commands

| Command | Purpose |
|---|---|
| `search` | Filter and rank offers for one traveller, explain the top offer, optionally `--book` |
| `compare` | Rank the same offers for several traveller profiles (`--explain`, `--export-dir`) |
| `book --offer ID --name NAME [--seats N]` | Book an offer directly |
| `bookings` | List all bookings |
| `show REFERENCE` | Show one booking |
| `cancel REFERENCE` | Cancel a booking, with a simulated refund or travel credit |
| `validate` | Data-quality report: accepted, corrected, rejected and review records |
| `criteria` | List the ranking criteria and hard constraints |
| `profiles` | List the traveller profiles |
| `interactive` | Guided search and booking (also the default with no arguments) |

Example of the profile comparison:

```
python main.py compare --to Malaga --from-date 2027-07-12 --to-date 2027-07-14 --explain
```

Hard constraints are `--budget`, `--max-transfers`, `--changeable`, `--refundable`, `--luggage`,
`--max-duration` and `--seats`. Soft preferences are `--depart` and `--weights`. The available weight names are
`price`, `duration`, `departure`, `flexibility`, `transfers`, `rating` and `emissions`. Weights must total 100,
or 1.0 as fractions, and the four core criteria must each have a positive weight.

## Input files and locations

| File | Content |
|---|---|
| `data/companies.csv` | The five companies |
| `data/destinations.csv` | The fifteen destinations, with road distance from Madrid and a coastal flag |
| `data/offers.csv` | 1,872 offer records for 10 to 16 July 2027, including 16 deliberately faulty records |
| `config/settings.json` | File paths, validation limits, scoring parameters, default weights, simulated clock |
| `config/profiles.json` | Traveller profiles: example, budget, business, flexible and eco |

`data/offers.csv` is produced by `python scripts/generate_offers.py`. The generator uses a fixed seed, so
re-running it reproduces the same file.

The simulated clock in `config/settings.json` is set to 2027-07-01 09:00. Bookings are therefore always made
"before departure", and booking timestamps are reproducible. Delete the `now` entry to use the real clock.

## Files written by the program

| File | Content |
|---|---|
| `logs/busbidder.log` | Log of important events and errors |
| `output/rejected_offers.csv` | Rejected records with category and reason |
| `output/review_queue.csv` | Inconsistent records placed aside for human review |
| `state/bookings.json` | Saved bookings. Delete this file to reset all bookings |

## Running the tests

```
python -m pytest
```

The suite contains 190 tests. They cover normal cases, invalid values, missing data and missing files,
boundary values, ranking reproducibility, booking and the command-line interface. Tests use temporary copies of
the data, so they never change `state/` or `output/`.

## Reproducing the report evidence

```
python scripts/make_evidence.py      # command transcripts  -> output/evidence/
python scripts/run_experiments.py    # experiments E1-E5    -> output/experiments/
python scripts/draw_diagrams.py      # diagrams             -> docs/
python scripts/build_report.py       # PDF report           -> report/BusBidder_Spain_Report.pdf
```

## Project structure

```
main.py                 entry point
busbidder/
  cli.py                command-line interface (argparse) and output formatting
  config.py             settings.json loading and validation
  models.py             domain classes (Company, Destination, FareConditions, Offer, TravelRequest ...)
  validation.py         regular expressions and field parsers
  loader.py             CSV loading, validation policy, Marketplace
  filters.py            hard constraints (decorator registry)
  scoring.py            criteria (self-registering classes), normalisation, weight validation
  ranking.py            weighted ranking, tie-breaking, sensitivity analysis
  explain.py            data-driven explanation of the top offer
  booking.py            simulated bookings, seat inventory, cancellation
  profiles.py           traveller profiles
  exceptions.py         custom exception hierarchy
  logging_setup.py      log file configuration
data/                   input data
config/                 settings and profiles
tests/                  pytest test suite
scripts/                data generator, experiments, evidence, diagrams, report builder
docs/                   architecture diagram and flowchart
output/                 generated evidence used in the report
report/                 report source and PDF
```
