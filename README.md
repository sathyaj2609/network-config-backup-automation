# Network Config Backup Automation

Automated Cisco/Meraki config backup with change detection & version control.

This tool connects to network devices over SSH (via Netmiko), pulls their
running configuration, and saves timestamped backups to disk so that
config drift and changes can be tracked over time — with git itself
serving as the version history.

## Folder Structure

```
network-config-backup-automation/
├── src/                    # Python source code
├── backups/                # Saved device config backups (gitignored)
├── config/
│   └── devices.yaml.example  # Template for the device inventory
├── logs/                   # Log output (gitignored)
├── tests/                  # Unit tests
├── requirements.txt        # Python dependencies
├── .env.example            # Template for SSH credential env vars
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
