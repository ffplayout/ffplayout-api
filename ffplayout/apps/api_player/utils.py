import json
import os
import re
from datetime import datetime
from platform import uname
from subprocess import PIPE, STDOUT, run
from time import sleep
from xmlrpc.client import ServerProxy

import psutil
import yaml
import zmq
from apps.api_player.models import GuiSettings
from django.conf import settings
from natsort import natsorted
from pymediainfo import MediaInfo
from rest_framework.response import Response


def gui_config(index):
    gui_settings = GuiSettings.objects.filter(id=index).values()

    return gui_settings[0] if gui_settings else {}


def read_yaml(channel):
    config = gui_config(channel)

    if config.get('playout_config') and \
            os.path.isfile(config['playout_config']):
        with open(config['playout_config'], 'r') as config_file:
            return yaml.safe_load(config_file)

    return None


def write_yaml(data, channel):
    config = gui_config(channel)

    if config.get('playout_config'):
        with open(config['playout_config'], 'w') as outfile:
            yaml.dump(data, outfile, default_flow_style=False,
                      sort_keys=False, indent=4)


def read_json(date_, channel):
    config = read_yaml(channel)

    if config:
        playlist_path = config['playlist']['path']
        year, month, _ = date_.split('-')
        input_ = os.path.join(playlist_path, year, month, f'{date_}.json')

        if os.path.isfile(input_):
            with open(input_, 'r') as playlist:
                return json.load(playlist)

    return None


def write_json(data, channel):
    config = read_yaml(channel)

    if config:
        playlist_path = config['playlist']['path']
        year, month, _ = data['date'].split('-')
        playlist = os.path.join(playlist_path, year, month)

        if not os.path.isdir(playlist):
            os.makedirs(playlist, exist_ok=True)

        output = os.path.join(playlist, f'{data["date"]}.json')

        if os.path.isfile(output) and data == read_json(data['date'], channel):
            return Response(
                {'detail': f'Playlist from {data["date"]} already exists'})

        with open(output, "w") as outfile:
            json.dump(data, outfile, indent=4)

        return Response({'detail': f'Playlist from {data["date"]} saved'})

    return Response({'detail': f'Saving playlist from {data["date"]} failed!'})


def read_log(type_, date_, channel):
    config = read_yaml(channel)
    if config and config.get('logging'):
        log_path = config['logging']['log_path']

        if date_ == datetime.now().strftime('%Y-%m-%d'):
            log_file = os.path.join(log_path, '{}.log'.format(type_))
        else:
            log_file = os.path.join(log_path, '{}.log.{}'.format(type_, date_))

        if os.path.isfile(log_file):
            with open(log_file, 'r') as log:
                return log.read().strip()

    return None


def send_message(data, channel):
    config = read_yaml(channel)
    drawtext_cmd = ':'.join(f"{key}='{val}'" for key, val in data.items())
    request = f"{settings.DRAW_TEXT_NODE} reinit {drawtext_cmd}"

    if config:
        if settings.MULTI_CHANNEL:
            address = settings.SOCKET_IP
            port = config['text']['bind_address'].split(':')[1]
        else:
            address, port = config['text']['bind_address'].split(':')

        context = zmq.Context(1)
        client = context.socket(zmq.REQ)
        client.connect('tcp://{}:{}'.format(address, port))

        poll = zmq.Poller()
        poll.register(client, zmq.POLLIN)
        client.send_string(request)
        socks = dict(poll.poll(settings.REQUEST_TIMEOUT))

        if socks.get(client) == zmq.POLLIN:
            reply_msg = client.recv_string()
        else:
            reply_msg = 'No response from server'

        client.setsockopt(zmq.LINGER, 0)
        client.close()
        poll.unregister(client)

        context.term()
        return {'Success': reply_msg}

    return {'Failed': 'No config exists'}


def sizeof_fmt(num, suffix='B'):
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


