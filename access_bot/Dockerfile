FROM python:3.9-slim

WORKDIR /app

COPY access_bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY access_bot/ .

CMD ["python", "main.py"]
