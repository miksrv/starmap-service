FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    libfreetype6-dev \
    libpng-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV MPLBACKEND=Agg \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Dependencies first (cached unless requirements change).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code and default config. The starplot data catalogs (~85 MB) are
# NOT baked into the image — they are mounted at /app/data (see docker-compose).
COPY src/ ./src/
COPY config/ ./config/
COPY main.py ./

CMD ["python", "main.py"]
