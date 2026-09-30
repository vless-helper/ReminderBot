FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
COPY init_db.py update_payment_date.py ./

ENV PYTHONPATH=/app
ENV TZ=Europe/Moscow

# Бот и цикл напоминаний живут в одном процессе (bot.main)
CMD ["python", "-m", "bot.main"]
