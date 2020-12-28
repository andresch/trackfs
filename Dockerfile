# =================================
# Dockerfile for trackfs
# 
# Copyright 2020 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#
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


