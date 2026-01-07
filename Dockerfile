FROM python:3.10-slim

WORKDIR /app

# تثبيت ffmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# نسخ requirements وتثبيتها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي المشروع
COPY . .

# Render يمرر PORT تلقائياً
ENV PORT=10000

# تشغيل Flask عبر gunicorn
CMD gunicorn main:app --bind 0.0.0.0:$PORT
