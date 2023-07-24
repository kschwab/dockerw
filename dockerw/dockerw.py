#!/usr/bin/env python3
# Copyright (c) 2022-2023, Kyle Schwab
# All rights reserved.
#
# This source code is licensed under the MIT license found at
# https://github.com/kschwab/dockerw/blob/main/LICENSE.md
'''
dockerw
#######

Docker run wrapper script.
'''

# To install latest version of dockerw (script only):
# wget -nv https://raw.githubusercontent.com/kschwab/dockerw/main/dockerw/dockerw.py -O dockerw && chmod a+x dockerw

# To install specific version of dockerw (script only):
# wget -nv https://raw.githubusercontent.com/kschwab/dockerw/<VERSION>/dockerw/dockerw.py -O dockerw && chmod a+x dockerw

# SemVer 2.0.0 (https://github.com/semver/semver/blob/master/semver.md)
# Given a version number MAJOR.MINOR.PATCH, increment the:
#  1. MAJOR version when you make incompatible API changes
#  2. MINOR version when you add functionality in a backwards compatible manner
#  3. PATCH version when you make backwards compatible bug fixes
# Additional labels for pre-release and build metadata are available as extensions to the MAJOR.MINOR.PATCH format.
__version__ = '1.0.0'
__title__ = 'dockerw'
__uri__ = 'https://github.com/kschwab/dockerw'
__author__ = 'Kyle Schwab'
__summary__ = 'Docker run wrapper script. Provides a super-set of docker run capabilities.'
__doc__ = __summary__
__copyright__ = 'Copyright (c) 2022-2023, Kyle Schwab'
__license__ = __copyright__ + '''
All rights reserved.

This source code is licensed under the MIT license found at
https://github.com/kschwab/dockerw/blob/main/LICENSE.md'''

import argparse
import copy
import grp
import os
import pathlib
import platform
import pwd
import re
import shlex
import subprocess
import sys
import tempfile
from importlib.machinery import SourceFileLoader

DOCKERW_UID = int(os.environ.get("SUDO_UID", os.getuid()))
DOCKERW_GID = int(os.environ.get("SUDO_GID", os.getgid()))
DOCKERW_UNAME = pwd.getpwuid(DOCKERW_UID).pw_name
DOCKERW_VENV_PATH = pathlib.PosixPath(f'/.dockerw')
DOCKERW_VENV_HOME_PATH = DOCKERW_VENV_PATH / f'home/{DOCKERW_UNAME}'
DOCKERW_VENV_COPY_PATH = DOCKERW_VENV_PATH / 'copy'
DOCKERW_VENV_RC_PATH   = DOCKERW_VENV_PATH / 'rc.sh'
DOCKERW_VENV_SHELLS = ('sh', 'bash', 'dash', 'ksh', 'ash')

class _DockerwParser(argparse.ArgumentParser):
    _dockerw_args = set()

    def __init__(self, *args, add_help: bool=False, **kwargs):
        super(_DockerwParser, self).__init__(*args, add_help=add_help, **kwargs)

    def add_argument(self, *args, is_dockerw_arg: bool=True, **kwargs):
        action = super(_DockerwParser, self).add_argument(*args, **kwargs)
        if is_dockerw_arg:
            _DockerwParser._dockerw_args.add(action.dest)

    @staticmethod
    def is_dockerw_arg(arg: str):
        return arg in _DockerwParser._dockerw_args

class _DefaultsAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        _VenvAction(option_string, None).__call__(parser, namespace, values, option_string)
        _DoodAction(option_string, None).__call__(parser, namespace, values, option_string)
        _X11Action(option_string, None).__call__(parser, namespace, values, option_string)
        namespace.defaults = True
        namespace.interactive = True
        namespace.tty = True
        namespace.rm = True
        namespace.init = True
        namespace.privileged = True
        namespace.network = 'host'
        namespace.security_opt = 'seccomp=unconfined'
        namespace.detach_keys = 'ctrl-q,ctrl-q'
        namespace.hostname = f'{platform.node()}'
        namespace.workdir = '/app'
        namespace.volume.append(f'{pathlib.Path.cwd()}:/app')
        namespace.env.append('TERM=xterm-256color')
        for is_copy, paths in [(False, ['~/.bash_history', '~/.vscode', '~/.emacs', '~/.emacs.d', '~/.vimrc']),
                               (True,  ['~/.gitconfig', '~/.ssh'])]:
            for path in paths:
                src_path = pathlib.PosixPath(re.sub(r'^~', pwd.getpwuid(DOCKERW_UID).pw_dir, path)).resolve()
                if src_path.exists():
                    namespace.volume += _update_volume_paths([f'{path}:{path}'], is_copy)

