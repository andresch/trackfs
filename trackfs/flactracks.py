#!/usr/bin/env python3
# 
# Copyright 2020-2021 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#

import time

class TrackRegistry():
   """Keeps track of all individual tracks that currently get processed
   
   Each track has a unique key (usually the path of the virtual track file).
   
   The registry distinguishes three states for a track:
   * Unregistered: The track is not (yet) known to the registry
   * Announced: The track is known, but not yet available yet (processing still ongoing)
   * Available: The information about the track is available.
   
   """
   def __init__(self, rwlock):
      self.rwlock = rwlock
      self.registry = {}
      
   def add(self, key, track_file):
      with self.rwlock: 
         self.registry[key] = (1, time.time(), track_file)

   def is_unregistered(self, key):
      """Is the track at the given key not yet registered?"""
      with self.rwlock:
         return key not in self.registry

   def announce(self, key):
      """Announce that a new track will get processed soon"""
      with self.rwlock:
         self.registry[key] = None

   def is_announced(self, key):
      """Is the track registered, but not yet processed?"""
      with self.rwlock:
         # default value "" for get ensures that we don't 
         # treat unknown tracks as announced
         return self.registry.get(key,"") is None

   def is_registered(self, key):
      with self.rwlock:
         return isinstance(self.registry.get(key),tuple)
         
   def register_usage(self, key): return self._change_usage(key, +1)
   def release_usage(self, key): return self._change_usage(key, -1)
   def _change_usage(self, key, incr):
      with self.rwlock:
         (count, last_access, track_file) = self.registry[key]
         result = (count+incr, time.time(), track_file)
         self.registry[key] = result
         return result
         
   def __getitem__(self, key):
      with self.rwlock:
         return self.registry[key]
         
   def get(self, key, default=None):
      with self.rwlock:
         return self.registry.get(key, default)
         
   def __delitem__(self, key):
      with self.rwlock:
         del self.registry[key]
         
