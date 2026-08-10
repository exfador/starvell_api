FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STARVELL_DATA_DIR=/data

WORKDIR /app

RUN addgroup --system starvell && adduser --system --ingroup starvell starvell

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=starvell:starvell . .
RUN mkdir -p /data && chown starvell:starvell /data

USER starvell
VOLUME ["/data"]

CMD ["python", "run_bot.py"]
