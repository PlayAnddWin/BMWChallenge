FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY config.py models.py ./
COPY contracts ./contracts
COPY services ./services
COPY pipeline ./pipeline
COPY ui ./ui
COPY .streamlit ./.streamlit
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "uvicorn", "services.api:app", "--host", "0.0.0.0", "--port", "8000"]
