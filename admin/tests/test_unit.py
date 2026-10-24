"""Юнит-тесты чистой логики админки (без баз): python -m unittest discover -s tests -v (из admin/, в контейнере: см. INFRASTRUCTURE.md)."""
import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("ADMIN_ACCESS", "anyone")
os.environ.setdefault("SESSION_SECRET", "x" * 40)

from fastapi import HTTPException  # noqa: E402

from adminapp import auth, config, dbcommon, moodview  # noqa: E402
from adminapp.routes import db_mongo, db_pg, kb  # noqa: E402


class AuthTests(unittest.TestCase):
    def test_csrf_stable_and_per_session(self):
        self.assertEqual(auth.csrf_for("sid1"), auth.csrf_for("sid1"))
        self.assertNotEqual(auth.csrf_for("sid1"), auth.csrf_for("sid2"))

    def test_role_allowed_modes(self):
        old = config.ACCESS
        try:
            config.ACCESS = "anyone"
            self.assertTrue(auth.role_allowed(None))
            config.ACCESS = "hr"
            self.assertTrue(auth.role_allowed("hr") and auth.role_allowed("hr_head"))
            self.assertFalse(auth.role_allowed("employee") or auth.role_allowed(None))
            config.ACCESS = "off"
            self.assertFalse(auth.role_allowed("hr"))
        finally:
            config.ACCESS = old

    def test_config_refuses_weak_secret(self):
        old = (config.ACCESS, config.SESSION_SECRET)
        try:
            config.ACCESS, config.SESSION_SECRET = "anyone", "short"
            with self.assertRaises(RuntimeError):
                config.validate()
            config.ACCESS = "bogus"
            with self.assertRaises(RuntimeError):
                config.validate()
            config.ACCESS, config.SESSION_SECRET = "off", ""
            config.validate()   # выключенной админке секрет не нужен
        finally:
            config.ACCESS, config.SESSION_SECRET = old


class DbCommonTests(unittest.TestCase):
    def test_confirm_word(self):
        dbcommon.require_confirm(f" {dbcommon.CONFIRM_WORD} ")
        for bad in ("", "подтверждаю", "yes", None):
            with self.assertRaises(HTTPException):
                dbcommon.require_confirm(bad)

    def test_fmt_value(self):
        self.assertEqual(dbcommon.fmt_value(None), "NULL")
        self.assertTrue(dbcommon.fmt_value(b"\x00\x01").startswith("\\x0001"))
        self.assertEqual(dbcommon.fmt_value({"a": "б"}), '{"a": "б"}')
        self.assertEqual(len(dbcommon.fmt_value("x" * 1000, 50)), 51)
        self.assertEqual(dbcommon.full_value(None), "")

    def test_pages(self):
        self.assertEqual(dbcommon.pages(0, 1), {"page": 1, "pages": 1, "offset": 0, "total": 0})
        self.assertEqual(dbcommon.pages(120, 3)["offset"], 100)
        self.assertEqual(dbcommon.pages(120, 99)["page"], 3)      # страница за пределами прижимается к последней


class SqlConsoleTests(unittest.TestCase):
    def test_first_word_skips_comments(self):
        self.assertEqual(db_pg._first_word("  -- c\n /* x */ select 1"), "SELECT")
        self.assertEqual(db_pg._first_word("(select 1)"), "SELECT")
        self.assertEqual(db_pg._first_word("DROP TABLE x"), "DROP")
        self.assertEqual(db_pg._first_word("--only comment"), "")

    def test_read_words_do_not_include_writes(self):
        for w in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "COPY", "DO", "CALL"):
            self.assertNotIn(w, db_pg.READ_WORDS)


class MongoGuardTests(unittest.TestCase):
    def test_forbidden_operators_found_anywhere(self):
        for bad in ({"$where": "1"}, {"a": {"$function": {}}}, {"$or": [{"x": {"$accumulator": 1}}]}):
            with self.assertRaises(HTTPException):
                db_mongo._check_ops(bad)
        db_mongo._check_ops({"status": "open", "n": {"$gt": 1}})

    def test_json_roundtrip_keeps_types(self):
        doc = {"_id": "a", "at": datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc), "n": 5}
        back = db_mongo._loads(db_mongo._dumps(doc))
        self.assertEqual(back["n"], 5)
        self.assertIsInstance(back["at"], datetime)
        with self.assertRaises(HTTPException):
            db_mongo._loads("{bad")

    def test_id_roundtrip(self):
        for _id in ("abc", 7):
            self.assertEqual(db_mongo._id_parse(db_mongo._id_str(_id)), _id)


class KbTests(unittest.TestCase):
    def test_clean_filename(self):
        self.assertEqual(kb._clean_filename("../../etc/passwd.txt"), "passwd.txt")
        self.assertEqual(kb._clean_filename("C:\\dir\\док.pdf"), "док.pdf")
        self.assertEqual(kb._clean_filename("a\x00b.md"), "ab.md")
        self.assertEqual(kb._clean_filename(None), "")


class MoodViewTests(unittest.TestCase):
    def test_weekly_index_buckets(self):
        now = datetime.now(timezone.utc)
        events = [{"ts": now - timedelta(days=1), "mood": "positive"}, {"ts": now - timedelta(days=1), "mood": "critical"},
                  {"ts": now - timedelta(weeks=3, days=1), "mood": "neutral"}, {"ts": now - timedelta(weeks=30), "mood": "positive"},
                  {"ts": now, "mood": "bogus"}]
        series = moodview.weekly_index(events, 8)
        self.assertEqual(len(series), 8)
        self.assertEqual(series[-1], 50.0)               # (100 + 0) / 2
        self.assertEqual(series[-4], 60.0)               # neutral
        self.assertEqual(sum(v is not None for v in series), 2)

    def test_spark_is_safe_svg(self):
        svg = str(moodview.spark([None, 80, 40, None, 60]))
        self.assertTrue(svg.startswith("<svg") and "polyline" in svg)
        self.assertIn("Нет данных", str(moodview.spark([None, None])))

    def test_stats_from_doc_tolerates_garbage(self):
        st = moodview.stats_from_doc({"uid": "u", "moods": {"positive": 2, "x": "y"}, "red_flag": 1, "updated_at": "2026-10-24T10:00:00+00:00"})
        self.assertEqual((st.uid, st.moods["positive"], st.red_flag, st.total), ("u", 2, True, 2))
        self.assertIsNotNone(st.updated_at)


if __name__ == "__main__":
    unittest.main()
