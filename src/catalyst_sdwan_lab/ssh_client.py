import base64
import logging
import re
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import paramiko
import typer
from rich.console import Console

log = logging.getLogger(__name__)
SSH_TIMEOUT = 30.0
CONFIG_TIMEOUT = 60.0


class _InteractiveHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, console: Console) -> None:
        self._console = console

    def missing_host_key(
        self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey
    ) -> None:
        fingerprint = ":".join(f"{b:02x}" for b in key.get_fingerprint())
        live_stack = getattr(self._console, "_live_stack", [])
        live = live_stack[-1] if live_stack else None
        if live is not None:
            old_transient = live.transient
            live.transient = True
            live.stop()
            live.transient = old_transient
            sys.stdout.flush()
        try:
            sys.stderr.write(
                f"The authenticity of host '{hostname}' cannot be established.\n"
                f"{key.get_name()} key fingerprint is {fingerprint}.\n"
                "Are you sure you want to continue connecting (yes/no)? "
            )
            sys.stderr.flush()
            answer = sys.stdin.readline().strip()
        finally:
            if live is not None:
                print()
                live.start()
        if answer.lower() != "yes":
            raise paramiko.SSHException(f"Host key verification failed for {hostname}.")
        known_hosts = Path.home() / ".ssh" / "known_hosts"
        entry = f"{hostname} {key.get_name()} {base64.b64encode(key.asbytes()).decode()}\n"
        with open(known_hosts, "a") as f:
            f.write(entry)
        client.get_host_keys().add(hostname, key.get_name(), key)


@contextmanager
def cml_shell(
    cml_host: str, cml_user: str, cml_password: str, console: Console | None = None
) -> Generator[paramiko.Channel, None, None]:
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    ssh.set_missing_host_key_policy(_InteractiveHostKeyPolicy(console or Console()))
    try:
        ssh.connect(
            cml_host, username=cml_user, password=cml_password,
            timeout=15, allow_agent=False, look_for_keys=False,
        )
    except paramiko.BadHostKeyException:
        log.error(
            "SSH host key for %s has changed. If this is expected (CML reinstall/upgrade), "
            "remove the old entry with: ssh-keygen -R %s",
            cml_host, cml_host,
        )
        raise typer.Exit(1)
    try:
        ch = ssh.invoke_shell()
        try:
            yield ch
        finally:
            ch.close()
    finally:
        ssh.close()


def ssh_recv(ch: paramiko.Channel, *prompts: str, timeout: float = SSH_TIMEOUT) -> str:
    buf = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ch.closed:
            raise RuntimeError("SSH channel closed unexpectedly")
        if ch.recv_ready():
            chunk = ch.recv(4096).decode("utf-8", errors="replace")
            buf += chunk
            log.debug("ssh_recv << %r", chunk)
            if any(p in buf for p in prompts):
                return buf
        else:
            time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for any of {prompts!r}, last received: {buf[-500:]!r}")


def ssh_drain(ch: paramiko.Channel, duration: float = 1.0) -> None:
    deadline = time.time() + duration
    while time.time() < deadline:
        if ch.recv_ready():
            ch.recv(4096)
        else:
            time.sleep(0.05)


def _edge_console_login(ch: paramiko.Channel, fallback_password: str = "") -> None:
    """Bring an already-opened CML edge console channel to a privileged (#) prompt."""
    ssh_drain(ch, duration=3)
    ch.send(b"\r\n")
    out = ssh_recv(ch, "login:", "Username:", "#", ">")
    if "login:" in out or "Username:" in out:
        ch.send(b"admin\r\n")
        out = ssh_recv(ch, "Password:", "#", ">")
    if "Password:" in out:
        ch.send(b"admin\r\n")
        out = ssh_recv(ch, "#", ">", "incorrect", "failed")
    if ("incorrect" in out.lower() or "failed" in out.lower()) and fallback_password:
        ch.send(b"admin\r\n")
        out = ssh_recv(ch, "Password:", "#", ">")
        if "Password:" in out:
            ch.send(f"{fallback_password}\r\n".encode())
            out = ssh_recv(ch, "#", ">")
    if ">" in out and "#" not in out:
        ch.send(b"enable\r\n")
        out = ssh_recv(ch, "#", "Password:")
        if "Password:" in out and "#" not in out:
            ch.send(b"\r\n")
            ssh_recv(ch, "#")



