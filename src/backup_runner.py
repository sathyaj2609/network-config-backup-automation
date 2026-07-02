"""
backup_runner.py

Orchestrates a full backup run across every device in config/devices.yaml.

Responsibilities:
    - Load the device inventory (reusing device_connector's loader so
      env-var substitution logic isn't duplicated).
    - Loop over every device, skip Meraki devices (SSH-based backup
      doesn't apply to them), and pull the running config for the rest.
    - Write each config to backups/<hostname>_<timestamp>.cfg.
    - Never let one bad device stop the run — log the failure and move on.
    - Print a summary of succeeded/failed/skipped counts at the end.

This module is intentionally "dumb" about *how* to talk to a device —
that's device_connector's job. It only decides *which* devices to visit
and *what to do* with the result (write to disk, count, report).
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from device_connector import DeviceConnectionError, _load_devices, get_running_config

logger = logging.getLogger(__name__)

# Devices with this device_type are cloud-managed and have no SSH driver
# in Netmiko (see device_connector.get_running_config, which would raise
# DeviceConnectionError for these anyway). We check for it here instead
# of just letting that exception happen so we can count these devices as
# "skipped" rather than "failed" in the summary — they're not a fault,
# they're simply out of scope for this SSH-based tool today.
UNSUPPORTED_DEVICE_TYPES = {"meraki"}


def backup_device(device: dict, backups_dir: Path, timestamp: str) -> Path:
    """Fetch one device's running config and write it to disk.

    Args:
        device: A single entry from the loaded device inventory.
        backups_dir: Directory to write the .cfg file into.
        timestamp: Pre-formatted timestamp string shared by the whole
            run, so every backup produced by one invocation of this
            script is easy to identify as belonging to the same run.

    Returns:
        The path the config was written to.

    Raises:
        DeviceConnectionError: propagated from get_running_config() if
            the device can't be reached/authenticated.
        OSError: if the backup file can't be written (disk full,
            permissions, etc.).
    """
    hostname = device.get("hostname", device.get("ip", "unknown-device"))

    config = get_running_config(device)

    backup_path = backups_dir / f"{hostname}_{timestamp}.cfg"
    backup_path.write_text(config, encoding="utf-8")
    logger.info("Saved backup for %s to %s", hostname, backup_path)
    return backup_path


def run_backups(devices: list, backups_dir: Path) -> dict:
    """Run the backup loop over every device and tally the outcome.

    Design choice: a single bad device (unreachable, bad credentials,
    unsupported type) must never abort the whole run — with dozens of
    devices, one flaky link shouldn't cost you every other backup. So
    each device gets its own try/except, and we log-and-continue rather
    than letting exceptions bubble out of the loop.

    Returns:
        A dict with "succeeded", "failed", and "skipped" lists of
        hostnames, suitable for building the end-of-run summary.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    results = {"succeeded": [], "failed": [], "skipped": []}

    for device in devices:
        hostname = device.get("hostname", device.get("ip", "unknown-device"))
        device_type = device.get("device_type")

        if device_type in UNSUPPORTED_DEVICE_TYPES:
            logger.warning(
                "Skipping %s: device_type '%s' is cloud-managed and has no SSH "
                "driver in Netmiko. Meraki backups require the Dashboard API, "
                "which is a separate future project, not this SSH-based tool.",
                hostname,
                device_type,
            )
            results["skipped"].append(hostname)
            continue

        try:
            backup_device(device, backups_dir, timestamp)
        except DeviceConnectionError as exc:
            # get_running_config() already logged the specific cause
            # (auth failure, timeout, etc.); log it again here at the
            # orchestration level so the failure is visible in the
            # context of the overall run, then move on to the next device.
            logger.error("Backup failed for %s: %s", hostname, exc)
            results["failed"].append(hostname)
        except OSError as exc:
            # Distinct from DeviceConnectionError: the device answered
            # fine, but writing the backup file to disk failed. Still
            # shouldn't stop the run — worth logging separately since
            # the fix (disk space, permissions) is different.
            logger.error("Could not write backup file for %s: %s", hostname, exc)
            results["failed"].append(hostname)
        else:
            results["succeeded"].append(hostname)

    return results


def print_summary(results: dict) -> None:
    """Print a human-readable summary of the run to stdout."""
    total = len(results["succeeded"]) + len(results["failed"]) + len(results["skipped"])

    print("\n" + "=" * 50)
    print("Backup run summary")
    print("=" * 50)
    print(f"Total devices in inventory: {total}")
    print(f"  Succeeded: {len(results['succeeded'])}")
    print(f"  Failed:    {len(results['failed'])}")
    print(f"  Skipped:   {len(results['skipped'])}")

    if results["failed"]:
        print("\nFailed devices:")
        for hostname in results["failed"]:
            print(f"  - {hostname}")

    if results["skipped"]:
        print("\nSkipped devices:")
        for hostname in results["skipped"]:
            print(f"  - {hostname}")

    print("=" * 50)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the backup run."""
    project_root = Path(__file__).resolve().parent.parent
    default_devices_path = project_root / "config" / "devices.yaml"

    parser = argparse.ArgumentParser(
        description="Back up the running config of every device in a device inventory file."
    )
    parser.add_argument(
        "--devices",
        type=Path,
        default=default_devices_path,
        help=f"Path to the device inventory YAML file (default: {default_devices_path}).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    # Same application-level logging setup pattern as device_connector.py:
    # log to both console and a file under logs/, so an unattended/
    # scheduled run still leaves a record.
    project_root = Path(__file__).resolve().parent.parent
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "backup_runner.log"),
        ],
    )

    args = parse_args()
    devices_path = args.devices
    if not devices_path.exists():
        logger.error(
            "%s not found. Copy config/devices.yaml.example to config/devices.yaml "
            "and fill in your real device details.",
            devices_path,
        )
        sys.exit(1)

    devices = _load_devices(devices_path)
    if not devices:
        logger.error("No devices found in %s.", devices_path)
        sys.exit(1)

    backups_dir = project_root / "backups"
    backups_dir.mkdir(exist_ok=True)

    logger.info("Starting backup run for %d device(s).", len(devices))
    run_results = run_backups(devices, backups_dir)
    logger.info(
        "Backup run complete: %d succeeded, %d failed, %d skipped.",
        len(run_results["succeeded"]),
        len(run_results["failed"]),
        len(run_results["skipped"]),
    )

    print_summary(run_results)

    # Exit non-zero if anything failed, so this plays nicely as a cron/
    # Task Scheduler job or in CI — a monitoring system can alert on a
    # non-zero exit code without parsing log output. Skips don't count
    # against the exit code since they're expected, not errors.
    sys.exit(1 if run_results["failed"] else 0)
