#!/usr/bin/env python3
# 
# Copyright 2020-2021 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#

import os
import time
import threading

from dataclasses import dataclass
from tempfile import mkstemp
from subprocess import DEVNULL, run

from . import flacinfo

import logging
log = logging.getLogger(__name__)

@dataclass(frozen=True)
class TrackInfo():
    temp_file_path          : str
    ref_count               : int   = 1
    last_accessed           : float = time.time()
    
    
class TrackManager():
    """Keeps track of all individual tracks that currently get processed

    Each track has a unique key (usually the path of the virtual track file).

    The registry distinguishes three states for a track:
    * Unregistered: The track is not (yet) known to the registry
    * Announced: The track is known, but not yet available yet (processing still ongoing)
    * Available: The information about the track is available.

    """
    def __init__(self):
        self.rwlock = threading.RLock()
        self.registry = {}
      
    def _add(self, key, track_file):
        with self.rwlock: 
            self.registry[key] = TrackInfo(track_file)

        def cleanup():
            still_in_use = True
            while still_in_use:
                with self.rwlock:
                    info = self.registry[key]
                if info.ref_count <= 0 and (time.time() - info.last_accessed > 60):
                   still_in_use = False
                else:
                    time.sleep(30)
            log.debug(f'delete track "{key}"')
            del self.registry[key]
            os.remove(info.temp_file_path)
            
        threading.Thread(target=cleanup).start()



    def _is_unregistered(self, key):
        """Is the track at the given key not yet registered?"""
        with self.rwlock:
            return key not in self.registry

    def _announce(self, key):
        """Announce that a new track will get processed soon"""
        with self.rwlock:
            self.registry[key] = None

    def _is_announced(self, key):
        """Is the track registered, but not yet processed?"""
        with self.rwlock:
            # default value "" for get ensures that we don't 
            # treat unknown tracks as announced
            return self.registry.get(key,"") is None

    def _is_registered(self, key):
        with self.rwlock:
            return isinstance(self.registry.get(key),TrackInfo)
         
    def _change_usage(self, key, delta):
        with self.rwlock:
            info = self.registry[key]
            info = TrackInfo(info.temp_file_path, info.ref_count+delta)
            self.registry[key] = info
        return info
            
         
    def __getitem__(self, key):
        with self.rwlock:
            return self.registry[key]
         
    def get(self, key, default=None):
        with self.rwlock:
            return self.registry.get(key, default)
         
    def __delitem__(self, key):
        with self.rwlock:
            del self.registry[key]
         
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
                del self[path]
                raise FlacSplitException(err_msg)
            else:
                self._add(path, trackfile)

        return trackfile
   
    def prepare_track(self, path, fusepath):
        assert fusepath.is_track
        ready_to_process = False
        while not ready_to_process:
            with self.rwlock:
                if self._is_unregistered(path):
                    ready_to_process = True
                    self._announce(path)
                elif self._is_registered(path):
                    # we already have cached that track => 
                    # register additional usage
                    return self._change_usage(path, +1).temp_file_path
              
            # give other thread time to finish processing
            time.sleep(0.5)
     
        return self._extract_track(path, fusepath)         
         
    def release_track(self, path, fusepath):
        assert fusepath.is_track
        self._change_usage(path, -1)
