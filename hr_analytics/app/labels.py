"""Русские подписи для технических значений."""

MOOD_LABELS = {"positive": "Позитивное", "neutral": "Нейтральное", "negative": "Негативное", "critical": "Критическое"}

REASON_LABELS = {
    "technical_issues": "Технические проблемы и доступы",
    "conflict": "Конфликты",
    "workload": "Нагрузка и переработки",
    "work_question": "Рабочие вопросы",
    "gratitude": "Благодарности",
    "absence": "Отпуска и отсутствие",
    "finance": "Зарплата и льготы",
    "documents": "Документы",
}

STAGE_LABELS = {
    "preparing_for_start_job": "Подготовка к выходу",
    "first_adaptation_days": "Первые дни",
    "beggining_of_adaptation": "Начало адаптации",
    "main_adaptation_part": "Основной этап",
    "completion_of_adaptation": "Завершение",
}
STAGE_UNKNOWN = "Этап не определён"

RISK_LABELS = {"high": "Высокий", "medium": "Повышенный", "low": "Норма", "no_data": "Нет данных"}

RISK_ADVICE = {
    "high": "Провести личную встречу в ближайшие 1–2 дня",
    "medium": "Запланировать личную встречу на этой неделе",
    "low": "Вмешательство не требуется",
    "no_data": "Обращений нет — уточнить, как проходит адаптация",
}

ROLE_LABELS = {
    "employee": "Новый сотрудник", "director": "Руководитель подразделения",
    "hr": "HR-менеджер", "hr_head": "Руководитель по подбору и развитию персонала",
}


def stage_label(stage):
    return STAGE_LABELS.get(stage, stage) if stage else STAGE_UNKNOWN