def fix_sdrouting_default_route(
    cml_host: str, cml_user: str, cml_password: str, lab_name: str, node_label: str,
    console: Console | None = None,
) -> bool:
    """Wait for SD-Routing daemons, then reload if default route is missing.
    Returns True if a reload was triggered."""
    with cml_shell(cml_host, cml_user, cml_password, console) as ch:
        ssh_drain(ch)
        ch.send(f"open /{lab_name}/{node_label}/0\n".encode())
        _edge_console_login(ch)
        ch.send(b"terminal length 0\r\n")
        ssh_recv(ch, "#", timeout=10.0)

        deadline = time.time() + 300.0
        while time.time() < deadline:
            ssh_drain(ch)
            ch.send(b"show sd-routing system status\r\n")
            out = ssh_recv(ch, "#", timeout=30.0)
            last_prompt = next((line for line in reversed(out.splitlines()) if "#" in line), "")
            if "not responding" not in out and "Router" not in last_prompt:
                break
            time.sleep(15)
        else:
            log.warning("%s: SD-Routing daemons not ready after 5 min, skipping route check",
                        node_label)
            return False

        ch.send(b"show ip route\r\n")
        out = ssh_recv(ch, "#", timeout=30.0)
        if "0.0.0.0/0" in out:
            log.debug("%s: default route present", node_label)
            return False
        log.info("%s: default route missing, reloading", node_label)
        ch.send(b"reload\r\n")
        out = ssh_recv(ch, "confirm", "yes/no", timeout=30.0)
        if "yes/no" in out:
            ch.send(b"yes\r\n")
            ssh_recv(ch, "confirm", timeout=60.0)
        ch.send(b"\r\n")
        return True


def extract_control_config(
    cml_host: str,
    cml_user: str,
    cml_password: str,
    lab_name: str,
    node_label: str,
    node_user: str,
    node_password: str,
    console: Console | None = None,
) -> str:
    with cml_shell(cml_host, cml_user, cml_password, console) as ch:
        ssh_drain(ch)
        ch.send(f"open /{lab_name}/{node_label}/0\n".encode())
        ssh_drain(ch, duration=3)
        # Ctrl+C breaks out of any in-progress wizard; Enter solicits a fresh prompt
        ch.send(b"\x03\r\n")
        out = ssh_recv(ch, "login:", "Username:", "#", "Password:", "Re-enter password:")
        # Navigate through a stuck password-change wizard (max 4 rounds)
        for _ in range(4):
            if "login:" in out or "Username:" in out or "#" in out:
                break
            if "Password:" in out or "Re-enter password:" in out:
                ch.send(f"{node_password}\r\n".encode())
                out = ssh_recv(ch, "login:", "Username:", "#", "Password:", "Re-enter password:")
            else:
                break
        if "login:" in out or "Username:" in out:
            ch.send(f"{node_user}\r\n".encode())
            out = ssh_recv(ch, "Password:", "#")
        if "Password:" in out:
            ch.send(f"{node_password}\r\n".encode())
            ssh_recv(ch, "#")
        ch.send(b"terminal length 0\r\n")
        ssh_recv(ch, "#", timeout=10.0)
        ch.send(b"show run | display xml | nomore\r\n")
        out = ssh_recv(ch, "</config>", timeout=CONFIG_TIMEOUT)
        start = out.find("<?xml")
        if start == -1:
            start = out.find("<config")
        end = out.rfind("</config>")
        if start == -1 or end == -1:
            raise RuntimeError(f"Could not find XML in output from {node_label}")
        return out[start:end + len("</config>")]


