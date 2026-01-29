from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

import rich_click as click
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from wake.config import WakeConfig
    from wake.mutators.api import Mutation, Mutator


def discover_mutators() -> dict[str, type["Mutator"]]:
    """Discover all mutator classes from wake_mutators package."""
    import importlib
    import pkgutil
    
    import wake_mutators as mutators_pkg
    from wake.mutators.api import Mutator
    
    found = {}
    
    for importer, modname, ispkg in pkgutil.iter_modules(mutators_pkg.__path__):
        if modname.startswith("_"):
            continue
        
        module = importlib.import_module(f"wake_mutators.{modname}")
        
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, Mutator)
                and attr is not Mutator
                and attr.__module__ == module.__name__  # defined here, not imported
            ):
                found[attr.name] = attr
    
    return found

async def compile_project(config: "WakeConfig", contract_paths: List[Path]):
    """Compile the project and return the build."""
    from wake.compiler.compiler import SolidityCompiler
    from wake.compiler.solc_frontend import SolcOutputSelectionEnum
    
    compiler = SolidityCompiler(config)
    compiler.load()  # Load previous build if available
    
    build, errors = await compiler.compile(
        contract_paths,
        [SolcOutputSelectionEnum.AST],  # We only need AST for mutations
        write_artifacts=False,
    )
    
    return build


def collect_mutations(
    config: "WakeConfig",
    contract_paths: List[Path],
    mutator_classes: List[type["Mutator"]],
) -> List["Mutation"]:
    """Run mutators on contracts to collect all mutations."""
    from wake.core.visitor import visit_map, group_map
    
    build = asyncio.run(compile_project(config, contract_paths))
    
    all_mutations = []
    
    for mutator_cls in mutator_classes:
        mutator = mutator_cls()
        
        for path in contract_paths:
            source_unit = build.source_units.get(path)
            if source_unit is None:
                continue
            
            mutator._current_file = path
            
            # Iterate all nodes in the source unit (same as detectors do)
            for node in source_unit:
                # Call visit methods using the visit_map dispatch
                if node.ast_node.node_type in group_map:
                    for group in group_map[node.ast_node.node_type]:
                        if group in visit_map:
                            visit_map[group](mutator, node)
                
                if node.ast_node.node_type in visit_map:
                    visit_map[node.ast_node.node_type](mutator, node)
        
        all_mutations.extend(mutator.mutations)
    
    return all_mutations

def run_tests(test_paths: List[str], timeout: int = 120) -> bool:
    """Regenerate pytypes and run wake test. Return True if tests pass."""
    # Regenerate pytypes after mutation
    compile_result = subprocess.run(
        ["wake", "up", "pytypes"],
        capture_output=True,
        timeout=timeout,
        cwd=Path.cwd(),
    )
    
    if compile_result.returncode != 0:
        # Compile error means mutation broke the code = killed
        return False
    
    # Run tests
    cmd = ["wake", "test", "-x"] + test_paths
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            cwd=Path.cwd(),
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