class EngineControlSystemD:
    """
    class for controlling the systemd service from ffplayout_engine
    """

    def __init__(self):
        self.service = ['ffplayout_engine.service']
        self.cmd = ['sudo', '/bin/systemctl']
        self.proc = None

    def run_cmd(self):
        self.proc = run(self.cmd + self.service, stdout=PIPE, stderr=STDOUT,
                        check=False, encoding="utf-8").stdout

    def start(self):
        self.cmd.append('start')
        self.run_cmd()

    def stop(self):
        self.cmd.append('stop')
        self.run_cmd()

    def reload(self):
        self.cmd.append('reload')
        self.run_cmd()

    def restart(self):
        self.cmd.append('restart')
        self.run_cmd()

    def status(self):
        self.cmd.append('is-active')
        self.run_cmd()

        return self.proc.replace('\n', '')


class EngineControlSocket:
    """
    class for controlling ffplayout_engine over supervisord socket
    """

    def __init__(self):
        self.engine = None
        self.server = ServerProxy(
            f'http://{settings.SOCKET_USER}:{settings.SOCKET_PASS}'
            f'@{settings.SOCKET_IP}:{settings.SOCKET_PORT}/RPC2')
        self.process = None

        try:
            self.proc_list = self.server.supervisor.getAllProcessInfo()
        except Exception:
            self.proc_list = []

    def get_process(self, engine):
        self.engine = engine
        self.process = None

        for proc in self.proc_list:
            if engine == proc.get('name'):
                self.process = proc
                break

    def start(self):
        if not self.process:
            return self.add_process()
        elif self.status() == 'STOPPED':
            return self.server.supervisor.startProcess(self.engine)

    def stop(self):
        if self.process and self.process.get('statename') == 'RUNNING':
            return self.server.supervisor.stopProcessGroup(self.engine)

    def restart(self):
        self.stop()
        sleep(2)
        self.start()

    def reload(self):
        if self.process:
            return self.server.supervisor.signalProcess(self.engine,
                                                        'SIGHUP')

    def add_process(self):
        return self.server.supervisor.addProcessGroup(self.engine)

    def remove_process(self, engine):
        return self.server.supervisor.removeProcessGroup(engine)

    def status(self):
        if self.process:
            info = self.server.supervisor.getProcessInfo(self.engine)
            return info.get('statename')


class SystemControl:
    """
    controlling the ffplayout_engine over systemd services,
    or over a socket connecting
    """

    def run_cmd(self, service, cmd):
        if cmd == 'start':
            service.start()
            return 200
        if cmd == 'stop':
            service.stop()
            return 200
        if cmd == 'reload':
            service.reload()
            return 200
        if cmd == 'restart':
            service.restart()
            return 200
        if cmd == 'status':
            return {"data": service.status()}

        return 400

    def systemd(self, cmd):
        return self.run_cmd(EngineControlSystemD(), cmd)

    def rpc_socket(self, cmd, engine):
        sock = EngineControlSocket()
        sock.get_process(engine)

        return self.run_cmd(sock, cmd)

    def run_service(self, cmd, channel=None):
        if settings.MULTI_CHANNEL:
            config = gui_config(channel)
            return self.rpc_socket(cmd, config.get('engine_service'))

        return self.systemd(cmd)


