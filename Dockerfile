# =================================
# Dockerfile for flacTrackFS
# 
# docker run -ti --rm --device /dev/fuse --cap-add SYS_ADMIN -v "/opt/samba/shares/musik/CD Archiv":/src -v /opt/samba/shares/musik/FLAC-Archiv:/dst:rshared andresch/flacTrackFS
# --security-opt apparmor:unconfined 
# =================================

FROM docker.io/python:3.8-alpine

RUN \
  apk --no-cache add fuse fuse-dev flac
  
RUN \
  PIPS="mutagen fusepy Lark" \
  && /usr/local/bin/python -m pip install --upgrade pip \
  && echo ${PIPS} | xargs pip install

# source directory containing flac images
VOLUME /src

# destination directory where to split up
VOLUME /dst
  
COPY flacTrackFS.py /usr/bin

ENV FUSE_LIBRARY_PATH=/usr/lib/libfuse.so
ENTRYPOINT ["/usr/bin/flacTrackFS.py", "/src", "/dst"]


