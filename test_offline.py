"""Тесты логики без LLM, Chroma, Redis и Kafka.

    python -m unittest test_offline -v
"""
import copy
import os
import tempfile
import unittest
from unittest import mock

import mood
import rag
from prompts import NO_INFO_ANSWER, NO_INFO_TAG, NOT_WORK_ANSWER, NOT_WORK_TAG
from store import FileStore

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


if __name__ == "__main__":
    unittest.main()
