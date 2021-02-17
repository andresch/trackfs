#!/usr/bin/env python3
# 
# Copyright 2020-2021 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#

#
# This module provides mapping of names between the virtual trackfs 
# filesystem and the underlying root filesystem, esp. the naming of
# the individual track-files
#

import os
import re
import string
import unicodedata

from dataclasses import dataclass, field
from functools import cached_property

from . import cuesheet
from . import albuminfo

import logging
log = logging.getLogger(__name__)

DEFAULT_TRACK_SEPARATOR     : str   = '.#-#.'
DEFAULT_MAX_TITLE_LEN       : int   = 20
DEFAULT_ALBUM_EXTENSION     : str   = '(?i:\\.flac|\\.wav)'
DEFAULT_VALID_CHARS         : str   = "-_() " + string.ascii_letters + string.digits
DEFAULT_KEEP_ALBUM          : bool  = False
DEFAULT_TRACK_EXTENSION     : str   = '.flac'

@dataclass(frozen=True)
class Factory:
    '''manages the configuration options for the virtual fuse paths'''
    
    track_separator         : str   = DEFAULT_TRACK_SEPARATOR
    max_title_len           : int   = DEFAULT_MAX_TITLE_LEN
    album_extension         : str   = DEFAULT_ALBUM_EXTENSION
    valid_filename_chars    : str   = DEFAULT_VALID_CHARS
    keep_album              : bool  = DEFAULT_KEEP_ALBUM
    track_extension         : bool  = DEFAULT_TRACK_EXTENSION
    
    @cached_property
    def track_file_regex(self):
        separator_rex = self.track_separator.replace('.','\\.')
        track_exentension_rex = self.track_extension.replace('.','\\.')
        flac_cue_rex = (
            '^(?P<basename>.*)(?P<extension>'+self.album_extension+')'+separator_rex
            + '(?P<num>\\d+)(?P<title>(\\.[^\\.]{,'+str(self.max_title_len)
            + '}?)?)'+track_exentension_rex+'$'
        )
        log.debug("Factory.track_file_regex: "+flac_cue_rex)
        return re.compile(flac_cue_rex)

    @cached_property
    def album_ext_regex(self):
        return re.compile(self.album_extension)

    def from_vpath(self, path):
        """Construct a FusePath instance from a given virtual path"""
        match = self.track_file_regex.match(path)
        if match is None: 
            log.debug(f'no track file in "{path}"');
            (root, ext) = os.path.splitext(path)
            return FusePath(root, ext, _factory=self)
        log.debug(f'track file in "{path}"');
        title = match['title'].lstrip()
        return FusePath(
            match['basename'], match['extension'], True,
            int(match['num']), title,
            self
        )
        
    def from_track(self, source_root, extension, track):
        return FusePath(
            source_root, extension, True,
            track.num, track.title, self
        )      
        
_DEFAULT_FACTORY = Factory()

@dataclass(frozen=True)
class FusePath:
    ''' represents an entry in the virtual trackfs filesystem'''
    source_root             : str
    extension               : str
    is_track                : bool          = False
    num                     : int           = None
    title                   : str           = None
    _factory                : Factory       = _DEFAULT_FACTORY
        
    @property
    def track_separator(self): return self._factory.track_separator
    @property
    def max_title_len(self): return self._factory.max_title_len
    @property
    def flac_extension(self): return self._factory.album_extension
    @property
    def valid_filename_chars(self): return self._factory.valid_filename_chars
    @property
    def track_file_regex(self): return self._factory.track_file_regex
    @property
    def album_ext_regex(self): return self._factory.album_ext_regex
    @property
    def keep_album(self): return self._factory.keep_album
    @property
    def track_extension(self): return self._factory.track_extension

    @cached_property
    def source(self):
        return self.source_root + self.extension
    
    @property
    def title_fragment(self):
        '''the fragment of a track's title that goes into a vpath'''
        if self.title is None or len(self.title) == 0: 
            return ""
        else:
            clean_title = unicodedata.normalize('NFKD', self.title)[:self.max_title_len]
            return "."+''.join(c if c in self.valid_filename_chars else "_" for c in clean_title)
        
    @property
    def vpath(self):
        if(self.is_track): 
            return (
                f'{self.source_root}{self.extension}{self.track_separator}{self.num:03d}'
                f'{self.title_fragment}{self.track_extension}'
            )
        else:  
            return self.source
            
    def dirname(self):
        return os.path.dirname(self.source_root)
    
    def readdir(self):
        entries = ['.', '..']
        for filename in os.listdir(self.source):
            (basename, extension) = os.path.splitext(filename)
            filepath = os.path.join(self.source, filename)
            if os.path.isfile(filepath) and self.album_ext_regex.fullmatch(extension):
                trx = albuminfo.get(filepath).tracks()
                if len(trx) > 0:
                    if self.keep_album:
                        entries.append(filename)
                    for t in trx:
                        entries.append( 
                            self._factory.from_track(basename, extension, t).vpath
                        )
                else:
                    entries.append(filename)
            else:
                entries.append(filename)
        log.debug(f'vdir entries:{entries}')
        return entries

    def for_other_track(self, num: int, title: str, start: cuesheet.Time, end: cuesheet.Time):
        '''Construct fusepath entry for another track of the same FLAC+CUE file'''
        return FusePath(
            self.source_root, self.extension, True,
            num, title, self._factory
        )