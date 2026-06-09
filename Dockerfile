FROM python:3.12-slim

# zbar (requis par pyzbar pour décoder les QR)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libzbar0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# PORT est fourni par la plateforme cloud (Railway, Render, Fly…)
ENV PORT=8080
EXPOSE $PORT

CMD python serve.py \
        --user     "$IZLY_USER" \
        --password "$IZLY_PASSWORD" \
        --port     "$PORT"
