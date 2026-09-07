FROM python:3.11-slim

# Install ffmpeg and clean up
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create necessary directories
RUN mkdir -p output _dropped _logs jobs .cache templates

# Environment variables for headless server deployment
ENV BEATCANVAS_NO_BROWSER=1
ENV BEATCANVAS_FFMPEG=/usr/bin/ffmpeg
ENV BEATCANVAS_FFPROBE=/usr/bin/ffprobe
ENV BEATCANVAS_PORT=8772

EXPOSE 8772

# Health check for Docker / load balancers
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8772/api/health')" || exit 1

CMD ["python", "run.py"]
