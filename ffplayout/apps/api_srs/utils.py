import re

import requests
from django.conf import settings
from rest_framework.response import Response

from ..api_player.utils import SystemControl


def get_publisher():
    """
    get a list of all publishers
    """
    publisher = []
    req = requests.get(
        'http://{}:{}/api/v1/clients/'.format(settings.SRS_IP,
                                              settings.SRS_API_PORT)).json()

    for client in req['clients']:
        if client['publish']:
            publisher.append(client)

    return publisher


def kick_streams():
    """
    check if low priority stream is running and when is true kick them out
    """
    for client in get_publisher():
        stream = client['url'].split('/')[-1]
        suffix = re.findall(r'\d{3}', client['url'])
        engine = f'engine-{suffix}' if suffix else 'engine-001'

        if stream == settings.LOW_PRIORITY_STREAM:
            requests.delete(
                'http://{}:{}/api/v1/clients/{}'.format(settings.SRS_IP,
                                                        settings.SRS_API_PORT,
                                                        client['id']))

            system_ctl = SystemControl()
            system_ctl.run_service('stop', engine)


def start_stream(last):
    """
    when last unpublished stream was the high priority stream,
    start the ffplayout_engine

    LIMITATION: for now only first engine-001 can be started
    """
    if last == settings.HIGH_PRIORITY_STREAM:
        system_ctl = SystemControl()
        system_ctl.run_service('start', 'engine-001')


def check_streams(data):
    """
    check which stream are running and depending on that kick one
    """
    if data['stream'] == settings.HIGH_PRIORITY_STREAM:
        kick_streams()
    elif data['stream'] == settings.LOW_PRIORITY_STREAM:
        for client in get_publisher():
            stream = client['url'].split('/')[-1]

            if stream == settings.HIGH_PRIORITY_STREAM:
                return Response({"code": 403, "data": None})

    return Response({"code": 0, "data": None})


def rtmp_key(req):
    """
    get all keys/values from rtmp address
    """
    param = req['param'].lstrip('?')
    params = param.split('&')
    obj = {}

    if params[0]:
        for param in params:
            key, value = param.split('=')
            obj[key] = value

        if obj.get('key') == settings.RTMP_KEY:
            return True

    return False
