FROM python:3.12-slim

LABEL maintainer="Haimasree Bhattacharya haimasree.bhattacharya@mail.huji.ac.il"

ARG FASTAPI_ENV=Docker

WORKDIR /Mafia-UI

COPY requirements.txt /Mafia-UI/requirements.txt
RUN pip install --no-cache-dir -r /Mafia-UI/requirements.txt
RUN rm /Mafia-UI/requirements.txt


RUN groupadd -g 1002 python && \
    useradd -r -u 997 -g python -m -d /Mafia-UI/ python


COPY --chown=python:python . /Mafia-UI

RUN mkdir -p /Mafia-UI/games && \
    chown python:python /Mafia-UI/games

RUN mkdir -p /Mafia-UI/configurations && \
    chown python:python /Mafia-UI/configurations

USER python

ENV FASTAPI_ENV=${FASTAPI_ENV}
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
