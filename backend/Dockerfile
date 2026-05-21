FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
# Install CPU-only PyTorch first to avoid pulling ~2GB CUDA version
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p data
EXPOSE 8000
# Apply migrations, then start the server. Alembic is the schema authority.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
