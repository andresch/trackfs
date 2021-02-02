#!/usr/bin/env python3
# 
# Copyright 2020-2021 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#

import re
import os
import shlex
from functools import lru_cache, cached_property
from typing import List, Optional

from mutagen import File
import chardet

from . import cuesheet

import logging

log = logging.getLogger(__name__)

DEFAULT_IGNORE_TAGS_REX = re.compile('CUE_TRACK.*|COMMENT')


class AlbumInfo:
    IGNORE_TAGS_REX = DEFAULT_IGNORE_TAGS_REX

    def __init__(self, path):
        self.path: str = path

    @cached_property
    def meta(self) -> File:
        return File(self.path)

    def format(self) -> str:
        return type(self.meta).__name__.upper()

    def _cue_from_external_file(self) -> Optional[str]:
        (base, ext) = os.path.splitext(self.path)
        cue_path = base + ".cue"
        if not os.path.exists(cue_path):
            return None
        log.debug(f"found accompanying cue sheet")
        with open(cue_path, "rb") as fh:
            cue_bytes = fh.read()
        cue_str = cue_bytes.decode(chardet.detect(cue_bytes)['encoding'])
        log.debug(f"cue-sheet:\n{cue_str}")
        return cue_str

    @cached_property
    def cue(self) -> Optional[cuesheet.CueSheet]:
        meta = self.meta
        raw_cue = meta.tags.get('CUESHEET', []) if meta.tags else []
        if len(raw_cue) == 0:
            log.debug(f"regular flac file without cue sheet")
            raw_cue = self._cue_from_external_file()
            if raw_cue is None:
                return None
        else:
            raw_cue = raw_cue[0]
        log.debug(f"raw cue sheet from FLAC file:\n{raw_cue}")
        result = cuesheet.parse(raw_cue, meta.info.length)
        log.debug(f"parsed cue sheet from FLAC file:\n{result}")
        return result

    def tracks(self) -> List[cuesheet.Track]:
        return self.cue.tracks if self.cue is not None else []

    def track(self, num) -> Optional[cuesheet.Track]:
        trx = self.tracks()
        if trx is None:
            return None
        t = trx[num - 1]
        if t.num == num:
            return t
        for t in trx:
            if t.num == num:
                return t
        return None

    def _album_tags(self):
        meta = self.meta
        tags = {}
        for (k, v) in (meta.tags if meta.tags else {}):
            k = k.upper()
            # skip multi-line tags
            if len(v.splitlines()) != 1:
                continue
            # skip _IGNORE_TAGS
            if AlbumInfo.IGNORE_TAGS_REX.match(k):
                continue
            if k not in tags: tags[k] = []
            tags[k].append(v)

        # make sure ALBUMARTIST and ALBUM are set
        # in case ARTIST and TITLE have been used instead
        if 'ALBUMARTIST' not in tags and 'ARTIST' in tags:
            tags['ALBUMARTIST'] = tags['ARTIST']
        if 'ALBUM' not in tags and 'TITLE' in tags:
            tags['ALBUM'] = tags['TITLE']

        # add missing tags from cue sheet
        for (k, v) in self.cue.tags().items():
            if k not in tags:
                tags[k] = v

        return tags

    def track_tags(self, num):
        tags = self._album_tags()
        for (k, vs) in self.track(num).tags().items():
            tags[k] = vs

        if 'TRACKTOTAL' not in tags:
            tags['TRACKTOTAL'] = [str(len(self.tracks()))]
        if 'ARTIST' in tags:
            tags['COMPOSER'] = tags['ARTIST']
        return tags

def init(ignore):
    log.info(f'Tags to ignore: "{ignore}"')
    AlbumInfo.IGNORE_TAGS_REX = re.compile(ignore)


@lru_cache(maxsize=5)
def get(path) -> AlbumInfo:
    return AlbumInfo(path)
