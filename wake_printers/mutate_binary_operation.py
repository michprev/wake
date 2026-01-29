from __future__ import annotations

from collections import defaultdict
import difflib
from pathlib import Path
import random

import networkx as nx
import rich_click as click
import wake.ir as ir
import wake.ir.types as types
from rich import print
from wake.cli import SolidityName
from wake.printers import Printer, printer


relational_operator_candidates = [
    ir.enums.BinaryOpOperator.LT,
    ir.enums.BinaryOpOperator.GT,
    ir.enums.BinaryOpOperator.LTE,
    ir.enums.BinaryOpOperator.GTE,
    ir.enums.BinaryOpOperator.EQ,
    ir.enums.BinaryOpOperator.NEQ,
]


class MutateBinaryOperationPrinter(Printer):

    binary_operations: list[ir.BinaryOperation] = []
    def __init__(self) -> None:
        super().__init__()
        self._edits = defaultdict(list)
        self._random_seed: int | None = None

    def binary_operator_replace(
        self,
        node: ir.BinaryOperation,
        operator: ir.enums.BinaryOpOperator,
        replacement: bytes,
    ) -> None:
        if node.operator != operator:
            return
        left_end = node.left_expression.byte_location[1]
        right_start = node.right_expression.byte_location[0]
        span = node.source_unit.file_source[left_end:right_start]
        old = node.operator.value.encode("utf-8")
        idx = span.find(old)
        if idx != -1:
            start = left_end + idx
            end = start + len(old)
            self._edits[node.source_unit.file].append((start, end, replacement))


    def print(self) -> None:
        if not self.binary_operations:
            print("no binary operations")
            return

        rng = random.Random(self._random_seed)

        self.binary_operations = [op for op in self.binary_operations if op.operator in relational_operator_candidates]

        node = rng.choice(self.binary_operations)
        candidates = [
            op for op in relational_operator_candidates if op != node.operator
        ]
        if not candidates:
            print("no alternate operator")
            return
        replacement_operator = rng.choice(candidates)
        self.binary_operator_replace(
            node,
            node.operator,
            replacement_operator.value.encode("utf-8"),
        )

        if not self._edits:
            print("no edits")
            return

        patch_lines = []
        project_root = self.config.project_root_path

        for path, edits in self._edits.items():
            original_bytes = Path(path).read_bytes()
            mutated_bytes = bytearray(original_bytes)
            for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
                mutated_bytes[start:end] = repl

            try:
                rel_path = Path(path).relative_to(project_root).as_posix()
            except ValueError:
                rel_path = Path(path).as_posix()

            diff = difflib.unified_diff(
                original_bytes.decode("utf-8").splitlines(keepends=True),
                bytes(mutated_bytes).decode("utf-8").splitlines(keepends=True),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
            patch_lines.extend(diff)

        patch_path = project_root / "mutations.patch"
        patch_path.write_text("".join(patch_lines), encoding="utf-8")
        print(f"wrote {patch_path}")


    def visit_binary_operation(self, node: ir.BinaryOperation) -> None:

        self.binary_operations.append(node)

    @printer.command(name="mutate-binary-operation")
    @click.option("--seed", type=int, default=None, help="Random seed.")
    def cli(self, seed: int | None) -> None:
        self._random_seed = seed
