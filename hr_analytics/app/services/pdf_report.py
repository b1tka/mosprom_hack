from __future__ import annotations

from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


# Цвета оформления
NAVY = colors.HexColor("#17324D")
BLUE = colors.HexColor("#2864A6")
LIGHT_BLUE = colors.HexColor("#EAF2F8")
LIGHT_GREY = colors.HexColor("#F3F5F7")
DARK_GREY = colors.HexColor("#455468")
GREEN = colors.HexColor("#E4F4E8")
ORANGE = colors.HexColor("#FFF0D9")
RED = colors.HexColor("#FCE8E6")
WHITE = colors.white

STAGE_LABELS = {
    "preparing_for_start_job": "Подготовка к выходу на работу",
    "first_adaptation_days": "Первые дни адаптации",
    "beggining_of_adaptation": "Начало адаптации",
    "main_adaptation_part": "Основная часть адаптации",
    "completion_of_adaptation": "Завершение адаптации",
}

MOOD_LABELS = {
    "positive": "Позитивное",
    "neutral": "Нейтральное",
    "negative": "Негативное",
    "critical": "Критическое",
}

TAG_LABELS = {
    "technical_issues": "Технические проблемы",
    "conflict": "Конфликты",
    "workload": "Рабочая нагрузка",
    "work_question": "Рабочие вопросы",
    "gratitude": "Благодарности",
    "absence": "Отсутствие",
    "finance": "Финансовые вопросы",
    "documents": "Документы",
}


def _register_fonts() -> tuple[str, str]:
    """Регистрирует шрифты с поддержкой кириллицы."""

    font_candidates = [
        (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        ),
    ]

    for regular_path, bold_path in font_candidates:
        if regular_path.exists() and bold_path.exists():
            pdfmetrics.registerFont(
                TTFont("ReportFont", str(regular_path))
            )
            pdfmetrics.registerFont(
                TTFont("ReportFont-Bold", str(bold_path))
            )
            return "ReportFont", "ReportFont-Bold"

    raise FileNotFoundError(
        "Не удалось найти шрифт с поддержкой кириллицы. "
        "Для Windows проверь наличие C:/Windows/Fonts/arial.ttf. "
        "Для Linux установи DejaVu Sans."
    )


def _format_datetime(value: str | None) -> str:
    if not value:
        return "Не указано"

    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%d.%m.%Y %H:%M UTC")
    except (TypeError, ValueError):
        return str(value)


def _paragraph(
    text: object,
    style: ParagraphStyle,
) -> Paragraph:
    """Создаёт абзац, безопасно обрабатывая текст из данных."""

    safe_text = escape(str(text)) if text is not None else "—"
    return Paragraph(safe_text, style)


def _make_table(
    data: list[list],
    widths: list[float],
    header: bool = True,
) -> Table:
    table = Table(
        data,
        colWidths=widths,
        repeatRows=1 if header else 0,
        hAlign="LEFT",
    )

    commands = [
        ("FONTNAME", (0, 0), (-1, -1), "ReportFont"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), DARK_GREY),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D9E0E6")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]

    if header:
        commands.extend([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "ReportFont-Bold"),
        ])

        if len(data) > 1:
            for row_index in range(1, len(data)):
                if row_index % 2 == 0:
                    commands.append(
                        ("BACKGROUND", (0, row_index), (-1, row_index), LIGHT_GREY)
                    )

    table.setStyle(TableStyle(commands))
    return table