def extract_edge_config(
    cml_host: str,
    cml_user: str,
    cml_password: str,
    lab_name: str,
    node_label: str,
    manager_password: str = "",
    console: Console | None = None,
) -> tuple[str, str, str]:
    """Returns (edge_type, config, uuid) where edge_type is 'sdwan' or 'sdrouting'."""
    with cml_shell(cml_host, cml_user, cml_password, console) as ch:
        ssh_drain(ch)
        ch.send(f"open /{lab_name}/{node_label}/0\n".encode())
        _edge_console_login(ch, fallback_password=manager_password)
        ch.send(b"terminal length 0\r\n")
        ssh_recv(ch, "#", timeout=10.0)

        ch.send(b"show version | include mode\r\n")
        mode_out = ssh_recv(ch, "#", timeout=30.0)

        if "Autonomous" in mode_out:
            edge_type = "sdrouting"
            ch.send(b"show sd-routing certificate serial\r\n")
            serial_out = ssh_recv(ch, "#", timeout=30.0)
            m = re.search(r"Chassis\s+number:\s+([\w-]+)", serial_out, re.IGNORECASE)
            if not m:
                log.debug("%s: certificate serial output: %r", node_label, serial_out)
                raise RuntimeError(f"Could not find UUID in output from {node_label}")
            uuid = m.group(1)
            ch.send(b"show run\r\n")
            out = ssh_recv(ch, "#", timeout=CONFIG_TIMEOUT)
            config = _strip_sdrouting_config(out)
        else:
            edge_type = "sdwan"
            ch.send(b"show sdwan certificate serial\r\n")
            serial_out = ssh_recv(ch, "#", timeout=30.0)
            m = re.search(r"Chassis\s+number:\s+([\w-]+)", serial_out, re.IGNORECASE)
            if not m:
                log.debug("%s: certificate serial output: %r", node_label, serial_out)
                raise RuntimeError(f"Could not find UUID in output from {node_label}")
            uuid = m.group(1)
            ch.send(b"show sdwan run\r\n")
            out = ssh_recv(ch, "#", timeout=CONFIG_TIMEOUT)
            sm = re.search(r"^system\b", out, re.MULTILINE)
            end = out.rfind("\n#")
            config = out[sm.start() if sm else 0 : end if end != -1 else None].strip()

        return edge_type, config, uuid


def _strip_sdrouting_config(raw: str) -> str:
    lines = raw.splitlines()
    start = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("!") or s.startswith("version"):
            start = i
            break
    end = len(lines)
    while end > start and lines[end - 1].rstrip().endswith("#"):
        end -= 1
    config = "\n".join(lines[start:end])
    config = re.sub(r"\ncrypto pki[\s\S]+?!", "!", config)
    config = re.sub(r"\nlicense udi[\s\S]+?\n", "\n", config, flags=re.DOTALL | re.MULTILINE)
    # Remove commands ConfD rejects on a fresh SD-Routing boot
    config = re.sub(r"\nspanning-tree[^\n]*", "", config)
    config = re.sub(r"\ntelemetry ietf subscription[\s\S]+?(?=\n\S|\Z)", "", config)
    config = re.sub(r"\ntelemetry receiver[\s\S]+?(?=\n\S|\Z)", "", config)
    config = re.sub(r"\n[ \t]+login local", "", config)
    config = re.sub(r"\nnetconf-yang feature candidate-datastore[^\n]*", "", config)
    config = re.sub(r"\nmgcp[\s\S]+?(?=\n\S|\Z)", "", config)
    config = re.sub(r"\nsubscriber templating[^\n]*", "", config)
    config = re.sub(r"\nservice password-encryption[^\n]*", "", config)
    config = re.sub(r"\naaa new-model[^\n]*", "", config)
    return config.strip()
