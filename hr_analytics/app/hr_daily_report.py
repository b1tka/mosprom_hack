import logging
from datetime import datetime
from pathlib import Path

from app.services.hr_report_aggregator import build_hr_daily_report
from app.services.pdf_report import build_hr_daily_pdf


logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)

logger = logging.getLogger(__name__)


def main():
    logger.info("Формируем ежедневный отчёт для HR")

    # Собираем аналитику по всем сотрудникам.
    report = build_hr_daily_report()

    # Формируем уникальное имя файла.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    output_path = (
        Path("reports")
        / f"hr_daily_report_{timestamp}.pdf"
    )

    # Генерируем PDF.
    pdf_path = build_hr_daily_pdf(
        report=report,
        output_path=output_path,
    )

    # Выводим результат.
    summary = report.get("summary", {})

    logger.info(
        "Ежедневный отчёт HR сформирован: %s",
        pdf_path.resolve(),
    )
    logger.info(
        "Всего сотрудников: %s",
        summary.get("total_employees", 0),
    )
    logger.info(
        "Сотрудников с аналитикой: %s",
        summary.get("employees_with_analytics", 0),
    )
    logger.info(
        "Сотрудников без аналитики: %s",
        summary.get("employees_without_analytics", 0),
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