class SystemStats:
    """
    get system statistics
    """

    def __init__(self):
        self.config = gui_config(1)

    def all(self):
        if self.config:
            return {
                **self.system(), **self.settings(),
                **self.cpu(), **self.ram(), **self.swap(),
                **self.disk(), **self.net(), **self.net_speed()
            }

    def system(self):
        return {
            'system': uname().system,
            'node': uname().node,
            'machine': uname().machine
        }

    def settings(self):
        return {
            'timezone': settings.TIME_ZONE,
            'multi_channel': settings.MULTI_CHANNEL
        }

    def cpu(self):
        load = psutil.getloadavg()
        return {
            'cpu_usage': psutil.cpu_percent(interval=1),
            'cpu_load': [
                '{:.2f}'.format(load[0]),
                '{:.2f}'.format(load[1]),
                '{:.2f}'.format(load[2])
            ]
        }

    def ram(self):
        mem = psutil.virtual_memory()
        return {
            'ram_total': [mem.total, sizeof_fmt(mem.total)],
            'ram_used': [mem.used, sizeof_fmt(mem.used)],
            'ram_free': [mem.free, sizeof_fmt(mem.free)],
            'ram_cached': [mem.cached, sizeof_fmt(mem.cached)]
        }

    def swap(self):
        swap = psutil.swap_memory()
        return {
            'swap_total': [swap.total, sizeof_fmt(swap.total)],
            'swap_used': [swap.used, sizeof_fmt(swap.used)],
            'swap_free': [swap.free, sizeof_fmt(swap.free)]
        }

    def disk(self):
        if 'media_disk' in self.config and self.config['media_disk']:
            root = psutil.disk_usage(self.config['media_disk'])
            return {
                'disk_total': [root.total, sizeof_fmt(root.total)],
                'disk_used': [root.used, sizeof_fmt(root.used)],
                'disk_free': [root.free, sizeof_fmt(root.free)]
            }

    def net(self):
        net = psutil.net_io_counters()
        return {
            'net_send': [net.bytes_sent, sizeof_fmt(net.bytes_sent)],
            'net_recv': [net.bytes_recv, sizeof_fmt(net.bytes_recv)],
            'net_errin': net.errin,
            'net_errout': net.errout
        }

    def net_speed(self):
        net = psutil.net_if_stats()

        if 'net_interface' not in self.config or \
                not self.config['net_interface']:
            return

        if self.config['net_interface'] not in net:
            return {
                'net_speed_send': 'no network interface set!',
                'net_speed_recv': 'no network interface set!'
            }

        net = psutil.net_io_counters(pernic=True)[self.config['net_interface']]

        send_start = net.bytes_sent
        recv_start = net.bytes_recv

        sleep(1)

        net = psutil.net_io_counters(pernic=True)[self.config['net_interface']]

        send_end = net.bytes_sent
        recv_end = net.bytes_recv

        send_sec = send_end - send_start
        recv_sec = recv_end - recv_start

        return {
            'net_speed_send': [send_sec, sizeof_fmt(send_sec)],
            'net_speed_recv': [recv_sec, sizeof_fmt(recv_sec)]
        }


def get_video_duration(clip):
    """
    return video duration from container
    """
    media_info = MediaInfo.parse(clip)
    duration = 0
    for track in media_info.tracks:
        if track.track_type == 'General':
            try:
                duration = float(
                    track.to_data()["duration"]) / 1000
                break
            except KeyError:
                pass

    return duration


def get_path(input_, media_folder):
    """
    return path and prevent breaking out of media root
    """
    media_root_list = media_folder.strip('/').split('/')
    media_root_list.pop()
    media_root = '/' + '/'.join(media_root_list)

    if input_:
        input_ = os.path.abspath(os.path.join(media_root, input_.strip('/')))

    if not input_.startswith(media_folder):
        input_ = os.path.join(media_folder, input_.strip('/'))

    return media_root, input_


def get_media_path(extensions, channel, _dir=''):
    config = read_yaml(channel)

    if config:
        media_folder = config['storage']['path']
        extensions = extensions.split(',')
        playout_extensions = config['storage']['extensions']
        gui_extensions = [x for x in extensions if x not in playout_extensions]
        media_root, search_dir = get_path(_dir, media_folder)

        for root, dirs, files in os.walk(search_dir, topdown=True):
            root = root.rstrip('/')
            media_files = []

            for file in files:
                ext = os.path.splitext(file)[1]
                if ext in playout_extensions:
                    duration = get_video_duration(os.path.join(root, file))
                    media_files.append({'file': file, 'duration': duration})
                elif ext in gui_extensions:
                    media_files.append({'file': file, 'duration': ''})

            dirs = natsorted(dirs)

            if root.strip('/') != media_folder.strip('/') or not dirs:
                dirs.insert(0, '..')

            root = re.sub(r'^{}'.format(media_root), '', root).strip('/')

            return [root, dirs,
                    natsorted(media_files, key=lambda x: x['file'])]
    return []
