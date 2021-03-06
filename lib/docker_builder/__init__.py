import os
import sys
import yaml

__version__ = '0.2'

#
# Helpers - to move separately
#

import select
import subprocess
from cStringIO import StringIO

PIPE = subprocess.PIPE


# http://stackoverflow.com/questions/5486717/python-select-doesnt-signal-all-input-from-pipe
class LineReader(object):
    def __init__(self, fd):
        self._fd = fd
        self._buf = ''

    def fileno(self):
        return self._fd

    def readlines(self):
        data = os.read(self._fd, 4096)
        if not data:
            # EOF
            return None
        self._buf += data
        if '\n' not in data:
            return []
        tmp = self._buf.split('\n')
        lines, self._buf = tmp[:-1], tmp[-1]
        return lines


def execute(params):
    '''
    Execute a command, Popen wrapper
    '''
    if type(params) in (str, unicode):
        params = [params]

    if type(params) != list:
        raise Exception('Invalid params type, need to be string or a list')

    try:
        p = subprocess.Popen(params, stdout=PIPE, stderr=PIPE)
    except OSError as e:
        return 1, '', e

    proc_stdout = LineReader(p.stdout.fileno())
    proc_stderr = LineReader(p.stderr.fileno())

    readable = [proc_stdout, proc_stderr]

    stdout = []
    stderr = []
    results = [stdout, stderr]

    while readable:
        ready = select.select(readable, [], [], 10.0)[0]
        if not ready:
            continue
        for idx, stream in enumerate(ready):
            lines = stream.readlines()
            if lines is None:
                # got EOF on this stream
                readable.remove(stream)
                continue
            results[idx].extend(lines)
            for line in lines:
                if idx == 0:
                    sys.stdout.write(line +'\n')
                else:
                    sys.stderr.write(line +'\n')

    # Wait until completion of the process
    while p.returncode == None:
        p.poll()

    # return a tuple (code, stdout, stderr)
    return p.returncode, '\n'.join(results[0]), '\n'.join(results[1])


class Builder(object):
    def __init__(self, config_file, no_cache=False, no_push=False, containers=[]):
        super(Builder, self).__init__()
        self.config = self.load_config(config_file)
        self.no_cache = no_cache
        self.no_push = no_push
        self.containers = containers

    def load_config(self, file_path):
        '''
        Load the configuration
        '''
        with open(file_path) as f:
            data = f.read()
            try:
                config = yaml.safe_load(data)
            except Exception as e:
                sys.stderr.write('Error while loading the config file %s:\n' % file_path)
                sys.stderr.write('    %s\n' % e.message)
                sys.exit(1)
        return config

    def build_containers(self):
        '''
        Build the containers listed
        '''
        containers = self.containers or self.config.get('containers')
        for container in containers:
            image_id = self._build_container(container)
            self._tag_container(container, image_id)
            if not self.no_push:
                self._push_container(container)

    def _build_container(self, container):
        '''
        Build the container
        '''
        if not os.path.exists(container):
            sys.stderr.write('Missing folder %s\n' % container)
            sys.exit(1)

        build = [
            'docker',
            'build',
            '--rm=true',
            '--no-cache=%s' % ('true' if self.no_cache else 'false'),
            '--tag="%s"' % (self.get_tag_prefix(0) + container),
            container
        ]

        sys.stdout.write('%s\n' % ' '.join(build))

        return_code, stdout, stderr = execute(build)
        if return_code != 0:
            sys.stderr.write('Error while creating the container: %s' % container)
            sys.exit(1)

        return get_image_id(stdout)

    def _tag_container(self, container, image_id):
        '''
        Push the container to the repositories
        '''
        for idx, registry in enumerate(self.config.get('registries', [])):
            # by default tagged during build with registries[0]
            if idx == 0:
                continue
            tag = [
                'docker',
                'tag',
                '-f',
                image_id,
                self.get_tag_prefix(idx) + container
            ]

            sys.stdout.write('%s\n' % ' '.join(tag))

            return_code, stdout, stderr = execute(tag)
            if return_code != 0:
                sys.stderr.write('Error while tagging the container: %s' % container)
                sys.exit(1)


    def _push_container(self, container):
        '''
        Push the container to the repositories
        '''
        for idx, registry in enumerate(self.config.get('registries', [])):
            if registry.get('registry') != 'local':
                # Need to login
                login = [
                    'docker',
                    'login',
                    '--email="%s"' % registry.get('email'),
                    '--username="%s"' % registry.get('username'),
                    '--password="%s"' % registry.get('password'),
                    registry.get('registry')
                ]

                sys.stdout.write('%s\n' % ' '.join(login))

                return_code, stdout, stderr = execute(login)
                if return_code != 0:
                    sys.stderr.write('Login error.')
                    sys.exit(1)

            push = [
                'docker',
                'push',
                self.get_tag_prefix(idx) + container
            ]

            sys.stdout.write('%s\n' % ' '.join(push))

            return_code, stdout, stderr = execute(push)
            if return_code != 0:
                sys.stderr.write('Error while pushing the container: %s' % container)
                sys.exit(1)


    def get_tag_prefix(self, idx=0):
        '''
        Return the tag prefix depending on the list of registries defined in the config
        '''
        prefix = ''
        if len(self.config.get('registries', [])) > idx:
            prefix = self.config.get('registries')[idx].get('username') +'/'
        else:
            sys.stderr.write('Invalid registry index (%s) - only %s registries defined\n'
                % (idx, len(self.config.get('registries', []))))
        return prefix


def get_image_id(content):
    '''
    Provided with the output of a `docker build` command, return the image id of the built container
    '''
    image_id = False
    for l in content.splitlines():
        if l.startswith('Successfully built'):
            image_id = l.split()[2]
        else:
            continue
    return image_id
