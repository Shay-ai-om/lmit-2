from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import uuid

import pytest


@pytest.fixture
def tmp_path(request) -> Path:
    root = Path(os.environ.get("LMIT_WIKI_TEST_TMPDIR", Path.cwd() / "pytest-tmp-safe"))
    root.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", request.node.name).strip(".-")
    path = root / f"{safe_name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
