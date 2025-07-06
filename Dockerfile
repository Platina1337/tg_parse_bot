FROM python:3.11-slim

WORKDIR /app
RUN mkdir -p /app/parser/sessions

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "parser.main:app", "--host", "0.0.0.0", "--port", "8000"] 