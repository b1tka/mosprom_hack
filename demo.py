"""Ручной тест всего пайплайна без FastAPI, Kafka и Redis.

    python demo.py ask "Сколько дней отпуска мне положено?"
    python demo.py mood user_1 "Я третий месяц работаю до полуночи, нет сил" --stage adaptation
    python demo.py kafka user_1 "Как проходит первая неделя?" "Нормально, но доступы так и не дали" adaptation
    python demo.py chat                 # интерактивный диалог: RAG + статистика настроения
    python demo.py stats user_1         # посмотреть накопленную статистику

Статистика пишется в stats_store.json (STORE_BACKEND=file по умолчанию).
"""
import argparse
import json

from kafka_consumer import handle_message
from mood import get_stats, process_message
from rag import ask_rag


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_ask(args) -> None:
    code, answer = ask_rag(args.question)
    print(f"[{code}] {answer}")


def cmd_mood(args) -> None:
    result = process_message(args.uid, args.text, args.stage)
    _print(result if result else "Классификация не удалась (см. логи)")


def cmd_kafka(args) -> None:
    msg = {"uid": args.uid, "hr_question": args.hr_question, "answer": args.answer, "stage": args.stage}
    result = handle_message(msg)
    _print(result if result else "Классификация не удалась (см. логи)")


def cmd_stats(args) -> None:
    stats = get_stats(args.uid)
    _print({args.uid: stats} if stats else "Статистики нет")


def cmd_chat(args) -> None:
    print(f"Диалог от имени {args.uid}. Пустая строка - выход.")
    while True:
        question = input("\nВы: ").strip()
        if not question:
            break
        code, answer = ask_rag(question)
        print(f"Корсик [{code}]: {answer}")
        result = process_message(args.uid, question, args.stage)
        if result:
            s = result[args.uid]
            print(f"  (статистика: запросов={s['total_requests']}, red_flag={s['red_flag']}, moods={s['moods']})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ask"); p.add_argument("question"); p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("mood"); p.add_argument("uid"); p.add_argument("text")
    p.add_argument("--stage", default=None); p.set_defaults(fn=cmd_mood)

    p = sub.add_parser("kafka"); p.add_argument("uid"); p.add_argument("hr_question")
    p.add_argument("answer"); p.add_argument("stage", nargs="?", default=None); p.set_defaults(fn=cmd_kafka)

    p = sub.add_parser("stats"); p.add_argument("uid"); p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("chat"); p.add_argument("--uid", default="test_user")
    p.add_argument("--stage", default=None); p.set_defaults(fn=cmd_chat)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
