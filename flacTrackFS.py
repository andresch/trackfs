#!/usr/bin/env python3

# Note that you do not want to run this as root as this will
# give anyone read access to any file by just prepending /flaccue/.

from __future__ import print_function, absolute_import, division

import logging
import os

import mutagen
import time
import threading
import math

from errno import EACCES
from os.path import realpath

import sys
sys.path.insert(0, '.')

from fuse import FUSE, FuseOSError, Operations, LoggingMixIn
from typing import NamedTuple
from collections import namedtuple

from mutagen.flac import FLAC
from subprocess import Popen, PIPE

import re

from lark import Lark, Transformer

# cue-sheet grammar according to original spec
# https://web.archive.org/web/20070614044112/http://www.goldenhawk.com/download/cdrwin.pdf

cue_grammar = r"""
   start          : disc_entries tracks
   disc_entries   : disc_entry* 
   tracks         : track*
    
   ?disc_entry : catalog 
      | comment
      | performer
      | songwriter
      | title
      | file

   track          : "TRACK" INT TRACKTYPE track_entries
   track_entries  : ( track_entry )*
   ?track_entry   : isrc
      | flags
      | pregap
      | index
      | postgap
      | comment
      | performer
      | songwriter
      | title
      | file
      
   catalog        : "CATALOG" UPC_EAN
   comment        : "REM" REST_OF_LINE
   performer      : "PERFORMER" STRING
   songwriter     : "SONGWRITER" STRING
   title          : "TITLE" STRING
   file           : "FILE" STRING FILETYPE
   isrc           : "ISRC" ISRC
   flags          : "FLAGS" FLAG+
   index          : "INDEX" INDEX mmssff
   pregap         : "PREGAP" mmssff
   postgap        : "POSTGAP" mmssff
   mmssff         : TIME_ELEM ":" TIME_ELEM ":" TIME_ELEM
   
   TIME_ELEM      : DIGIT ~ 2
   INDEX          : DIGIT~1..2
   UPC_EAN        : DIGIT ~ 12..13
   REST_OF_LINE   : /[^\n]*/ NEWLINE
   ISRC           : LETTER LETTER (DIGIT|LETTER) ~ 10
   
   STRING : ("\"" /.*?/ "\"") | /[^ \n]+/
   FILETYPE : "BINARY"  // Intel binary file (LSBF). Use for data files.
      | "MOTOROLA"      // Motorola binary file (MSBF). Use for data files.
      | "AIFF"          // Audio AIFF file (44.1KHz 16-bit stereo)
      | "WAVE"          // Audio WAVE file (44.1KHz 16-bit stereo)
      | "MP3"           // Audio MP3 file (44.1KHz 16-bit stereo)

   TRACKTYPE: "AUDIO"   // Audio/Music (2352)
      | "CDG"           // Karaoke CD+G (2448)
      | "MODE1/2048"    // CD-ROM Mode1 Data (cooked)
      | "MODE1/2352"    // CD-ROM Mode1 Data (raw)
      | "MODE2/2336"    // CD-ROM XA Mode2 Data
      | "MODE2/2352"    // CD-ROM XA Mode2 Data
      | "CDI/2336"      // CD-I Mode2 Data
      | "CDI/2352"      // CD-I Mode2 Data

   FLAG: "DCP"       // Digital copy permitted
      | "4CH"        // Four channel audio
      | "PRE"        // Pre-emphasis enabled (audio tracks only)
      | "SCMS"       // Serial Copy Management System
      | "DATA"       // set for data files

   %import common.NEWLINE
   %import common.INT
   %import common.LETTER
   %import common.DIGIT
   %import common.ESCAPED_STRING
   %import common.LETTER
   %import common.WS
   %ignore WS"""

class CueSheet():
   def __init__(self,args):
      entries = args[0]
      self.tracks = args[1].children
      
   def __repr__(self):
      return f'cuesheet:\n{self.tracks}'

   def calc_track_times(self, disc_duration):
      for i in range(0,len(self.tracks)-1):
         curr = self.tracks[i]
         curr.end = self.tracks[i+1].start
         curr.duration = curr.end-curr.start
      last = self.tracks[-1];
      last.end = MmSsCc(disc_duration)
      last.duration = last.end-last.start
      return self
         
