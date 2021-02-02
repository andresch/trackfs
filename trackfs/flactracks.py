#!/usr/bin/env python3
# 
# Copyright 2020-2021 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#

import os
import shlex
import time
import wave

from dataclasses import dataclass
from math import trunc
from tempfile import mkstemp
from subprocess import DEVNULL, run
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple, Dict, Optional
from threading import RLock, Thread

from . import albuminfo
from .fusepath import FusePath
from .cuesheet import Track

import logging

log = logging.getLogger(__name__)


class FlacSplitException(Exception):
    pass


@dataclass(frozen=True)
class TrackInfo:
    temp_file_path: os.PathLike
    ref_count: int = 1
    last_accessed: float = time.time()


class TrackManager:
    """Keeps track of all individual tracks that currently get processed

    Each track has a unique key (usually the path of the virtual track file).

    The registry distinguishes three states for a track:
    * Unregistered: The track is not (yet) known to the registry
    * Announced: The track is known, but not yet available yet (processing still ongoing)
    * Available: The information about the track is available.

    """

    DEFAULT_TEMP_FILE_TTL = 60
    # We should keep the lead time big enough, as the calculation of the 
    # remaining track time is based on percentage of file-size
    DEFAULT_PRELOAD_LEAD_TIME = DEFAULT_TEMP_FILE_TTL // 2

    def __init__(self) -> None:
        self.rwlock: RLock = RLock()
        self.registry: Dict[os.PathLike, TrackInfo or None] = {}
        self.preload_pool: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="preload")
        self.preloaded_next_tracks: Dict[os.PathLike, FusePath] = {}
        self.preload_lead_time: int = TrackManager.DEFAULT_PRELOAD_LEAD_TIME
        self.temp_file_ttl: int = TrackManager.DEFAULT_TEMP_FILE_TTL

    def _add(self, key: os.PathLike, track_file: os.PathLike) -> None:
        with self.rwlock:
            self.registry[key] = TrackInfo(track_file)

        def cleanup():
            still_in_use = True
            while still_in_use:
                with self.rwlock:
                    info = self.registry[key]
                if info.ref_count <= 0 and (time.time() - info.last_accessed > self.temp_file_ttl):
                    still_in_use = False
                else:
                    time.sleep(self.temp_file_ttl / 2)
            log.debug(f'delete track "{key}"')
            del self.registry[key]
            os.remove(info.temp_file_path)

        Thread(target=cleanup).start()

    def _is_unregistered(self, key: os.PathLike) -> bool:
        """Is the track at the given key not yet registered?"""
        with self.rwlock:
            return key not in self.registry

    def _announce(self, key: os.PathLike) -> None:
        """Announce that a new track will get processed soon"""
        with self.rwlock:
            self.registry[key] = None

    def _is_announced(self, key: os.PathLike) -> bool:
        """Is the track registered, but not yet processed?"""
        with self.rwlock:
            # default value "" for get ensures that we don't 
            # treat unknown tracks as announced
            return self.registry.get(key, "") is None

    def _is_registered(self, key: os.PathLike) -> bool:
        with self.rwlock:
            return isinstance(self.registry.get(key), TrackInfo)

    def _change_usage(self, key: os.PathLike, delta: int) -> TrackInfo:
        with self.rwlock:
            info = self.registry[key]
            info = TrackInfo(info.temp_file_path, info.ref_count + delta)
            self.registry[key] = info
        return info

    def __getitem__(self, key: os.PathLike) -> TrackInfo:
        with self.rwlock:
            return self.registry[key]

    def get(self, key: os.PathLike, default: TrackInfo = None) -> TrackInfo:
        with self.rwlock:
            return self.registry.get(key, default)

    def __delitem__(self, key: os.PathLike) -> None:
        with self.rwlock:
            del self.registry[key]

    @staticmethod
    def _tag_as_flac_arg(tag_name, tag_value):
        # for whatever weird reason quote has a problem with some values if
        # not casted into str before
        return f"--tag={shlex.quote(str(tag_name))}={shlex.quote(str(tag_value))}"

    @staticmethod
    def track_tags_as_flac_args(album_info, num):
        tags = album_info.track_tags(num)

        log.debug(f"tags for current track: {tags}")
        return ' '.join([TrackManager._tag_as_flac_arg(k, v) for k, vs in tags.items() for v in vs])

    @staticmethod
    def _new_temp_filename() -> os.PathLike:
        (fh, temp_file) = mkstemp()
        # we don't want to process the file in python; just want a unique filename
        # that we let flac write the track into
        # => close right away
        os.close(fh)
        return temp_file

    @staticmethod
    def _find_albmum_art(fp: FusePath) -> Optional[os.PathLike]:
        for fn in [
            fp.source_root+".jpg",
            os.path.dirname(fp.source_root)+'folder.jpg'
        ]:
            if os.path.exists(fn): return fn
        return None

    def _extract_flac_track(self, path: os.PathLike, fp: FusePath, album_info: albuminfo.AlbumInfo) -> os.PathLike:
        """creates a real file for a given virtual track file

        extracts the track from the underlying FLAC+CUE file into 
        a temporary file and then opens the temporary file"""
        log.info(f'open track "{path}"')

        track_file = self._new_temp_filename()

        # extract picture from flac if available
        picture_file = self._new_temp_filename()
        metaflac_cmd = f'metaflac --export-picture-to="{picture_file}" "{fp.source}"'
        log.debug(f'extracting picture with command: "{metaflac_cmd}"')
        rc = run(metaflac_cmd, shell=True, stdout=None, stderr=DEVNULL).returncode
        picture_arg = ""
        if rc == 0:
            picture_arg = f' --picture="{picture_file}"'
        else:
            local_album_art = self._find_albmum_art(fp)
            if local_album_art:
                picture_arg = f' --picture="{local_album_art}"'

        track = album_info.track(fp.num)
        flac_cmd = (
            f'flac -d --silent --stdout --skip={track.start.flac_time()}'
            f'  --until={track.end.flac_time()} "{fp.source}" '
            f'| flac --silent -f --fast'
            f'  {self.track_tags_as_flac_args(album_info,fp.num)}{picture_arg} -o {track_file} -'
        )
        log.debug(f'extracting track with command: "{flac_cmd}"')
        rc = run(flac_cmd, shell=True, stdout=None, stderr=DEVNULL).returncode
        os.remove(picture_file)
        with self.rwlock:
            if rc != 0:
                err_msg = f'failed to extract track #{fp.num} from file "{fp.source}"'
                log.error(err_msg)
                os.remove(track_file)
                del self[path]
                raise FlacSplitException(err_msg)
            else:
                self._add(path, track_file)

        return track_file

    def _extract_wave_track(self, path: os.PathLike, fp: FusePath, album_info: albuminfo.AlbumInfo) -> os.PathLike:
        """creates a real file for a given virtual track file

        extracts the track from the underlying WAVE  file and associated CUE-Sheet into
        a temporary file and then opens the temporary file"""
        log.info(f'open track "{path}"')

        track_file = self._new_temp_filename()

        track = album_info.track(fp.num)

        # search for album-art in same directory
        local_album_art = self._find_albmum_art(fp)
        if local_album_art:
            picture_arg = f' --picture="{local_album_art}"'
        else:
            picture_arg = ""

        wave_track_file = self._new_temp_filename()
        with wave.open(fp.source, 'r') as wav_in:
            params = wav_in.getparams()
            wav_in.setpos(trunc(track.start.seconds() * params.framerate))
            nframes = trunc(track.duration.seconds() * params.framerate)
            with wave.open(wave_track_file, 'w') as wav_out:
                out_params = (params.nchannels, params.sampwidth, params.framerate,
                              nframes, params.comptype, params.compname)
                wav_out.setparams(out_params)
                chunk_size = 512*1025
                while nframes > 0:
                    wav_out.writeframes(wav_in.readframes(min(nframes, chunk_size)))
                    nframes -= chunk_size

        flac_cmd = (
            f'flac --silent -f --fast'
            f'  {self.track_tags_as_flac_args(album_info, fp.num)}{picture_arg} -o "{track_file}" "{wave_track_file}"'
        )
        log.debug(f'extracting track with command: "{flac_cmd}"')
        rc = run(flac_cmd, shell=True, stdout=None, stderr=DEVNULL).returncode
        with self.rwlock:
            if rc != 0:
                err_msg = f'failed to extract track #{fp.num} from file "{fp.source}"'
                log.error(err_msg)
                os.remove(track_file)
                del self[path]
                raise FlacSplitException(err_msg)
            else:
                self._add(path, track_file)

        return track_file

    def prepare_track(self, path: os.PathLike, fp: FusePath) -> os.PathLike:
        log.info(f'prepare track "{path}"')
        assert fp.is_track
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
        album_info = albuminfo.get(fp.source)
        audio_format = album_info.format()
        if audio_format == 'WAVE':
            return self._extract_wave_track(path, fp, album_info)
        elif audio_format == 'FLAC':
            return self._extract_flac_track(path, fp, album_info)
        else:
            err_msg = f'unexpected audio format "{audio_format}"; can\'t proceed'
            log.error(err_msg)
            raise FlacSplitException(err_msg)

    def release_track(self, path: os.PathLike, fp: FusePath):
        log.info(f'release track "{path}"')
        assert fp.is_track
        if self._change_usage(path, -1).ref_count == 0:
            log.debug(f'check fo preloaded next track of "{path}"')
            with self.rwlock:
                next_track = self.preloaded_next_tracks.get(path, None)
                if next_track is not None:
                    log.debug(f'release preloaded next track of "{next_track.vpath}"')
                    self.release_track(next_track.vpath, next_track)
                    del self.preloaded_next_tracks[path]

    @staticmethod
    def _find_this_and_next_track(album_info: albuminfo.AlbumInfo, num: int) -> Tuple[Track or None, Track or None]:
        log.info(f'checking for subsequent track of track "{num}"')
        tracks = album_info.tracks()
        if tracks is not None:
            total_tracks = len(tracks)
            track = None
            found = False
            i = num - 1
            while (not found) and (i < total_tracks):
                track = tracks[i]
                found = track.num == num
                i += 1
            return track, tracks[i] if i < total_tracks else None
        else:
            log.warning('could not find any tracks')
            return None, None

    def _do_check_next_track(self, path: os.PathLike, fp: FusePath, offset: int) -> None:
        log.info(f'_do_check_next_track: "{path}" [{offset}]')

        album_info = albuminfo.get(fp.source)
        (track, next_track) = self._find_this_and_next_track(album_info, fp.num)
        if next_track is None:
            log.debug(f'got last track: "{fp.num}"; no preload')
            return

        file_size = os.stat(self[path].temp_file_path).st_size
        if (1.0 - (float(offset) / float(file_size))) * track.duration > self.preload_lead_time:
            log.debug(f'more than ~{self.preload_lead_time} seconds to play; no preload')
            return

        with self.rwlock:
            if self.preloaded_next_tracks.get(path, None) is not None:
                log.debug(f'next track of "{path}" got preloaded in the meanwhile')
                return
            # mark as preloaded, so that we don't do it twice

            log.debug(f'preloading next track "{next_track.num}"')
            next_fp = fp.for_other_track(
                next_track.num, next_track.title, next_track.start, next_track.end,
            )
            self.preloaded_next_tracks[path] = next_fp

        # prepare next track and by that add a reference to it to keep it it cache
        # for the lifetime if this track.
        self.prepare_track(next_fp.vpath, next_fp)

    def check_next_track(self, path: os.PathLike, fp: FusePath, offset: int) -> None:
        assert fp.is_track
        log.info(f'check_next_track for "{path}" [{offset}]')
        with self.rwlock:
            if self.preloaded_next_tracks.get(path, None) is not None:
                log.debug(f'next track already preloaded "{path}"')
                return
        log.info(f'enqueue next track check of "{path}" [{offset}]')
        self.preload_pool.submit(self._do_check_next_track, path, fp, offset)

    def estimate_track_file_size(self, path: os.PathLike, fp: FusePath) -> int:
        track_info = self.get(path, None)
        if track_info is None:
            # TODO: can we find a better estimation?
            # use raw-audio size as estimation
            album_info = albuminfo.get(fp.source)
            meta = album_info.meta
            track = album_info.track(fp.num)
            return int(
                (track.end - track.start).seconds()
                * meta.info.channels
                * (meta.info.bits_per_sample / 8)
                * meta.info.sample_rate
            )
        else:
            # use the actual size of the track-file
            return os.stat(track_info.temp_file_path).st_size
