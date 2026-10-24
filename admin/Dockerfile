# Контекст сборки — корень репозитория (нужен код метрик из hr_analytics): docker compose --profile admin build admin
FROM python:3.12-slim
WORKDIR /srv
COPY admin/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY admin/adminapp ./adminapp
# расчёт риска и индекса настроения берём из hr_analytics, без копии логики
COPY hr_analytics/app/__init__.py hr_analytics/app/models.py hr_analytics/app/analytics.py ./hr/app/
ENV PYTHONPATH=/srv/hr PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "adminapp.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]