def build_director_pdf(
    report: dict,
    output_path: str | Path,
) -> Path:
    """
    Создаёт PDF-отчёт на основании результата build_director_report().

    report — словарь с общей статистикой и данными сотрудников.
    output_path — путь, по которому нужно сохранить PDF.
    """

    regular_font, bold_font = _register_fonts()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name="ReportTitle",
        fontName=bold_font,
        fontSize=21,
        leading=27,
        textColor=NAVY,
        alignment=TA_LEFT,
        spaceAfter=5 * mm,
    ))

    styles.add(ParagraphStyle(
        name="SectionTitle",
        fontName=bold_font,
        fontSize=14,
        leading=18,
        textColor=NAVY,
        spaceBefore=5 * mm,
        spaceAfter=3 * mm,
        keepWithNext=True,
    ))

    styles.add(ParagraphStyle(
        name="ReportBody",
        fontName=regular_font,
        fontSize=9,
        leading=13,
        textColor=DARK_GREY,
        spaceAfter=2 * mm,
    ))

    styles.add(ParagraphStyle(
        name="SmallText",
        fontName=regular_font,
        fontSize=8,
        leading=11,
        textColor=DARK_GREY,
    ))

    styles.add(ParagraphStyle(
        name="EmployeeTitle",
        fontName=bold_font,
        fontSize=11,
        leading=15,
        textColor=BLUE,
        spaceBefore=3 * mm,
        spaceAfter=2 * mm,
        keepWithNext=True,
    ))

    styles.add(ParagraphStyle(
        name="Centered",
        fontName=regular_font,
        fontSize=8.5,
        leading=12,
        alignment=TA_CENTER,
        textColor=DARK_GREY,
    ))

    summary = report.get("summary") or {}
    stage_counts = report.get("stage_counts") or {}
    mood_counts = report.get("mood_counts") or {}
    request_tag_counts = report.get("request_tag_counts") or {}
    employees = report.get("employees") or []

    generated_at = _format_datetime(report.get("generated_at"))
    director_id = report.get("director_id", "Не указан")

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Отчёт по адаптации сотрудников",
        author="HR Analytics",
    )

    story = []

    # ---------------------------------------------------------
    # 1. Заголовок и общая сводка
    # ---------------------------------------------------------

    story.append(Paragraph(
        "Отчёт по адаптации сотрудников",
        styles["ReportTitle"],
    ))

    story.append(Paragraph(
        f"Руководитель: {escape(str(director_id))}",
        styles["ReportBody"],
    ))

    story.append(Paragraph(
        f"Дата формирования: {escape(generated_at)}",
        styles["ReportBody"],
    ))

    story.append(HRFlowable(
        width="100%",
        thickness=1.2,
        color=BLUE,
        spaceBefore=2 * mm,
        spaceAfter=4 * mm,
    ))

    story.append(Paragraph("Общая сводка", styles["SectionTitle"]))

    summary_rows = [
        ["Показатель", "Значение"],
        ["Всего сотрудников", str(summary.get("total_employees", 0))],
        [
            "Сотрудников с аналитикой",
            str(summary.get("employees_with_analytics", 0)),
        ],
        [
            "Сотрудников без аналитики",
            str(summary.get("employees_without_analytics", 0)),
        ],
        ["Всего обращений к боту", str(summary.get("total_requests", 0))],
        [
            "Сотрудников с красными флагами",
            str(summary.get("employees_with_red_flags", 0)),
        ],
    ]

    summary_table = _make_table(
        summary_rows,
        [110 * mm, 55 * mm],
    )

    # Подсвечиваем количество сотрудников с красными флагами.
    if int(summary.get("employees_with_red_flags", 0) or 0) > 0:
        summary_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 5), (-1, 5), RED),
            ("FONTNAME", (0, 5), (-1, 5), bold_font),
        ]))

    story.append(summary_table)
    story.append(Spacer(1, 3 * mm))

    # ---------------------------------------------------------
    # 2. Этапы адаптации
    # ---------------------------------------------------------

    story.append(Paragraph(
        "Распределение по этапам адаптации",
        styles["SectionTitle"],
    ))

    stage_rows = [["Этап", "Сотрудников"]]

    for stage, count in stage_counts.items():
        stage_rows.append([
            STAGE_LABELS.get(stage, stage),
            str(count),
        ])

    if len(stage_rows) == 1:
        stage_rows.append(["Нет данных", "0"])

    story.append(_make_table(
        stage_rows,
        [130 * mm, 35 * mm],
    ))

    # ---------------------------------------------------------
    # 3. Настроение
    # ---------------------------------------------------------

    story.append(Paragraph(
        "Распределение настроений",
        styles["SectionTitle"],
    ))

    mood_rows = [["Настроение", "Количество"]]

    for mood_key, mood_label in MOOD_LABELS.items():
        mood_rows.append([
            mood_label,
            str(mood_counts.get(mood_key, 0)),
        ])

    story.append(_make_table(
        mood_rows,
        [130 * mm, 35 * mm],
    ))

    # ---------------------------------------------------------
    # 4. Темы обращений
    # ---------------------------------------------------------

    story.append(Paragraph(
        "Темы обращений к боту",
        styles["SectionTitle"],
    ))

    tag_rows = [["Тема", "Количество"]]

    sorted_tags = sorted(
        request_tag_counts.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    for tag, count in sorted_tags:
        tag_rows.append([
            TAG_LABELS.get(tag, tag),
            str(count),
        ])

    if len(tag_rows) == 1:
        tag_rows.append(["Нет данных", "0"])

    story.append(_make_table(
        tag_rows,
        [130 * mm, 35 * mm],
    ))

    # ---------------------------------------------------------
    # 5. Подробная информация по сотрудникам
    # ---------------------------------------------------------

    story.append(Paragraph(
        "Сотрудники",
        styles["SectionTitle"],
    ))

    if not employees:
        story.append(Paragraph(
            "Нет сотрудников для отображения.",
            styles["ReportBody"],
        ))

    for employee in employees:
        full_name = " ".join(filter(
            None,
            [
                employee.get("surname"),
                employee.get("name"),
            ],
        )) or "Имя не указано"

        department = employee.get("department") or "Не указано"
        analytics = employee.get("analytics")
        analytics_available = employee.get("analytics_available", False)

        employee_content = [
            Paragraph(
                escape(full_name),
                styles["EmployeeTitle"],
            ),
            Paragraph(
                f"Подразделение: {escape(str(department))}",
                styles["ReportBody"],
            ),
        ]

        if not analytics_available or not analytics:
            employee_content.append(Paragraph(
                "Аналитика для сотрудника пока недоступна.",
                styles["ReportBody"],
            ))
            story.append(KeepTogether(employee_content))
            continue

        stage = analytics.get("stage", "Не указан")
        stage_label = STAGE_LABELS.get(stage, stage)
        request_count = analytics.get("request_count", 0)
        red_flag = bool(analytics.get("red_flag", False))
        reason = analytics.get("reason") or "Причина не указана"
        mood = analytics.get("mood") or {}

        employee_rows = [
            ["Показатель", "Значение"],
            ["Этап адаптации", stage_label],
            ["Количество обращений", str(request_count)],
            ["Позитивное настроение", str(mood.get("positive", 0))],
            ["Нейтральное настроение", str(mood.get("neutral", 0))],
            ["Негативное настроение", str(mood.get("negative", 0))],
            ["Критическое настроение", str(mood.get("critical", 0))],
            [
                "Красный флаг",
                "Требуется внимание" if red_flag else "Не выявлен",
            ],
        ]

        employee_table = _make_table(
            employee_rows,
            [90 * mm, 75 * mm],
        )

        if red_flag:
            employee_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 7), (-1, 7), RED),
                ("FONTNAME", (0, 7), (-1, 7), bold_font),
            ]))
            employee_content.append(Paragraph(
                f"Причина: {escape(str(reason))}",
                styles["ReportBody"],
            ))

        employee_content.append(employee_table)
        employee_content.append(Spacer(1, 3 * mm))

        story.append(KeepTogether(employee_content))

    # ---------------------------------------------------------
    # Нижний колонтитул
    # ---------------------------------------------------------

    def draw_footer(canvas, document):
        canvas.saveState()
        canvas.setFont(regular_font, 8)
        canvas.setFillColor(DARK_GREY)

        canvas.drawString(
            18 * mm,
            10 * mm,
            "HR Analytics | Отчёт по адаптации",
        )

        canvas.drawRightString(
            A4[0] - 18 * mm,
            10 * mm,
            f"Страница {document.page}",
        )

        canvas.restoreState()

    doc.build(
        story,
        onFirstPage=draw_footer,
        onLaterPages=draw_footer,
    )

    return output_path

