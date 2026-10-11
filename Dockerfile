FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock.txt
COPY config.py models.py ./
COPY contracts ./contracts
COPY services ./services
COPY pipeline ./pipeline
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "uvicorn", "services.api:app", "--host", "0.0.0.0", "--port", "8000"]
