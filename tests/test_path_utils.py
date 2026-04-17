from ui.path_utils import normalize_input_path


def test_normalize_input_path_unc_file_uri():
    raw = "file://srv-nt/Games-MLV/Nexters/01_RAG/ready-tm-backup/Marketing/HW.tmx"
    normalized = normalize_input_path(raw)
    assert normalized == r"\\srv-nt\Games-MLV\Nexters\01_RAG\ready-tm-backup\Marketing\HW.tmx"


def test_normalize_input_path_local_file_uri():
    raw = "file:///C:/Data/tmx/sample.tmx"
    normalized = normalize_input_path(raw)
    assert normalized == r"C:\Data\tmx\sample.tmx"


def test_normalize_input_path_plain_path_kept():
    raw = r"C:\Data\tmx\sample.tmx"
    assert normalize_input_path(raw) == raw
