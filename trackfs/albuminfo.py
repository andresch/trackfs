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

from mutagen.flac import FLAC
import chardet

from . import cuesheet

import logging

log = logging.getLogger(__name__)

DEFAULT_IGNORE_TAGS_REX = re.compile('CUE_TRACK.*|COMMENT')


class AlbumInfo():
    IGNORE_TAGS_REX = DEFAULT_IGNORE_TAGS_REX

    def __init__(self, path):
        self.path: str = path

    @cached_property
    def meta(self) -> FLAC:
        return FLAC(self.path)

    def _cue_from_external_file(self):
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
    def cue(self):
        meta = self.meta
        raw_cue = meta.tags.get('CUESHEET', [])
        if len(raw_cue) == 0:
            log.debug(f"regular flac file without cue sheet")
            raw_cue = self._cue_from_external_file()
            if raw_cue is None:
                return None
        else:
            raw_cue =raw_cue[0]
        log.debug(f"raw cue sheet from FLAC file:\n{raw_cue}")
        result = cuesheet.parse(raw_cue, meta.info.length)
        log.debug(f"parsed cue sheet from FLAC file:\n{result}")
        return result

    def tracks(self):
        return self.cue.tracks if self.cue is not None else None

    def track(self, num):
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

    def _cue_album_tags(self):
        cue = self.cue
        tags = {}
        if cue is not None:
            if cue.albumartists: tags['ALBUMARTIST'] = cue.albumartists
            if cue.composers: tags['COMPOSER'] = cue.composers
            if cue.album: tags['ALBUM'] = [cue.album]
            if cue.catalog: tags['CATALOG'] = [cue.catalog]
            if cue.discid: tags['DISCID'] = [cue.discid]
            if cue.year: tags['YEAR'] = [cue.year]
            if cue.discnumber: tags['DISCNUMBER'] = [cue.discnumber]
            if cue.totaldiscs: tags['TOTALDISCS'] = [cue.totaldiscs]

        return tags

    def _album_tags(self):
        meta = self.meta
        tags = {}
        for (k, v) in meta.tags:
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
        for (k, v) in self._cue_album_tags().items():
            if k not in tags:
                tags[k] = v

        return tags

    def _track_tags(self, num):
        t = self.track(num)
        tags = {}
        if t is not None:
            if t.artists:     tags['ARTIST'] = t.artists
            if t.composers:   tags['COMPOSER'] = t.composers
            if t.isrc:        tags['ISRC'] = [t.isrc]
            if t.num:         tags['TRACKNUMBER'] = [t.num]
            if t.title:       tags['TITLE'] = [t.title]

        return tags

    def _tag_as_flac_arg(self, tag_name, tag_value):
        log.debug(f'{tag_name}={tag_value}')
        # for whatever weird reason quote has a problem with some values if
        # not casted into str before
        return f"--tag={shlex.quote(str(tag_name))}={shlex.quote(str(tag_value))}"

    def track_tags_as_flac_args(self, num):
        tags = self._album_tags()
        for (k, vs) in self._track_tags(num).items():
            tags[k] = vs

        if 'TRACKTOTAL' not in tags:
            tags['TRACKTOTAL'] = [str(len(self.tracks()))]
        if 'ARTIST' in tags:
            tags['COMPOSER'] = tags['ARTIST']

        log.debug(f"tags for current track: {tags}")
        return ' '.join([self._tag_as_flac_arg(k, v) for k, vs in tags.items() for v in vs])


def init(ignore):
    log.info(f'Tags to ignore: "{ignore}"')
    AlbumInfo.IGNORE_TAGS_REX = re.compile(ignore)


@lru_cache(maxsize=5)
def get(path) -> AlbumInfo:
    return AlbumInfo(path)
