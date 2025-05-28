import os
import subprocess


def test_csdwan():
    commands = [
        ["csdwan", "--version"],
        ["csdwan", "setup"],
        ["csdwan", "deploy", "20.16.1"],
        ["csdwan", "add", "2", "edges", "17.16.01a"],
        ["csdwan", "add", "1", "validator", "20.16.1"],
        ["csdwan", "add", "1", "controller", "20.16.1"],
        ["csdwan", "backup", "--workdir", "backup"],
        ["csdwan", "delete", "--force"],
        ["csdwan", "restore", "--workdir", "backup"],
        ["csdwan", "delete", "--force"],
    ]
    print()
    for cmd in commands:
        print(f"==== {' '.join(cmd)} ====")
        result = subprocess.run(cmd, env=os.environ)
        assert result.returncode == 0, f"Command {' '.join(cmd)} failed"
