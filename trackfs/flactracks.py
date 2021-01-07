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
from concurrent.futures import ThreadPoolExecutor

from . import flacinfo
from . import fusepath

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

    DEFAULT_TEMPFILE_TTL        = 60
    # We should keep the lead time big enough, as the calculation of the 
    # remaining track time is based on percentage of file-size
    DEFAULT_PRELOAD_LEAD_TIME   = DEFAULT_TEMPFILE_TTL / 2
    

    def __init__(self):
        self.rwlock = threading.RLock()
        self.registry = {}
        self.preload_pool = ThreadPoolExecutor(max_workers=2)
        self.preloaded = {}
        self.preload_lead_time = TrackManager.DEFAULT_PRELOAD_LEAD_TIME
        self.tempfile_ttl = TrackManager.DEFAULT_TEMPFILE_TTL
      
    def _add(self, key, track_file):
        with self.rwlock: 
            self.registry[key] = TrackInfo(track_file)

        def cleanup():
            still_in_use = True
            while still_in_use:
                with self.rwlock:
                    info = self.registry[key]
                if info.ref_count <= 0 and (time.time() - info.last_accessed > self.tempfile_ttl):
                   still_in_use = False
                else:
                    time.sleep(self.tempfile_ttl / 2)
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
        log.info(f'prepare track "{path}"')
        assert fusepath.is_track
        ready_to_process = False
        with self.rwlock:
            self.preloaded[path] = False
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
        log.info(f'release track "{path}"')
        assert fusepath.is_track
        if self._change_usage(path, -1).ref_count == 0:
            log.debug(f'remove preloaded flag for "{path}"')
            with self.rwlock:
                del self.preloaded[path]
           
    
    def _find_this_and_next_track(self, flac_info: flacinfo.FlacInfo, num: int):
        log.info(f'checking for subsequent track "{num}"')
        tracks = flac_info.tracks()
        if tracks is not None:
            total_tracks = len(tracks)
            track = None
            found = False
            i = num-1
            while (not found) and (i<total_tracks):
                track = tracks[i]
                found = track.num == num
                i+=1
            return (track, tracks[i] if i<total_tracks else None)
        else:
            log.warn('could not find any tracks')
            return (None, None)

    def _do_check_next_track(self, path, fusepath, offset):
        log.info(f'_do_check_next_track: "{path}" [{offset}]')
        
        duration = (fusepath.end - fusepath.start).seconds()
        fsize = os.stat(self[path].temp_file_path).st_size
        if (1.0 - (float(offset) / float(fsize)))*duration > self.preload_lead_time:
            log.debug(f'more than ~{self.preload_lead_time} seconds to play; no preload')
            return
            
        flac_info = flacinfo.get(fusepath.source)
        (track, next_track) = self._find_this_and_next_track(flac_info, fusepath.num)
        if next_track is None:
            log.debug(f'got last track: "{fusepath.num}"; no preload')
            return
        with self.rwlock:
            self.preloaded[path] = True
            
        log.debug(f'preloading next track "{next_track.num}"')
        next_fusepath = fusepath.for_other_track(
            next_track.num, next_track.title, next_track.start, next_track.end,
        )
        self.prepare_track(next_fusepath.vpath, next_fusepath)
        # we just want to keep the file in the cache, but not mark it as "open"
        self.release_track(next_fusepath.vpath, next_fusepath)

    def check_next_track(self, path, fusepath, offset):
        assert fusepath.is_track
        with self.rwlock:
            if self.preloaded.get(path, False):
                log.debug(f'is already preloaded: "{path}"')
                return
        self.preload_pool.submit(self._do_check_next_track, path, fusepath, offset)

    
