#!/usr/bin/env python3
# 
# Copyright 2020-2021 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#
# This file is derived work of the FLACCue project.
# See https://github.com/acenko/FLACCue for details
#

from __future__ import print_function, absolute_import, division

import os
import time
import threading

from fuse import Operations

from . import fusepath
from . import flacinfo
from .flactracks import TrackManager

import logging
log = logging.getLogger(__name__)

class FlacSplitException(Exception):
   pass

class TrackFSOps(Operations):

    def __init__(self, 
        root, 
        keep_flac       = fusepath.DEFAULT_KEEP_PATH,
        separator       = fusepath.DEFAULT_TRACK_SEPARATOR,
        flac_extension  = fusepath.DEFAULT_FLAC_EXTENSION,
        title_length    = fusepath.DEFAULT_MAX_TITLE_LEN,
        tags_ignored    = flacinfo.DEFAULT_IGNORE_TAGS_REX
    ):
        self.root = os.path.realpath(root)
        self.keep_flac = keep_flac
        self.tracks = TrackManager()
        self._last_positions = {}
        self._fusepath_factory = fusepath.Factory(
            track_separator         = separator,
            max_title_len           = title_length,
            flac_extension          = flac_extension,
            keep_flac               = keep_flac
        )
        #TODO: avoid global init function
        flacinfo.init(tags_ignored)

    def __call__(self, op, path, *args):
      return super(TrackFSOps, self).__call__(op, self.root + path, *args)

    def _fusepath(self, path):
        return self._fusepath_factory.from_vpath(path)
        
    def getattr(self, path, fh=None):
        log.info(f"getattr for ({path}) [{fh}]")
        fusepath = self._fusepath(path)
        log.debug(fusepath)
        st = os.lstat(fusepath.source)
        result = dict((key, getattr(st, key)) for key in (
            'st_atime', 'st_ctime', 'st_gid', 'st_mode', 'st_mtime',
            'st_nlink', 'st_size', 'st_uid'))

        if(fusepath.is_track):
            # If it's one of the FlacTrackFS track paths, 
            # we need to adjust the file size to be roughly
            # appropriate for the individual track.
            f = flacinfo.get(fusepath.source).meta
            result['st_size'] = int(
                (fusepath.end - fusepath.start).seconds() 
                * f.info.channels 
                * (f.info.bits_per_sample/8) 
                * f.info.sample_rate
            )
        return result

    getxattr = None

    listxattr = None
   
    def open(self, path, flags, *args, **pargs):
        log.info(f'open file "{path}"')
        # We don't want FlacTrackFS messing with actual data.
        # Only allow Read-Only access.
        if((flags | os.O_RDONLY) == 0):
            raise ValueError('Can only open files read-only.')
        fusepath = self._fusepath(path)
        if fusepath.is_track:
            path = self.tracks.prepare_track(path, fusepath)
        log.debug(f'file to open = "{path}"')
        fh = os.open(path, flags, *args, **pargs)
        self._last_positions[fh] = 0
        return fh
      
    def read(self, path, size, offset, fh):
        log.info(f"read from [{fh}] {offset} until {offset+size}")
        if self._last_positions[fh] != offset:
            log.debug(f"out of band read; seek file to offset {offset}")
            os.lseek(fh, offset, 0)
        self._last_positions[fh] = offset+size
        return os.read(fh, size)

    def release(self, path, fh):
        log.info(f'release [{fh}] ({path})')
        del self._last_positions[fh]
        fusepath = self._fusepath(path)
        if fusepath.is_track:
            self.tracks.release_track(path, fusepath)
        return os.close(fh)

    def readdir(self, path, fh):
        log.info(f'readdir [{fh}] ({path})')
        return self._fusepath(path).readdir()

    def readlink(self, path, *args, **pargs):
        log.info(f'readlink ({path})')
        path = self._fusepath(path).source
        return os.readlink(path, *args, **pargs)

    def statfs(self, path):
        log.info(f'statfs ({path})')
        path = self._fusepath(path).source
        stv = os.statvfs(path)
        return dict((key, getattr(stv, key)) for key in (
             'f_bavail', 'f_bfree', 'f_blocks', 'f_bsize', 'f_favail',
             'f_ffree', 'f_files', 'f_flag', 'f_frsize', 'f_namemax')
        )

