# -*- coding: utf-8 -*-

#    Copyright 2013 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from nailgun import consts
from nailgun.db.sqlalchemy.models import Node
from nailgun import objects

from nailgun.orchestrator import provisioning_serializers as ps
from nailgun.settings import settings
from nailgun.test.base import BaseIntegrationTest
from nailgun import utils


class TestGetSerializerForCluster(BaseIntegrationTest):

    def _get_cluster(self, version):
        """Returns cluster object of a given version."""
        release = self.env.create_release(api=False, version=version)
        cluster = self.env.create_cluster(api=False, release_id=release.id)
        return cluster

    def test_env_5_0(self):
        cluster = self._get_cluster('2014.1')
        serializer = ps.get_serializer_for_cluster(cluster)

        self.assertIs(serializer, ps.ProvisioningSerializer)

    def test_env_5_0_1(self):
        cluster = self._get_cluster('2014.1.1-5.0.1')
        serializer = ps.get_serializer_for_cluster(cluster)

        self.assertIs(serializer, ps.ProvisioningSerializer)

    def test_env_5_1(self):
        cluster = self._get_cluster('2014.1.1-5.1')
        serializer = ps.get_serializer_for_cluster(cluster)

        self.assertIs(serializer, ps.ProvisioningSerializer)

    def test_env_5_1_1(self):
        cluster = self._get_cluster('2014.1.1-5.1.1')
        serializer = ps.get_serializer_for_cluster(cluster)

        self.assertIs(serializer, ps.ProvisioningSerializer)

    def test_env_6_0(self):
        cluster = self._get_cluster('2014.2-6.0')
        serializer = ps.get_serializer_for_cluster(cluster)

        self.assertIs(serializer, ps.ProvisioningSerializer)

    def test_env_6_0_1(self):
        cluster = self._get_cluster('2014.2-6.0.1')
        serializer = ps.get_serializer_for_cluster(cluster)

        self.assertIs(serializer, ps.ProvisioningSerializer)

    def test_env_6_1(self):
        cluster = self._get_cluster('2014.2-6.1')
        serializer = ps.get_serializer_for_cluster(cluster)

        self.assertIs(serializer, ps.ProvisioningSerializer61)

    def test_env_7_0(self):
        cluster = self._get_cluster('2015.1-7.0')
        serializer = ps.get_serializer_for_cluster(cluster)

        self.assertIs(serializer, ps.ProvisioningSerializer70)

    def test_env_8_0(self):
        cluster = self._get_cluster('2015.1-8.0')
        serializer = ps.get_serializer_for_cluster(cluster)

        self.assertIs(serializer, ps.ProvisioningSerializer80)

    def test_env_9_0(self):
        cluster = self._get_cluster('2016.1-9.0')
        serializer = ps.get_serializer_for_cluster(cluster)

        self.assertIs(serializer, ps.ProvisioningSerializer90)


