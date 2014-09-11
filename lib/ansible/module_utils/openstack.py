# This code is part of Ansible, but is an independent component.
# This particular file snippet, and this file snippet only, is BSD licensed.
# Modules you write using this snippet, which is embedded dynamically by Ansible
# still belong to the author of the module, and may assign their own license
# to the complete work.
#
# Copyright (c) 2014 Hewlett-Packard Development Company, L.P.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
# USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import ConfigParser
import logging
import os

HAVE_NOVACLIENT = True
HAVE_GLANCECLIENT = True
HAVE_KEYSTONECLIENT = True
HAVE_CINDERCLIENT = True

try:
    from novaclient.v1_1 import client as nova_client
    from novaclient import exceptions as nova_exceptions
except:
    HAVE_NOVACLIENT = False

try:
    from keystoneclient.v2_0 import client as keystone_client
except:
    HAVE_KEYSTONECLIENT = False

try:
    import glanceclient
except ImportError:
    HAVE_GLANCECLIENT = False

try:
    from cinderclient.v1 import client as cinder_client
    from cinderclient import exceptions as cinder_exceptions
except:
    HAVE_CINDERCLIENT = False



def openstack_argument_spec():
    # Consume standard OpenStack environment variables.
    # This is mainly only useful for ad-hoc command line operation as
    # in playbooks one would assume variables would be used appropriately
    OS_AUTH_URL=os.environ.get('OS_AUTH_URL', 'http://127.0.0.1:35357/v2.0/')
    OS_PASSWORD=os.environ.get('OS_PASSWORD', None)
    OS_REGION_NAME=os.environ.get('OS_REGION_NAME', None)
    OS_USERNAME=os.environ.get('OS_USERNAME', 'admin')
    OS_TENANT_NAME=os.environ.get('OS_TENANT_NAME', OS_USERNAME)

    spec = dict(
        login_username                  = dict(default=OS_USERNAME),
        auth_url                        = dict(default=OS_AUTH_URL),
        region_name                     = dict(default=OS_REGION_NAME),
        availability_zone               = dict(default=None),
        endpoint_type                   = dict(default='publicURL', choices=['publicURL', 'internalURL']),
    )
    if OS_PASSWORD:
        spec['login_password'] = dict(default=OS_PASSWORD)
    else:
        spec['login_password'] = dict(required=True)
    if OS_TENANT_NAME:
        spec['login_tenant_name'] = dict(default=OS_TENANT_NAME)
    else:
        spec['login_tenant_name'] = dict(required=True)
    return spec

def openstack_find_nova_addresses(addresses, ext_tag, key_name=None):

    ret = []
    for (k, v) in addresses.iteritems():
        if key_name and k == key_name:
            ret.extend([addrs['addr'] for addrs in v])
        else:
            for interface_spec in v:
                if 'OS-EXT-IPS:type' in interface_spec and interface_spec['OS-EXT-IPS:type'] == ext_tag:
                    ret.append(interface_spec['addr'])
    return ret


def openstack_cloud_from_module(module, name='openstack'):

    return OpenStackCloud(
        name=name,
        username=module.params['login_username'],
        password=module.params['login_password'],
        project_id=module.params['login_tenant_name'],
        auth_url=module.params['auth_url'],
        region_name=module.params['region_name'],
        endpoint_type=module.params['endpoint_type'],
        token=module.params.get('token', None))


def openstack_clouds():
    return OpenStackConfig().get_all_clouds()


def openstack_cloud(cloud='openstack'):
    return OpenStackConfig().get_one_cloud(cloud)


class OpenStackCloudException(Exception):
    pass


