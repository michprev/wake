from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Set, Tuple

import rich_click as click

from wake.core import get_logger
from wake.core.visitor import Visitor


logger = get_logger(__name__)


def mutate_operation() -> None:
    # repeat 10 times
    total = 10
    failed = 0
    unexpected_success = 0
    for idx in range(total):
        patch_applied = False
        try:
            subprocess.run(
                ["wake", "print", "mutate-binary-operation"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
           
            subprocess.run(
                ["git", "apply", "mutations.patch"],
                check=True,
            )
            patch_applied = True
            
            subprocess.run(
                ["wake", "up", "pytypes"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            result = subprocess.run(
                ["wake", "test", "-x"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                failed += 1
            else:
                unexpected_success += 1

            progress = int(((idx + 1) / total) * 100)
            fail_rate = int((failed / (idx + 1)) * 100)
            click.echo(
                f"progress={progress}% failure_rate={fail_rate}% "
                f"unexpected_success={unexpected_success}"
            )
        except subprocess.CalledProcessError as exc:
            raise click.ClickException(
                f"Mutation loop failed with exit code {exc.returncode}."
            ) from exc
        finally:
            if patch_applied:
                subprocess.run(
                    ["git", "apply", "-R", "mutations.patch"],
                    check=False,
                )

    if unexpected_success > 0:
        raise click.ClickException(
            "Some mutations unexpectedly passed tests. "
            f"unexpected_success={unexpected_success}/{total}"
        )


@click.command(name="mutate")
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--no-artifacts", is_flag=True, default=False, help="Do not write build artifacts."
)
@click.option(
    "--no-warnings",
    is_flag=True,
    default=False,
    help="Do not print compilation warnings.",
)
@click.pass_context
def run_mutate(
    ctx: click.Context,
    paths: Tuple[str, ...],
    no_artifacts: bool,
    no_warnings: bool,
) -> None:
    """Identify binary operations using the visitor system."""
    from wake.config import WakeConfig

    config = WakeConfig(local_config_path=ctx.obj.get("local_config_path", None))
    config.load_configs()

    mutate_operation()
