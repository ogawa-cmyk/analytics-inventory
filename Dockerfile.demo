FROM python:3.11-slim

WORKDIR /app

# 依存関係を先にコピー（レイヤーキャッシュ活用）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# アプリケーションコードをコピー
COPY . .

# 不要なものを削除（.dockerignore で除外するが念のため）
RUN rm -f .env tokens/*.json 2>/dev/null || true

# Cloud Run が $PORT を注入するので sh -c で展開
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 120 demo_server:app"]
