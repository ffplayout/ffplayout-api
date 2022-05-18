import configparser
from pathlib import Path
from shutil import copyfile

from apps.api_player.models import GuiSettings, MessengePresets
from apps.api_player.utils import read_yaml, write_yaml
from django.contrib.auth.models import User
from rest_framework import serializers

CONF_PATH = Path('/etc/ffplayout/supervisor/conf.d/')


def create_engine_config(service, yml_config):
    suffix = service.split('-')[1]
    config = configparser.ConfigParser()
    config.read(CONF_PATH.joinpath('engine-001.conf'))
    items = config.items('program:engine-001')

    config.add_section(f'program:engine-{suffix}')

    for (key, value) in items:
        if key == 'command':
            value = ('/opt/ffplayout_engine/venv/bin/python ffplayout.py '
                     f'-c {yml_config}')
        elif key == 'stdout_logfile':
            value = f'/var/log/ffplayout/engine-{suffix}.log'
        config.set(f'program:engine-{suffix}', key, value)

    config.remove_section('program:engine-001')

    with open(CONF_PATH.joinpath(f'{service}.conf'), 'w') as file:
        config.write(file)


class UserSerializer(serializers.ModelSerializer):
    new_password = serializers.CharField(write_only=True, required=False)
    old_password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ['id', 'username', 'old_password',
                  'new_password', 'email']

    def update(self, instance, validated_data):
        instance.password = validated_data.get('password', instance.password)

        if 'new_password' in validated_data and \
                'old_password' in validated_data:
            if not validated_data['new_password']:
                raise serializers.ValidationError(
                    {'new_password': 'not found'})

            if not validated_data['old_password']:
                raise serializers.ValidationError(
                    {'old_password': 'not found'})

            if not instance.check_password(validated_data['old_password']):
                raise serializers.ValidationError(
                    {'old_password': 'wrong password'})

            if validated_data['new_password'] and \
                    instance.check_password(validated_data['old_password']):
                # instance.password = validated_data['new_password']
                instance.set_password(validated_data['new_password'])
                instance.save()
                return instance
        elif 'email' in validated_data:
            instance.email = validated_data['email']
            instance.save()
            return instance
        return instance


class GuiSettingsSerializer(serializers.ModelSerializer):
    def create(self, validated_data):
        settings = GuiSettings.objects.create(**validated_data)
        config = Path(validated_data['playout_config'])

        if not CONF_PATH.joinpath(
                f'{validated_data["engine_service"]}.conf').is_file():
            create_engine_config(validated_data['engine_service'], str(config))
        if not config.is_file():
            suffix = config.stem.split('-')[1]
            yaml_obj = read_yaml(1)
            old_log_path = Path(yaml_obj['logging']['log_path'])
            old_pls_path = Path(yaml_obj['playlist']['path'])

            if old_log_path.name == 'ffplayout':
                log_path = old_log_path.joinpath(f'channel-{suffix}')
            else:
                log_path = old_log_path.parent.joinpath(f'channel-{suffix}')

            if old_pls_path.name == 'playlists':
                play_path = old_pls_path.joinpath(f'channel-{suffix}')
            else:
                play_path = old_pls_path.parent.joinpath(f'channel-{suffix}')

            yaml_obj['logging']['log_path'] = str(log_path)
            yaml_obj['playlist']['path'] = str(play_path)

            if not log_path.is_dir():
                log_path.mkdir(exist_ok=True)

            if not play_path.is_dir():
                play_path.mkdir(exist_ok=True)

            write_yaml(yaml_obj, settings.id)

        return settings

    def update(self, instance, validated_data):
        service = validated_data['engine_service']
        config = Path(validated_data['playout_config'])
        if not CONF_PATH.joinpath(f'{service}.conf').is_file() and \
                CONF_PATH.joinpath('engine-001.conf').is_file():
            create_engine_config(service, str(config))
        if not config.is_file():
            copyfile('/etc/ffplayout/ffplayout-001.yml', str(config))

        instance.channel = validated_data.get('channel', instance.channel)
        instance.player_url = validated_data.get('player_url',
                                                 instance.player_url)
        instance.playout_config = validated_data.get('playout_config',
                                                     instance.playout_config)
        instance.engine_service = validated_data.get('engine_service',
                                                     instance.engine_service)
        instance.net_interface = validated_data.get('net_interface',
                                                    instance.net_interface)
        instance.media_disk = validated_data.get('media_disk',
                                                 instance.media_disk)
        instance.save()
        return instance

    class Meta:
        model = GuiSettings
        fields = '__all__'


class MessengerSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessengePresets
        fields = '__all__'
