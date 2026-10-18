FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN addgroup --system planyourroute \
    && adduser --system --ingroup planyourroute --home /app planyourroute

COPY --chown=planyourroute:planyourroute . .
RUN mkdir -p /app/data/prepared \
    && chown planyourroute:planyourroute /app/data/prepared \
    && chmod -R a-w /app \
    && chmod u+w /app/data/prepared
USER planyourroute
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
