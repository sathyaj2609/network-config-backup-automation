"""
device_connector.py

Core device connection logic for the network config backup tool.

Responsibilities:
    - Open an SSH session to a network device using Netmiko.
    - Pull the running configuration as a plain string.
    - Fail loudly (with specific, catchable exceptions) when a device
      can't be reached, won't authenticate, or times out, so callers
      (e.g. a future backup script that loops over devices.yaml) can
      decide whether to retry, skip, or abort.

This module deliberately does NOT decide *what* to do with the config
(saving to disk, diffing against the last backup, etc.) — that belongs
in a separate script/module. Keeping "connect and fetch" isolated from
"store and version" makes each piece easier to test on its own.
"""

import logging
import os
import re
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)

# Load variables from .env into the process environment as soon as this
# module is imported. python-dotenv silently no-ops if .env doesn't
# exist, which keeps this safe to import in contexts (like tests) where
# credentials aren't needed.
load_dotenv()

# Module-level logger, named after this file (e.g. "device_connector").
# We intentionally do NOT call logging.basicConfig() here — configuring
# handlers/formatters is the application's job, not a library module's.
# Doing it here would clobber logging config for anything that imports
# this module. Handler setup lives in the __main__ block below instead.
logger = logging.getLogger(__name__)


class DeviceConnectionError(Exception):
    """Raised when we fail to connect to a device or pull its config.

    Wraps the underlying Netmiko/socket exception so callers can catch
    one thing (this) instead of needing to know Netmiko's exception
    hierarchy, while the original error is still available via
    `__cause__` for debugging/logging.
    """


def get_running_config(device: dict, command: str = "show running-config", timeout: int = 10) -> str:
    """Connect to a device over SSH and return its running config.

    Args:
        device: A dict matching one entry from config/devices.yaml, e.g.
            {
                "hostname": "core-switch-01",
                "ip": "192.168.1.1",
                "device_type": "cisco_ios",
                "username": "...",
                "password": "...",
                "secret": "...",   # optional, enable/privileged mode
            }
        command: The show command used to pull the config. Defaults to
            the Cisco IOS command since that's what devices.yaml.example
            targets today; pass a different command for other platforms.
        timeout: Seconds to wait for the SSH connection before giving up.

    Returns:
        The running configuration as a single string.

    Raises:
        DeviceConnectionError: on timeout, unreachable host, or auth
            failure. The original exception is chained via `raise ... from`.
    """
    hostname = device.get("hostname", device.get("ip", "unknown-device"))
    device_type = device.get("device_type")

    # Meraki devices are cloud-managed via a REST API, not raw SSH — see
    # the note in devices.yaml.example. Netmiko has no SSH driver for
    # them, so fail fast with a clear message instead of letting Netmiko
    # raise a confusing "unsupported device_type" error deep in its stack.
    if device_type == "meraki":
        raise DeviceConnectionError(
            f"{hostname}: device_type 'meraki' is not SSH-reachable; "
            "Meraki backups require the Dashboard API (not yet implemented)."
        )

    netmiko_params = {
        "device_type": device_type,
        "host": device.get("ip"),
        "username": device.get("username"),
        "password": device.get("password"),
        "timeout": timeout,
    }

    logger.info("Connecting to %s (%s)...", hostname, device.get("ip"))

    try:
        with ConnectHandler(**netmiko_params) as connection:
            logger.info("Connected to %s.", hostname)

            # Enter privileged/enable mode if a secret was supplied.
            # Some IOS devices only expose the full running-config once
            # in enable mode, so this isn't optional in practice on
            # those devices — but we don't hard-require it here since
            # not every platform/lab setup needs it.
            secret = device.get("secret")
            if secret:
                connection.secret = secret
                connection.enable()
                logger.info("Entered enable mode on %s.", hostname)

            config = connection.send_command(command)
            logger.info("Retrieved running config from %s (%d bytes).", hostname, len(config))
            return config

    except NetmikoAuthenticationException as exc:
        logger.error("Authentication failed for %s: %s", hostname, exc)
        raise DeviceConnectionError(f"Authentication failed for {hostname}") from exc

    except NetmikoTimeoutException as exc:
        # Netmiko raises this for both "connection timed out" and
        # "device unreachable" (e.g. no route to host, port filtered) —
        # both surface as a socket-level timeout during the SSH handshake,
        # so Netmiko doesn't distinguish them and neither can we here.
        logger.error("Timed out connecting to %s: %s", hostname, exc)
        raise DeviceConnectionError(f"Timed out connecting to {hostname} ({device.get('ip')})") from exc

    except Exception as exc:
        # Catch-all for anything unexpected (e.g. paramiko SSHException
        # on a refused connection, DNS failure, etc.) so a single flaky
        # device can't crash a caller that's looping over an inventory.
        logger.error("Unexpected error connecting to %s: %s", hostname, exc)
        raise DeviceConnectionError(f"Unexpected error connecting to {hostname}") from exc


def _substitute_env_vars(value):
    """Replace ${VAR_NAME} placeholders with values from os.environ.

    devices.yaml stores credential *references* (e.g. "${NET_USERNAME}"),
    not real values — the actual secrets live in .env. This does the
    substitution at load time so get_running_config() only ever deals
    with resolved values. Using a regex instead of os.path.expandvars
    keeps behavior identical across Windows/macOS/Linux, since expandvars
    treats %VAR% vs $VAR differently per platform.
    """
    if not isinstance(value, str):
        return value
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)


def _load_devices(config_path: Path) -> list:
    """Load and env-resolve the device inventory from a devices.yaml file."""
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)

    devices = data.get("devices", []) if data else []
    for device in devices:
        device["username"] = _substitute_env_vars(device.get("username"))
        device["password"] = _substitute_env_vars(device.get("password"))
        if "secret" in device:
            device["secret"] = _substitute_env_vars(device["secret"])
    return devices


if __name__ == "__main__":
    # Application-level logging setup: log to both the console and a
    # rotating-by-run file under logs/, so a scheduled/unattended run
    # still leaves a record even if nobody watches the console.
    project_root = Path(__file__).resolve().parent.parent
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "device_connector.log"),
        ],
    )

    devices_path = project_root / "config" / "devices.yaml"
    if not devices_path.exists():
        logger.error(
            "config/devices.yaml not found. Copy config/devices.yaml.example "
            "to config/devices.yaml and fill in your real device details."
        )
        sys.exit(1)

    devices = _load_devices(devices_path)
    if not devices:
        logger.error("No devices found in %s.", devices_path)
        sys.exit(1)

    # Manual test hook: just grab the first device in the inventory.
    # This isn't meant to be the real backup loop (that's a separate
    # script) — it exists so this module can be exercised standalone
    # with `python src/device_connector.py` while building it out.
    target = devices[0]

    try:
        running_config = get_running_config(target)
    except DeviceConnectionError as exc:
        logger.error("Failed to fetch config from %s: %s", target.get("hostname"), exc)
        sys.exit(1)

    print("\n" + "=" * 70)
    print(f"Running config for {target.get('hostname')} ({target.get('ip')}):")
    print("=" * 70)
    print(running_config)
