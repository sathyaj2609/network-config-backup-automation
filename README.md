# Network Config Backup Automation

A lightweight CLI tool that connects to network devices over SSH (via
[Netmiko](https://github.com/ktbyers/netmiko)), pulls their running
configuration, and saves timestamped backups to disk. Git itself serves
as the version history, so config drift and changes can be tracked over
time without any additional infrastructure.

A single bad device — unreachable, wrong credentials, unsupported
platform — never aborts the run: each device is backed up independently,
and the run ends with a summary of what succeeded, failed, or was
skipped.

## Folder Structure

```
network-config-backup-automation/
├── src/
│   ├── backup_runner.py       # CLI entry point; orchestrates the backup run
│   └── device_connector.py    # SSH connection + config retrieval (Netmiko)
├── backups/                   # Saved device config backups (gitignored)
├── config/
│   └── devices.yaml.example   # Template for the device inventory
├── logs/                      # Log output (gitignored)
├── tests/                     # Unit tests
├── requirements.txt           # Python dependencies
├── .env.example               # Template for SSH credential env vars
└── .gitignore
```

## Setup

1. Create and activate a virtual environment:
   ```
   python -m venv venv
   venv\Scripts\activate      # Windows
   source venv/bin/activate   # macOS/Linux
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Copy the environment template and fill in your SSH credentials:
   ```
   cp .env.example .env
   ```

4. Copy the device inventory template and list your devices:
   ```
   cp config/devices.yaml.example config/devices.yaml
   ```

`.env` and `config/devices.yaml` are gitignored since they contain real
credentials and device details — never commit them.

## Usage

Run a backup across every device in your inventory:

```
python src/backup_runner.py --devices config/devices.yaml
```

`--devices` defaults to `config/devices.yaml`, so it can be omitted for
the common case:

```
python src/backup_runner.py
```

Each run writes one `<hostname>_<timestamp>.cfg` file per device to
`backups/`, and a log of the run to `logs/backup_runner.log`.

### Example output

```
==================================================
Backup run summary
==================================================
Total devices in inventory: 3
  Succeeded: 1
  Failed:    1
  Skipped:   1

Failed devices:
  - unreachable-router

Skipped devices:
  - office-ap-meraki-01
==================================================
```

The process exits non-zero if any device failed, so the run can be
wired into cron, Task Scheduler, or CI and alert without needing to
parse log output. Skipped devices don't affect the exit code — they're
expected, not errors.

## Supported Device Types

- **cisco_ios** — fully supported via Netmiko SSH.
- **meraki** — recognized but intentionally skipped. Meraki devices are
  cloud-managed via the Dashboard API rather than direct SSH, so
  API-based Meraki backups are planned as a separate future project,
  not part of this SSH-based tool.