class _InfoAction(argparse.Action):
    def __call__(self, parser, _namespace=None, _values=None, option_string=None):
        if option_string == '--help':
            dockerw_options = parser.format_help().split('optional arguments:')[-1].lstrip('\n')
            print(f"{_run_os_cmd('docker run --help').stdout.replace('docker run', 'dockerw run')}")
            print(f"Dockerw Options:\n{dockerw_options}")
            exit(0)
        else:
            print(_run_os_cmd('docker --version').stdout.rstrip())
            print('Dockerw version', __version__)
            exit(0)

class _UserAction(argparse.Action):
    def __call__(self, _parser, namespace, values, _option_string=None):
        if not namespace.venv:
            namespace.user = values[0]

class _VenvAction(argparse.Action):
    def __call__(self, _parser, namespace, _values, _option_string=None):
        namespace.venv = True
        namespace.user = 'root'
        namespace.entrypoint = 'sh'
        namespace.env.append('DOCKERW_VENV=1')
        namespace.env.append(f'ENV={DOCKERW_VENV_RC_PATH}')

class _X11Action(argparse.Action):
    def __call__(self, _parser, namespace, _values, _option_string=None):
        if os.geteuid() != 0:
            result = _run_os_cmd('xauth info | grep "Authority file" | awk \'{ print $3 }\'')
        else:
            result = _run_os_cmd(f'su {DOCKERW_UNAME} -c "xauth info" | grep "Authority file" | awk \'{{ print $3 }}\'')
        if result.returncode == 0 and pathlib.PosixPath('/tmp/.X11-unix').exists():
            namespace.x11 = True
            namespace.env.append('DISPLAY')
            namespace.volume.append('/tmp/.X11-unix:/tmp/.X11-unix:ro')
            namespace.volume.append(f'{result.stdout.strip()}:~/.Xauthority:ro')

class _DoodAction(argparse.Action):
    def __call__(self, _parser, namespace, _values, _option_string=None):
        namespace.dood = True
        namespace.volume.append('/var/run/docker.sock:/var/run/docker.sock')

class _CopyAction(argparse.Action):
    def __call__(self, _parser, namespace, values, _option_string=None):
        for arg in _update_volume_paths(values, True):
            namespace.volume.append(arg)

def _run_os_cmd(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, universal_newlines=True)

def _update_volume_paths(volumes: list, is_copy: bool=False) -> list:
    for volume in range(len(volumes)):
        src_path, dest_path, options = (volumes[volume].split(':') + [''])[:3]
        src_path = re.sub(r'^~', pwd.getpwuid(DOCKERW_UID).pw_dir, src_path)
        src_path = str(pathlib.PosixPath(src_path).resolve())
        dest_path = re.sub(r'^~', f'/home/{DOCKERW_UNAME}', dest_path)
        if is_copy == True and not dest_path.startswith(str(DOCKERW_VENV_COPY_PATH)):
            options = options.split(',') if options else []
            dest_path = str(DOCKERW_VENV_COPY_PATH / dest_path.lstrip(os.sep))
            if 'ro' not in options:
                options = [ opt for opt in options if opt != 'rw'] + ['ro']
            options = ','.join(options)
        elif dest_path.startswith(f'/home/{DOCKERW_UNAME}'):
            dest_path = dest_path.replace(f'/home/{DOCKERW_UNAME}', f'{DOCKERW_VENV_HOME_PATH}', 1)
        volumes[volume] = f'{src_path}:{dest_path}{":" + options if options else ""}'
    return volumes

def _parse_image_name(image_name: str) -> tuple:
    try:
        image_name = \
            re.match('^((?P<registry>([^/]*[\.:]|localhost)[^/]*)/)?/?(?P<name>[a-z0-9][^:]*):?(?P<tag>.*)', image_name).groupdict()
    except:
        exit(f'Error: Invalid image name provided: "{image_name}"')
    return (image_name['registry'] if image_name['registry'] else 'docker.io',
            image_name['name'],
            image_name['tag'] if image_name['tag'] else 'latest')

def _parsed_args_to_list(parsed_args: argparse.Namespace) -> list:
    parsed_args_list = []
    parsed_args_dict = vars(parsed_args)
    for arg_name in parsed_args_dict.keys():
        if _DockerwParser.is_dockerw_arg(arg_name):
            continue
        arg_value = parsed_args_dict[arg_name]
        if arg_value not in [None, False, []]:
            if isinstance(arg_value, str):
                parsed_args_list.append(f'--{arg_name.replace("_","-")}={arg_value}')
            elif isinstance(arg_value, list):
                if arg_name == 'volume':
                    arg_value = _update_volume_paths(arg_value)
                parsed_args_list += list(set([ f'--{arg_name.replace("_","-")}={val}' for val in arg_value ]))
            else:
                parsed_args_list.append(f'--{arg_name.replace("_","-")}')
    if hasattr(parsed_args, 'image') and parsed_args.image != ['']:
        parsed_args_list += parsed_args.image
    return parsed_args_list

def _shlex_join(arg_list) -> str:
    # This is a python 3.6 workaround for shlex.join (added in python 3.8)
    return ' '.join([shlex.quote(arg) for arg in arg_list])

