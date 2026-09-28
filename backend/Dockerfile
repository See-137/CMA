FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
# Install CPU-only PyTorch first to avoid pulling ~2GB CUDA version
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Run as an unprivileged user. sentence-transformers downloads the embedding
# model on first start; point its cache inside the writable data dir so the
# named volume for chroma and the model cache share one owner.
RUN groupadd -r cma && useradd -r -g cma -d /app cma \
    && mkdir -p data/chroma data/hf \
    && chown -R cma:cma /app
ENV HF_HOME=/app/data/hf
USER cma
EXPOSE 8000
# Apply migrations, then start the server. Alembic is the schema authority.
# --proxy-headers: the compose frontend (nginx) is the only peer that can reach
# this port, so its X-Forwarded-For is trusted and request.client reflects the
# real caller (the rate limiter keys on it).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
