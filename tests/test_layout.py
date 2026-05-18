from pathlib import Path


def test_source_modules_live_directly_under_src():
    assert not Path("src/fel_dolby_vision_movies").exists()
    assert Path("src/main.py").exists()
    assert Path("src/parser.py").exists()


def test_root_has_no_python_utility_scripts():
    root_scripts = sorted(Path(".").glob("*.py"))
    assert root_scripts == []