def _add_docker_args(parser: argparse.ArgumentParser, docker_cmd: str, ignore_args: list=[]) -> list:
    for line in _run_os_cmd(f'docker {docker_cmd} --help').stdout.splitlines():
        matched = re.match(r'\s*(?P<short>-\w)?,?\s*(?P<long>--[^\s]+)\s+(?P<val_type>[^\s]+)?\s{2,}(?P<help>\w+.*)', line)
        if matched:
            arg = matched.groupdict()
            flags = (arg['short'], arg['long']) if arg['short'] else (arg['long'],)
            if arg['long'] not in ignore_args:
                if arg['val_type'] == 'list':
                    parser.add_argument(*flags, action='append', default=[], help=argparse.SUPPRESS, is_dockerw_arg=False)
                elif arg['val_type']:
                    parser.add_argument(*flags, type=str, help=argparse.SUPPRESS, is_dockerw_arg=False)
                else:
                    parser.add_argument(*flags, action='store_true', default=False, help=argparse.SUPPRESS, is_dockerw_arg=False)

def _dockerw_load(dockerw_load_path: str) -> list:
    defaults_file_path = pathlib.Path(dockerw_load_path, '.dockerw/defaults.py')
    return shlex.split(' '.join(parse_defaults_file(defaults_file_path).get('dockerw_defaults', '')))

def _yes_no_prompt(prompt_msg: str, answer_default: bool, is_interactive: bool) -> bool:
    prompt_default = 'Y/n' if answer_default else 'N/y'
    answer_default = 'yes' if answer_default else 'no'
    prompt_str = f'{prompt_msg} [{prompt_default}]: '
    if is_interactive:
        answer = input(prompt_str).lower() or answer_default
    else:
        answer = answer_default
        print(f'{prompt_str}{answer}')
    while answer[:1] not in ['y', 'n']:
        print('Please answer yes or no...')
        answer = input(prompt_str).lower() or answer_default
    return True if answer[:1] == 'y' else False

