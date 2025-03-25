FROM python:3.9-slim

WORKDIR /app

# Копируем файлы зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY bot.py ssh_manager.py .

# Устанавливаем необходимые пакеты для SSH
RUN apt-get update && \
    apt-get install -y openssh-client && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Запускаем бота
CMD ["python", "bot.py"] 