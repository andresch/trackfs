#!/bin/sh
# 
# Copyright 2020 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#

# make sure the current user has an entry in /etc/passwd
# otherwise fusermount will not be able to mount FUSE fs
# so unless the current user already has an entry in /etc/passwd
# we add a fake user "trackfs" with uid and gid of current user
my_uid="$(id -u)"
if ! getent passwd "${my_uid}" >/dev/null; then
	echo "trackfs:x:${my_uid}:$(id -g)::/tmp:/sbin/nologin" >> /etc/passwd
fi

# start trackfs
export FUSE_LIBRARY_PATH=/usr/lib/libfuse.so
exec /usr/local/bin/trackfs.py $@