@click.command(name="mutate")
@click.option(
    "--contracts",
    "-c",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Contract files to mutate.",
)
@click.option(
    "--mutations",
    "-m",
    multiple=True,
    type=str,
    help="Mutation operators to use (default: all).",
)
@click.option(
    "--list-mutations",
    is_flag=True,
    default=False,
    help="List available mutation operators.",
)
@click.option(
    "--timeout",
    "-t",
    type=int,
    default=60,
    help="Timeout for each test run in seconds.",
)
@click.option(
    "-v",
    "--verbosity",
    default=0,
    count=True,
    help="Increase verbosity.",
)
@click.argument("test_paths", nargs=-1, type=click.Path(exists=True))
@click.pass_context
def run_mutate(
    context: click.Context,
    contracts: Tuple[Path, ...],
    mutations: Tuple[str, ...],
    list_mutations: bool,
    timeout: int,
    verbosity: int,
    test_paths: Tuple[str, ...],
) -> None:
    """Run mutation testing on Solidity contracts."""
    from wake.config import WakeConfig
    from wake.mutators.api import MutantStatus
    
    console = Console()
    
    # Discover available mutators
    available_mutators = discover_mutators()
    
    if list_mutations:
        table = Table(title="Available Mutation Operators")
        table.add_column("Name", style="cyan")
        table.add_column("Description")
        
        for name, cls in sorted(available_mutators.items()):
            table.add_row(name, cls.description)
        
        console.print(table)
        return
    
    # Default to all tests if none specified
    if test_paths:
        test_paths_list = list(test_paths)
    else:
        # Find all test files in tests/ directory
        tests_dir = Path.cwd() / "tests"
        if tests_dir.exists():
            test_paths_list = [str(p) for p in tests_dir.glob("test_*.py")]
        else:
            test_paths_list = []
        
        if not test_paths_list:
            raise click.BadParameter("No test files found. Specify test paths or create tests/test_*.py files.")
        
        console.print(f"[dim]Auto-discovered {len(test_paths_list)} test file(s)[/dim]")
    
    # Select mutators
    if mutations:
        selected = []
        for m in mutations:
            if m not in available_mutators:
                raise click.BadParameter(f"Unknown mutation operator: {m}")
            selected.append(available_mutators[m])
    else:
        selected = list(available_mutators.values())
    
    # Load config
    config = WakeConfig(local_config_path=context.obj.get("local_config_path", None))
    config.load_configs()
    
    # Resolve contract paths
    resolved_contracts = [p.resolve() for p in contracts]
    
    console.print(f"[bold]Collecting mutations from {len(contracts)} contract(s)...[/bold]")
    
    # Collect all mutations
    all_mutations = collect_mutations(config, resolved_contracts, selected)
    
    console.print(f"[bold]Found {len(all_mutations)} mutation(s)[/bold]\n")
    
    if not all_mutations:
        console.print("[yellow]No mutations found.[/yellow]")
        return
    
    # Run baseline test first
    console.print("[bold]Running baseline tests...[/bold]")
    if not run_tests(list(test_paths), timeout):
        console.print("[red]Baseline tests failed! Fix tests before mutation testing.[/red]")
        sys.exit(1)
    
    console.print("[green]Baseline tests passed.[/green]\n")
    
    # Test each mutation
    results = []
    test_paths_list = list(test_paths)
    
    for i, mutation in enumerate(all_mutations, 1):
        console.print(f"[{i}/{len(all_mutations)}] {mutation.description}")
        console.print(f"    File: {mutation.file_path.name}:{mutation.line_number}")
        console.print(f"    {mutation.original} → {mutation.replacement if len(mutation.replacement) else "DELETED"}")
        
        # Read original source
        original_source = mutation.file_path.read_bytes()
        
        try:
            # Apply mutation
            mutated_source = mutation.apply(original_source)
            mutation.file_path.write_bytes(mutated_source)
            
            # Run tests
            passed = run_tests(test_paths_list, timeout)
            
            if passed:
                status = MutantStatus.SURVIVED
                console.print("    [red]SURVIVED[/red] ✗")
            else:
                status = MutantStatus.KILLED
                console.print("    [green]KILLED[/green] ✓")
            
            results.append((mutation, status))
            
        except Exception as e:
            console.print(f"    [yellow]ERROR: {e}[/yellow]")
            results.append((mutation, MutantStatus.COMPILE_ERROR))
        
        finally:
            # Always restore original
            mutation.file_path.write_bytes(original_source)
        
        console.print()
    
    # Summary
    killed = sum(1 for _, s in results if s == MutantStatus.KILLED)
    survived = sum(1 for _, s in results if s == MutantStatus.SURVIVED)
    errors = sum(1 for _, s in results if s == MutantStatus.COMPILE_ERROR)
    
    console.print("\n[bold]═══ Mutation Testing Summary ═══[/bold]")
    console.print(f"Total mutations: {len(results)}")
    console.print(f"[green]Killed: {killed}[/green]")
    console.print(f"[red]Survived: {survived}[/red]")
    if errors:
        console.print(f"[yellow]Errors: {errors}[/yellow]")
    
    if killed + survived > 0:
        score = killed / (killed + survived) * 100
        console.print(f"\n[bold]Mutation Score: {score:.1f}%[/bold]")
    
    # List survivors
    survivors = [(m, s) for m, s in results if s == MutantStatus.SURVIVED]
    if survivors:
        console.print("\n[bold red]Surviving Mutations (tests need improvement):[/bold red]")
        for mutation, _ in survivors:
            console.print(f"  • {mutation.file_path.name}: {mutation.description}")
    
    sys.exit(0 if survived == 0 else 1)