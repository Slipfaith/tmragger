from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path


def test_sibling_output_dir_is_beside_each_input() -> None:
    spec = importlib.util.find_spec("core.output_paths")
    assert spec is not None
    module = importlib.import_module("core.output_paths")

    assert module.sibling_output_dir(Path("a/source.tmx")) == Path("a/output")
    assert module.sibling_output_dir(Path("b/nested/book.xlsx")) == Path(
        "b/nested/output"
    )
