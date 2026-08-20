ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1 DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        prusa-slicer \
        libgl1 libegl1 xvfb \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir \
        flask==3.1.* requests==2.32.* gunicorn==23.*

ENV PATH="/opt/venv/bin:$PATH"

COPY printer.py slicer.py preview.py app.py /opt/ffupload/
COPY profiles /opt/ffupload/profiles
COPY run.sh /
RUN chmod a+x /run.sh

WORKDIR /opt/ffupload
CMD ["/run.sh"]
