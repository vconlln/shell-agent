import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def shellcheck_path() -> str:
    candidate = os.environ.get("TU_SHELLCHECK") or str(REPO_ROOT / "tools" / "shellcheck")
    if Path(candidate).is_file():
        return candidate
    found = shutil.which("shellcheck")
    if found:
        return found
    pytest.skip("找不到 shellcheck（tools/shellcheck 或 PATH）")


@pytest.fixture(scope="session")
def bash_path() -> str:
    candidate = os.environ.get("TU_BASH") or (
        "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else "/usr/bin/bash"
    )
    if Path(candidate).is_file():
        return candidate
    found = shutil.which("bash")
    if found:
        return found
    pytest.skip("找不到 bash")
