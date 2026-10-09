ARG PYTHON_IMAGE = 3.12-slim

FROM $(PYTHON_IMAGE) AS builder 

RUN usersadd --create-home --uid 10001 utilisateur

WORKDIR /build
COPY requirements.txt
RUN pip install -r requirements.txt
COPY src/ src/
COPY models/ models/

RUN python -m venv /opt/venv

USER utilisateur

EXPOSE 8000

HEALTHCHECK CMD ["python", "-c", "... urlopen('http://127.0.0.1:8000/ready')"]
CMD ["uvicorn", "velov.api.main:app","--host", "0.0.0.0", "--port", "8000"]