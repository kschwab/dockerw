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
__version__ = '0.9.4'
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

DOCKERW_UID = int(os.environ.get("SUDO_UID", os.getuid()))
DOCKERW_GID = int(os.environ.get("SUDO_GID", os.getgid()))
DOCKERW_UNAME = pwd.getpwuid(DOCKERW_UID).pw_name
DOCKERW_VENV_PATH = pathlib.PosixPath(f'/.dockerw')
DOCKERW_VENV_HOME_PATH = DOCKERW_VENV_PATH / f'home/{DOCKERW_UNAME}'
DOCKERW_VENV_COPY_PATH = DOCKERW_VENV_PATH / 'copy'
DOCKERW_VENV_RC_PATH   = DOCKERW_VENV_PATH / 'rc.sh'

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
    image_name = re.match('^((?P<registry>([^/]*[\.:]|localhost)[^/]*)/)?/?(?P<name>[^:]*):?(?P<tag>.*)', image_name).groupdict()
    return (image_name['registry'] if image_name['registry'] else 'docker.io',
            image_name['name'],
            image_name['tag'] if image_name['tag'] else 'latest')

def _parse(parser: argparse.ArgumentParser, args: dict) -> tuple:
    parsed_args, image_cmd = parser.parse_known_args(shlex.split(' '.join(args if args != None else [])))
    parsed_args = vars(parsed_args)
    return { arg: parsed_args[arg] for arg in parsed_args if parsed_args[arg] not in [None, False] }, image_cmd

def _merge_parsed_args(parsed_args: dict, new_args: dict) -> None:
    for new_arg in new_args:
        if new_arg in parsed_args:
            if isinstance(parsed_args[new_arg], list):
                parsed_args[new_arg] = list(set(parsed_args[new_arg] + new_args[new_arg]))
        else:
            parsed_args[new_arg] = new_args[new_arg]

