


from __future__ import annotations

import os
import pty
import re
import subprocess
import sys
from typing import List


# run mutation testing with slither-mutate


def run_mutation_test(contracts: List[str], tests: List[str]) -> None:
    test_args = " ".join(tests) if tests else ""
    test_cmd = f"wake up && wake test {test_args}".strip()
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        [
            "slither-mutate",
            *contracts,
            "--test-cmd",
            test_cmd,
            "--timeout",
            "200",
        ],
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    replace_re = re.compile(br"slither", re.IGNORECASE)
    pending = b""
    tail_len = len(b"slither") - 1
    try:
        while True:
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            chunk = pending + data
            if len(chunk) > tail_len:
                emit = chunk[:-tail_len]
                pending = chunk[-tail_len:]
            else:
                pending = chunk
                continue
            sys.stdout.buffer.write(replace_re.sub(b"wake", emit))
            sys.stdout.buffer.flush()
    finally:
        if pending:
            sys.stdout.buffer.write(replace_re.sub(b"wake", pending))
            sys.stdout.buffer.flush()
        os.close(master_fd)
    proc.wait()