class CueTrack():
   def __init__(self, args):
      self.num = args[0]
      self.type = args[1]
      self.artists = None
      self.composers = None
      self.title = None
      self.isrc = None
      self.start = None
      self.end = None
      self.duration = None
      self.track_entries = args[2]
      for entry in args[2].children:
         if entry.data == 'performer':
            self._extend__list_attr("artists",entry.children[0])
         if entry.data == 'songwriter':
            self._extend__list_attr("composers",entry.children[0])
         elif entry.data == 'title':
            self.title = entry.children[0]
         elif entry.data == 'isrc':
            self.isrc = entry.children[0]
         elif entry.data == 'index':
            idx = entry.children
            # we're only interested in index 1
            if idx[0] == 1:
               self.start = idx[1];
            
   def _extend__list_attr(self,name,value):
      # most rippers use ";" as delimiter for multiple values inside a 
      # single entry rather than having multiple entries
      values = [p.strip() for p in value.split(";")]
      old_value = getattr(self,name)
      if old_value == None:
         setattr(self,name,values)
      else:
         setattr(self,name,old_value+values)
            
   def __repr__(self):
      return ( f"track #{self.num} {self.type} [{self.isrc}] [{self.start}-{self.end}]\n"
         + (f"  title: '{self.title}'\n" if self.title else "")
         + (f"  artists: {self.artists}\n" if self.artists else "")
         + (f"  composers: {self.composers}\n" if self.composers else "")
         + '\n'
      )
      
class MmSsCc():
   mm = 0
   sscc = 0
   
   def __init__(self,*args):
      if len(args) == 0:
         self.mm = self.sscc = 0
      elif isinstance(args[0],float):
         # value as seconds with faction
         (self.mm, self.sscc) = divmod( int(math.trunc(args[0] * 100)), 6000 )
      elif isinstance(args[0],tuple):
         # value is tuple (minutes, seconds, frames)
         (self.mm, ss, fr) = args[0]
         self.sscc = ss*100 + (fr*100)//75
      elif isinstance(args[0],str):
         # value is string template "mmsscc"
         self.mm = int(args[0][0:2])
         self.sscc = int(args[0][2:6])
      elif isinstance(args[0],int):
         # value is two ints: mm, sscc
         self.mm = args[0]
         self.sscc = args[1]

   def seconds(self):
      return 60.0 * self.mm + self.sscc / 100
   
   def flac_time(self):
      return '%02d:%05.2f' % (self.mm, self.sscc / 100)
      
   def __repr__(self):
      return '%02d%04d' % (self.mm, self.sscc)
   
   def __ne__(self, other):
     return self._time != other._time

   def __eq__(self, other):
      return self.mm == other.mm and self.sscc == other.sscc

   def __add__(self, other):
     c, sscc = divmod( self.sscc+other.sscc, 6000 )
     return MmSsCc(self.mm+other.mm+c, sscc)
       
   def __sub__(self, other):
     sscc = self.sscc - other.sscc
     if sscc<0:
        return MmSsCc(self.mm-other.mm-1, sscc+6000)
     else:
        return MmSsCc(self.mm-other.mm, sscc)
      
