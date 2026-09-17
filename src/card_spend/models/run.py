"""Thin wrapper around the dbt CLI.

dbt is invoked as a subprocess rather than through its Python API on purpose: the API is not a
supported public interface, and a wrapper that breaks on a dbt minor release is worse than no
wrapper. This exists only to point dbt at whichever dataset was generated and to keep the profile
inside the repo so a fresh clone needs no `~/.dbt` setup.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def dbt_env(data_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["CSA_DUCKDB"] = str((data_dir / "warehouse.duckdb").resolve())
    env["DBT_PROFILES_DIR"] = str(PROJECT_ROOT)
    # dbt writes logs and compiled SQL beside the project; keeping them out of the repo root keeps
    # `git status` readable.
    env.setdefault("DBT_LOG_PATH", str((data_dir / "dbt_logs").resolve()))
    return env


def dbt(command: list[str], data_dir: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    lake = (data_dir / "lake").resolve()
    full = [
        "dbt",
        *command,
        "--project-dir",
        str(PROJECT_ROOT),
        "--vars",
        f"{{lake_path: '{lake}'}}",
    ]
    try:
        proc = subprocess.run(full, env=dbt_env(data_dir), capture_output=True, text=True, cwd=PROJECT_ROOT)
    except FileNotFoundError as exc:
        # Without this the failure is a bare FileNotFoundError from CreateProcess, several frames
        # deep, naming nothing. dbt is a declared dependency, so reaching here means a partial
        # install rather than a missing extra.
        raise RuntimeError("the dbt CLI is not on PATH; install the project with `pip install -e .`") from exc
    if check and proc.returncode != 0:
        raise RuntimeError(f"dbt {' '.join(command)} failed:\n{proc.stdout[-4000:]}\n{proc.stderr[-2000:]}")
    return proc


def run_dbt(data_dir: Path, target: str = "dev") -> int:
    """Staging, then snapshots, then everything.

    Three phases rather than one `dbt build` because the snapshots read the staging views and dbt
    does not order snapshots against models in a single invocation. Building staging first is the
    supported way round it, and it is also the order a real deployment would use: land the source
    views, capture the version, then build the marts on top.
    """
    phases = [
        ["run", "--select", "staging", "--target", target],
        ["snapshot", "--target", target],
        ["build", "--target", target],
    ]
    for command in phases:
        proc = dbt(command, data_dir, check=False)
        print(proc.stdout[-6000:])
        if proc.returncode != 0:
            print(proc.stderr[-2000:])
            return proc.returncode

    # The gold layer. Without it the DuckDB side of the benchmark would read unmodelled rows and the
    # two engines would answer the same question differently. See models/export.py.
    from card_spend.models.export import export_marts

    report = export_marts(data_dir)
    print(f"exported gold layer: {report['tables']} in {report['seconds']}s")
    return 0