def build_hr_daily_pdf(
    report: dict,
    output_path: str | Path,
) -> Path:
    """
    Создаёт ежедневный PDF-отчёт для HR по всем сотрудникам.
    """

    regular_font, bold_font = _register_fonts()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name="HRTitle",
        fontName=bold_font,
        fontSize=21,
        leading=27,
        textColor=NAVY,
        spaceAfter=5 * mm,
    ))

    styles.add(ParagraphStyle(
        name="HRSection",
        fontName=bold_font,
        fontSize=13,
        leading=17,
        textColor=NAVY,
        spaceBefore=5 * mm,
        spaceAfter=3 * mm,
        keepWithNext=True,
    ))

    styles.add(ParagraphStyle(
        name="HRBody",
        fontName=regular_font,
        fontSize=9,
        leading=13,
        textColor=DARK_GREY,
        spaceAfter=2 * mm,
    ))

    styles.add(ParagraphStyle(
        name="HRSmall",
        fontName=regular_font,
        fontSize=8,
        leading=11,
        textColor=DARK_GREY,
    ))

    summary = report.get("summary") or {}
    stage_counts = report.get("stage_counts") or {}
    mood_counts = report.get("mood_counts") or {}
    request_tag_counts = report.get("request_tag_counts") or {}
    employees = report.get("employees") or []

    generated_at = _format_datetime(report.get("generated_at"))

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Ежедневный HR-отчёт по адаптации",
        author="HR Analytics",
    )

    story = []

    # -----------------------------------------------------
    # 1. Заголовок
    # -----------------------------------------------------

    story.append(Paragraph(
        "Ежедневный отчёт HR",
        styles["HRTitle"],
    ))

    story.append(Paragraph(
        "Адаптация новых сотрудников",
        styles["HRBody"],
    ))

    story.append(Paragraph(
        f"Дата формирования: {escape(generated_at)}",
        styles["HRBody"],
    ))

    story.append(HRFlowable(
        width="100%",
        thickness=1.2,
        color=BLUE,
        spaceBefore=2 * mm,
        spaceAfter=4 * mm,
    ))

    # -----------------------------------------------------
    # 2. Общая сводка
    # -----------------------------------------------------

    story.append(Paragraph(
        "Общая сводка",
        styles["HRSection"],
    ))

    summary_rows = [
        ["Показатель", "Значение"],
        [
            "Всего сотрудников на адаптации",
            str(summary.get("total_employees", 0)),
        ],
        [
            "Сотрудников с доступной аналитикой",
            str(summary.get("employees_with_analytics", 0)),
        ],
        [
            "Сотрудников без аналитики",
            str(summary.get("employees_without_analytics", 0)),
        ],
        [
            "Всего обращений к боту",
            str(summary.get("total_requests", 0)),
        ],
        [
            "Сотрудников с красными флагами",
            str(summary.get("employees_with_red_flags", 0)),
        ],
    ]

    summary_table = _make_table(
        summary_rows,
        [115 * mm, 50 * mm],
    )

    if int(summary.get("employees_with_red_flags", 0) or 0) > 0:
        summary_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 5), (-1, 5), RED),
            ("FONTNAME", (0, 5), (-1, 5), bold_font),
        ]))

    if int(summary.get("employees_without_analytics", 0) or 0) > 0:
        summary_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 3), (-1, 3), ORANGE),
        ]))

    story.append(summary_table)

    # -----------------------------------------------------
    # 3. Распределение по этапам
    # -----------------------------------------------------

    story.append(Paragraph(
        "Распределение по этапам адаптации",
        styles["HRSection"],
    ))

    stage_rows = [["Этап", "Количество сотрудников"]]

    for stage, count in stage_counts.items():
        stage_rows.append([
            STAGE_LABELS.get(stage, stage),
            str(count),
        ])

    if len(stage_rows) == 1:
        stage_rows.append(["Нет данных", "0"])

    story.append(_make_table(
        stage_rows,
        [105 * mm, 60 * mm],
    ))

    # -----------------------------------------------------
    # 4. Темы обращений
    # -----------------------------------------------------

    story.append(Paragraph(
        "Наиболее частые темы обращений",
        styles["HRSection"],
    ))

    tag_rows = [["Тема", "Количество обращений"]]

    for tag, count in sorted(
        request_tag_counts.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        tag_rows.append([
            TAG_LABELS.get(tag, tag),
            str(count),
        ])

    if len(tag_rows) == 1:
        tag_rows.append(["Нет данных", "0"])

    story.append(_make_table(
        tag_rows,
        [105 * mm, 60 * mm],
    ))

    # -----------------------------------------------------
    # 5. Распределение настроений
    # -----------------------------------------------------

    story.append(Paragraph(
        "Распределение настроений",
        styles["HRSection"],
    ))

    mood_rows = [["Настроение", "Количество оценок"]]

    for mood_key, mood_label in MOOD_LABELS.items():
        mood_rows.append([
            mood_label,
            str(mood_counts.get(mood_key, 0)),
        ])

    story.append(_make_table(
        mood_rows,
        [105 * mm, 60 * mm],
    ))

    # -----------------------------------------------------
    # 6. Сотрудники с красными флагами
    # -----------------------------------------------------

    story.append(Paragraph(
        "Сотрудники, требующие внимания",
        styles["HRSection"],
    ))

    flagged_employees = []

    for employee in employees:
        analytics = employee.get("analytics")

        if not employee.get("analytics_available") or not analytics:
            continue

        if analytics.get("red_flag"):
            flagged_employees.append(employee)

    if flagged_employees:
        for employee in flagged_employees:
            full_name = " ".join(filter(
                None,
                [
                    employee.get("surname"),
                    employee.get("name"),
                ],
            )) or "Имя не указано"

            department = employee.get("department") or "Не указано"
            analytics = employee["analytics"]
            stage = analytics.get("stage", "Не указан")
            reason = analytics.get("reason") or "Причина не указана"

            risk_text = (
                f"<b>{escape(full_name)}</b><br/>"
                f"Подразделение: {escape(str(department))}<br/>"
                f"Этап: {escape(STAGE_LABELS.get(stage, stage))}<br/>"
                f"Обращений: {escape(str(analytics.get('request_count', 0)))}<br/>"
                f"Причина: {escape(str(reason))}"
            )

            risk_table = Table(
                [[Paragraph(risk_text, styles["HRBody"])]],
                colWidths=[165 * mm],
            )

            risk_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), RED),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E6B8B5")),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]))

            story.append(risk_table)
            story.append(Spacer(1, 3 * mm))
    else:
        story.append(Paragraph(
            "Сотрудников с установленным красным флагом нет.",
            styles["HRBody"],
        ))

    # -----------------------------------------------------
    # 7. Сотрудники без аналитики
    # -----------------------------------------------------

    missing_analytics = [
        employee
        for employee in employees
        if not employee.get("analytics_available")
    ]

    if missing_analytics:
        story.append(Paragraph(
            "Сотрудники без доступной аналитики",
            styles["HRSection"],
        ))

        missing_rows = [["Сотрудник", "Подразделение"]]

        for employee in missing_analytics:
            full_name = " ".join(filter(
                None,
                [
                    employee.get("surname"),
                    employee.get("name"),
                ],
            )) or "Имя не указано"

            missing_rows.append([
                full_name,
                employee.get("department") or "Не указано",
            ])

        story.append(_make_table(
            missing_rows,
            [90 * mm, 75 * mm],
        ))

        story.append(Spacer(1, 2 * mm))

        story.append(Paragraph(
            "Отсутствие аналитики не означает отсутствие проблем. "
            "Рекомендуется проверить доступность данных по этим сотрудникам.",
            styles["HRSmall"],
        ))

    # -----------------------------------------------------
    # Нижний колонтитул
    # -----------------------------------------------------

    def draw_footer(canvas, document):
        canvas.saveState()
        canvas.setFont(regular_font, 8)
        canvas.setFillColor(DARK_GREY)

        canvas.drawString(
            18 * mm,
            10 * mm,
            "HR Analytics | Ежедневный отчёт",
        )

        canvas.drawRightString(
            A4[0] - 18 * mm,
            10 * mm,
            f"Страница {document.page}",
        )

        canvas.restoreState()

    doc.build(
        story,
        onFirstPage=draw_footer,
        onLaterPages=draw_footer,
    )

    return output_path