class CueTransformer(Transformer):
   start = CueSheet
   track = CueTrack
   def mmssff(self, elems):
      return MmSsCc(elems[0], elems[1]*100+elems[2]//75)
   def REST_OF_LINE(self, comment_line):
      return comment_line[1:-1] if len(comment_line)>0 else ""
   def STRING(self, string):
      return string[1:-1] if string[0]=='"' else str(string)
   UPC_EAN=str
   ISRC=str
   TRACKTYPE=str
   INT=int
   INDEX=int
   TIME_ELEM=int

cue_parser = Lark(cue_grammar)
    
test_cue = r"""REM DISCID A10A2E0D
PERFORMER "Zaz"
TITLE "Paris"
CATALOG 5054196339524
REM DATE 2014
REM DISCNUMBER 1
REM TOTALDISCS 1
REM COMMENT "CUERipper v2.1.4 Copyright (C) 2008-12 Grigory Chudov"
FILE "Zaz - Paris.flac" WAVE
  TRACK 01 AUDIO
    PERFORMER Zaz
    PERFORMER Zazo
    TITLE "Paris sera toujours Paris"
    ISRC FR2PY1403200
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    PERFORMER "Zaz"
    TITLE "Sous le ciel de Paris"
    ISRC FR2PY1403250
    INDEX 01 02:58:68"""
    
#text = '{"key": ["item0", "item1", 3.14, true]}'
#print( CueTransformer(visit_tokens=True).transform(cue_parser.parse(test_cue)) )
#exit()    

class FilenameInfo(NamedTuple):
   path : str
   is_split : bool
   num : int
   start : MmSsCc
   end : MmSsCc
   
   def to_filename(self):
      (basename, extension) = os.path.splitext(self.path)
      return f'{basename}.flaccuesplit.{self.num:03d}.{self.start}-{self.end}{extension}'
      
   def parse(path):
      splits = path.split('.flaccuesplit.')
      if len(splits) == 1:
         return FilenameInfo(path, False, None, None, None)
      
      (extra, extension) = os.path.splitext(splits[1])
      (num, start_end) = extra.split('.')
      (start, end) = [MmSsCc(t) for t in start_end.split('-') ]
      return FilenameInfo(splits[0]+extension, True, int(num), start, end)

class FlacInfo():
   
   def __init__(self,path):
      self.path : str = path
      self._meta : FLAC = None
      self._cue : mktoc.parser.ParseData = None
   
   def meta(self) -> FLAC:
      if self._meta is None:
         self._meta = FLAC(self.path)
      return self._meta
      
   def cue(self) -> CueSheet:
      if self._cue is None:
         meta = self.meta()
         raw_cue = meta.tags['CUESHEET']
         if len(raw_cue) == 0:
            return None
         #print(raw_cue)
         self._cue = CueTransformer(visit_tokens=True
            ).transform(cue_parser.parse(raw_cue[0])
            ).calc_track_times(meta.info.length)
         print(self._cue)
      return self._cue
   
   def tracks(self):
      cue = self.cue()
      return cue.tracks if cue is not None else None
      
   def track(self, num):
      trx = self.tracks();
      if trx is None: return None
      t = trx[num-1];
      if t.num == num: return t
      for t in trx:
         if t.num == num: return t
      return None
   
   _IGNORE_TAGS = [ re.compile(rex) for rex in [ 'CUE_TRACK.*', 'COMMENT', 'ALBUM ARTIST' ] ]
   def _ignore_tag(tag):
      for ignorex in FlacInfo._IGNORE_TAGS:
         if ignorex.match(tag): return True
      return False
      
   def _album_tags(self):
      meta = self.meta()
      tags = {}
      for (k,v) in meta.tags:
         k = k.upper()
         # skip multi-line tags
         if len(v.splitlines()) != 1: continue
         # skip _IGNORE_TAGS
         if FlacInfo._ignore_tag(k): continue
         if k not in tags: tags[k] = []
         tags[k].append(v)
      
      # make sure ALBUMARTIST and ALBUM are set 
      # in case ARTIST and TITLE have been used instead
      if 'ALBUMARTIST' not in tags and 'ARTIST' in tags:
         tags['ALBUMARTIST'] = tags['ARTIST']
      if 'ALBUM' not in tags and 'TITLE' in tags:
         tags['ALBUM'] = tags['TITLE']
      return tags
   
   def _track_tags(self, num):
      t = self.track(num)
      tags = {}
      if t is not None: 
         if t.artists:     tags['ARTIST']       = t.artists
         if t.isrc:        tags['ISRC']         = [t.isrc]
         if t.num:         tags['TRACKNUMBER']  = [t.num]
         if t.title:       tags['TITLE']        = [t.title]
         
      return tags
         
   
   def track_tags(self, num):
      tags = self._album_tags()
      for (k,vs) in self._track_tags(num).items():
         tags[k] = vs
         
      if 'TRACKTOTAL' not in tags: 
         tags['TRACKTOTAL'] = [str(len(self.tracks()))]
      if 'ARTIST' in tags:
         tags['COMPOSER'] = tags['ARTIST']

      print(tags)
      return ' '.join([ f'--tag="{k}"="{v}"' for k,vs in tags.items() for v in vs ])

class TagModifications():
   def __init__(self,spec):
      self.replacements = self.parse(spec)
   
   def parse(self,spec):
      pass
   
   
class FlacTrackFS(LoggingMixIn, Operations):
   def __init__(self, root):
     self.root = realpath(root)
     self.rwlock = threading.RLock()
     self._open_subtracks = {}
     self._last_info = None

   def __call__(self, op, path, *args):
      return super(FlacTrackFS, self).__call__(op, self.root + path, *args)

   def clean_path(self, path):
      # Get a file path for the FLAC file from a FlacTrackFS path.
      # Note that files accessed through FlacTrackFS will
      # still read normally--we just need to trim off the song
      # times.
      if('.flaccuesplit.' in path):
         splits = path.split('.flaccuesplit.')
         times, extension = os.path.splitext(splits[1])
         try:
            # The extension should not parse as an int nor split into ints
            # separated by :. If it does, we have no extension.
            int(extension.split('_')[0])
            extension = ''
         except ValueError:
            pass
         path = splits[0] + extension
      return path
      
   def getattr(self, path, fh=None):
      # If it's one of the FlacTrackFS paths, we need to adjust the file size to be
      # appropriate for the shortened data.
      
      info = FilenameInfo.parse(path)
      if(info.is_split):
         try:
            st = os.lstat(info.path)
            toreturn = dict((key, getattr(st, key)) for key in (
                            'st_atime', 'st_ctime', 'st_gid', 'st_mode', 'st_mtime',
                            'st_nlink', 'st_size', 'st_uid'))
            # Estimate the file size.
            f = self.flac_info(info.path).meta()
            toreturn['st_size'] = int((info.end - info.start).seconds() *
                                      f.info.channels *
                                      (f.info.bits_per_sample/8) *
                                      f.info.sample_rate)
            return toreturn
         except:
            import traceback
            traceback.print_exc()
      # Otherwise, just get the normal info.
      path = self.clean_path(path)
      st = os.lstat(path)
      return dict((key, getattr(st, key)) for key in (
         'st_atime', 'st_ctime', 'st_gid', 'st_mode', 'st_mtime',
         'st_nlink', 'st_size', 'st_uid'))

   getxattr = None

   listxattr = None

   def flac_info(self, path):
      with self.rwlock:
         if self._last_info is None or self._last_info.path != path:
            self._last_info = FlacInfo(path)
         return self._last_info
         
   def open(self, path, flags, *args, **pargs):
      # We don't want FlacTrackFS messing with actual data.
      # Only allow Read-Only access.
      if((flags | os.O_RDONLY) == 0):
         raise ValueError('Can only open files read-only.')
      raw_path = path
      # Handle the FlacTrackFS files.
      info = FilenameInfo.parse(path)
      if(info.is_split):
         with self.rwlock:
            # Hold a file handle for the actual file.
            fd = os.open(info.path, flags, *args, **pargs)
            # If we've already processed this file and still have it in memory.
            if(raw_path in self._open_subtracks):
               if(self._open_subtracks[raw_path] is not None):
                  # Update the stored info.
                  (positions, audio, count, last_access) = self._open_subtracks[raw_path]
                  count += 1
                  last_access = time.time()
                  positions[fd] = 0
                  self._open_subtracks[raw_path] = (positions, audio, count, last_access)
                  # Return the file handle.
                  return fd
               else:
                  # We're still processing this track. Wait for it to finish.
                  process = False
            else:
               # This is a new track to process.
               process = True
               self._open_subtracks[raw_path] = None
         if(process):
            # Otherwise, we have to process the FLAC file to extract the track.
            fi = self.flac_info(info.path)
            flac_cmd = f'flac -d --stdout --skip={info.start.flac_time()} --until={info.end.flac_time()} "{info.path}" | flac --totally-silent --no-seektable --fast --stdout {fi.track_tags(info.num)} -'
            # re-encode: -0 --disable-fixed-subframes --disable-constant-subframes
            flac = Popen(flac_cmd, shell=True, stdout=PIPE)
            (audio, xxx) = flac.communicate()
            # Store some extra info in addition to the wave file.
            positions = {}
            positions[fd] = 0
            count = 1
            last_access = time.time()
            # Keep a copy of the data in memory.
            self._open_subtracks[raw_path] = (positions, audio, count, last_access)
            # Define a function that will clean up the memory use once it's no longer needed.
            def cleanup():
               (positions, audio, count, last_access) = self._open_subtracks[raw_path]
               # Wait for all open instances of this file to be closed.
               # Also ensure there has been no access to the data for 60 seconds.
               while(count > 0 or (time.time() - last_access < 60)):
                  with(self.rwlock):
                     (positions, audio, count, last_access) = self._open_subtracks[raw_path]
                  # Check every 5 seconds.
                  time.sleep(5)
               # Delete the entry. This removes all references to the data which allows
               # garbage collection to clean up when appropriate.
               with(self.rwlock):
                  del self._open_subtracks[raw_path]
            # Start a thread running that function.
            thread = threading.Thread(target=cleanup)
            thread.start()
            # Return the file handle.
            return fd
         else:
            acquired = False
            try:
               while(True):
                  self.rwlock.acquire()
                  acquired = True
                  if(self._open_subtracks[raw_path] is not None):
                     break
                  self.rwlock.release()
                  acquired = False
                  time.sleep(0.1)
               # Update the stored info.
               (positions, audio, count, last_access) = self._open_subtracks[raw_path]
               count += 1
               last_access = time.time()
               positions[fd] = 0
               self._open_subtracks[raw_path] = (positions, audio, count, last_access)
               # Return the file handle.
               return fd
            finally:
               if(acquired):
                  self.rwlock.release()
      else:
         # With any other file, just pass it along normally.
         # This allows FLAC files to be read with a FlacTrackFS path.
         # Note that you do not want to run this as root as this will
         # give anyone read access to any file.
         with self.rwlock:
            return os.open(path, flags, *args, **pargs)

   def read(self, path, size, offset, fh):
      with self.rwlock:
         if(path in self._open_subtracks):
            # For files we've processed.
            positions, audio, count, last_access = self._open_subtracks[path]
            # Store the current offset.
            positions[fh] = offset
            # Update the last accessed time.
            last_access = time.time()
            # Update the stored data.
            self._open_subtracks[path] = (positions, audio, count, last_access)
            # Return the data requested.
            return bytes(audio[positions[fh]:positions[fh]+size])
         else:
            # For all other files, just access it normally.
            os.lseek(fh, offset, 0)
            return os.read(fh, size)

   def readdir(self, path, fh):
      path = self.clean_path(path)
      entries = []
      for filename in os.listdir(path):
         basename, extension = os.path.splitext(filename)
         if( extension == ".flac" ):
            trx = self.flac_info(os.path.join(path, filename)).tracks()
            if trx:
               for t in trx:
                  entries.append(FilenameInfo(filename, True, t.num, t.start, t.end).to_filename())
            else:
               entries.append(filename)
         else:
            entries.append(filename)
      return ['.', '..'] + entries

   def readlink(self, path, *args, **pargs):
      path = self.clean_path(path)
      return os.readlink(path, *args, **pargs)

   def release(self, path, fh):
      with(self.rwlock):
         # If we're closing a FlacTrackFS file...
         if(path in self._open_subtracks):
            positions, audio, count, last_access = self._open_subtracks[path]
            # Delete the file handle from the stored list.
            del positions[fh]
            # Decrement the access count.
            count -= 1
            # Update the last access time.
            last_access = time.time()
            # Update the stored info.
            self._open_subtracks[path] = (positions, audio, count, last_access)
         # Close the OS reference to the file.
         return os.close(fh)

   def statfs(self, path):
      path = self.clean_path(path)
      stv = os.statvfs(path)
      return dict((key, getattr(stv, key)) for key in (
         'f_bavail', 'f_bfree', 'f_blocks', 'f_bsize', 'f_favail',
         'f_ffree', 'f_files', 'f_flag', 'f_frsize', 'f_namemax'))



if __name__ == '__main__':
   import argparse
   parser = argparse.ArgumentParser()
   parser.add_argument('root')
   parser.add_argument('mount')
   args = parser.parse_args()

   #logging.basicConfig(level=logging.DEBUG)
   fuse = FUSE(
      FlacTrackFS(args.root), args.mount, foreground=True, allow_other=True)