def dockerw_run(args: list) -> None:
    last_parse_index = args.index('--') if '--' in args else len(args)
    args, container_cmds = args[0:last_parse_index], args[last_parse_index:]
    image_nargs = '*' if container_cmds else argparse.REMAINDER

    try:
        load_parser = argparse.ArgumentParser()
        load_parser.add_argument('--load', default='')
        load_parser.add_argument('--disable-auto-load', action='store_true')
        load_args, ignore_other_args = load_parser.parse_known_args(args)
        if load_args.load:
            os.chdir(pathlib.Path(re.sub(r'^~', pwd.getpwuid(DOCKERW_UID).pw_dir, load_args.load)).resolve())
        elif not load_args.disable_auto_load:
            defaults_file_path = find_nearest_defaults_file_path()
            if defaults_file_path:
                load_args.load = str(defaults_file_path.parent.parent)
        if load_args.load:
            args = _dockerw_load(load_args.load) + args
    except FileNotFoundError:
        exit(f'Error: Load path does not exist: {load_args.load}')

    exec_parser = _DockerwParser()
    exec_parser.add_argument('image', nargs=image_nargs, help=argparse.SUPPRESS)
    _add_docker_args(exec_parser, 'exec')

    run_parser = _DockerwParser()
    _add_docker_args(run_parser, 'run', ignore_args=['--help', '--version', '--user'])
    run_parser.add_argument('image',               nargs=image_nargs, help=argparse.SUPPRESS)
    run_parser.add_argument('--help',              action=_InfoAction, nargs=0, help=argparse.SUPPRESS, is_dockerw_arg=False)
    run_parser.add_argument('--version',           action=_InfoAction, nargs=0, help=argparse.SUPPRESS, is_dockerw_arg=False)
    run_parser.add_argument('--user',              action=_UserAction, nargs=1, help=argparse.SUPPRESS, is_dockerw_arg=False)
    run_parser.add_argument('--load',              metavar='string', help='Load dockerw project')
    run_parser.add_argument('--disable-auto-load', action='store_true', help='Disable auto loading of dockerw project')
    run_parser.add_argument('--default-image',     metavar='string', help='Default image if not specified')
    run_parser.add_argument('--default-shell',     metavar='string', help='Default shell to use inside container')
    run_parser.add_argument('--defaults',          action=_DefaultsAction, nargs=0, help='Enable dockerw default args')
    run_parser.add_argument('--x11',               action=_X11Action, nargs=0, help='Enable x11 support if possible')
    run_parser.add_argument('--venv',              action=_VenvAction, nargs=0, help='Enable user creation')
    run_parser.add_argument('--login-shell',       action='store_true', help='Enable login shell for venv (venv must be enabled)')
    run_parser.add_argument('--dood',              action=_DoodAction, nargs=0, help='Enable Docker-outside-of-Docker')
    run_parser.add_argument('--print-cmd',         action='store_true', help='Print dockerw generated command')
    run_parser.add_argument('--print-defaults',    action='store_true', help='Print dockerw args generated by "--defaults" flag')
    run_parser.add_argument('--copy',              action=_CopyAction, nargs=1, metavar='list',
                            help='Bind mount and copy a volume (venv must be enabled)')
    run_parser.add_argument('--prompt-banner',     metavar='string',
                            help='CLI prompt banner to display. Default is docker image name (venv must be enabled)')
    run_parser.add_argument('--auto-attach',       action='store_true', default=None,
                            help='Enable auto attach to named container if already running')
    run_parser.add_argument('--auto-replace',      action='store_true', default=None,
                            help='Enable auto replace of named container if already running')
    run_parser.add_argument('--user-lock',         action='store_true',
                            help='User lock container shell (venv must be enabled). NOTE: This is not a secure lock and can be easily bypassed. It is intended as a lightweight guard for use in trusted environments')

    args = run_parser.parse_args(args)
    if not args.image and args.default_image:
        args.image = [args.default_image]
    if args.image:
        args.image += container_cmds[1:]
        image_repo, image_name, image_tag = _parse_image_name(args.image[0])
        args.image[0] = f'{image_repo}/{image_name}:{image_tag}'
    else:
        image_repo, image_name, image_tag = '', '', ''
        args.image = ['']

    docker_cmd = 'run'
    container_name = args.name if args.name else ''
    current_container_name_image = ''
    is_current_container_name_running = True
    filter_status = f"--filter status={' --filter status='.join(['restarting', 'running', 'removing', 'paused'])}"
    containers = _run_os_cmd(f'docker ps -a {filter_status} --format {{{{.Names}}}},{{{{.Image}}}}').stdout.splitlines()
    containers = [container.split(',')[-1] for container in containers if container.split(',')[0] == container_name]
    if not containers:
        is_current_container_name_running = False
        filter_status = f"--filter status={' --filter status='.join(['created', 'exited', 'dead'])}"
        containers = _run_os_cmd(f'docker ps -a {filter_status} --format {{{{.Names}}}},{{{{.Image}}}}').stdout.splitlines()
        containers = [container.split(',')[-1] for container in containers if container.split(',')[0] == container_name] + ['']
    current_container_name_image = containers[0]
    if not current_container_name_image:
        is_attach_container, is_replace_container = False, False
    else:
        is_attach_container, is_replace_container = args.auto_attach, args.auto_replace
        current_container_name_image = '{}/{}:{}'.format(*_parse_image_name(current_container_name_image))
        if current_container_name_image != args.image[0] or not is_current_container_name_running:
            if is_attach_container and not is_replace_container:
                if not is_current_container_name_running:
                    print(f'Warning: Cannot auto-attach to container name "{container_name}" when in non-running state', file=sys.stderr)
                else:
                    print(f'Warning: Cannot auto-attach to container name "{container_name}" using a different image', file=sys.stderr)
                    print(f'         running image:   {current_container_name_image}', file=sys.stderr)
                    print(f"         requested image: {args.image[0]}", file=sys.stderr)
                print(f'Warning: Disabled auto-attach', file=sys.stderr)
            is_attach_container = False
        if is_attach_container == None and is_replace_container == None:
            is_attach_container = _yes_no_prompt('Attach to already running instance?', args.interactive, args.interactive)
        if is_attach_container:
            docker_cmd = 'exec'
            args.image[0] = container_name
            if len(args.image) == 1:
                args.image.append(args.default_shell if args.default_shell else 'sh')
            if len(args.image) == 2 and args.image[1] in DOCKERW_VENV_SHELLS:
                args.detach = False
            elif args.detach:
                args.detach = _yes_no_prompt('Still run command in background?', True, args.interactive)
            if args.venv:
                container_user = \
                    re.search('HOME=/home/(\w+)', _run_os_cmd(f'docker exec {args.image[0]} cat {DOCKERW_VENV_RC_PATH}').stdout)
                if container_user and container_user.group(1) == DOCKERW_UNAME:
                    args.user = DOCKERW_UNAME
            is_replace_container = False
        elif is_replace_container == None:
            is_replace_container = _yes_no_prompt('Replace already running instance?', False, args.interactive)

    if args.print_cmd:
        if is_replace_container:
            print(f'docker rm -f {container_name}')
        elif docker_cmd == 'exec':
            exec_args, ignore_other_args = exec_parser.parse_known_args(_parsed_args_to_list(args))
            exec_args.image = args.image
            args = exec_args
        print(_shlex_join(['docker', docker_cmd] + _parsed_args_to_list(args)))
        exit(0)
    elif args.print_defaults:
        default_args = run_parser.parse_args(['--defaults'])
        print('', f'DOCKERW DEFAULT ARGS (--defaults):', '', _shlex_join(_parsed_args_to_list(default_args)), '', sep='\n')
        if load_args.load:
            print(f'PROJECT DEFAULT ARGS ({load_args.load}):', '', _shlex_join(_dockerw_load(load_args.load)), '', sep='\n')
        exit(0)
    if docker_cmd == 'run' and args.venv:
        oldmask = os.umask(0o000)
        pathlib.Path('/tmp/dockerw').mkdir(parents=True, exist_ok=True)
        os.umask(oldmask)
        venv_file = tempfile.NamedTemporaryFile('w', dir='/tmp/dockerw', delete=False)
        args.env.append(f'DOCKERW_VENV_IMG={args.image[0]}')
        args.env.append(f'DOCKERW_VENV_IMG_REPO={image_repo}')
        args.env.append(f'DOCKERW_VENV_IMG_NAME={image_name}')
        args.env.append(f'DOCKERW_VENV_IMG_TAG={image_tag}')
        prompt_banner = args.prompt_banner if args.prompt_banner else args.image[0]
        blue, green, normal, invert = '\033[34m', '\033[32m', '\033[0m', '\033[7m'
        cpu_name = _run_os_cmd("grep -m 1 'model name[[:space:]]*:' /proc/cpuinfo | cut -d ' ' -f 3- | sed 's/(R)/®/g; s/(TM)/™/g;'").stdout
        cpu_vcount = _run_os_cmd("grep -o 'processor[[:space:]]*:' /proc/cpuinfo | wc -l").stdout
        cpu = f'{cpu_name.strip()} ({cpu_vcount.strip()} vCPU)'
        fl = 52 # format length for middle column
        cfl = fl + len(bytearray(cpu, sys.stdout.encoding)) - len(cpu) # cpu format length for middle column
        print(f'##################################################################',
              f'# This file is generated by dockerw. Please do not modify by hand.\n',
              f'# shellcheck disable=SC2148,SC2016',
              f'if [ -z "$SHELL" ]; then SHELL="$(command -v sh)"; export SHELL; fi',
              f'if [ "$(basename "$SHELL")" = "sh" ]; then',
              f'  if bash --help > /dev/null 2>&1; then SHELL="$(command -v bash)"; export SHELL; fi',
              f'fi',
              f'mkdir -p {DOCKERW_VENV_HOME_PATH}',
              f'mv {venv_file.name} {DOCKERW_VENV_HOME_PATH}/.dockerw_entrypoint.sh',
              f'_existing_user=$(awk -v uid={DOCKERW_UID} -F":" \'{{ if($3==uid){{print $1}} }}\' /etc/passwd 2>/dev/null)',
              f'if [ -n "$_existing_user" ]; then',
              f'  if userdel --help > /dev/null 2>&1; then',
              f'    userdel "$_existing_user" > /dev/null 2>&1',
              f'  else',
              f'    deluser "$_existing_user" > /dev/null 2>&1',
              f'  fi',
              f'  mv /home/"$_existing_user" /home/_venv_orig_user_"$_existing_user"',
              f'fi',
              f'if groupadd --help > /dev/null 2>&1; then',
              f'  groupadd -g {DOCKERW_GID} {DOCKERW_UNAME} > /dev/null 2>&1',
              f'  useradd -s "$SHELL" -u {DOCKERW_UID} -m {DOCKERW_UNAME} -g {DOCKERW_GID} > /dev/null 2>&1',
              f'  {"" if args.dood else "# "}groupadd -g {os.stat("/var/run/docker.sock").st_gid} dood > /dev/null 2>&1',
              f'  {"" if args.dood else "# "}usermod -aG dood {DOCKERW_UNAME} > /dev/null 2>&1',
              f'  usermod -aG wheel {DOCKERW_UNAME} > /dev/null 2>&1',
              f'else',
              f'  addgroup -g {DOCKERW_GID} {DOCKERW_UNAME} > /dev/null 2>&1',
              f'  adduser -s "$SHELL" -u {DOCKERW_UID} -D {DOCKERW_UNAME} -G {DOCKERW_UNAME} > /dev/null 2>&1',
              f'  {"" if args.dood else "# "}addgroup -g {os.stat("/var/run/docker.sock").st_gid} dood > /dev/null 2>&1',
              f'  {"" if args.dood else "# "}addgroup {DOCKERW_UNAME} dood > /dev/null 2>&1',
              f'  addgroup {DOCKERW_UNAME} wheel > /dev/null 2>&1',
              f'fi',
              f'mkdir -p /home/{DOCKERW_UNAME}',
              f'cp -a /home/{DOCKERW_UNAME} /.dockerw/home',
              f'rm -rf /home/{DOCKERW_UNAME}',
              f'mv {DOCKERW_VENV_HOME_PATH} /home',
              f'rmdir {DOCKERW_VENV_HOME_PATH.parent} > /dev/null 2>&1',
              f'rmdir {DOCKERW_VENV_HOME_PATH.parent.parent} > /dev/null 2>&1',
              f'passwd -d {DOCKERW_UNAME} > /dev/null 2>&1',
              f'echo "{DOCKERW_UNAME} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers',
              f'ln -s "$PWD" /home/{DOCKERW_UNAME}/workdir > /dev/null 2>&1',
              f'chown -h {DOCKERW_UID}:{DOCKERW_GID} /home/{DOCKERW_UNAME}/workdir > /dev/null 2>&1',
              f'mkdir -p {DOCKERW_VENV_PATH}',
              f'# shellcheck disable=SC2129',
              f'echo \'##################################################################\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'# This file is generated by dockerw. Please do not modify by hand.\n\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'# shellcheck disable=SC2148\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo unset PROMPT_COMMAND >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'HOSTNAME="${{HOSTNAME:-{platform.node()}}}"\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'export HOSTNAME\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo _g=\"{green}\" >> {DOCKERW_VENV_RC_PATH}',
              f'echo _b=\"{blue}\" >> {DOCKERW_VENV_RC_PATH}',
              f'echo _i=\"{invert}\" >> {DOCKERW_VENV_RC_PATH}',
              f'echo _n=\"{normal}\" >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'_curr_shell=\"$(command -v "$0")\"\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'if readlink -f \"$_curr_shell\" > /dev/null 2>&1; then _curr_shell=\"$(readlink -f \"$_curr_shell\")\"; fi\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'case "$(basename "$_curr_shell\")" in\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'  dash|ksh)\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'    _ps1_user="$(whoami)"\' >> {DOCKERW_VENV_RC_PATH}',
              f'# shellcheck disable=SC2028',
              f'echo \'    PS1="$_i📦{prompt_banner}$_n\\n$_g$_ps1_user@$HOSTNAME$_n $_b\\$PWD$_n\\n\\\$ " ;;\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'  *)\' >> {DOCKERW_VENV_RC_PATH}',
              f'# shellcheck disable=SC2028',
              f'echo \'    PS1="$_i📦{prompt_banner}$_n\\n$_g\\u@\\h$_n $_b\\w$_n\\n\\\$ " ;;\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'esac\' >> {DOCKERW_VENV_RC_PATH}',
              f'# shellcheck disable=SC2129',
              f'echo \'if [ "$(id -u)" != "{DOCKERW_UID}" ] && [ "$SUDO_UID" != "{DOCKERW_UID}" ]; then\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'  _is_user_lock={str(args.user_lock).lower()}\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'  if $_is_user_lock; then\' >> {DOCKERW_VENV_RC_PATH}',
              fr"""cat << 'EOF' >> {DOCKERW_VENV_RC_PATH}
cat << 'EOT'
              ,
     __  _.-"` `'-.
    /||\'._ __{{}}_(
    ||||  |'--.__\
    |  L.(   ^_\^
    \ .-' |   _ |
    | |   )\___/
    |  \-'`:._]
    \__/;      '-.
EOT
EOF""",
              f"echo '    echo \"Container shell is user locked.\"' >> {DOCKERW_VENV_RC_PATH}",
              f"echo '    exit' >> {DOCKERW_VENV_RC_PATH}",
              f"echo '  fi' >> {DOCKERW_VENV_RC_PATH}",
              f'echo "  cd $PWD || exit" >> {DOCKERW_VENV_RC_PATH}',
              f'echo "  HOME=/home/{DOCKERW_UNAME}" >> {DOCKERW_VENV_RC_PATH}',
              f'echo "  export HOME" >> {DOCKERW_VENV_RC_PATH}',
              f"echo '  if chroot --userspec={DOCKERW_UID}:{DOCKERW_GID} --skip-chdir / id > /dev/null 2>&1; then' >> {DOCKERW_VENV_RC_PATH}",
              f"echo '    exec chroot --userspec={DOCKERW_UID}:{DOCKERW_GID} --skip-chdir / \"$0\"' >> {DOCKERW_VENV_RC_PATH}",
              f"echo '  elif su -p {DOCKERW_UNAME} --session-command \"id\" > /dev/null 2>&1; then' >> {DOCKERW_VENV_RC_PATH}",
              f"echo '    exec su -p {DOCKERW_UNAME} --session-command \"$0\"' >> {DOCKERW_VENV_RC_PATH}",
              f"echo '  else' >> {DOCKERW_VENV_RC_PATH}",
              f"echo '    exec su -p {DOCKERW_UNAME} \"$0\"' >> {DOCKERW_VENV_RC_PATH}",
              f"echo '  fi' >> {DOCKERW_VENV_RC_PATH}",
              f"echo 'fi' >> {DOCKERW_VENV_RC_PATH}",
              f'# shellcheck disable=SC1083',
              fr"echo '_uptime'=\"\$\(awk \'{{ printf \"%d\", \$1 }}\' /proc/uptime\)\" >> {DOCKERW_VENV_RC_PATH}",
              fr"echo '_minutes'=\$\(\(_uptime / 60\)\) >> {DOCKERW_VENV_RC_PATH}",
              fr"echo '_hours'=\$\(\(_minutes / 60\)\) >> {DOCKERW_VENV_RC_PATH}",
              fr"echo '_minutes'=\$\(\(_minutes % 60\)\) >> {DOCKERW_VENV_RC_PATH}",
              fr"echo '_days'=\$\(\(_hours / 24\)\) >> {DOCKERW_VENV_RC_PATH}",
              fr"echo '_hours'=\$\(\(_hours % 24\)\) >> {DOCKERW_VENV_RC_PATH}",
              fr"echo '_weeks'=\$\(\(_days / 7\)\) >> {DOCKERW_VENV_RC_PATH}",
              fr"echo '_days'=\$\(\(_days % 7\)\) >> {DOCKERW_VENV_RC_PATH}",
              fr"echo '_uptime'=\"up \$_weeks weeks, \$_days days, \$_hours hours, \$_minutes minutes\" >> {DOCKERW_VENV_RC_PATH}",
              f'# shellcheck disable=SC1083',
              fr"echo '_mem_total'=\$\(grep \'MemTotal:\' /proc/meminfo \| awk \'{{ print \$2 }}\'\) >> {DOCKERW_VENV_RC_PATH}",
              f'# shellcheck disable=SC1083',
              fr"echo '_mem_avail'=\$\(grep \'MemAvailable:\' /proc/meminfo \| awk \'{{ print \$2 }}\'\) >> {DOCKERW_VENV_RC_PATH}",
              fr"echo '_mem_used'=\$\(\(_mem_total - _mem_avail\)\) >> {DOCKERW_VENV_RC_PATH}",
              f'# shellcheck disable=SC1083',
              fr"echo '_mem_used'=\$\(awk -v mem_kb=\"\$_mem_used\" \'BEGIN{{ printf \"%.1fG\", mem_kb / 1000000}}\'\) >> {DOCKERW_VENV_RC_PATH}",
              f'# shellcheck disable=SC1083',
              fr"echo '_mem_total'=\$\(awk -v mem_kb=\"\$_mem_total\" \'BEGIN{{ printf \"%.1fG\", mem_kb / 1000000}}\'\) >> {DOCKERW_VENV_RC_PATH}",
              f'# shellcheck disable=SC1083',
              fr"echo '_mem_avail'=\$\(awk -v mem_kb=\"\$_mem_avail\" \'BEGIN{{ printf \"%.1fG\", mem_kb / 1000000}}\'\) >> {DOCKERW_VENV_RC_PATH}",
              fr"echo '_mem'=\"\$_mem_used used, \$_mem_total total \(\$_mem_avail avail\)\" >> {DOCKERW_VENV_RC_PATH}",
              f'# shellcheck disable=SC1083',
              fr"echo '_disk_free'=\$\(df -h / \| awk \'FNR == 2 {{ print \$4 }}\'\) >> {DOCKERW_VENV_RC_PATH}",
              f'# shellcheck disable=SC1083',
              fr"echo '_disk_used'=\$\(df -h / \| awk \'FNR == 2 {{ print \$3 }}\'\) >> {DOCKERW_VENV_RC_PATH}",
              fr"""cat << 'EOF' >> {DOCKERW_VENV_RC_PATH}
cat << 'EOT'
                 ,,))))))));,
              __)))))))))))))),
   \|/       -\(((((''''((((((((.     .----------------------------.
   -*-==//////((''  .     `)))))),   /  DOCKERW VENV _____________)
   /|\      ))| o    ;-.    '(((((  /            _______________)   ,(,
            ( `|    /  )    ;))))' /         _______________)    ,_))^;(~
               |   |   |   ,))((((_/      ________) __          %,;(;(>';'~
               o_);   ;    )))(((`    \ \   ~---~  `:: \       %%~~)(v;(`('~
                     ;    ''''````         `:       `:: |\,__,%%    );`'; ~ %
                    |   _                )     /      `:|`----'     `-'
              ______/\/~    |                 /        /
            /~;;.____/;;'  /          ___--,-(   `;;;/
           / //  _;______;'------~~~~~    /;;/\    /
          //  | |                        / ;   \;;,\
         (<_  | ;                      /',/-----'  _>
          \_| ||_                     //~;~~~~~~~~~
EOT
EOF""",
              f'# shellcheck disable=SC2129',
              f'echo \'echo \"$_g─────────────╴$_n\\`\-| $_g─────────────────$_n \\(,~~ $_g─────────────────────────────────────\"\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'echo \"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$_n \~| $_g━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\"\' >> {DOCKERW_VENV_RC_PATH}',
              f'# shellcheck disable=SC2028',
              f'echo \'printf \"┃$_n    CPU $_g┃$_n %-{cfl}.{cfl}s $_g┃$_n  DISK SPACE  $_g┃\\\\n\" \"{cpu}\"\' >> {DOCKERW_VENV_RC_PATH}',
              f'# shellcheck disable=SC2028',
              f'echo \'printf \"┃$_n    RAM $_g┃$_n %-{fl}.{fl}s $_g┃$_n free  %6s $_g┃\\\\n\" \"$_mem\" \"$_disk_free\"\' >> {DOCKERW_VENV_RC_PATH}',
              f'# shellcheck disable=SC2028',
              f'echo \'printf \"┃$_n UPTIME $_g┃$_n %-{fl}.{fl}s $_g┃$_n used  %6s $_g┃$_n\\\\n\" \"$_uptime\" \"$_disk_used\"\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo . {DOCKERW_VENV_RC_PATH} >> /home/{DOCKERW_UNAME}/.bashrc',
              f'echo . {DOCKERW_VENV_RC_PATH} >> /root/.bashrc',
              f'HOME=/home/{DOCKERW_UNAME}',
              f'export HOME',
              f'ENV={DOCKERW_VENV_RC_PATH}',
              f'export ENV',
              f'run_user_cmd() {{',
              f'  _is_exec=$1; shift',
              f'  _userspec=$1; shift',
              f'  _username=$1; shift',
              f'  if $_is_exec; then _exec="exec"; fi',
              f'  if chroot --userspec="$_userspec" --skip-chdir / id > /dev/null 2>&1; then',
              f'    $_exec chroot --userspec="$_userspec" --skip-chdir / "$@"',
              f'  elif su -p "$_username" --session-command "id" > /dev/null 2>&1; then',
              f'    $_exec su -p "$_username" --session-command "$*"',
              f'  else',
              f'    $_exec su -p "$_username" -c "$*"',
              f'  fi',
              f'}}', sep='\n', file=venv_file)
        for dest_path in [ volume.split(':')[1] for volume in args.volume ]:
            dest_path = pathlib.Path(dest_path)
            if str(dest_path).startswith(str(DOCKERW_VENV_COPY_PATH)):
                cp_cmd = f'cp -afT {dest_path} /{dest_path.relative_to(DOCKERW_VENV_COPY_PATH)}'
                print(f'mkdir -p /{dest_path.relative_to(DOCKERW_VENV_COPY_PATH).parent}',
                      f'if [ -d "{dest_path}" ]; then',
                      f'  mkdir -p /{dest_path.relative_to(DOCKERW_VENV_COPY_PATH)}',
                      f'  # shellcheck disable=SC2046',
                      f'  chown $(stat -c \"%u:%g\" {dest_path}) /{dest_path.relative_to(DOCKERW_VENV_COPY_PATH)}',
                      f'fi',
                      f'run_user_cmd false {DOCKERW_UID}:{DOCKERW_GID} {DOCKERW_UNAME} {cp_cmd}', sep='\n', file=venv_file)
        if len(args.image) == 1:
            cmd = args.default_shell if args.default_shell else '"$SHELL"'
        else:
            cmd = ' '.join(args.image[1:])
        print(f'run_user_cmd true {DOCKERW_UID}:{DOCKERW_GID} {DOCKERW_UNAME} {cmd}', file=venv_file)
        venv_file.close()
        args.volume.append('/tmp/dockerw:/tmp/dockerw')
        args.image = [args.image[0]]
        args.image += ['-l', venv_file.name] if args.login_shell else [venv_file.name]
    if is_replace_container:
        _run_os_cmd(f'docker rm -f {container_name}')
    elif docker_cmd == 'exec':
        exec_args, ignore_other_args = exec_parser.parse_known_args(_parsed_args_to_list(args))
        exec_args.image = args.image
        args = exec_args
    os.execvpe('docker', ['docker', docker_cmd] + _parsed_args_to_list(args), env=os.environ.copy())

