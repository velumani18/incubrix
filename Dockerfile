FROM python:3.12-slim

# Install system dependencies & FFmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specifications
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application codebase and built frontend
COPY app/ app/
COPY frontend/dist/ frontend/dist/
COPY timeline.schema.json .
COPY openapi.yaml .
COPY test_pack/ test_pack/

# Create data directories
RUN mkdir -p data/uploads data/projects data/outputs

ENV APP_ENV=production \
    PORT=8000 \
    HOST=0.0.0.0 \
    TIME_POLICY=SOURCE_TIME \
    STT_PROVIDER=mock

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