def dockerw_run(args: list) -> None:
    try:
        load_path = re.search(r'--load\s*=?\s*([^\s]+)', ' '.join(args)).group(1)
        os.chdir(pathlib.Path(re.sub(r'^~', pwd.getpwuid(DOCKERW_UID).pw_dir, load_path)).resolve())
    except FileNotFoundError:
        exit(f'Error: Load path does not exist: {load_path}')
    except AttributeError:
        defaults_file_path = find_nearest_defaults_file_path()
        if defaults_file_path:
            args.insert(0, f'--load={defaults_file_path.parent.parent}')
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--help', dest='dockerw_help', action='store_const', const=parser, default=None, help=argparse.SUPPRESS)
    parser.add_argument('--version', dest='dockerw_version', action='store_true', default=None, help=argparse.SUPPRESS)
    parser.add_argument('--load', dest='dockerw_load', metavar='string', help='Load dockerw project')
    parser.add_argument('--image-default', dest='dockerw_image_default', metavar='string', help='Default image if not specified')
    parser.add_argument('--defaults', dest='dockerw_defaults', action='store_true', default=None, help='Enable dockerw default args')
    parser.add_argument('--x11', dest='dockerw_x11', action='store_true', default=None, help='Enable x11 support if possible')
    parser.add_argument('--venv', dest='dockerw_venv', action='store_true', default=None, help='Enable user creation')
    parser.add_argument('--login-shell', dest='dockerw_login_shell', action='store_true', default=None,
                        help='Enable login shell for venv (venv must be enabled)')
    parser.add_argument('--dood', dest='dockerw_dood', action='store_true', default=None, help='Enable Docker-outside-of-Docker')
    parser.add_argument('--print', dest='dockerw_print', action='store_true', default=None, help='Print dockerw generated command')
    parser.add_argument('--print-defaults', dest='dockerw_print_defaults', action='store_true', default=None,
                        help='Print dockerw args generated by "--defaults" flag')
    parser.add_argument('--copy', dest='dockerw_copy', metavar='list', action='append',
                        help='Bind mount and copy a volume (venv must be enabled)')
    parser.add_argument('--prompt-banner', dest='dockerw_prompt_banner', default=None,
                        help='CLI prompt banner to display. Default is docker image name (venv must be enabled)')
    dockerw_long_flags = [ f'--{arg.replace("dockerw_","").replace("_","-")}' for arg in vars(parser.parse_args([])).keys() ]
    for line in _run_os_cmd('docker run --help').stdout.splitlines():
        matched = re.match(r'\s*(?P<short>-\w)?,?\s*(?P<long>--[^\s]+)\s+(?P<val_type>[^\s]+)?\s{4,}(?P<help>\w+.*)', line)
        if matched:
            arg = matched.groupdict()
            if arg["long"] not in dockerw_long_flags:
                flags = (arg['short'], arg['long']) if arg['short'] else (arg['long'],)
                if arg['val_type'] == 'list':
                    parser.add_argument(*flags, action='append', help=argparse.SUPPRESS)
                elif arg['val_type']:
                    parser.add_argument(*flags, nargs=1, help=argparse.SUPPRESS)
                else:
                    parser.add_argument(*flags, action='store_true', default=False, help=argparse.SUPPRESS)
    post_args = {}
    while True:
        parsed_args, parsed_image_cmd = _parse(parser, args)
        parsed_dockerw_args = [ arg_name for arg_name in parsed_args if arg_name.startswith('dockerw') ]
        if parsed_dockerw_args:
            for arg_name in parsed_dockerw_args:
                new_args, new_image_cmd = _parse(parser, eval(f'_{arg_name}_args(parsed_args, parsed_image_cmd, post_args)'))
                assert new_image_cmd == [], 'Parsed dockerw arg created new image command'
                parsed_args.pop(arg_name)
                _merge_parsed_args(parsed_args, new_args)
        args = []
        is_dockerw_flag_found = False
        for arg_name in parsed_args:
            arg_value = parsed_args[arg_name]
            is_dockerw_flag_found = arg_name.startswith('dockerw') or is_dockerw_flag_found
            arg_name = arg_name.replace("dockerw_","")
            if isinstance(arg_value, str):
                args.append(f'--{arg_name.replace("_","-")}={arg_value}')
            elif isinstance(arg_value, list):
                if arg_name == 'volume':
                    _update_volume_paths(arg_value)
                args += [ f'--{arg_name.replace("_","-")}={val}' for val in arg_value ]
            else:
                args.append(f'--{arg_name.replace("_","-")}')
        if is_dockerw_flag_found:
            args += parsed_image_cmd
            continue
        break
    image_repo, image_name, image_tag = _parse_image_name(parsed_image_cmd[0])
    parsed_image_cmd[0] = f'{image_repo}/{image_name}:{image_tag}'
    if 'dockerw_print' in post_args:
        print(' '.join(['docker', 'run'] + args + parsed_image_cmd))
        exit(0)
    elif 'dockerw_print_defaults' in post_args:
        print(' '.join(post_args['dockerw_print_defaults']))
        exit(0)
    if '--env=DOCKERW_VENV=1' in args:
        oldmask = os.umask(0o000)
        pathlib.Path('/tmp/dockerw').mkdir(parents=True, exist_ok=True)
        os.umask(oldmask)
        venv_file = tempfile.NamedTemporaryFile('w', dir='/tmp/dockerw', delete=False)
        args.append(f'--env=DOCKERW_VENV_IMG={parsed_image_cmd[0]}')
        args.append(f'--env=DOCKERW_VENV_IMG_REPO={image_repo}')
        args.append(f'--env=DOCKERW_VENV_IMG_NAME={image_name}')
        args.append(f'--env=DOCKERW_VENV_IMG_TAG={image_tag}')
        prompt_banner = post_args.get('dockerw_prompt_banner', parsed_image_cmd[0])
        blue, green, normal, invert = '\033[34m', '\033[32m', '\033[0m', '\033[7m'
        cpu_name = _run_os_cmd("grep -m 1 'model name[[:space:]]*:' /proc/cpuinfo | cut -d ' ' -f 3- | sed 's/(R)/®/g; s/(TM)/™/g;'").stdout
        cpu_vcount = _run_os_cmd("grep -o 'processor[[:space:]]*:' /proc/cpuinfo | wc -l").stdout
        cpu = f'{cpu_name.strip()} ({cpu_vcount.strip()} vCPU)'
        fl = 52 # format length for middle column
        cfl = fl + len(bytearray(cpu, sys.stdout.encoding)) - len(cpu) # cpu format length for middle column
        print(f'# shellcheck disable=SC2148,SC2016',
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
              f'  {"" if "dockerw_dood" in post_args else "# "}groupadd -g {os.stat("/var/run/docker.sock").st_gid} dood > /dev/null 2>&1',
              f'  {"" if "dockerw_dood" in post_args else "# "}usermod -aG dood {DOCKERW_UNAME} > /dev/null 2>&1',
              f'  usermod -aG wheel {DOCKERW_UNAME} > /dev/null 2>&1',
              f'else',
              f'  addgroup -g {DOCKERW_GID} {DOCKERW_UNAME} > /dev/null 2>&1',
              f'  adduser -s "$SHELL" -u {DOCKERW_UID} -D {DOCKERW_UNAME} -G {DOCKERW_UNAME} > /dev/null 2>&1',
              f'  {"" if "dockerw_dood" in post_args else "# "}addgroup -g {os.stat("/var/run/docker.sock").st_gid} dood > /dev/null 2>&1',
              f'  {"" if "dockerw_dood" in post_args else "# "}addgroup {DOCKERW_UNAME} dood > /dev/null 2>&1',
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
              fr"""_login_banner=$(cat << "EOF"
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
          \_| ||_                     //~;~~~~~~~~~""",
              f'EOF',
              f')',
              f'# shellcheck disable=SC2129',
              f'echo \'cat << "EOF"\' >> {DOCKERW_VENV_RC_PATH}',
              f'echo "$_login_banner" >> {DOCKERW_VENV_RC_PATH}',
              f'echo \'EOF\' >> {DOCKERW_VENV_RC_PATH}',
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
        for dest_path in [ volume.split(':')[1] for volume in args if volume.startswith('--volume=') ]:
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
        if len(parsed_image_cmd) == 1:
            cmd = '"$SHELL"'
        elif '--' == parsed_image_cmd[1]:
            cmd = ' '.join(parsed_image_cmd[2:]) if parsed_image_cmd[2:] != [] else '"$SHELL"'
        else:
            cmd = ' '.join(parsed_image_cmd[1:])
        print(f'run_user_cmd true {DOCKERW_UID}:{DOCKERW_GID} {DOCKERW_UNAME} {cmd}', file=venv_file)
        venv_file.close()
        args.append('--volume=/tmp/dockerw:/tmp/dockerw')
        parsed_image_cmd = [parsed_image_cmd[0]]
        parsed_image_cmd += ['-l', venv_file.name] if post_args.get('dockerw_login_shell') else [venv_file.name]
    os.execvpe('docker', ['docker', 'run'] + args + parsed_image_cmd, env=os.environ.copy())

def _dockerw_help_args(parsed_args: dict, parsed_image_cmd: list, post_args: dict) -> None:
    print(_run_os_cmd('docker run --help').stdout.replace('docker run', 'dockerw'))
    print('Dockerw Options:')
    print(parsed_args['dockerw_help'].format_help().split('options:')[-1].lstrip('\n'))
    exit(0)

def _dockerw_version_args(parsed_args: dict, parsed_image_cmd: list, post_args: dict) -> None:
    print('Dockerw version', __version__)
    print(_run_os_cmd('docker --version').stdout.rstrip())
    exit(0)

def _dockerw_image_default_args(parsed_args: dict, parsed_image_cmd: list, post_args: dict) -> list:
    if parsed_image_cmd == [] or parsed_image_cmd[0] == '--':
        parsed_image_cmd.insert(0, parsed_args['dockerw_image_default'])
    return []

def _dockerw_x11_args(parsed_args: dict, parsed_image_cmd: list, post_args: dict) -> list:
    if os.geteuid() != 0:
        result = _run_os_cmd('xauth info | grep "Authority file" | awk \'{ print $3 }\'')
    else:
        result = _run_os_cmd(f'su {DOCKERW_UNAME} -c "xauth info" | grep "Authority file" | awk \'{{ print $3 }}\'')
    if result.returncode == 0 and pathlib.PosixPath('/tmp/.X11-unix').exists():
        return ['-e=DISPLAY', '-v=/tmp/.X11-unix:/tmp/.X11-unix:ro',
                f'-v={result.stdout.strip()}:~/.Xauthority:ro']
    return []

def _dockerw_dood_args(parsed_args: dict, parsed_image_cmd: list, post_args: dict) -> list:
    post_args['dockerw_dood'] = parsed_args['dockerw_dood']
    return ['-v=/var/run/docker.sock:/var/run/docker.sock']

def _dockerw_venv_args(parsed_args: dict, parsed_image_cmd: list, post_args: dict) -> list:
    return ['--user=root', '--entrypoint=sh', '-e=DOCKERW_VENV=1',
            f'-e=ENV={DOCKERW_VENV_RC_PATH}'] if 'user' not in parsed_args else []

def _dockerw_login_shell_args(parsed_args: dict, parsed_image_cmd: list, post_args: dict) -> list:
    post_args['dockerw_login_shell'] = True
    return []

def _dockerw_copy_args(parsed_args: dict, parsed_image_cmd: list, post_args: dict) -> list:
    return [ f'-v {arg}' for arg in _update_volume_paths(parsed_args['dockerw_copy'], True) ]

def find_nearest_defaults_file_path() -> pathlib.Path:
    for path in [pathlib.Path.cwd(), *pathlib.Path.cwd().parents]:
        dockerw_defaults_file_path = path / pathlib.Path('.dockerw/defaults.py')
        if dockerw_defaults_file_path.exists() == True:
            return dockerw_defaults_file_path
    return None

def parse_defaults_file(defaults_file_path: pathlib.Path) -> dict:
    if defaults_file_path and defaults_file_path.exists():
        cfg = { '__file__': str(defaults_file_path) }
        exec(open(cfg['__file__']).read(), cfg)
        return cfg
    return {}

def _dockerw_load_args(parsed_args: dict, parsed_image_cmd: list, post_args: dict) -> list:
    defaults_file_path = pathlib.Path(parsed_args['dockerw_load'], '.dockerw/defaults.py')
    return parse_defaults_file(defaults_file_path).get('dockerw_defaults', [])

def get_volume_arg(src: str, dest_path: str='', is_copy: bool=False) -> str:
    src_path = pathlib.PosixPath(re.sub(r'^~', pwd.getpwuid(DOCKERW_UID).pw_dir, src)).resolve()
    if src_path.exists():
        action = 'copy' if is_copy else 'volume'
        dest_path = src if not dest_path else dest_path
        return f'--{action} {src_path}:{dest_path}'
    return ''

def _dockerw_defaults_args(parsed_args: dict, parsed_image_cmd: list, post_args: dict) -> list:
    defaults = ['-it --venv --x11 --rm --init --privileged --network host --security-opt seccomp=unconfined',
                f'--dood --detach-keys=ctrl-q,ctrl-q --hostname {platform.node()} -e TERM=xterm-256color']
    for is_copy, paths in [(False, ['~/.bash_history', '~/.vscode', '~/.emacs', '~/.emacs.d', '~/.vimrc']),
                           (True,  ['~/.gitconfig', '~/.ssh'])]:
        for path in paths:
            defaults.append(get_volume_arg(path, is_copy=is_copy))
    if 'workdir' not in parsed_args:
        defaults.append('-w /app')
        defaults.append(f'-v {pathlib.Path.cwd()}:/app')
    return defaults

def _dockerw_print_defaults_args(parsed_args: dict, parsed_image_cmd: list, post_args: dict) -> list:
    post_args['dockerw_print_defaults'] = _dockerw_defaults_args(parsed_args, parsed_image_cmd, post_args)
    return []

def _dockerw_prompt_banner_args(parsed_args: dict, parsed_image_cmd: list, post_args: dict) -> list:
    post_args['dockerw_prompt_banner'] = parsed_args['dockerw_prompt_banner']
    return []

def _dockerw_print_args(parsed_args: dict, parsed_image_cmd: list, post_args: dict) -> list:
    post_args['dockerw_print'] = True
    return []

def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == 'run':
        dockerw_run(sys.argv[2:])
    os.execvpe('docker', sys.argv, env=os.environ.copy())

if __name__ == '__main__':
    main()