class TestProvisioningSerializer(BaseIntegrationTest):

    def setUp(self):
        super(TestProvisioningSerializer, self).setUp()
        self.env.create()
        self.cluster_db = self.env.clusters[0]
        self.env.create_nodes_w_interfaces_count(
            1, 1,
            **{
                'roles': ['controller'],
                'pending_addition': True,
                'cluster_id': self.cluster_db.id
            }
        )
        self.env.create_nodes_w_interfaces_count(
            1, 1,
            **{
                'roles': ['compute'],
                'pending_addition': True,
                'cluster_id': self.cluster_db.id
            }
        )
        self.attributes = self.cluster_db.attributes.editable
        self.serialized_cluster = ps.serialize(
            self.cluster_db, self.cluster_db.nodes)

    def test_cloud_init_templates_serialization(self):
        release = self.env.create_release(api=False, version='2014.2-6.1.1')
        cluster = self.env.create_cluster(api=False, release_id=release.id)
        serializer = ps.get_serializer_for_cluster(cluster)
        self.assertEqual(
            serializer.serialize_cloud_init_templates(release), {
                'boothook': 'boothook_fuel_6.1.1_centos.jinja2',
                'cloud_config': 'cloud_config_fuel_6.1.1_centos.jinja2',
                'meta_data': 'meta_data_fuel_6.1.1_centos.jinja2'
            }
        )

    def test_cluster_info_serialization(self):
        engine = self.serialized_cluster['engine']
        self.assertDictEqual(engine, {
            'url': settings.COBBLER_URL,
            'username': settings.COBBLER_USER,
            'password': settings.COBBLER_PASSWORD,
            'master_ip': settings.MASTER_IP
        })

    def test_node_serialization(self):
        for node in self.serialized_cluster['nodes']:
            node_db = self.db.query(Node).filter_by(
                hostname=node['name']
            ).first()
            # Get interface (in our case we created only one for each node)
            intr_db = node_db.nic_interfaces[0]
            intr_name = intr_db.name
            intr_mac = intr_db.mac
            kernal_params = self.attributes.get('kernel_params', {}) \
                .get('kernel', {}).get('value')
            self.assertEqual(
                node['ks_meta']['cloud_init_templates'], {
                    'boothook': 'boothook_fuel_6.1_centos.jinja2',
                    'cloud_config': 'cloud_config_fuel_6.1_centos.jinja2',
                    'meta_data': 'meta_data_fuel_6.1_centos.jinja2'
                }
            )
            self.assertEqual(node['uid'], node_db.uid)
            self.assertEqual(node['power_address'], node_db.ip)
            self.assertEqual(node['name'], "node-{0}".format(node_db.id))
            self.assertEqual(node['hostname'],
                             objects.Node.get_node_fqdn(node_db))
            self.assertEqual(
                node['power_pass'], settings.PATH_TO_BOOTSTRAP_SSH_KEY)

            self.assertDictEqual(node['kernel_options'], {
                'netcfg/choose_interface':
                objects.Node.get_admin_physical_iface(node_db).mac,
                'udevrules': '{0}_{1}'.format(intr_mac, intr_name)
            })

            self.assertEqual(
                node['ks_meta']['pm_data']['kernel_params'], kernal_params)
            # Check node interfaces section
            self.assertEqual(
                node['interfaces'][intr_name]['mac_address'], intr_mac)
            self.assertEqual(
                node['interfaces'][intr_name]['static'], '0')
            self.assertEqual(
                node['interfaces'][intr_name]['dns_name'],
                objects.Node.get_node_fqdn(node_db))
            # Check node interfaces extra section
            self.assertEqual(node['interfaces_extra'][intr_name], {
                'peerdns': 'no',
                'onboot': 'yes'
            })
            # check identity key for mcollective
            self.assertEqual(node['ks_meta']['mco_identity'], node_db.id)

    def test_node_serialization_w_bonded_admin_iface(self):
        self.cluster_db = self.env.clusters[0]
        # create additional node to test bonding
        admin_mac = self.env.generate_random_mac()
        meta = {
            'interfaces': [
                {'name': 'eth1', 'mac': admin_mac, 'pxe': True},
                {'name': 'eth2', 'mac': self.env.generate_random_mac()},
                {'name': 'eth3', 'mac': self.env.generate_random_mac()},
                {'name': 'eth4', 'mac': self.env.generate_random_mac()}
            ]
        }
        node = self.env.create_node(
            pending_addition=True,
            cluster_id=self.cluster_db.id,
            meta=meta,
            mac=admin_mac
        )
        # get node from db
        node_db = objects.Node.get_by_uid(node['id'])
        # bond admin iface
        self.env.make_bond_via_api('lnx_bond',
                                   '',
                                   ['eth1', 'eth4'],
                                   node['id'],
                                   bond_properties={
                                       'mode': consts.BOND_MODES.balance_rr
                                   })
        # check serialized data
        serialized_node = ps.serialize(self.cluster_db, [node_db])['nodes'][0]
        out_mac = serialized_node['kernel_options']['netcfg/choose_interface']
        self.assertEqual(out_mac, admin_mac)


