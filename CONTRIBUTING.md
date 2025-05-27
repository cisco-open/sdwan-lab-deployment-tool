# How to Contribute

Thank you for your interest in contributing to Catalyst SD-WAN Lab! Please take a moment to review this document **before submitting a pull request**:

- Want to add something yourself? [Make a PR](https://github.com/cisco-open/sdwan-lab-deployment-tool/pulls).
  - To make a PR, fork the repository, make your changes, and submit the pull request. Then, wait for review and feedback from our developers.
  - Write a clear PR description and include docstrings in your code to make it easily understandable.
  - Always write clear log messages for your commits.
  - Before submitting a PR, verify that the existing SD-WAN Lab tasks work without any issues (see [Testing Before Pull Requests](#testing-before-pull-requests)).
  - For CML interactions, use the official [CML SDK](https://github.com/CiscoDevNet/virl2-client).
  - For SD-WAN interactions, use the official [Catalyst WAN SDK](https://github.com/CiscoDevNet/catalystwan).
- Found a bug or have a feature request? [Report it here](https://github.com/cisco-open/sdwan-lab-deployment-tool/issues) and provide as much information as possible.

## Testing Before Pull Requests

Before opening a pull request, please ensure all tests pass:

1. Create a virtual environment:

   ```
   python3 -m venv venv
   ```

2. Activate the virtual environment:

   ```
   source venv/bin/activate
   ```

   The prompt will update with the virtual environment name (`venv`), indicating that it is active.

3. Upgrade initial virtual environment packages:

   ```
   pip install --upgrade pip setuptools pytest
   ```

4. Install the tool from your local repository:

   ```
   pip install --upgrade .
   ```

   Alternatively, you can install the tool from your branch, for example:

   ```
   pip install git+https://github.com/cisco-open/sdwan-lab-deployment-tool.git@restore_version_change
   ```

5. Load the environment variables used to deploy a test lab:

   ```
   source lab.env
   ```

6. Run tests:

   ```
   pytest -s test_csdwan.py
   ```

   This will run all tasks and display real-time output. Make sure to resolve any test failures before submitting your PR. If needed, you can modify the control components and edge versions in the test_csdwan.py file.
