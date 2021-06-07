import os
import shutil
from time import sleep
from urllib.parse import unquote

from apps.api_player.models import GuiSettings, MessengePresets
from apps.api_player.serializers import (GuiSettingsSerializer,
                                         MessengerSerializer, UserSerializer)
from django.conf import settings
from django.contrib.auth.models import User
from django_filters import rest_framework as filters
from rest_framework import viewsets
from rest_framework.parsers import FileUploadParser, JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .utils import (EngineControlSocket, SystemControl, SystemStats,
                    get_media_path, read_json, read_log, read_yaml,
                    send_message, write_json, write_yaml)


class CurrentUserView(APIView):
    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


class UserFilter(filters.FilterSet):

    class Meta:
        model = User
        fields = ['username']


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = UserFilter


class GuiSettingsViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows media to be viewed.
    """
    queryset = GuiSettings.objects.all()
    serializer_class = GuiSettingsSerializer

    def destroy(self, request, *args, **kwargs):
        obj = GuiSettings.objects.get(id=kwargs['pk'])
        service_name = os.path.basename(obj.engine_service).split('.')[0]

        if os.path.isfile(obj.engine_service):
            os.remove(obj.engine_service)
        if os.path.isfile(obj.playout_config):
            os.remove(obj.playout_config)

        if settings.MULTI_CHANNEL:
            engine = EngineControlSocket()
            engine.get_process(service_name)
            engine.stop()
            count = 0

            while engine.status().lower() != 'stopped' and count < 10:
                sleep(0.5)
                count += 1

            if engine.status().lower() == 'stopped':
                engine.remove_process(service_name)

        return super(
            GuiSettingsViewSet, self).destroy(request, *args, **kwargs)


class MessengerFilter(filters.FilterSet):

    class Meta:
        model = MessengePresets
        fields = ['name']


class MessengerViewSet(viewsets.ModelViewSet):
    queryset = MessengePresets.objects.all()
    serializer_class = MessengerSerializer
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = MessengerFilter


class MessageSender(APIView):
    """
    send messages with zmq to the playout engine
    """

    def post(self, request, *args, **kwargs):
        if 'data' in request.data:
            response = send_message(request.data['data'],
                                    request.data['channel'])
            return Response({"success": True, 'status': response})

        return Response({"success": False})


class Config(APIView):
    """
    read and write config from ffplayout engine
    for reading endpoint is:
        http://127.0.0.1:8000/api/player/config/?configPlayout
    """
    parser_classes = [JSONParser]

    def get(self, request, *args, **kwargs):
        if 'configPlayout' in request.GET.dict() and \
                'channel' in request.GET.dict():
            channel = request.GET.dict()['channel']
            yaml_input = read_yaml(channel)

            if yaml_input:
                return Response(yaml_input)

            return Response(status=204)

        return Response(status=404)

    def post(self, request, *args, **kwargs):
        if 'data' in request.data and 'channel' in request.data:
            write_yaml(request.data['data'], request.data['channel'])
            return Response(status=200)

        return Response(status=404)


class SystemCtl(APIView):
    """
    controlling the ffplayout_engine over systemd services,
    or over a socket connecting
    """

    def post(self, request, *args, **kwargs):
        if 'run' in request.data:
            system_ctl = SystemControl()
            if settings.MULTI_CHANNEL:
                control = system_ctl.run_service(request.data['run'],
                                                 request.data['channel'])
            else:
                control = system_ctl.run_service(request.data['run'])

            if isinstance(control, int):
                return Response(status=control)

            return Response(control)

        return Response(status=404)


class LogReader(APIView):
    def get(self, request, *args, **kwargs):
        if 'type' in request.GET.dict() and 'date' in request.GET.dict():
            type_ = request.GET.dict()['type']
            date_ = request.GET.dict()['date']
            channel = request.GET.dict()['channel']

            log = read_log(type_, date_, channel)

            if log:
                return Response({'log': log})

            return Response(status=204)

        return Response(status=404)


class Playlist(APIView):
    """
    read and write config from ffplayout engine
    for reading endpoint:
        http://127.0.0.1:8000/api/player/playlist/?date=2020-04-12
    """

    def get(self, request, *args, **kwargs):
        if 'date' in request.GET.dict():
            date = request.GET.dict()['date']
            channel = request.GET.dict()['channel']
            json_input = read_json(date, channel)

            if json_input:
                return Response(json_input)

            return Response({
                "success": False,
                "error": "Playlist from {} not found!".format(date)})

        return Response(status=400)

    def post(self, request, *args, **kwargs):
        if 'data' in request.data:
            if 'channel' in request.data:
                return write_json(request.data['data'],
                                  request.data['channel'])
            if 'delete' in request.data['data']:
                if os.path.isfile(request.data['data']['delete']):
                    os.remove(request.data['data']['delete'])

                return Response(status=200)

        return Response({'detail': 'Unspecified save error'}, status=400)


class Statistics(APIView):
    """
    get system statistics: cpu, ram, etc.
    for reading, endpoint is: http://127.0.0.1:8000/api/player/stats/?stats=all
    """

    def get(self, request, *args, **kwargs):
        stats = SystemStats()
        if 'stats' in request.GET.dict() and request.GET.dict()['stats'] \
                and hasattr(stats, request.GET.dict()['stats']):
            return Response(
                getattr(stats, request.GET.dict()['stats'])())

        return Response(status=404)


class Media(APIView):
    """
    get folder/files tree, for building a file explorer
    for reading, endpoint is: http://127.0.0.1:8000/api/player/media/?path
    """

    def get(self, request, *args, **kwargs):
        if 'extensions' in request.GET.dict():
            extensions = request.GET.dict()['extensions']
            channel = request.GET.dict()['channel']

            if 'path' in request.GET.dict() and request.GET.dict()['path']:
                return Response({'tree': get_media_path(
                    extensions, channel, request.GET.dict()['path']
                )})

            if 'path' in request.GET.dict():
                return Response({'tree': get_media_path(extensions, channel)})

            return Response(status=204)

        return Response(status=404)


class FileUpload(APIView):
    parser_classes = [FileUploadParser]

    def put(self, request, filename, format=None):
        root = read_yaml(
            request.query_params['channel'])['storage']['path']
        file_obj = request.data['file']
        filename = unquote(filename)
        path = unquote(request.query_params['path']).split('/')[1:]

        with open(os.path.join(root, *path, filename), 'wb') as outfile:
            for chunk in file_obj.chunks():
                outfile.write(chunk)
        return Response(status=204)


class FileOperations(APIView):

    def delete(self, request, *args, **kwargs):
        if 'file' in request.GET.dict() and 'path' in request.GET.dict():
            channel = request.GET.dict()['channel']
            root = read_yaml(channel)['storage']['path']
            _file = unquote(request.GET.dict()['file'])
            folder = unquote(request.GET.dict()['path']).lstrip('/')
            _path = os.path.join(*(folder.split(os.path.sep)[1:]))
            full_path = os.path.join(root, _path)

            if not _file or _file == 'null':
                if os.path.isdir(full_path):
                    shutil.rmtree(full_path, ignore_errors=True)
                    return Response(status=200)

                return Response(status=404)

            if os.path.isfile(os.path.join(full_path, _file)):
                os.remove(os.path.join(full_path, _file))
                return Response(status=200)

            return Response(status=404)

        return Response(status=404)

    def post(self, request, *args, **kwargs):
        if 'folder' in request.data and 'path' in request.data:
            channel = request.data['channel']
            root = read_yaml(channel)['storage']['path']
            folder = request.data['folder']
            path_ = request.data['path'].split(os.path.sep)
            path_ = '' if len(path_) == 1 else os.path.join(*path_[1:])
            full_path = os.path.join(root, path_, folder)

            try:
                os.mkdir(full_path)
                return Response(status=200)
            except OSError:
                Response(status=500)

        return Response(status=404)

    def patch(self, request, *args, **kwargs):
        if 'path' in request.data and 'oldname' in request.data \
                and 'newname' in request.data:
            channel = request.data['channel']
            root = read_yaml(channel)['storage']['path']
            old_name = request.data['oldname']
            new_name = request.data['newname']
            path_ = os.path.join(
                *(request.data['path'].split(os.path.sep)[2:]))
            old_file = os.path.join(root, path_, old_name)
            new_file = os.path.join(root, path_, new_name)

            os.rename(old_file, new_file)

            return Response(status=200)

        return Response(status=204)