class OpenStackConfig(object):

    _config_files = [
        os.getcwd() + "/openstack.ini",
        os.getcwd() + "/nova.ini",
        os.path.expanduser("~/openstack.ini"),
        os.path.expanduser("~/nova.ini"),
        "/etc/openstack/openstack.ini"
        "/etc/openstack/nova.ini"
    ]

    def __init__(self, config_files=None, private=False):
        if config_files:
            self._config_files = config_files

        if private:
            private_default = 'true'
        else:
            private_default = 'false'

        OS_USERNAME = os.environ.get('OS_USERNAME', 'admin')
        OS_DEFAULTS = {
            'username': OS_USERNAME,
            'password': os.environ.get('OS_PASSWORD', ''),
            'project_id': os.environ.get('OS_TENANT_NAME', os.environ.get('OS_PROJECT_ID', OS_USERNAME)),
            'auth_url': os.environ.get('OS_AUTH_URL', 'https://127.0.0.1:35357/v2.0/'),
            'region_name': os.environ.get('OS_REGION_NAME', ''),
            'insecure': 'false',
            'private': private_default,
            # historical
            'service_type': 'compute',
            'cache_max_age': '300',
            'cache_path': '~/.cache/openstack',
        }

        # use a config file if it exists where expected
        self.config = self._load_config_file(OS_DEFAULTS)

        self.cloud_sections = [ section for section in self.config.sections() if section != 'cache' ]
        if not self.cloud_sections:
            # Add a default section so that our cloud defaults always work
            self.config.add_section('openstack')
            self.cloud_sections = ['openstack']

    def _load_config_file(self, defaults):
        p = ConfigParser.SafeConfigParser(defaults)

        for path in self._config_files:
            if os.path.exists(path):
                p.read(path)
                return p
        return p

    def _get_region(self, cloud):
        return self.config.get(cloud, 'region_name')

    def get_all_clouds(self):

        clouds = []

        for cloud in self.cloud_sections:
            if cloud == 'cache':
                continue

            for region in self._get_region(cloud).split(','):
                clouds.append(self.get_one_cloud(cloud, region))
        return clouds


    def get_one_cloud(self, name='openstack', region=None):

        if not region:
            region = self._get_region(name)

        client_params = dict(name=name)
        client_params['username'] = self.config.get(name, 'username')
        client_params['password'] = self.config.get(name, 'password')
        client_params['project_id'] = self.config.get(name, 'project_id')
        client_params['auth_url'] = self.config.get(name, 'auth_url')
        client_params['region_name'] = region
        client_params['nova_service_type'] = self.config.get(name, 'service_type')
        client_params['insecure'] = self.config.getboolean(name, 'insecure')
        client_params['private'] = self.config.getboolean(name, 'private')
        # Provide backwards compat for older nova.ini files
        if client_params['password'] == '':
            client_params['password'] = self.config.get(name, 'api_key')

        if (client_params['username'] == "" and client_params['password'] == ""):
            sys.exit(
                'Unable to find auth information for cloud %s'
                ' in config files %s or environment variables'
                % (name, ','.join(self._config_files)))

        return OpenStackCloud(**client_params)


