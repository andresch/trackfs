# =================================
# Dockerfile for trackfs
# 
# docker run -ti --rm --device /dev/fuse --cap-add SYS_ADMIN -v "/opt/samba/shares/musik/CD Archiv":/src -v /opt/samba/shares/musik/FLAC-Archiv:/dst:rshared andresch/trackfs
# --security-opt apparmor:unconfined 
# =================================

FROM docker.io/python:3.8-alpine

RUN \
  apk --no-cache add fuse fuse-dev flac
  
RUN \
  PIPS="mutagen fusepy Lark" \
  && /usr/local/bin/python -m pip install --upgrade pip \
  && echo ${PIPS} | xargs pip install

# enable non-root users to make FUSE fs non-private
RUN echo "user_allow_other" >> /etc/fuse.conf 
	
# source directory containing flac images
VOLUME /src

# destination directory where to split up
VOLUME /dst

# what is the UID to mount the filesystem
ENV TRACKFS_UID=""  

COPY launcher.sh /usr/bin
RUN chmod 555 /usr/bin/launcher.sh

COPY trackfs.py /usr/bin
RUN chmod 555 /usr/bin/trackfs.py

ENTRYPOINT ["/usr/bin/launcher.sh", "/src", "/dst"]


