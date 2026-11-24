FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 TORCH_HOME=/home/app/.cache/torch OMP_NUM_THREADS=2 PORT=8080
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/* && useradd -m app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -r requirements.txt
COPY app.py wire.py .
COPY static static
RUN install -d -m 700 -o app -g app /data
ENV DATA_DIR=/data
USER app
RUN python -c 'from demucs.pretrained import get_model; get_model("htdemucs")'
EXPOSE 8080
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 4 app:app"]
