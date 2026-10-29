# Offline optimization result viewer

From the workspace root (the folder containing both `venv_midterm` and
`dc_network_optimization`):

```powershell
.\venv_midterm\Scripts\python.exe .\dc_network_optimization\run_demo.py
```

Or, from `dc_network_optimization`:

```powershell
..\venv_midterm\Scripts\python.exe .\run_demo.py
```

Open **http://127.0.0.1:8000/demo.html**. The launcher opens it automatically
unless `--no-browser` is supplied. Use `--port 8080` if port 8000 is occupied;
the printed URL reflects the chosen port. Stop the server with Ctrl+C.

The viewer needs no new dependencies, internet connection, OSRM service or
solver run. It uses Python's standard library and local HTML/CSS/JavaScript.
Opening [demo.html](./demo.html) directly as a `file://` URL does not work;
the page needs the launcher's localhost result endpoints.

## What is displayed

- The saved 1,970-store / 60-site run, its recommended 11 DCs and 183 owned vehicles.
- An offline coordinate map with store/site hover details, DC filtering,
  changed-assignment filtering, unused candidates, pan and zoom.
- D28 and H14 comparisons, cost components, weighted service, paired-bootstrap
  savings intervals and the mean-demand sensitivity.
- Every saved D28 finalist and guided/random D7 evaluation in the network picker.
- A DC/day explorer with owned, needed and spot vehicles and service checks.
- The k-sweep, feasible best-so-far search curves, G/E scatter and filterable logs.
- Original JSON/JSONL previews and byte-preserving downloads, including nested files.

[report.json](../code/results/higher_level/report.json) is authoritative.
[presentation.py](./presentation.py) reads it and joins coordinates and site
limits from the nationwide [data tables](../data/), not the old toy generator.
Standalone files are available for source inspection but are not used to resurrect
recommendations absent from the current report.

Map lines/road geometry and a live solve are deliberately absent: the saved
higher-level report contains assignments and DC/day summaries, not road paths.
Infeasible assignments have no fabricated cost or route result. “Unknown” seed
status after a time limit is not displayed as proven infeasibility.

## Read-only checks and options

```powershell
..\venv_midterm\Scripts\python.exe .\run_demo.py --check-results
..\venv_midterm\Scripts\python.exe -B -m unittest discover -s ui -p test_presentation.py -v
..\venv_midterm\Scripts\python.exe .\run_demo.py --no-browser --port 8080
```

`--results-dir PATH` selects another saved higher-level report directory.
Relative paths are resolved from the calling directory; the default is anchored
to the project, so the normal launch works from any working directory.
The coordinate/site tables still come from this project's nationwide data.

Refreshing rereads the saved report when its modification time or size changes.
No viewing endpoint writes model files or recalculates results.

The launcher's pre-existing **`--build`, `--network` and `--routing` options still
explicitly run optimization**. They are not needed to view the saved outputs.
The presentation does not modify or invoke the optimization code or configuration.
The old [solver.js](./solver.js) and [toy_data.js](./toy_data.js) are retained as
legacy assets but are not loaded by the updated page.
