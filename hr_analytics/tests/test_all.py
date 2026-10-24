"""Тесты без реальных баз и почты: python -m unittest discover -s tests -v"""
import dataclasses
import email
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analytics import assess_risk, mood_index, top_problem  # noqa: E402
from app.config import Settings  # noqa: E402
from app.document import to_plain_text  # noqa: E402
from app.mailer import Mailer, build_message  # noqa: E402
from app.models import Stats  # noqa: E402
from app.render_html import render_html  # noqa: E402
from app.render_pdf import render_pdf  # noqa: E402
from app.reports import Data, build_for_role  # noqa: E402
from app.scheduler import cron_matches  # noqa: E402
from app.service import load_data, run  # noqa: E402
from app.sources import demo  # noqa: E402
from app.sources.mongo import MongoStatsSource, normalize_event, normalize_stats  # noqa: E402
from app.sources.postgres import PeopleSource  # noqa: E402

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
S = dataclasses.replace(Settings(), smtp_user="bot@yandex.ru", smtp_password="x", smtp_pause_sec=0,
                        recipient_override="", mongo_events_collection="events")


def demo_dataset():
    p, s, e = demo.demo_data(NOW)
    return Data(p, s, e, NOW)


class Normalize(unittest.TestCase):
    def test_ml_format_and_null_stage(self):
        st = normalize_stats({"uid": "u1", "stage": None, "total_requests": 3, "moods": {"neutral": 2, "negative": 1},
                              "reasons": {"workload": 1}, "red_flag": True, "last_reason": "x"})
        self.assertEqual((st.uid, st.stage, st.total, st.moods["negative"], st.red_flag), ("u1", None, 3, 1, True))

    def test_legacy_format(self):
        st = normalize_stats({"_id": "u2", "request_count": 8, "mood": {"positive": 3}, "request_tags": {"documents": 2},
                              "reason": "r"})
        self.assertEqual((st.uid, st.total, st.moods["positive"], st.reasons["documents"], st.last_reason), ("u2", 8, 3, 2, "r"))

    def test_no_uid_and_total_fallback(self):
        self.assertIsNone(normalize_stats({"moods": {}}))
        self.assertEqual(normalize_stats({"uid": "a", "moods": {"positive": 2, "critical": 1}}).total, 3)

    def test_event(self):
        e = normalize_event({"uid": "a", "ts": "2026-10-01T10:00:00Z", "mood": "critical", "reason": "workload", "urgent": True})
        self.assertTrue(e.urgent and e.ts.tzinfo)
        self.assertIsNone(normalize_event({"uid": "a"}))


class Metrics(unittest.TestCase):
    def test_risk(self):
        self.assertEqual(assess_risk(None), "no_data")
        self.assertEqual(assess_risk(Stats("a", total=5, moods={"positive": 5, "neutral": 0, "negative": 0, "critical": 0})), "low")
        self.assertEqual(assess_risk(Stats("a", total=5, moods={"positive": 1, "neutral": 2, "negative": 2, "critical": 0})), "medium")
        self.assertEqual(assess_risk(Stats("a", total=1, red_flag=True)), "high")

    def test_index_and_topic(self):
        self.assertEqual(mood_index({"positive": 1, "critical": 1}), 50)
        self.assertIsNone(mood_index({}))
        self.assertEqual(top_problem({"gratitude": 9, "workload": 2}), "workload")


class Cron(unittest.TestCase):
    def test_matches(self):
        mon = datetime(2026, 10, 26, 9, 0)   # понедельник
        sat = datetime(2026, 10, 24, 9, 0)
        self.assertTrue(cron_matches("0 9 * * 1-5", mon))
        self.assertFalse(cron_matches("0 9 * * 1-5", sat))
        self.assertTrue(cron_matches("*/15 9 * * 1", mon))
        self.assertTrue(cron_matches("0 9 * * 0", datetime(2026, 10, 25, 9, 0)))
        with self.assertRaises(ValueError):
            cron_matches("0 9 * *", mon)


class Sources(unittest.TestCase):
    def test_mongo_source(self):
        class Col:
            def __init__(s, docs): s.docs, s.q = docs, None
            def find(s, q): s.q = q; return iter(s.docs)
        cols = {"stats": Col([{"uid": "u1", "total_requests": 1, "moods": {"positive": 1}}, {"foo": 1}]),
                "events": Col([{"uid": "u1", "ts": NOW, "mood": "positive"}])}
        class Cl(dict):
            def close(self): pass
        db = {"onboarding": cols}
        src = MongoStatsSource(S, client=Cl(db))
        self.assertEqual(list(src.load_stats(["u1"])), ["u1"])
        self.assertEqual(cols["stats"].q, {"uid": {"$in": ["u1"]}})
        self.assertEqual(len(src.load_events(NOW)), 1)

    def test_postgres_source(self):
        class Cur:
            description = [SimpleNamespace(name=n) for n in ("id", "name", "surname", "role", "tag", "director_id", "email", "max_id")]
            def fetchall(s): return [("1", "Иван", "Козлов", "employee", "GTP", "2", " a@b.ru ", 5), ("2", "А", "Б", "director", None, None, None, 6)]
        class Conn:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def execute(s, q): return Cur()
        people = PeopleSource(S, connect=lambda *a, **k: Conn()).load_people()
        self.assertEqual((people[0].email, people[0].full_name, people[1].email), ("a@b.ru", "Козлов Иван", None))