def find_nearest_defaults_file_path() -> pathlib.Path:
    for path in [pathlib.Path.cwd(), *pathlib.Path.cwd().parents]:
        dockerw_defaults_file_path = path / pathlib.Path('.dockerw/defaults.py')
        if dockerw_defaults_file_path.exists() == True:
            return dockerw_defaults_file_path
    return None

def parse_defaults_file(defaults_file_path: pathlib.Path) -> dict:
    if defaults_file_path and defaults_file_path.exists():
        cfg = { '__file__': str(defaults_file_path),
                'dockerw': SourceFileLoader('dockerw', str(pathlib.PosixPath(__file__).resolve())).load_module() }
        exec(open(cfg['__file__']).read(), cfg)
        return cfg
    return {}

def get_volume_arg(src: str, dest_path: str='', is_copy: bool=False) -> str:
    src_path = pathlib.PosixPath(re.sub(r'^~', pwd.getpwuid(DOCKERW_UID).pw_dir, src)).resolve()
    if src_path.exists():
        action = 'copy' if is_copy else 'volume'
        dest_path = src if not dest_path else dest_path
        return f'--{action} {src_path}:{dest_path}'
    return ''

def main() -> None:
    try:
        if len(sys.argv) > 1 and sys.argv[1] == 'run':
            dockerw_run(sys.argv[2:])
        elif sys.argv[1] == '--version':
            _InfoAction('--version', None).__call__(None, option_string='--version')
        os.execvpe('docker', sys.argv, env=os.environ.copy())
    except KeyboardInterrupt:
        exit('\nError: KeyboardInterrupt received')

if __name__ == '__main__':
    main()
