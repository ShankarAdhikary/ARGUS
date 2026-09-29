FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libsm6 libxext6 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-indic.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Optional Hindi NER stack (large): docker compose build --build-arg INSTALL_INDIC_NER=true api
ARG INSTALL_INDIC_NER=false
RUN if [ "$INSTALL_INDIC_NER" = "true" ]; then \
      pip install --no-cache-dir -r requirements-indic.txt --extra-index-url https://download.pytorch.org/whl/cpu; \
    fi

COPY . ./
RUN mkdir -p /app/uploads

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