class Reports(unittest.TestCase):
    def test_roles_render(self):
        d = demo_dataset()
        for role, n in (("hr", 1), ("director", 3), ("hr_head", 1)):
            out = build_for_role(role, d)
            self.assertEqual(len(out), n, role)
            for person, rep in out:
                html = render_html(rep, "x")
                self.assertIn("cid:", html)
                self.assertTrue(render_pdf(rep, "x").startswith(b"%PDF"))
                self.assertIn(rep.title, to_plain_text(rep))

    def test_director_sees_only_own(self):
        d = demo_dataset()
        smirnov = [p for p in d.people if p.surname == "Смирнов"][0]
        rep = [r for p, r in build_for_role("director", d) if p == smirnov][0]
        txt = to_plain_text(rep)
        self.assertIn("Козлов", txt)
        self.assertNotIn("Беляева", txt)

    def test_null_stage_hides_stage_column(self):
        d = demo_dataset()
        _, rep = build_for_role("hr", d)[0]
        table = [s for s in rep.sections if s["type"] == "table"][0]
        self.assertNotIn("Этап", table["columns"])
        self.assertTrue(any("Этап онбординга пока не передаётся" in s.get("text", "") for s in rep.sections))

    def test_stage_shown_when_available(self):
        d = demo_dataset()
        for st in d.stats.values():
            st.stage = "main_adaptation_part"
        _, rep = build_for_role("hr", d)[0]
        table = [s for s in rep.sections if s["type"] == "table"][0]
        self.assertIn("Этап", table["columns"])
        self.assertIn("stages", rep.charts)

    def test_director_without_subordinates_skipped(self):
        d = demo_dataset()
        d.people = [p for p in d.people if p.role != "employee"]
        self.assertEqual(build_for_role("director", d), [])

    def test_html_escaping(self):
        d = demo_dataset()
        next(iter(d.stats.values())).last_reason = "<script>alert(1)</script>"
        next(iter(d.stats.values())).red_flag = True
        _, rep = build_for_role("hr", d)[0]
        self.assertNotIn("<script>alert", render_html(rep, "x"))


class Mail(unittest.TestCase):
    class FakeSMTP:
        sent, logged = [], None
        def login(self, u, p): Mail.FakeSMTP.logged = (u, p)
        def send_message(self, m): Mail.FakeSMTP.sent.append(m)
        def quit(self): pass

    def setUp(self):
        Mail.FakeSMTP.sent = []

    def test_message_structure(self):
        msg = build_message(S, "to@x.ru", "Тема", "text", '<img src="cid:mood">', {"mood": b"\x89PNG"}, ("r.pdf", b"%PDF"))
        parsed = email.message_from_bytes(msg.as_bytes())
        kinds = [p.get_content_type() for p in parsed.walk()]
        for k in ("text/plain", "text/html", "image/png", "application/pdf"):
            self.assertIn(k, kinds)
        self.assertIn("bot@yandex.ru", parsed["From"])

    def test_run_sends_per_recipient_and_skips_without_email(self):
        d = demo_dataset()
        d.people[0].email = None   # у HR нет почты
        res = run(S, data=d, mailer=Mailer(S, smtp_factory=Mail.FakeSMTP))
        self.assertEqual([r.status for r in res].count("skipped"), 1)
        self.assertEqual(len(Mail.FakeSMTP.sent), 4)
        self.assertEqual(Mail.FakeSMTP.logged, ("bot@yandex.ru", "x"))

    def test_override_and_test_prefix(self):
        s = dataclasses.replace(S, recipient_override="me@ya.ru")
        run(s, data=demo_dataset(), mailer=Mailer(s, smtp_factory=Mail.FakeSMTP))
        self.assertTrue(all(m["To"] == "me@ya.ru" and "[TEST]" in str(email.header.make_header(email.header.decode_header(m["Subject"]))) for m in Mail.FakeSMTP.sent))

    def test_one_failure_does_not_stop_others(self):
        class Bad(Mail.FakeSMTP):
            n = 0
            def send_message(self, m):
                Bad.n += 1
                if Bad.n == 1:
                    import smtplib
                    raise smtplib.SMTPResponseException(550, b"bad rcpt")
                super().send_message(m)
        res = run(S, data=demo_dataset(), mailer=Mailer(S, smtp_factory=Bad))
        self.assertEqual([r.status for r in res].count("error"), 1)
        self.assertEqual([r.status for r in res].count("sent"), 4)

    def test_no_credentials(self):
        from app.mailer import MailerError
        with self.assertRaises(MailerError):
            Mailer(dataclasses.replace(S, smtp_password="")).check()

    def test_preview_writes_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            res = run(S, data=demo_dataset(), send=False, out_dir=Path(t))
            self.assertEqual(len(list(Path(t).glob("*.pdf"))), 5)
            self.assertNotIn("cid:", next(Path(t).glob("*.html")).read_text(encoding="utf-8"))
            self.assertTrue(all(r.status == "saved" for r in res))


if __name__ == "__main__":
    unittest.main()
