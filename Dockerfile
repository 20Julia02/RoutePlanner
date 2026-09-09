FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN addgroup --system wanderplan \
    && adduser --system --ingroup wanderplan --home /app wanderplan

COPY --chown=wanderplan:wanderplan . .
RUN chmod -R a-w /app
USER wanderplan
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
