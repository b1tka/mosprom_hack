import logging
import os
from datetime import datetime
from pathlib import Path

from app.services.pdf_report import build_director_pdf
from app.services.report_aggregator import build_director_report


logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)

logger = logging.getLogger(__name__)


def main():
    # UUID тестового руководителя.
    director_id = os.getenv(
        "HR_ANALYTICS_TEST_DIRECTOR_ID",
        "10000000-0000-0000-0000-000000000001",
    )

    logger.info(
        "Формируем отчёт по руководителю %s",
        director_id,
    )

    # Шаг 1. Получаем и объединяем данные из PostgreSQL и Redis.
    report = build_director_report(director_id)

    # Шаг 2. Формируем имя выходного PDF-файла.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    output_path = (
        Path("reports")
        / f"director_report_{timestamp}.pdf"
    )

    # Шаг 3. Создаём PDF.
    pdf_path = build_director_pdf(
        report=report,
        output_path=output_path,
    )

    # Шаг 4. Выводим краткую информацию о результате.
    summary = report.get("summary", {})

    logger.info("PDF успешно сформирован: %s", pdf_path.resolve())
    logger.info(
        "Всего сотрудников: %s",
        summary.get("total_employees", 0),
    )
    logger.info(
        "Сотрудников с аналитикой: %s",
        summary.get("employees_with_analytics", 0),
    )
    logger.info(
        "Всего обращений: %s",
        summary.get("total_requests", 0),
    )
    logger.info(
        "Сотрудников с красными флагами: %s",
        summary.get("employees_with_red_flags", 0),
    )


if __name__ == "__main__":
    main()