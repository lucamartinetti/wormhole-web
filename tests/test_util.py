from wormhole_web.util import sanitize_filename


def test_passthrough_normal_filename():
    assert sanitize_filename("document.pdf") == "document.pdf"


def test_strips_path_traversal():
    assert sanitize_filename("../../etc/passwd") == "passwd"


def test_strips_leading_slashes():
    assert sanitize_filename("/etc/passwd") == "passwd"


def test_strips_backslash_paths():
    assert sanitize_filename("..\\..\\file.txt") == "file.txt"


def test_removes_null_bytes():
    assert sanitize_filename("file\x00.txt") == "file.txt"


def test_fallback_for_empty_after_sanitization():
    assert sanitize_filename("../../") == "upload"
    assert sanitize_filename("") == "upload"
    assert sanitize_filename(None) == "upload"


def test_preserves_spaces_and_dots():
    assert sanitize_filename("my file.tar.gz") == "my file.tar.gz"


def test_strips_control_characters():
    assert sanitize_filename("file\nname\r.txt") == "filename.txt"


def test_strips_double_quotes():
    assert sanitize_filename('my"file.txt') == "my_file.txt"