class OpenStackCloud(object):

    def __init__(self, name, username, password, project_id, auth_url,
                 region_name, nova_service_type='compute',
                 private=False, insecure=False,
                 endpoint_type='publicURL', token=None, image_cache=None,
                 flavor_cache=None, volume_cache=None):

        self.name = name
        self.username = username
        self.password = password
        self.project_id = project_id
        self.auth_url = auth_url
        self.region_name = region_name
        self.nova_service_type = nova_service_type
        self.insecure = insecure
        self.private = private
        self.endpoint_type = endpoint_type
        self.token = token
        self._image_cache = image_cache
        self._flavor_cache = flavor_cache
        self._volume_cache = volume_cache

        self._nova_client = None
        self._glance_client = None
        self._keystone_client = None
        self._cinder_client = None

    def get_name(self):
        return self.name

    def get_region(self):
        return self.region_name

    def get_flavor_name(self, flavor_id):
        if not self._flavor_cache:
            self._flavor_cache = dict([(flavor.id, flavor.name) for flavor in self.nova_client.flavors.list()])
        return self._flavor_cache.get(flavor_id, None)

    @property
    def nova_client(self):
        if not HAVE_NOVACLIENT:
            raise OpenStackCloudException(
                "novaclient is required. Install python-novaclient and try again")

        if self._nova_client is None:
            kwargs = dict(
                region_name=self.region_name,
                service_type=self.nova_service_type,
                insecure=self.insecure,
            )
            # Try to use keystone directly first, for potential token reuse
            try:
                kwargs['auth_token'] = self.keystone_client.auth_token
                kwargs['bypass_url'] = self.get_endpoint(self.nova_service_type)
            except OpenStackCloudException:
                pass

            # Make the connection
            self._nova_client = nova_client.Client(
                self.username,
                self.password,
                self.project_id,
                self.auth_url,
                **kwargs
            )

            try:
                self._nova_client.authenticate()
            except nova_exceptions.Unauthorized, e:
                raise OpenStackCloudException(
                    "Invalid OpenStack Nova credentials.: %s" % e.message)
            except nova_exceptions.AuthorizationFailure, e:
                raise OpenStackCloudException(
                    "Unable to authorize user: %s" % e.message)

            if self._nova_client is None:
                raise OpenStackCloudException(
                    "Failed to instantiate nova client. This could mean that your"
                    " credentials are wrong.")

        return self._nova_client

    @property
    def keystone_client(self):
        if not HAVE_KEYSTONECLIENT:
            raise OpenStackCloudException(
                "keystoneclient is required. Install python-keystoneclient and try again")

        if self._keystone_client is None:
            # keystoneclient does crazy things with logging that are
            # none of them interesting
            keystone_logging = logging.getLogger('keystoneclient')
            keystone_logging.addHandler(logging.NullHandler())

            try:
                if self.token:
                    self._keystone_client = keystone_client.Client(
                            endpoint=self.auth_url,
                            token=self.token)
                else:
                    self._keystone_client = keystone_client.Client(
                            username=self.username,
                            password=self.password,
                            tenant_name=self.project_id,
                            region_name=self.region_name,
                            auth_url=self.auth_url)
            except Exception as e:
                raise OpenStackCloudException("Error authenticating to the keystone: %s " % e.message)
        return self._keystone_client

    @property
    def glance_client(self):
        if not HAVE_GLANCECLIENT:
            raise OpenStackCloudException(
                "glanceclient is required. Install python-glanceclient and try again")
        if self._glance_client is None:
            token = self.keystone_client.auth_token
            endpoint = self.get_endpoint(service_type='image')
            try:
                self._glance_client = glanceclient.Client('1', endpoint, token=token)
            except Exception as e:
                raise OpenStackCloudException("Error in connecting to glance: %s" % e.message)
            if self._glance_client is None:
                raise OpenStackCloudException("Error connecting to glance")
        return self._glance_client

    @property
    def cinder_client(self):
        if not HAVE_CINDERCLIENT:
            raise OpenStackCloudException(
                "cinderclient is required. Install python-cinderclient and try again")
        if self._cinder_client is None:
            # Make the connection
            self._cinder_client = cinder_client.Client(
                self.username,
                self.password,
                self.project_id,
                self.auth_url,
                region_name=self.region_name,
            )

            try:
                self._cinder_client.authenticate()
            except cinder_exceptions.Unauthorized, e:
                raise OpenStackCloudException(
                    "Invalid OpenStack Cinder credentials.: %s" % e.message)
            except cinder_exceptions.AuthorizationFailure, e:
                raise OpenStackCloudException(
                    "Unable to authorize user: %s" % e.message)

            if self._cinder_client is None:
                raise OpenStackCloudException(
                    "Failed to instantiate cinder client. This could mean that your"
                    " credentials are wrong.")

        return self._cinder_client

    def get_endpoint(self, service_type):
        try:
            endpoint = self.keystone_client.service_catalog.url_for(
                service_type=service_type, endpoint_type=self.endpoint_type)
        except Exception as e:
            raise OpenStackCloudException(
                "Error getting %s endpoint: %s" % (service_type, e.message))
        return endpoint

    def list_servers(self):
        return self.nova_client.servers.list()

    def list_keypairs(self):
        return self.nova_client.keypairs.list()

    def create_keypair(self, name, public_key):
        return self.nova_client.keypairs.create(name, public_key)

    def delete_keypair(self, name):
        return self.nova_client.keypairs.delete(name)

    def _get_images_from_cloud(self):
        # First, try to actually get images from glance, it's more efficient
        images = dict()
        try:
            # This can fail both because we don't have glanceclient installed
            # and because the cloud may not expose the glance API publically
            for image in self.glance_client.images.list():
                images[image.id] = image.name
        except Exception:
            # We didn't have glance, let's try nova
            # If this doesn't work - we just let the exception propagate
            for image in self.nova_client.images.list():
                images[image.id] = image.name
        return images

    def list_images(self):
        if self._image_cache is None:
            self._image_cache = self._get_images_from_cloud()
        return self._image_cache

    def get_image_name(self, image_id):
        if image_id not in self.list_images():
            self._image_cache[image_id] = None
        return self._image_cache[image_id]

    def get_image_id(self, image_name):
        for (image_id, name) in self.list_images().items():
            if name == image_name:
                return image_id
        return None

    def _get_volumes_from_cloud(self):
        try:
            return self.cinder_client.volumes.list()
        except Exception:
            return []

    def list_volumes(self):
        if self._volume_cache is None:
            self._volume_cache = self._get_volumes_from_cloud()
        return self._volume_cache

    def get_volumes(self, server):
        volumes = []
        for volume in self.list_volumes():
            for attach in volume.attachments:
                if attach['server_id'] == server.id:
                    volumes.append(volume)
        return volumes

    def get_volume_id(self, volume_name):
        for v in self.cinder_client.volumes.list():
            if v.display_name == volume_name:
                return v.id
        return None

    def get_server_id(self, server_name):
        for server in self.nova_client.servers.list():
            if server.name == server_name:
                return server.id
        return None
