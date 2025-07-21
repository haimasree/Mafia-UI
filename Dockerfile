FROM python:3.12-slim

WORKDIR /Mafia-UI

COPY . /Mafia-UI

RUN pip install --no-cache-dir -r requirements.txt

ARG FASTAPI_ENV=Docker
ENV FASTAPI_ENV=${FASTAPI_ENV}

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
