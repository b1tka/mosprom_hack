"""Тесты логики без LLM, Chroma, Redis и Kafka.

    python -m unittest discover -s tests -v   (из папки ml/)
"""
import copy
import os
import tempfile
import unittest
from unittest import mock

from app import alerts
from app import kafka_consumer
from app import mood
from app import rag
from app.prompts import NO_INFO_ANSWER, NO_INFO_TAG, NOT_WORK_ANSWER, NOT_WORK_TAG
from app.store import FileStore

_real_publish = alerts.publish_red_flag
_patcher = mock.patch.object(alerts, "publish_red_flag", return_value=True)   # тесты не ходят в Kafka


def setUpModule():
    _patcher.start()


def tearDownModule():
    _patcher.stop()


CRITICAL = {"mood": "critical", "reason": "absence", "urgent": True, "explanation": "Не дают отпуск."}
NEUTRAL = {"mood": "neutral", "reason": "work_question", "urgent": False, "explanation": ""}


class ParseTests(unittest.TestCase):
    def test_plain_json(self):
        raw = '{"mood": "neutral", "reason": "absence", "urgent": false, "explanation": ""}'
        self.assertEqual(mood._parse(raw)["reason"], "absence")

    def test_markdown_fence_and_chatter(self):
        raw = 'Вот ответ:\n```json\n{"mood": "critical", "reason": "workload", "urgent": true, "explanation": "Переработки."}\n```'
        parsed = mood._parse(raw)
        self.assertTrue(parsed["urgent"])
        self.assertEqual(parsed["explanation"], "Переработки.")

    def test_not_urgent_clears_explanation(self):
        raw = '{"mood": "neutral", "reason": "finance", "urgent": false, "explanation": "лишнее"}'
        self.assertEqual(mood._parse(raw)["explanation"], "")

    def test_invalid_values_rejected(self):
        self.assertIsNone(mood._parse('{"mood": "angry", "reason": "absence", "urgent": false}'))
        self.assertIsNone(mood._parse("не json"))


class StatsTests(unittest.TestCase):
    def test_new_user(self):
        s = mood.update_stats(None, CRITICAL, "adaptation")
        self.assertEqual(s["total_requests"], 1)
        self.assertEqual(s["moods"]["critical"], 1)
        self.assertEqual(s["reasons"]["absence"], 1)
        self.assertEqual(s["stage"], "adaptation")
        self.assertTrue(s["red_flag"])

    def test_old_stats_not_mutated(self):
        old = mood.update_stats(None, NEUTRAL, "adaptation")
        snapshot = copy.deepcopy(old)
        mood.update_stats(old, CRITICAL, "probation")
        self.assertEqual(old, snapshot)

    def test_red_flag_and_last_reason_are_sticky(self):
        s = mood.update_stats(None, CRITICAL)
        s = mood.update_stats(s, NEUTRAL)
        self.assertTrue(s["red_flag"])
        self.assertEqual(s["last_reason"], "Не дают отпуск.")
        self.assertEqual(s["total_requests"], 2)

    def test_incomplete_old_record(self):
        s = mood.update_stats({"total_requests": 3, "moods": {"neutral": 3}}, NEUTRAL)
        self.assertEqual(s["total_requests"], 4)
        self.assertEqual(s["moods"]["critical"], 0)


class StoreTests(unittest.TestCase):
    def test_file_store_roundtrip_and_process_message(self):
        with tempfile.TemporaryDirectory() as d:
            store = FileStore(os.path.join(d, "stats.json"))
            self.assertIsNone(store.get("u1"))
            with mock.patch.object(mood, "get_store", return_value=store), \
                 mock.patch.object(mood, "classify_message", side_effect=[CRITICAL, NEUTRAL, None]):
                r1 = mood.process_message("u1", "текст", "adaptation")
                r2 = mood.process_message("u1", "текст")
                r3 = mood.process_message("u1", "текст")  # классификация не удалась
            self.assertIn("u1", r1)
            self.assertEqual(r2["u1"]["total_requests"], 2)
            self.assertIsNone(r3)
            self.assertEqual(store.get("u1")["total_requests"], 2)  # r3 ничего не испортил


class RagTests(unittest.TestCase):
    def _ask(self, llm_answer):
        with mock.patch.object(rag, "_retrieve", return_value="контекст"), \
             mock.patch.object(rag, "_generate", return_value=llm_answer):
            return rag.ask_rag("вопрос")

    def test_answer_found(self):
        self.assertEqual(self._ask("28 календарных дней."), (200, "28 календарных дней."))

    def test_no_info_calls_hr(self):
        self.assertEqual(self._ask(NO_INFO_TAG), (400, NO_INFO_ANSWER))

    def test_not_work_question(self):
        self.assertEqual(self._ask(NOT_WORK_TAG), (200, NOT_WORK_ANSWER))

    def test_tag_with_extra_text(self):
        self.assertEqual(self._ask(f"Ответ: {NO_INFO_TAG}.")[0], 400)

    def test_empty_question_does_not_call_model(self):
        with mock.patch.object(rag, "_generate") as gen:
            self.assertEqual(rag.ask_rag("   "), (200, NOT_WORK_ANSWER))
            gen.assert_not_called()


