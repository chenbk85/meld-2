### Copyright (C) 2002-2005 Stephen Kennedy <stevek@gnome.org>
### Copyright (C) 2011-2012 Kai Willadsen <kai.willadsen@gmail.com>

### Redistribution and use in source and binary forms, with or without
### modification, are permitted provided that the following conditions
### are met:
### 
### 1. Redistributions of source code must retain the above copyright
###    notice, this list of conditions and the following disclaimer.
### 2. Redistributions in binary form must reproduce the above copyright
###    notice, this list of conditions and the following disclaimer in the
###    documentation and/or other materials provided with the distribution.

### THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
### IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
### OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
### IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT,
### INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
### NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
### DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
### THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
### (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
### THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import errno
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ElementTree

from . import _vc


class Vc(_vc.CachedVc):

    CMD = "svn"
    NAME = "Subversion"
    VC_DIR = ".svn"
    VC_ROOT_WALK = False
    PATCH_INDEX_RE = "^Index:(.*)$"
    state_map = {
        "unversioned": _vc.STATE_NONE,
        "added": _vc.STATE_NEW,
        "normal": _vc.STATE_NORMAL,
        "missing": _vc.STATE_MISSING,
        "ignored": _vc.STATE_IGNORED,
        "modified": _vc.STATE_MODIFIED,
        "deleted": _vc.STATE_REMOVED,
        "conflicted": _vc.STATE_CONFLICT,
    }

    def commit_command(self, message):
        return [self.CMD,"commit","-m",message]
    def diff_command(self):
        if hasattr(self, "external_diff"):
            return [self.CMD, "diff", "--diff-cmd", self.external_diff]
        else:
            return [self.CMD, "diff"]

    def update_command(self):
        return [self.CMD,"update"]
    def add_command(self):
        return [self.CMD,"add"]
    def remove_command(self, force=0):
        return [self.CMD,"rm","--force"]
    def revert_command(self):
        return [self.CMD,"revert"]
    def resolved_command(self):
        return [self.CMD,"resolved"]

    def get_path_for_repo_file(self, path, commit=None):
        if commit is not None:
            raise NotImplementedError()

        if not path.startswith(self.root + os.path.sep):
            raise _vc.InvalidVCPath(self, path, "Path not in repository")
        path = path[len(self.root) + 1:]

        base, fname = os.path.split(path)
        svn_path = os.path.join(base, ".svn", "text-base", fname + ".svn-base")

        with tempfile.NamedTemporaryFile(prefix='meld-tmp', delete=False) as f:
            try:
                with open(svn_path, 'r') as vc_file:
                    shutil.copyfileobj(vc_file, f)
            except IOError as err:
                if err.errno != errno.ENOENT:
                    raise
                # If the repository path doesn't exist, we either have an
                # invalid path (difficult to check) or a new file. Either way,
                # we just return an empty file

        return f.name

    def _repo_version_support(self, version):
        return version < 12

    def valid_repo(self):
        if _vc.call([self.CMD, "info"], cwd=self.root):
            return False

        # Check for repository version, trusting format file then entries file
        format_path = os.path.join(self.root, self.VC_DIR, "format")
        entries_path = os.path.join(self.root, self.VC_DIR, "entries")
        if os.path.exists(format_path):
            version_file = format_path
        elif os.path.exists(entries_path):
            version_file = entries_path
        else:
            return False

        with open(version_file) as f:
            content = f.readline().strip()
        return self._repo_version_support(int(content))

    def switch_to_external_diff(self):
        self.external_diff = "diff"

    def _update_tree_state_cache(self, path, tree_state):
        while 1:
            try:
                status_cmd = [self.CMD, "status", "-v", "--xml", path]
                tree = ElementTree.parse(_vc.popen(status_cmd))
                break
            except OSError as e:
                if e.errno != errno.EAGAIN:
                    raise

        for target in tree.findall("target") + tree.findall("changelist"):
            for entry in (t for t in target.getchildren() if t.tag == "entry"):
                path = entry.attrib["path"]
                if path == "":
                    continue
                for status in (e for e in entry.getchildren() \
                               if e.tag == "wc-status"):
                    item = status.attrib["item"]
                    if item == "":
                        continue
                    rev = None
                    if "revision" in status.attrib:
                        rev = status.attrib["revision"]
                    mydir, name = os.path.split(path)
                    if mydir not in tree_state:
                        tree_state[mydir] = {}
                    tree_state[mydir][name] = (item, rev)

    def _lookup_tree_cache(self, rootdir):
        # Get a list of all files in rootdir, as well as their status
        tree_state = {}
        self._update_tree_state_cache(rootdir, tree_state)
        return tree_state

    def update_file_state(self, path):
        tree_state = self._get_tree_cache(os.path.dirname(path))
        self._update_tree_state_cache(path, tree_state)

    def _get_dirsandfiles(self, directory, dirs, files):
        tree = self._get_tree_cache(directory)

        if not directory in tree:
            return [], []

        retfiles = []
        retdirs = []

        dirtree = tree[directory]

        for name in sorted(dirtree.keys()):
            svn_state, rev = dirtree[name]
            path = os.path.join(directory, name)

            isdir = os.path.isdir(path)
            if isdir:
                if os.path.exists(path):
                    state = _vc.STATE_NORMAL
                else:
                    state = _vc.STATE_MISSING
                # svn adds the directory reported to the status list we get.
                if name != directory:
                    retdirs.append( _vc.Dir(path,name,state) )
            else:
                state = self.state_map.get(svn_state, _vc.STATE_NONE)
                retfiles.append(_vc.File(path, name, state, rev))

        return retdirs, retfiles
