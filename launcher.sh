#!/bin/sh
FUSE_LIBRARY_PATH=/usr/lib/libfuse.so
export FUSE_LIBRARY_PATH
if test -z "$TRACKFS_UID"; then
	echo "No environment variable \$TRACKFS_UID defined. Launching directly"
	/usr/bin/trackfs.py $@
else
	deluser trackfs
	adduser -S -H -D -u $TRACKFS_UID trackfs
	cmd="/usr/bin/trackfs.py $@"
	su -s "/bin/sh" trackfs -c "$cmd"
fi