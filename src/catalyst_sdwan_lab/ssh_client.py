import logging
import re
import time
from collections.abc import Generator
from contextlib import contextmanager

import paramiko

log = logging.getLogger(__name__)
SSH_TIMEOUT = 30.0
CONFIG_TIMEOUT = 60.0


@contextmanager
def cml_shell(
    cml_host: str, cml_user: str, cml_password: str
) -> Generator[paramiko.Channel, None, None]:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(cml_host, username=cml_user, password=cml_password, timeout=15)
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


def extract_control_config(
    cml_host: str,
    cml_user: str,
    cml_password: str,
    lab_name: str,
    node_label: str,
    node_user: str,
    node_password: str,
) -> str:
    with cml_shell(cml_host, cml_user, cml_password) as ch:
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
) -> tuple[str, str, str]:
    """Returns (edge_type, config, uuid) where edge_type is 'sdwan' or 'sdrouting'."""
    with cml_shell(cml_host, cml_user, cml_password) as ch:
        ssh_drain(ch)
        ch.send(f"open /{lab_name}/{node_label}/0\n".encode())
        ssh_drain(ch, duration=3)
        ch.send(b"\r\n")
        out = ssh_recv(ch, "login:", "Username:", "#", ">")
        if "login:" in out or "Username:" in out:
            ch.send(b"admin\r\n")
            out = ssh_recv(ch, "Password:", "#", ">")
        if "Password:" in out:
            ch.send(b"admin\r\n")
            out = ssh_recv(ch, "#", ">", "incorrect", "failed")
        if ("incorrect" in out.lower() or "failed" in out.lower()) and manager_password:
            ch.send(b"admin\r\n")
            out = ssh_recv(ch, "Password:", "#", ">")
            if "Password:" in out:
                ch.send(f"{manager_password}\r\n".encode())
                out = ssh_recv(ch, "#", ">")
        if ">" in out and "#" not in out:
            ch.send(b"enable\r\n")
            out = ssh_recv(ch, "#", "Password:")
            if "Password:" in out and "#" not in out:
                ch.send(b"\r\n")
                ssh_recv(ch, "#")
        ch.send(b"terminal length 0\r\n")
        ssh_recv(ch, "#", timeout=10.0)

        ch.send(b"show sdwan run\r\n")
        out = ssh_recv(ch, "#", timeout=CONFIG_TIMEOUT)

        m = re.search(r"^system\b", out, re.MULTILINE)
        if m:
            edge_type = "sdwan"
            end = out.rfind("\n#")
            config = out[m.start():end if end != -1 else len(out)].strip()
            ch.send(b"show sdwan certificate serial\r\n")
        else:
            edge_type = "sdrouting"
            ch.send(b"show run\r\n")
            out = ssh_recv(ch, "#", timeout=CONFIG_TIMEOUT)
            config = _strip_sdrouting_config(out)
            # Flush any stale prompt before sending the next command
            ch.send(b"\r\n")
            ssh_recv(ch, "#", timeout=10.0)
            ch.send(b"show sd-routing certificate serial\r\n")

        serial_out = ssh_recv(ch, "#", timeout=30.0)
        m = re.search(r"Chassis\s+number:\s+([\w-]+)", serial_out, re.IGNORECASE)
        if not m:
            raise RuntimeError(f"Could not find UUID in output from {node_label}")
        return edge_type, config, m.group(1)


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
