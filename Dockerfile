FROM python:3.13-slim-bookworm
WORKDIR /app
COPY ledger ./ledger
COPY config ./config
RUN useradd --uid 10001 --create-home ledger && mkdir /data && chown ledger:ledger /data
USER ledger
VOLUME /data
EXPOSE 8001
CMD ["python3", "-m", "ledger.http", "--root", "/data", "--host", "0.0.0.0", "--port", "8001"]
