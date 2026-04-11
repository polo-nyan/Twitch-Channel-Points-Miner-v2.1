FROM python:3.12-slim-bookworm

ARG BUILDX_QEMU_ENV

WORKDIR /usr/src/app

COPY ./requirements.txt ./

RUN apt-get update \
  && apt-get upgrade -y \
  && DEBIAN_FRONTEND=noninteractive apt-get install -qq -y --no-install-recommends \
    gcc \
    libffi-dev \
    zlib1g-dev \
    libjpeg-dev \
    libssl-dev \
    libblas-dev \
    liblapack-dev \
    g++ \
    python3-dev \
  && pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir -r requirements.txt \
  && apt-get remove -y gcc g++ \
  && apt-get autoremove -y \
  && apt-get clean -y \
  && rm -rf /var/lib/apt/lists/* /usr/share/doc/*

COPY ./TwitchChannelPointsMiner ./TwitchChannelPointsMiner
COPY ./settings_loader.py ./settings_loader.py
COPY ./runpy_converter.py ./runpy_converter.py
COPY ./main.py ./main.py
COPY ./export.py ./export.py
COPY ./assets ./assets

HEALTHCHECK --interval=60s --timeout=5s --retries=3 --start-period=30s \
  CMD python -c "import json,os,urllib.request; p=json.load(open('settings.json')).get('analytics',{}).get('port',5000) if os.path.exists('settings.json') else 5000; urllib.request.urlopen(f'http://localhost:{p}/health')" || exit 1

ENTRYPOINT [ "python", "main.py" ]
