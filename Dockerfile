# Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY bot/ ./bot/

# Создаем директорию для базы данных
RUN mkdir -p /app/data

# Переменные окружения
ENV PYTHONPATH=/app
ENV TZ=Europe/Moscow

# Команда по умолчанию (запуск бота)
CMD ["python", "-m", "bot.main"]