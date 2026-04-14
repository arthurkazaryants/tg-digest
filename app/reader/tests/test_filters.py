import importlib.util
import sys
import types
import unittest
from pathlib import Path

import yaml


def _install_dependency_stubs() -> None:
    """Install lightweight stubs so reader main can be imported in unit tests."""
    if "asyncpg" not in sys.modules:
        asyncpg_stub = types.ModuleType("asyncpg")
        asyncpg_stub.Pool = object
        sys.modules["asyncpg"] = asyncpg_stub

    if "telethon" not in sys.modules:
        telethon_stub = types.ModuleType("telethon")
        telethon_stub.TelegramClient = object
        sys.modules["telethon"] = telethon_stub

    if "telethon.sessions" not in sys.modules:
        telethon_sessions_stub = types.ModuleType("telethon.sessions")
        telethon_sessions_stub.StringSession = object
        sys.modules["telethon.sessions"] = telethon_sessions_stub

    if "telethon.types" not in sys.modules:
        telethon_types_stub = types.ModuleType("telethon.types")
        telethon_types_stub.PeerChannel = object
        sys.modules["telethon.types"] = telethon_types_stub


def _load_reader_module():
    _install_dependency_stubs()
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "app" / "reader" / "src" / "main.py"
    spec = importlib.util.spec_from_file_location("reader_main", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReaderFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = _load_reader_module()
        repo_root = Path(__file__).resolve().parents[3]
        config_path = repo_root / "config" / "config.yml"
        config = yaml.safe_load(config_path.read_text())
        cls.jobs_filter = config["tag_filters"]["jobs"]
        cases_path = repo_root / "app" / "reader" / "tests" / "filter_cases.yml"
        cls.filter_cases = yaml.safe_load(cases_path.read_text())

    def test_jobs_filter_pass_cases(self):
        pass_cases = self.filter_cases.get("jobs_filter_cases", {}).get("should_pass", [])
        self.assertGreater(len(pass_cases), 0, "Add at least one pass-case in filter_cases.yml")
        for idx, text in enumerate(pass_cases, start=1):
            with self.subTest(case=f"pass_{idx}"):
                self.assertTrue(self.reader.apply_tag_filters(text.strip(), self.jobs_filter))

    def test_jobs_filter_fail_cases(self):
        fail_cases = self.filter_cases.get("jobs_filter_cases", {}).get("should_fail", [])
        self.assertGreater(len(fail_cases), 0, "Add at least one fail-case in filter_cases.yml")
        for idx, text in enumerate(fail_cases, start=1):
            with self.subTest(case=f"fail_{idx}"):
                self.assertFalse(self.reader.apply_tag_filters(text.strip(), self.jobs_filter))

    def test_exclude_keywords_have_priority(self):
        tag_filter = {
            "include_keywords": ["cto"],
            "exclude_keywords": ["intern"],
        }
        text = "CTO Intern at startup"
        self.assertFalse(self.reader.apply_tag_filters(text, tag_filter))

    def test_should_save_post_accepts_when_any_tag_matches(self):
        tag_filters = {
            "jobs": {"include_keywords": ["cto"]},
            "other": {"include_keywords": ["release notes"]},
        }
        text = "Ищем CTO в продуктовую компанию"
        self.assertTrue(self.reader.should_save_post(text, ["other", "jobs"], tag_filters))


if __name__ == "__main__":
    unittest.main()