class DialogTests(unittest.TestCase):
    TICKET = {
        "type": "ticket", "uid": "u1", "ticket_number": 7, "closed_at": "2026-10-24T12:00:00Z", "stage": None,
        "messages": [
            {"from": "employee", "text": "Не дают пропуск", "attachments": 0},
            {"from": "bot", "text": "Передаю HR", "attachments": 0},
            {"from": "hr", "text": "Оформили", "attachments": 0},
            {"from": "employee", "text": "", "attachments": 1},
        ],
    }

    def test_transcript(self):
        t = kafka_consumer.build_transcript(self.TICKET["messages"])
        self.assertEqual(t.splitlines(), [
            "Сотрудник: Не дают пропуск", "Бот: Передаю HR", "HR: Оформили", "Сотрудник: [вложений: 1]"])

    def test_transcript_drops_oldest_when_too_long(self):
        msgs = [{"from": "employee", "text": f"{i} " + "x" * 5000} for i in range(5)]
        t = kafka_consumer.build_transcript(msgs)
        self.assertLessEqual(len(t), kafka_consumer.MAX_DIALOG_CHARS)
        self.assertIn("4 xxx", t)
        self.assertNotIn("0 xxx", t)

    def test_ticket_one_verdict_and_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            store = FileStore(os.path.join(d, "stats.json"))
            with mock.patch.object(mood, "get_store", return_value=store), \
                 mock.patch.object(mood, "classify_dialog", return_value=NEUTRAL) as cls:
                r1 = kafka_consumer.handle_message(self.TICKET)
                r2 = kafka_consumer.handle_message(self.TICKET)             # повторная доставка
                reopened = {**self.TICKET, "closed_at": "2026-10-25T09:00:00Z"}
                r3 = kafka_consumer.handle_message(reopened)               # закрыт заново
            self.assertEqual(r1["u1"]["total_requests"], 1)
            self.assertEqual(r2["u1"]["total_requests"], 1)                # не удвоилось
            self.assertEqual(r3["u1"]["total_requests"], 2)
            self.assertEqual(store.get("u1")["total_requests"], 2)
            self.assertIn("updated_at", store.get("u1"))
            self.assertIn("Сотрудник: Не дают пропуск", cls.call_args_list[0].args[0])

    def test_ticket_without_employee_text_skipped(self):
        msg = {**self.TICKET, "messages": [{"from": "hr", "text": "Здравствуйте"}]}
        with mock.patch.object(mood, "classify_dialog") as cls:
            self.assertIsNone(kafka_consumer.handle_message(msg))
            cls.assert_not_called()

    def test_ticket_classification_failure_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            store = FileStore(os.path.join(d, "stats.json"))
            with mock.patch.object(mood, "get_store", return_value=store), \
                 mock.patch.object(mood, "classify_dialog", return_value=None):
                self.assertIsNone(kafka_consumer.handle_message(self.TICKET))
            self.assertIsNone(store.get("u1"))

    def test_old_hr_answer_format_still_works(self):
        with tempfile.TemporaryDirectory() as d:
            store = FileStore(os.path.join(d, "stats.json"))
            with mock.patch.object(mood, "get_store", return_value=store), \
                 mock.patch.object(mood, "classify_message", return_value=NEUTRAL) as cls:
                r = kafka_consumer.handle_message({"uid": "u2", "hr_question": "Как дела?", "answer": "Нормально", "stage": None})
            self.assertEqual(r["u2"]["total_requests"], 1)
            self.assertIn("Вопрос HR: Как дела?", cls.call_args.args[0])


class AlertTests(unittest.TestCase):
    def _store(self, d):
        return FileStore(os.path.join(d, "stats.json"))

    def test_urgent_question_publishes_alert_with_text(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(mood, "get_store", return_value=self._store(d)), \
             mock.patch.object(mood, "classify_message", return_value=CRITICAL), \
             mock.patch.object(alerts, "publish_red_flag", return_value=True) as pub:
            mood.process_message("u1", "нет сил, увольняюсь")
        pub.assert_called_once()
        args = pub.call_args.args
        self.assertEqual(args[:2], ("u1", "bot_question"))
        self.assertEqual(args[4], "нет сил, увольняюсь")

    def test_not_urgent_no_alert(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(mood, "get_store", return_value=self._store(d)), \
             mock.patch.object(mood, "classify_message", return_value=NEUTRAL), \
             mock.patch.object(alerts, "publish_red_flag") as pub:
            mood.process_message("u1", "как оформить отпуск")
        pub.assert_not_called()

    def test_ticket_alert_once_even_if_delivered_twice(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(mood, "get_store", return_value=self._store(d)), \
             mock.patch.object(mood, "classify_dialog", return_value=CRITICAL), \
             mock.patch.object(alerts, "publish_red_flag", return_value=True) as pub:
            msg = DialogTests.TICKET
            kafka_consumer.handle_message(msg)
            kafka_consumer.handle_message(msg)
        pub.assert_called_once()
        self.assertEqual(pub.call_args.args[1], "ticket")
        self.assertEqual(pub.call_args.args[3], 7)

    def test_publish_failure_does_not_break_stats(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            with mock.patch.object(mood, "get_store", return_value=store), \
                 mock.patch.object(mood, "classify_message", return_value=CRITICAL), \
                 mock.patch.object(alerts, "publish_red_flag", _real_publish), \
                 mock.patch.object(alerts, "_get_producer", side_effect=RuntimeError("kafka down")):
                r = mood.process_message("u1", "нет сил")
            self.assertEqual(r["u1"]["total_requests"], 1)
            self.assertTrue(store.get("u1")["red_flag"])

    def test_alert_payload(self):
        a = alerts.build_alert("u1", "ticket", CRITICAL, 7, "x" * 500)
        self.assertEqual((a["type"], a["uid"], a["source"], a["ticket_number"]), ("red_flag", "u1", "ticket", 7))
        self.assertEqual(len(a["text"]), alerts.MAX_TEXT)
        self.assertEqual(a["explanation"], "Не дают отпуск.")


if __name__ == "__main__":
    unittest.main()
