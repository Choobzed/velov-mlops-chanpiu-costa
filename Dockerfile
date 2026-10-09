ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE}

RUN useradd --create-home --uid 10001 utilisateur

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PYTHONPATH=/build/src

COPY requirements.txt .
RUN pip install -r requirements.txt
COPY src/ src/
COPY models/ models/

USER utilisateur

EXPOSE 8000

HEALTHCHECK CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready')"]
CMD ["uvicorn", "velov.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
