
`trackfs`
=======

The `trackfs` python package provides a read-only FUSE filesystem that splits FLAC+CUE files (FLAC files with cue sheet embedded as vorbis comment) into individual FLAC files per track.

The recommended way to use `trackfs` is with docker and image `andresch/trackfs`. Please refer to the [`trackfs` homepage](https://github.com/andresch/trackfs) for further details. 

Usage
-----

Once you have installed `trackfs` [see [section "Installation"](#installation) below)  you can simply run it from the command line:

```
trackfs /path/to/yourmusiclibrary /path/to/mountpoint
```

Replace `/path/to/yourmusiclibrary` with the root directory where `trackfs` scans for FLAC+CUE files and `/path/to/yourmountpoint` with the directory that you want to use as mount point for the `trackfs`-filesystem. The mount point should be an existing, empty directory.

Once started you will find all directories and files from your music library also in the `trackfs`-filesystem. Only FLAC+CUE files got replaced: Instead of a single FLAC+CUE file you will find individual FLAC files for each track found in the embedded cue sheet. The track-files will have the following names:

    {basename(FLAC+CUE-file)}.#-#.{tracknumber}.{tracktitle}.{start}-{end}.flac

While the tracks can be used like regular files, they don't exist in the physical file system on your machine. Instead `trackfs` creates them on the fly whenever an application starts loading any of the track files. This usually takes (depending on your system) a few seconds.

### Finetuning

You should **NOT** run `trackfs` as user root. Instead it is recommended to run it with a user account who has _just_ the rights necessary to read the files in the music libary. If you accidentially launch `trackfs` as root, trackfs exit with an error messgage. If you know what you are doing and want to run track as root, you have to add the option `--root-allowed`.

In addition `trackfs` provides a bunch of options to fine-tune its behaviour. Call `trackfs --help` to learn about the options or visit the [`trackfs` homepage](https://github.com/andresch/trackfs#all-trackfs-options)

Installation
----

### Precondition

If you want/have to run `trackfs` on some linux system without docker make sure that your system meet the following 
preconditions / has the following software installed:

* **[python](https://www.python.org/)**: use recent a python version (>=3.8) (trackfs is developped and tested with 3.8), including pip
* **[fuse](https://github.com/libfuse/libfuse)**: make sure that you have FUSE support enabled in your kernal and the FUSE libraries installed
* **[flac](https://xiph.org/flac/)**: make sure you have official flac binaries (flac and metaflac) installed and on your path
* **[mp3splt](http://mp3splt.sourceforge.net/)**: make sure you have official mp3splt binaries installed and on your path

On most recent debian based system you should get all dependencies with

```
sudo apt-get install python3 python3-pip fuse libfuse-dev flac mp3splt
```

On alpine linux (used for the dockerized version of `trackfs`) you would use

```
sudo apk add python3 py3-pip fuse fuse-dev flac mp3splt
``` 

#### Verify that you have the expected python version

`trackfs` has been developed and tested with python 3.8. So better check that your distribution supports at least 3.8.

```
python3 --version
```

If you have an older version, we can't guarantee that trackfs works as expected.

It is unfortunately beyond the scope of this document to describe how you might get version 3.8 on your machine if not supported by your distribution.

#### Verify that pip is avaialbe and up-to-date
 
1. Make sure that pip is availalbe

    ```
    pip --version
    ```
    
   If this command exits with an error then your system doesn't have pip installed. On some systems python might be able to help you:

    ```
    sudo python3 -m ensurepip --default-pip
    ```
    
   On some systems, this might fail; just proceed with the next one
 
1. Make sure you have the latest pip version 

    ```
    sudo python3 -m pip install --upgrade pip
    ```

If you fail installing pip on your system, then you might want to consult the [python package documentation](https://packaging.python.org/tutorials/installing-packages/#ensure-you-can-run-pip-from-the-command-line)

### Installing `trackfs`

Now we can use pip to install trackfs

    ``´
    pip install --user trackfs
    ```

This command installs trackfs only for the current user (--user). Systemwide installation of `trackfs` is not recommended as you might run into dependency conflicts with package that come with your distribution. So don't execute without --user / as root, unless you know what you are doing. Please refer to pip's documentation for virtual environments, if you want to make `trackfs` available for more users.
	
Acknowledgments
---------------

`trackfs` began its live as a clone of [FLACCue](https://github.com/acenko/FLACCue). While FLACCue is designed for the usage with the Plex media server, the underlying idea of both projects is the same. Although there is little unmodified code of FLACCue left in `trackfs`, the project would most likely not have been started without the ideas in this groundwork. Kudos go to [acenkos](https://github.com/acenko)!

License
-------

`trackfs` is licensed under the terms of the [GNU Lesser General Public License v3.0](https://github.com/andresch/trackfs/blob/master/LICENSE.md)

