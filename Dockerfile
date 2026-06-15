FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    wget curl gnupg xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install chromium
RUN playwright install-deps chromium

COPY . .

CMD ["sh", "-c", "Xvfb :99 -screen 0 1280x720x24 -ac & sleep 1 && DISPLAY=:99 uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
