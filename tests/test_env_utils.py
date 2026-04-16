import os
from pathlib import Path

from core.env_utils import load_project_env


def test_load_project_env_reads_dotenv_from_cwd():
    runtime_dir = (Path("tests") / "fixtures" / "runtime" / "env_loader").resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    env_file = runtime_dir / ".env"
    env_file.write_text("GEMINI_API_KEY=test_from_env_file\n", encoding="utf-8")

    previous_cwd = Path.cwd()
    previous_api_key = os.environ.get("GEMINI_API_KEY")
    try:
        os.chdir(runtime_dir)
        os.environ.pop("GEMINI_API_KEY", None)

        loaded = load_project_env()
        loaded_resolved = {path.resolve() for path in loaded}
        assert env_file in loaded_resolved
        assert os.getenv("GEMINI_API_KEY") == "test_from_env_file"
    finally:
        os.chdir(previous_cwd)
        if previous_api_key is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = previous_api_key
        env_file.unlink(missing_ok=True)


def test_load_project_env_overwrites_empty_env_value():
    runtime_dir = (Path("tests") / "fixtures" / "runtime" / "env_loader").resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    env_file = runtime_dir / ".env"
    env_file.write_text("GEMINI_API_KEY=test_from_env_file\n", encoding="utf-8")

    previous_cwd = Path.cwd()
    previous_api_key = os.environ.get("GEMINI_API_KEY")
    try:
        os.chdir(runtime_dir)
        os.environ["GEMINI_API_KEY"] = "   "

        load_project_env()
        assert os.getenv("GEMINI_API_KEY") == "test_from_env_file"
    finally:
        os.chdir(previous_cwd)
        if previous_api_key is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = previous_api_key
        env_file.unlink(missing_ok=True)