class TestProvisioningSerializer61(BaseIntegrationTest):

    serializer = ps.ProvisioningSerializer61

    def test_make_privisioning_package_list(self):
        provisioning_data = {
            'packages': """
                linux-firmware
                linux-firmware-nonfree
                linux-headers-generic-lts-trusty
                linux-image-generic-lts-trusty"""
        }
        packages = self.serializer._make_provisioning_package_list(
            provisioning_data)
        self.assertEqual(packages, [
            'linux-firmware',
            'linux-firmware-nonfree',
            'linux-headers-generic-lts-trusty',
            'linux-image-generic-lts-trusty',
        ])

    def test_ubuntu_prov_task_for_images(self):
        release = self.env.create_release(
            api=False, operating_system=consts.RELEASE_OS.ubuntu)
        self.cluster = self.env.create_cluster(
            api=False, release_id=release.id)
        self.cluster.attributes.editable['provision']['method'] = \
            consts.PROVISION_METHODS.image
        self.cluster.attributes.editable['provision']['packages'] = """
            linux-firmware
            linux-firmware-nonfree
            linux-headers-generic-lts-trusty
            linux-image-generic-lts-trusty
        """

        serialized_info = self.serializer.serialize(self.cluster, [])

        self.assertIn('pre_provision', serialized_info)
        self.assertTrue(filter(
            lambda task: all([
                task['uids'] == ['master'],
                task['type'] == 'shell',
                task['parameters']['cmd'].startswith('fa_build_image')
            ]),
            serialized_info['pre_provision']))
        self.assertFalse(filter(
            lambda task: all([
                task['uids'] == ['master'],
                task['type'] == 'shell',
                task['parameters']['cmd'].startswith(
                    'LOCAL_KERNEL_FILE')
            ]),
            serialized_info['pre_provision']))

    def test_centos_prov_task_for_cobbler(self):
        release = self.env.create_release(
            api=False, operating_system=consts.RELEASE_OS.centos)
        self.cluster = self.env.create_cluster(
            api=False, release_id=release.id)
        self.cluster.attributes.editable['provision']['method'] = \
            consts.PROVISION_METHODS.cobbler

        serialized_info = self.serializer.serialize(self.cluster, [])

        self.assertIn('pre_provision', serialized_info)
        self.assertFalse(filter(
            lambda task: all([
                task['priority'] == 100,
                task['uids'] == ['master'],
                task['type'] == 'shell',
                task['parameters']['cmd'].startswith('fuel-image')
            ]),
            serialized_info['pre_provision']))
        self.assertIn('pre_provision', serialized_info)
        self.assertEquals([], serialized_info['pre_provision'])

    def test_centos_prov_task_for_images(self):
        release = self.env.create_release(
            api=False, operating_system=consts.RELEASE_OS.centos)
        self.cluster = self.env.create_cluster(
            api=False, release_id=release.id)
        self.cluster.attributes.editable['provision']['method'] = \
            consts.PROVISION_METHODS.image

        serialized_info = self.serializer.serialize(self.cluster, [])

        self.assertIn('pre_provision', serialized_info)
        self.assertFalse(filter(
            lambda task: all([
                task['priority'] == 100,
                task['uids'] == ['master'],
                task['type'] == 'shell',
                task['parameters']['cmd'].startswith('fuel-image')
            ]),
            serialized_info['pre_provision']))
        self.assertIn('pre_provision', serialized_info)
        self.assertEquals([], serialized_info['pre_provision'])

    def test_engine_does_not_contain_provisioning_method(self):
        self.cluster = self.env.create_cluster(api=False)
        serialized_info = self.serializer.serialize(self.cluster, [])

        self.assertNotIn('provision_method', serialized_info['engine'])

    def test_centos_fedora_kernel_selection(self):
        release = self.env.create_release(
            api=False, operating_system=consts.RELEASE_OS.centos)
        self.cluster = self.env.create_cluster(
            api=False, release_id=release.id)
        self.env.create_node(
            api=False, cluster_id=self.cluster['id'], pending_addition=True)
        self.cluster.attributes.editable['use_fedora_lt']['kernel'] = \
            'fedora_lt_kernel'

        serialized_info = self.serializer.serialize(
            self.cluster,
            self.cluster.nodes)

        node_info = serialized_info['nodes'][0]
        self.assertIn('kernel_lt', node_info['ks_meta'])
        self.assertEqual(1, node_info['ks_meta']['kernel_lt'])


class TestProvisioningSerializer80(BaseIntegrationTest):

    serializer = ps.ProvisioningSerializer80

    def test_generate_ironic_bootstrap_keys_task(self):
        release = self.env.create_release(
            api=False,
            operating_system=consts.RELEASE_OS.ubuntu)
        self.cluster = self.env.create_cluster(
            api=False, release_id=release.id)
        editable = self.cluster.attributes.editable
        editable['additional_components']['ironic']['value'] = True

        serialized_info = self.serializer.serialize(self.cluster, [])

        self.assertIn('pre_provision', serialized_info)
        self.assertTrue(filter(
            lambda task: all([
                task['uids'] == ['master'],
                task['type'] == 'shell',
                task['parameters']['cmd'].startswith(
                    'sh /etc/puppet/modules/osnailyfacter/modular/'
                    'astute/generate_keys.sh')
            ]),
            serialized_info['pre_provision']))

        self.assertTrue(filter(
            lambda task: all([
                'fuel-bootstrap' in task['parameters']['cmd'],
                'ironic.pub' in task['parameters']['cmd']]),
            serialized_info['pre_provision']))

    def test_do_not_generate_ironic_bootstrap_keys_task(self):
        release = self.env.create_release(
            api=False,
            operating_system=consts.RELEASE_OS.ubuntu)
        self.cluster = self.env.create_cluster(
            api=False, release_id=release.id)
        editable = self.cluster.attributes.editable
        editable['additional_components']['ironic']['value'] = False

        serialized_info = self.serializer.serialize(self.cluster, [])

        self.assertIn('pre_provision', serialized_info)
        self.assertFalse(filter(
            lambda task: task['parameters']['cmd'].startswith(
                'sh /etc/puppet/modules/osnailyfacter/modular/'
                'astute/generate_keys.sh'),
            serialized_info['pre_provision']))

        self.assertFalse(filter(
            lambda task: all([
                'fuel-bootstrap-image' in task['parameters']['cmd'],
                'ironic.pub' in task['parameters']['cmd']]),
            serialized_info['pre_provision']))


