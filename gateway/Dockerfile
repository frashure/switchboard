FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY switchboard ./switchboard
COPY config ./config

RUN pip install --no-cache-dir .

EXPOSE 8000
# --ws-ping-interval/--ws-ping-timeout: uvicorn's defaults (20s/20s = 40s
# combined) are shorter than this app's legitimate LLM-turn wait times
# (worst case ~95s with the REST fallback in owui_client.py) -- confirmed
# via a real disconnect that the default closes the connection mid-turn.
# 120s/120s comfortably covers that with margin.
CMD ["uvicorn", "switchboard.main:app", "--host", "0.0.0.0", "--port", "8000", "--ws-ping-interval", "120", "--ws-ping-timeout", "120"]
