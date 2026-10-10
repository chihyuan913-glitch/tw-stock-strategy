FROM python:3.11-slim

WORKDIR /app

# 安裝基本套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製程式碼 (排除 .env 與暫存)
COPY . .

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn line_bot_server:app --host 0.0.0.0 --port ${PORT:-8000}"]