class TestProvisioningSerializer90(BaseIntegrationTest):

    serializer = ps.ProvisioningSerializer90

    def test_user_account_info(self):
        self.env.create(
            release_kwargs={'version': 'liberty-9.0'},
        )
        self.cluster_db = self.env.clusters[0]
        self.env.create_nodes_w_interfaces_count(
            1, 1,
            roles=['controller'],
            pending_addition=True,
            cluster_id=self.cluster_db.id
        )
        self.env.create_nodes_w_interfaces_count(
            1, 1,
            roles=['compute'],
            pending_addition=True,
            cluster_id=self.cluster_db.id
        )

        attributes = objects.Cluster.get_attributes(self.cluster_db)
        operator_user = attributes['editable']['operator_user']
        service_user = attributes['editable']['service_user']

        serialized_cluster = self.serializer.serialize(
            self.cluster_db, self.cluster_db.nodes)

        operator_user_keys = utils.get_lines(
            operator_user['authkeys']['value']
        )
        common_keys = settings.AUTHORIZED_KEYS

        operator_user_dict = {
            'name': operator_user['name']['value'],
            'password': operator_user['password']['value'],
            'homedir': operator_user['homedir']['value'],
            'sudo': utils.get_lines(operator_user['sudo']['value']),
            'ssh_keys': operator_user_keys + common_keys
        }
        service_user_dict = {
            'name': service_user['name']['value'],
            'password': service_user['password']['value'],
            'homedir': service_user['homedir']['value'],
            'sudo': utils.get_lines(service_user['sudo']['value']),
            'ssh_keys': common_keys
        }
        root_user_dict = {
            'name': 'root',
            'homedir': '/root',
            'password': service_user['root_password']['value'],
            'ssh_keys': common_keys
        }

        user_accounts = [operator_user_dict,
                         service_user_dict,
                         root_user_dict]

        for node in serialized_cluster['nodes']:
            self.assertEqual(
                node['ks_meta']['user_accounts'],
                user_accounts
            )

    def test_serialize_iommu_parameters_for_sriov(self):
        self.env.create(
            release_kwargs={
                'version': 'liberty-9.0',
                'operating_system': consts.RELEASE_OS.ubuntu},
            nodes_kwargs=[
                {'roles': ['compute']}]
        )

        sriov_nic = self.env.nodes[0].nic_interfaces[0]
        sriov_nic.interface_properties['sriov']['available'] = True
        sriov_nic.interface_properties['sriov']['enabled'] = True
        objects.NIC.update(sriov_nic, {})

        serialized_node = self.serializer.serialize(
            self.env.clusters[0], self.env.nodes)['nodes'][0]
        kernel_opts = serialized_node['ks_meta']['pm_data']['kernel_params']
        self.assertIn("intel_iommu=on", kernel_opts)
        self.assertIn("amd_iommu=on", kernel_opts)

    def test_serialize_node_hugepages(self):
        self.env.create(
            api=False,
            release_kwargs={'operating_system': consts.RELEASE_OS.ubuntu},
            nodes_kwargs=[
                {'roles': ['compute']}])

        node = self.env.nodes[0]
        node.attributes['hugepages']['nova']['value'] = {'2048': 5}

        serialized_info = self.serializer.serialize(node.cluster, [node])

        serialized_node = serialized_info['nodes'][0]
        kernel_opts = serialized_node['ks_meta']['pm_data']['kernel_params']

        self.assertIn(" hugepagesz=2M hugepages=5", kernel_opts)

    def test_serialize_node_cpu_pinning(self):
        self.env.create(
            api=False,
            release_kwargs={'operating_system': consts.RELEASE_OS.ubuntu},
            nodes_kwargs=[
                {'roles': ['compute']}])

        node = self.env.nodes[0]
        node.attributes['cpu_pinning']['nova']['value'] = 2

        serialized_info = self.serializer.serialize(node.cluster, [node])

        serialized_node = serialized_info['nodes'][0]
        kernel_opts = serialized_node['ks_meta']['pm_data']['kernel_params']

        self.assertIn(" isolcpus=0,1", kernel_opts)
