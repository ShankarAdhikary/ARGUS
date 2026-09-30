# --- Stage 1: SourceAFIS (Java, Apache-2.0) and its transitive jars, resolved by Maven from Maven Central ---------------
# Every jar is pinned by SHA-256 in vendor/sourceafis/jars.sha256; the build fails if anything differs, so a compromised
# or changed artifact cannot slip into the image. To upgrade: edit vendor/sourceafis/pom.xml, rebuild, refresh the hashes.
FROM maven:3.9-eclipse-temurin-17 AS sourceafis-jars
WORKDIR /w
COPY vendor/sourceafis/pom.xml vendor/sourceafis/jars.sha256 ./
RUN mvn -q -B dependency:copy-dependencies -DoutputDirectory=/out -DincludeScope=runtime \
 && cd /out && sha256sum -c /w/jars.sha256 && [ "$(ls -1 /out | wc -l)" = "$(wc -l < /w/jars.sha256)" ]

# --- Stage 2: the application image ---------------------------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libsm6 libxext6 ffmpeg && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-indic.txt requirements-voice.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --default-timeout=120 --retries 10 -r requirements.txt
# Indic NER stack (on by default, ~2-3 GB). Opt out with: docker compose build --build-arg INSTALL_INDIC_NER=false api
# ai4bharat/IndicNER is a GATED HuggingFace model: accept its terms on huggingface.co, then pass a read token as a build
# secret (never a build arg, which would be stored in the image):
#     echo hf_xxx > ~/.hf_token && HF_TOKEN_FILE=~/.hf_token docker compose build api
# Without a token the libraries still install but the weights are not baked in, and Indic NER falls back to the regex
# extractor at run time (set HF_TOKEN in the api environment to download them on first use instead).
ARG INSTALL_INDIC_NER=true
ARG INDIC_NER_MODEL=ai4bharat/IndicNER
ENV HF_HOME=/opt/hf-cache
RUN --mount=type=secret,id=hf_token,required=false --mount=type=cache,target=/root/.cache/pip \
    if [ "$INSTALL_INDIC_NER" = "true" ]; then \
      pip install --default-timeout=120 --retries 10 -r requirements-indic.txt --extra-index-url https://download.pytorch.org/whl/cpu && \
      if [ -s /run/secrets/hf_token ]; then \
        HF_TOKEN="$(cat /run/secrets/hf_token)" python -c "from huggingface_hub import snapshot_download; snapshot_download('${INDIC_NER_MODEL}')"; \
      else \
        echo "WARNING: no HuggingFace token given - IndicNER weights not downloaded (regex fallback will be used)"; \
      fi; \
    fi

# Offline voice-to-FIR (open-weight Whisper, model "small", ~500 MB, weights baked in so it runs air-gapped).
# Opt out with: docker compose build --build-arg INSTALL_VOICE=false api
ARG INSTALL_VOICE=true
ARG WHISPER_MODEL=small
ENV WHISPER_CACHE_DIR=/opt/whisper-cache
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ "$INSTALL_VOICE" = "true" ]; then \
      pip install --default-timeout=120 --retries 10 -r requirements-voice.txt --extra-index-url https://download.pytorch.org/whl/cpu && \
      mkdir -p "$WHISPER_CACHE_DIR" && \
      python -c "import whisper; whisper.load_model('${WHISPER_MODEL}', download_root='${WHISPER_CACHE_DIR}')"; \
    fi

# Voice biometrics: SpeechBrain ECAPA-TDNN speaker embeddings (public model, weights baked in so it runs air-gapped).
# Installed so the evaluation (scripts/evaluate_voiceprint.py) can run in the image; the endpoints are OFF at run time until
# VOICEPRINT_ENABLED=true, which must wait for that evaluation. Opt out with --build-arg INSTALL_VOICEPRINT=false.
COPY requirements-voiceprint.txt ./
ARG INSTALL_VOICEPRINT=true
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ "$INSTALL_VOICEPRINT" = "true" ]; then \
      pip install --default-timeout=120 --retries 10 -r requirements-voiceprint.txt --extra-index-url https://download.pytorch.org/whl/cpu && \
      mkdir -p /opt/speechbrain-cache && \
      python -c "from speechbrain.inference.speaker import EncoderClassifier; EncoderClassifier.from_hparams(source='speechbrain/spkrec-ecapa-voxceleb', savedir='/opt/speechbrain-cache/ecapa', run_opts={'device': 'cpu'})"; \
    fi
ENV VOICEPRINT_MODEL_DIR=/opt/speechbrain-cache/ecapa

# Fingerprint matching: the SourceAFIS Java library, bridged to Python with JPype. Needs a JRE (headless, ~190 MB).
# The base image is Debian 13 (trixie), which no longer packages OpenJDK 17; 21 is fine (SourceAFIS needs Java 8+).
# Opt out of the JVM with: docker compose build --build-arg INSTALL_JVM=false api   (fingerprint matching then answers 501)
ARG INSTALL_JVM=true
RUN if [ "$INSTALL_JVM" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends openjdk-21-jre-headless && rm -rf /var/lib/apt/lists/*; \
    fi
COPY --from=sourceafis-jars /out /opt/sourceafis
ENV SOURCEAFIS_JAR_DIR=/opt/sourceafis

COPY . ./
RUN mkdir -p /app/uploads

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
