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
import sys

from tempfile import mkstemp
from subprocess import DEVNULL, run

from fuse import Operations

from . import fusepath
from . import flacinfo
from .flactracks import TrackRegistry

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
        self.rwlock = threading.RLock()
        self.tracks = TrackRegistry(self.rwlock)
        self._processed_tracks = {}
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

    def _new_temp_filename(self):
        (fh,tempfile) = mkstemp()
        # we don't want to process the file in python; just want a unique filename
        # that we let flac write the track into
        # => close right away
        os.close(fh)
        return tempfile
      
    def _extract_track(self, path, fusepath):
        """creates a real file for a given virtual track file

        extracts the track from the underlying FLAC+CUE file into 
        a temporary file and then opens the temporary file"""
        log.info(f'open track "{path}"')

        trackfile = self._new_temp_filename()

        flac_info = flacinfo.get(fusepath.source)

        # extract picture from flac if available
        picturefile = self._new_temp_filename()
        metaflac_cmd = f'metaflac --export-picture-to="{picturefile}" "{fusepath.source}"'
        log.debug(f'extracting picture with command: "{metaflac_cmd}"')
        rc = run(metaflac_cmd, shell=True, stdout=None, stderr=DEVNULL).returncode
        picture_arg = ""
        if rc == 0:
            picture_arg =f' --picture="{picturefile}"'

        flac_cmd = (
            f'flac -d --silent --stdout --skip={fusepath.start.flac_time()}'
            f'  --until={fusepath.end.flac_time()} "{fusepath.source}" '
            f'| flac --silent -f --fast'
            f'  {flac_info.track_tags(fusepath.num)}{picture_arg} -o {trackfile} -'
        )
        log.debug(f'extracting track with command: "{flac_cmd}"')
        rc = run(flac_cmd, shell=True, stdout=None, stderr=DEVNULL).returncode
        os.remove(picturefile)
        with self.rwlock:
            if rc != 0:
                err_msg = f'failed to extract track #{fusepath.num} from file "{fusepath.source}"'
                log.error(err_msg)
                os.remove(trackfile)
                del self.tracks[path]
                raise FlacSplitException(err_msg)
            else:
                self.tracks.add(path, trackfile)

        def cleanup():
            still_in_use = True
            while still_in_use:
                (count, last_access, trackfile) = self.tracks[path]
                if count <= 0 and (time.time() - last_access > 60):
                   still_in_use = False
                time.sleep(30)
            log.debug(f'delete track "{path}"')
            del self.tracks[path]
            os.remove(trackfile)

        threading.Thread(target=cleanup).start()
        return trackfile
   
    def _file_to_open(self, path):
        fusepath = self._fusepath(path)
        if fusepath.is_track:
            ready_to_process = False
            while not ready_to_process:
                with self.rwlock:
                    if self.tracks.is_unregistered(path):
                        ready_to_process = True
                        self.tracks.announce(path)
                    elif self.tracks.is_registered(path):
                        # we already have cached that track => 
                        # register additional usage
                        return self.tracks.register_usage(path)[2] 
                  
                # give other thread time to finish processing
                time.sleep(0.5)
         
            return self._extract_track(path, fusepath)         
        else:
            # With any other file, just pass it along normally.
            return path
         
    def open(self, path, flags, *args, **pargs):
        log.info(f'open file "{path}"')
        # We don't want FlacTrackFS messing with actual data.
        # Only allow Read-Only access.
        if((flags | os.O_RDONLY) == 0):
            raise ValueError('Can only open files read-only.')
        realfile = self._file_to_open(path)
        log.debug(f'file to open = "{realfile}"')
        fh = os.open(realfile, flags, *args, **pargs)
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
        if self._fusepath(path).is_track:
            self.tracks.release_usage(path)
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

