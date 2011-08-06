#!/usr/bin/python
# Copyright (c) 2010 OpenStack, LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import gettext
import os
import subprocess

from setuptools import setup, find_packages
from setuptools.command.sdist import sdist

# In order to run the i18n commands for compiling and
# installing message catalogs, we use DistUtilsExtra.
# Don't make this a hard requirement, but warn that
# i18n commands won't be available if DistUtilsExtra is
# not installed...
try:
    from DistUtilsExtra.auto import setup
except ImportError:
    from setuptools import setup
    print "Warning: DistUtilsExtra required to use i18n builders. "
    print "To build glance with support for message catalogs, you need "
    print "  https://launchpad.net/python-distutils-extra >= 2.18"

gettext.install('glance', unicode=1)

from glance import version


def run_git_command(cmd):
    output = subprocess.Popen(["/bin/sh", "-c", cmd],
                              stdout=subprocess.PIPE)
    return output.communicate()[0].strip()


if os.path.isdir('.git'):
    branch_nick_cmd = 'git branch | grep -Ei "\* (.*)" | cut -f2 -d" "'
    branch_nick = run_git_command(branch_nick_cmd)
    revid_cmd = "git --no-pager log --max-count=1 | cut -f2 -d' ' | head -1"
    revid = run_git_command(revid_cmd)
    revno_cmd = "git --no-pager log --oneline | wc -l"
    revno = run_git_command(revno_cmd)
    with open("glance/vcsversion.py", 'w') as version_file:
        version_file.write("""
# This file is automatically generated by setup.py, So don't edit it. :)
version_info = {
    'branch_nick': '%s',
    'revision_id': '%s',
    'revno': %s
}
""" % (branch_nick, revid, revno))


class local_sdist(sdist):
    """Customized sdist hook - builds the ChangeLog file from VC first"""

    def run(self):
        if os.path.isdir('.bzr'):
            # We're in a bzr branch

            log_cmd = subprocess.Popen(["bzr", "log", "--gnu"],
                                       stdout=subprocess.PIPE)
            changelog = log_cmd.communicate()[0]
            with open("ChangeLog", "w") as changelog_file:
                changelog_file.write(changelog)
        sdist.run(self)

cmdclass = {'sdist': local_sdist}

# If Sphinx is installed on the box running setup.py,
# enable setup.py to build the documentation, otherwise,
# just ignore it
try:
    from sphinx.setup_command import BuildDoc

    class local_BuildDoc(BuildDoc):
        def run(self):
            for builder in ['html', 'man']:
                self.builder = builder
                self.finalize_options()
                BuildDoc.run(self)
    cmdclass['build_sphinx'] = local_BuildDoc

except:
    pass


setup(
    name='glance',
    version=version.canonical_version_string(),
    description='The Glance project provides services for discovering, '
                'registering, and retrieving virtual machine images',
    license='Apache License (2.0)',
    author='OpenStack',
    author_email='openstack@lists.launchpad.net',
    url='http://glance.openstack.org/',
    packages=find_packages(exclude=['bin']),
    test_suite='nose.collector',
    cmdclass=cmdclass,
    include_package_data=True,
    classifiers=[
        'Development Status :: 4 - Beta',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python :: 2.6',
        'Environment :: No Input/Output (Daemon)',
    ],
    scripts=['bin/glance',
             'bin/glance-api',
             'bin/glance-cache-prefetcher',
             'bin/glance-cache-pruner',
             'bin/glance-cache-reaper',
             'bin/glance-control',
             'bin/glance-manage',
             'bin/glance-registry',
             'bin/glance-scrubber',
             'bin/glance-upload'],
    py_modules=[])
