from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import List, Set, Tuple

import rich_click as click

from wake.core import get_logger
from wake.core.visitor import Visitor


logger = get_logger(__name__)


class BinaryOperationVisitor(Visitor):
    def __init__(self) -> None:
        self.count = 0

    def visit_binary_operation(self, node) -> None:
        self.count += 1
        start, end = node.byte_location
        source = node.source.strip()
        click.echo(f"{node.source_unit.file}:{start}-{end}: {source}")


async def mutate_(config, paths: Tuple[str, ...], no_artifacts: bool, no_warnings: bool):
    import glob

    from wake.compiler import SolidityCompiler
    from wake.compiler.solc_frontend import SolcOutputSelectionEnum
    from wake.core.visitor import group_map, visit_map
    from wake.utils.file_utils import is_relative_to
    from .console import console

    sol_files: Set[Path] = set()
    start = time.perf_counter()
    with console.status("[bold green]Searching for *.sol files...[/]"):
        if len(paths) == 0:
            for f in glob.iglob(str(config.project_root_path / "**/*.sol"), recursive=True):
                file = Path(f)
                if (
                    not any(
                        is_relative_to(file, p)
                        for p in config.compiler.solc.exclude_paths
                    )
                    and file.is_file()
                ):
                    sol_files.add(file)
        else:
            for p in paths:
                path = Path(p)
                if path.is_file():
                    if not path.match("*.sol"):
                        raise click.BadParameter(
                            f"Argument `{p}` is not a Solidity file."
                        )
                    sol_files.add(path)
                elif path.is_dir():
                    for f in glob.iglob(str(path / "**/*.sol"), recursive=True):
                        file = Path(f)
                        if (
                            not any(
                                is_relative_to(file, p)
                                for p in config.compiler.solc.exclude_paths
                            )
                            and file.is_file()
                        ):
                            sol_files.add(file)
                else:
                    raise click.BadParameter(
                        f"Argument `{p}` is not a file or directory."
                    )
    end = time.perf_counter()
    console.log(
        f"[green]Found {len(sol_files)} *.sol files in [bold green]{end - start:.2f} s[/bold green][/]"
    )

    compiler = SolidityCompiler(config)
    compiler.load(console=console)
    build, _ = await compiler.compile(
        sol_files,
        [SolcOutputSelectionEnum.ALL],
        write_artifacts=not no_artifacts,
        console=console,
        no_warnings=no_warnings,
    )

    visitor = BinaryOperationVisitor()
    visitor.build = build
    visitor.build_info = compiler.latest_build_info
    visitor.config = config
    visitor.imports_graph = compiler.latest_graph
    visitor.logger = logger

    for _, source_unit in build.source_units.items():
        for node in source_unit:
            visitor.visit_ir_abc(node)
            if node.ast_node.node_type in group_map:
                for group in group_map[node.ast_node.node_type]:
                    visit_map[group](visitor, node)
            visit_map[node.ast_node.node_type](visitor, node)

    console.log(f"[green]Found {visitor.count} binary operations.[/]")


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

    asyncio.run(mutate_(config, paths, no_artifacts, no_warnings))
