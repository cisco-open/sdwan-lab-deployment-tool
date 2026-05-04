import os
import subprocess


def test_csdwan():
    commands = [
        ["csdwan", "--version"],
        ["csdwan", "setup"],
        ["csdwan", "deploy", "26.1.1"],
        ["csdwan", "add", "2", "edges", "26.01.01"],
        ["csdwan", "add", "1", "validator", "26.1.1"],
        ["csdwan", "add", "1", "controller", "26.1.1"],
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
