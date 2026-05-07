from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


def upload_image_file(
    session: Any,
    path: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    name = path.name
    size = path.stat().st_size
    headers = {"X-Original-File-Name": name}

    with path.open("rb") as f:
        if on_progress is not None:
            original_read = f.read
            uploaded = 0

            def tracked_read(n: int) -> bytes:
                nonlocal uploaded
                chunk = original_read(n)
                uploaded += len(chunk)
                on_progress(uploaded, size)
                return chunk

            f.read = tracked_read  # type: ignore[method-assign]

        session.post("images/upload", files={"field0": (name, f)}, headers=headers)
