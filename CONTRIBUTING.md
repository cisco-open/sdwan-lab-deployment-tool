# How to Contribute

Thank you for your interest in contributing to Catalyst SD-WAN Lab! Please take a moment to review this document **before submitting a pull request**:

- Want to add something yourself? [Make a PR](https://github.com/cisco-open/sdwan-lab-deployment-tool/pulls).
  - Fork the repository, make your changes, and submit the pull request. Then wait for review and feedback.
  - Write a clear PR description explaining what changed and why.
  - Always write clear commit messages.
  - Before submitting a PR, make sure unit tests pass and, where possible, verify that the affected tasks work end-to-end (see [Testing](#testing)).
  - For CML interactions, use the official [CML SDK](https://github.com/CiscoDevNet/virl2-client).
  - For SD-WAN Manager interactions, use the thin `manager_client.py` HTTP client in this repo (direct `requests` calls — no external SDK).
- Found a bug or have a feature request? [Report it here](https://github.com/cisco-open/sdwan-lab-deployment-tool/issues) and provide as much detail as possible.

## Development Setup

This project uses [uv](https://docs.astral.sh/uv/) for packaging and dependency management.

1. Install `uv` if you don't have it:

   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Clone the repository and install dependencies (including dev extras):

   ```sh
   git clone https://github.com/cisco-open/sdwan-lab-deployment-tool.git
   cd sdwan-lab-deployment-tool
   uv sync
   ```

3. Verify the installation:

   ```sh
   uv run csdwan --version
   ```

## Testing

### Unit tests

Run the unit test suite:

```sh
uv run pytest
```

### Integration tests

Integration tests run the full CLI against a live CML environment. They are excluded from the default `pytest` run and must be invoked explicitly.

Set the required environment variables (the same ones used in `lab.env`, plus `SDWAN_VERSION`):

```sh
export CML_IP='cml.example.com'
export CML_USER='admin'
export CML_PASSWORD='your-password'
export LAB_NAME='sdwan-lab'
export MANAGER_PORT=2000          # PATty mode, or use MANAGER_IP/MANAGER_MASK/MANAGER_GATEWAY
export MANAGER_USER='sdwan'
export MANAGER_PASSWORD='your-manager-password'
export SDWAN_VERSION='20.15.1'
```

Or simply source your existing `lab.env` and export `SDWAN_VERSION`:

```sh
source lab.env
export SDWAN_VERSION=20.15.1
```

Then run all integration tests:

```sh
uv run pytest tests/integration/ -v -s
```

The `-s` flag disables output capture so you can watch the progress of each `csdwan` step in real time. The full workflow test (deploy → add → backup → restore → delete) takes 30–60 minutes depending on your environment.

If any required environment variable is missing, the tests are automatically skipped rather than failing.
