# -*- coding: utf-8 -*-

#    Copyright 2015 Mirantis, Inc.
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

import mock
import six

from oslo_serialization import jsonutils

from nailgun import consts
from nailgun import objects
from nailgun.orchestrator import deployment_serializers
from nailgun.plugins import adapters
from nailgun.utils import reverse

from nailgun.orchestrator.neutron_serializers import \
    NeutronNetworkDeploymentSerializer90
from nailgun.orchestrator.neutron_serializers import \
    NeutronNetworkTemplateSerializer90

from nailgun.test.integration import test_orchestrator_serializer_80


class TestSerializer90Mixin(object):
    env_version = "mitaka-9.0"
    task_deploy = True

    @classmethod
    def create_serializer(cls, cluster):
        serializer_type = deployment_serializers.get_serializer_for_cluster(
            cluster
        )
        return serializer_type(None)


class TestDeploymentAttributesSerialization90(
    TestSerializer90Mixin,
    test_orchestrator_serializer_80.TestDeploymentAttributesSerialization80
):
    @mock.patch('nailgun.objects.Release.get_supported_dpdk_drivers')
    def test_serialization_with_dpdk(self, drivers_mock):
        drivers_mock.return_value = {
            'driver_1': ['test_id:1', 'test_id:2']
        }
        node = self.env.create_nodes_w_interfaces_count(
            1, 3,
            cluster_id=self.cluster_db.id,
            roles=['compute'])[0]

        nic_1 = node.interfaces[0]
        nic_2 = node.interfaces[2]
        nets_1 = nic_1.assigned_networks_list
        nets_2 = nic_2.assigned_networks_list

        dpdk_interface_name = nic_2.name

        for i, net in enumerate(nets_1):
            if net['name'] == 'private':
                nets_2.append(nets_1.pop(i))
                break
        objects.NIC.assign_networks(nic_1, nets_1)
        objects.NIC.assign_networks(nic_2, nets_2)

        objects.NIC.update(nic_2,
                           {'interface_properties':
                               {
                                   'dpdk': {'enabled': True,
                                            'available': True},
                                   'pci_id': 'test_id:2'
                               }})
        objects.Cluster.prepare_for_deployment(self.cluster_db)

        serialised_for_astute = self.serializer.serialize(
            self.cluster_db, self.cluster_db.nodes)
        self.assertEqual(len(serialised_for_astute), 1)
        node = serialised_for_astute[0]
        dpdk = node.get('dpdk')
        self.assertIsNotNone(dpdk)
        self.assertTrue(dpdk.get('enabled'))

        transformations = node['network_scheme']['transformations']
        private_br = filter(lambda t: t.get('name') ==
                            consts.DEFAULT_BRIDGES_NAMES.br_prv,
                            transformations)[0]
        dpdk_ports = filter(lambda t: t.get('name') ==
                            dpdk_interface_name,
                            transformations)
        all_ports = filter(lambda t: t.get('action') ==
                           'add-port',
                           transformations)
        self.assertEqual(private_br.get('vendor_specific'),
                         {'datapath_type': 'netdev'})
        self.assertEqual(len(all_ports) - len(dpdk_ports), len(nets_1))
        self.assertEqual(len(dpdk_ports), 1)
        self.assertEqual(dpdk_ports[0]['bridge'],
                         consts.DEFAULT_BRIDGES_NAMES.br_prv)
        self.assertEqual(dpdk_ports[0].get('provider'),
                         consts.NEUTRON_L23_PROVIDERS.dpdkovs)

        interfaces = node['network_scheme']['interfaces']
        dpdk_interface = interfaces[dpdk_interface_name]
        vendor_specific = dpdk_interface.get('vendor_specific', {})
        self.assertEqual(vendor_specific.get('dpdk_driver'), 'driver_1')

    def _check_dpdk_bond_serializing(self, bond_properties):
        node = self.env.create_nodes_w_interfaces_count(
            1, 4,
            cluster_id=self.cluster_db.id,
            roles=['compute'])[0]

        node.attributes['hugepages'] = {
            'dpdk': {'type': 'number', 'value': 1024},
            'nova': {'type': 'custom_hugepages', 'value': {'2048': 1}}
        }
        cluster_attrs = objects.Cluster.get_editable_attributes(node.cluster)
        cluster_attrs['common']['libvirt_type'].update(
            {'value': consts.HYPERVISORS.kvm})
        objects.Cluster.update_attributes(
            node.cluster, {'editable': cluster_attrs})

        for iface in node.interfaces:
            iface['interface_properties'].update({'pci_id': 'test_id:1'})
            iface['interface_properties']['dpdk']['available'] = True

        interfaces = self.env.node_nics_get(node.id).json_body
        first_nic = interfaces[0]
        nics_for_bond = [interfaces.pop(), interfaces.pop()]
        networks_for_bond = []
        bond_interface_name = 'bond0'
        first_nic_networks = first_nic['assigned_networks']
        for i, net in enumerate(first_nic_networks):
            if net['name'] == 'private':
                networks_for_bond.append(first_nic_networks.pop(i))
                break
        bond_interface = {
            'name': bond_interface_name,
            'type': consts.NETWORK_INTERFACE_TYPES.bond,
            'slaves': nics_for_bond,
            'assigned_networks': networks_for_bond,
            'bond_properties': bond_properties,
            'interface_properties': {'dpdk': {'enabled': True}}}
        interfaces.append(bond_interface)
        self.env.node_nics_put(node.id, interfaces)
        objects.Cluster.prepare_for_deployment(self.cluster_db)
        serialised_for_astute = self.serializer.serialize(
            self.cluster_db, self.cluster_db.nodes)
        self.assertEqual(len(serialised_for_astute), 1)
        node = serialised_for_astute[0]
        dpdk = node.get('dpdk')
        self.assertIsNotNone(dpdk)
        self.assertTrue(dpdk.get('enabled'))
        transformations = node['network_scheme']['transformations']
        private_br = filter(lambda t: t.get('name') ==
                            consts.DEFAULT_BRIDGES_NAMES.br_prv,
                            transformations)[0]
        dpdk_bonds = filter(lambda t: t.get('name') ==
                            bond_interface_name,
                            transformations)
        self.assertEqual(len(dpdk_bonds), 1)
        self.assertEqual(dpdk_bonds[0]['bridge'],
                         consts.DEFAULT_BRIDGES_NAMES.br_prv)
        self.assertEqual(private_br.get('vendor_specific'),
                         {'datapath_type': 'netdev'})
        self.assertEqual(dpdk_bonds[0].get('provider'),
                         consts.NEUTRON_L23_PROVIDERS.dpdkovs)
        self.assertEqual(dpdk_bonds[0].get('bond_properties').get('mode'),
                         bond_interface['bond_properties'].get('mode'))
        interfaces = node['network_scheme']['interfaces']
        for iface in nics_for_bond:
            dpdk_interface = interfaces[iface['name']]
            vendor_specific = dpdk_interface.get('vendor_specific', {})
            self.assertEqual(vendor_specific.get('dpdk_driver'), 'driver_1')

    @mock.patch('nailgun.objects.Release.get_supported_dpdk_drivers')
    def test_serialization_with_dpdk_on_bond(self, drivers_mock):
        drivers_mock.return_value = {
            'driver_1': ['test_id:1', 'test_id:2']
        }
        bond_properties = {
            'mode': consts.BOND_MODES.balance_slb,
            'type__': consts.BOND_TYPES.dpdkovs,
        }

        self._check_dpdk_bond_serializing(bond_properties)

    @mock.patch('nailgun.objects.Release.get_supported_dpdk_drivers')
    def test_serialization_with_dpdk_on_lacp_bond(self, drivers_mock):
        drivers_mock.return_value = {
            'driver_1': ['test_id:1', 'test_id:2']
        }
        bond_properties = {
            'mode': consts.BOND_MODES.balance_tcp,
            'lacp': 'active',
            'lacp_rate': 'fast',
            'xmit_hash_policy': 'layer2',
            'type__': consts.BOND_TYPES.dpdkovs}
        self._check_dpdk_bond_serializing(bond_properties)

    def test_attributes_cpu_pinning(self):
        numa_nodes = [
            {'id': 0, 'memory': 2 ** 31, 'cpus': [1, 2, 3, 4]},
            {'id': 1, 'memory': 2 ** 31, 'cpus': [5, 6, 7, 8]}
        ]
        node = self.env.create_node(cluster_id=self.cluster_db.id,
                                    roles=['compute'])

        node.meta['numa_topology']['numa_nodes'] = numa_nodes
        node.attributes.update({
            'cpu_pinning': {
                'nova': {'value': 2},
                'dpdk': {'value': 2},
            }
        })
        objects.Cluster.prepare_for_deployment(self.cluster_db)
        serialized_for_astute = self.serializer.serialize(
            self.cluster_db, self.cluster_db.nodes)

        serialized_node = serialized_for_astute[0]

        self.assertEqual(serialized_node['dpdk']['ovs_core_mask'], '0x2')
        self.assertEqual(serialized_node['dpdk']['ovs_pmd_core_mask'], '0x4')
        self.assertEqual(serialized_node['nova']['cpu_pinning'], [3, 4])
        node_name = objects.Node.get_slave_name(node)
        node_common_attrs = \
            serialized_node['network_metadata']['nodes'][node_name]
        self.assertTrue(node_common_attrs['nova_cpu_pinning_enabled'])

    def test_pinning_one_cpu_for_dpdk(self):
        numa_nodes = [
            {'id': 0, 'memory': 2 ** 31, 'cpus': [1, 2, 3, 4]},
            {'id': 1, 'memory': 2 ** 31, 'cpus': [5, 6, 7, 8]}
        ]
        node = self.env.create_node(cluster_id=self.cluster_db.id,
                                    roles=['compute'])

        node.meta['numa_topology']['numa_nodes'] = numa_nodes
        node.attributes.update({
            'cpu_pinning': {
                'dpdk': {'value': 1},
            }
        })
        objects.Cluster.prepare_for_deployment(self.cluster_db)
        serialized_for_astute = self.serializer.serialize(
            self.cluster_db, self.cluster_db.nodes)

        serialized_node = serialized_for_astute[0]

        self.assertEqual(serialized_node['dpdk']['ovs_core_mask'], '0x2')
        self.assertNotIn('ovs_pmd_core_mask', serialized_node['dpdk'])
        self.assertNotIn('cpu_pinning', serialized_node['nova'])
        node_name = objects.Node.get_slave_name(node)
        node_common_attrs = \
            serialized_node['network_metadata']['nodes'][node_name]
        self.assertFalse(node_common_attrs['nova_cpu_pinning_enabled'])

    def test_dpdk_hugepages(self):
        numa_nodes = []
        for i in six.moves.range(3):
            numa_nodes.append({
                'id': i,
                'cpus': [i],
                'memory': 1024 ** 3
            })

        meta = {
            'numa_topology': {
                'supported_hugepages': [2048],
                'numa_nodes': numa_nodes
            }
        }
        node = self.env.create_node(
            cluster_id=self.cluster_db.id,
            roles=['compute'],
            meta=meta)
        node.attributes.update({
            'hugepages': {
                'dpdk': {
                    'value': 128},
                'nova': {
                    'value': {'2048': 1}}}}
        )

        objects.Cluster.prepare_for_deployment(self.cluster_db)
        serialized_for_astute = self.serializer.serialize(
            self.cluster_db, self.cluster_db.nodes)

        serialized_node = serialized_for_astute[0]

        self.assertEquals(
            "128,128,128",
            serialized_node['dpdk']['ovs_socket_mem'])
        self.assertTrue(serialized_node['nova']['enable_hugepages'])

    def test_attributes_no_hugepages_distribution_with_gig_hugepage(self):
        meta = {
            'numa_topology': {
                'supported_hugepages': [2048, 1048576],
                'numa_nodes': [
                    {'id': 0, 'memory': 2 ** 31, 'cpus': [1, 2, 3, 4]},
                    {'id': 1, 'memory': 2 ** 31, 'cpus': [5, 6, 7, 8]}],
            }
        }
        node = self.env.create_node(
            cluster_id=self.cluster_db.id,
            roles=['compute'],
            meta=meta)
        node.attributes.update({
            'hugepages': {
                'nova': {
                    'type': 'custom_hugepages',
                    'value': {
                        '2048': 512,
                        '1048576': 1
                    }
                },
                'dpdk': {
                    'type': 'number',
                    'value': 512,
                }
            }
        })

        objects.Cluster.prepare_for_deployment(self.cluster_db)
        serialized_for_astute = self.serializer.serialize(
            self.cluster_db, self.cluster_db.nodes)
        serialized_node = serialized_for_astute[0]

        self.assertNotIn('hugepages', serialized_node)

    def test_attributes_hugepages_distribution(self):
        meta = {
            'numa_topology': {
                'supported_hugepages': [2048, 1048576],
                'numa_nodes': [
                    {'id': 0, 'memory': 2 ** 31, 'cpus': [1, 2, 3, 4]},
                    {'id': 1, 'memory': 2 ** 31, 'cpus': [5, 6, 7, 8]}],
            }
        }
        node = self.env.create_node(
            cluster_id=self.cluster_db.id,
            roles=['compute'],
            meta=meta)
        node.attributes.update({
            'hugepages': {
                'nova': {
                    'type': 'custom_hugepages',
                    'value': {
                        '2048': 512,
                    }
                },
                'dpdk': {
                    'type': 'number',
                    'value': 512,
                }
            }
        })

        objects.Cluster.prepare_for_deployment(self.cluster_db)
        serialized_for_astute = self.serializer.serialize(
            self.cluster_db, self.cluster_db.nodes)
        serialized_node = serialized_for_astute[0]

        expected = [
            {'numa_id': 0, 'size': 2048, 'count': 512},
            {'numa_id': 1, 'size': 2048, 'count': 512},
        ]

        self.assertEqual(serialized_node['hugepages'], expected)

    def test_cpu_pinning_disabled(self):
        nodes_roles = [['compute'], ['controller']]
        for roles in nodes_roles:
            self.env.create_node(
                cluster_id=self.cluster_db.id,
                roles=roles)

        objects.Cluster.prepare_for_deployment(self.cluster_db)
        serialized_for_astute = self.serializer.serialize(
            self.cluster_db, self.cluster_db.nodes)

        for serialized_node in serialized_for_astute:
            nova = serialized_node.get('nova', {})
            self.assertNotIn('cpu_pinning', nova)

            dpdk = serialized_node.get('dpdk', {})
            self.assertNotIn('ovs_core_mask', dpdk)
            self.assertNotIn('ovs_pmd_core_mask', dpdk)

            nodes_attrs = serialized_node['network_metadata']['nodes']
            for node_attrs in six.itervalues(nodes_attrs):
                self.assertFalse(node_attrs['nova_cpu_pinning_enabled'])

    def test_hugepages_disabled(self):
        nodes_roles = [['compute'], ['controller']]
        for roles in nodes_roles:
            self.env.create_node(
                cluster_id=self.cluster_db.id,
                roles=roles)

        objects.Cluster.prepare_for_deployment(self.cluster_db)
        serialized_for_astute = self.serializer.serialize(
            self.cluster_db, self.cluster_db.nodes)

        for serialized_node in serialized_for_astute:
            nova = serialized_node.get('nova', {})
            self.assertFalse(nova.get('enable_hugepages', False))

            dpdk = serialized_node.get('dpdk', {})
            self.assertNotIn('ovs_socket_mem', dpdk)

            self.assertNotIn('hugepages', serialized_node)

            nodes_attrs = serialized_node['network_metadata']['nodes']
            for node_attrs in six.itervalues(nodes_attrs):
                self.assertFalse(node_attrs['nova_hugepages_enabled'])

    def test_immutable_metadata_key(self):
        node = self.env.create_node(
            api=True,
            cluster_id=self.cluster_db.id,
            pending_roles=['controller'],
            pending_addition=True)
        self.db.flush()
        resp = self.app.put(
            reverse('NodeHandler', kwargs={'obj_id': node['id']}),
            jsonutils.dumps({'hostname': 'new-name'}),
            headers=self.default_headers)
        self.assertEqual(200, resp.status_code)
        objects.Cluster.prepare_for_deployment(self.cluster_db)
        serialized_for_astute = self.serializer.serialize(
            self.cluster_db, self.cluster_db.nodes)
        for node_data in serialized_for_astute:
            for k, v in six.iteritems(node_data['network_metadata']['nodes']):
                node = objects.Node.get_by_uid(v['uid'])
                self.assertEqual(objects.Node.permanent_id(node), k)


class TestDeploymentLCMSerialization90(
    TestSerializer90Mixin,
    test_orchestrator_serializer_80.BaseDeploymentSerializer
):
    def setUp(self):
        super(TestDeploymentLCMSerialization90, self).setUp()
        self.cluster = self.env.create(
            release_kwargs={
                'version': self.env_version,
                'operating_system': consts.RELEASE_OS.ubuntu
            },
            cluster_kwargs={
                'mode': consts.CLUSTER_MODES.ha_compact,
                'net_provider': consts.CLUSTER_NET_PROVIDERS.neutron,
                'net_segment_type': consts.NEUTRON_SEGMENT_TYPES.vlan})
        self.cluster_db = self.env.clusters[-1]
        self.node = self.env.create_node(
            cluster_id=self.cluster_db.id, roles=['compute']
        )
        self.serializer = self.create_serializer(self.cluster_db)

    @classmethod
    def create_serializer(cls, cluster):
        return deployment_serializers.DeploymentLCMSerializer()

    def test_openstack_configuration_in_serialized(self):

        self.env.create_openstack_config(
            cluster_id=self.cluster_db.id,
            configuration={
                'glance_config': 'value1',
                'nova_config': 'value1',
                'ceph_config': 'value1'
            }
        )
        self.env.create_openstack_config(
            cluster_id=self.cluster_db.id, node_role='compute',
            configuration={'ceph_config': 'value2'}
        )
        self.env.create_openstack_config(
            cluster_id=self.cluster_db.id, node_id=self.node.id,
            configuration={'nova_config': 'value3'}
        )
        objects.Cluster.prepare_for_deployment(self.cluster_db)
        serialized = self.serializer.serialize(self.cluster_db, [self.node])
        self.assertEqual(
            {'glance_config': 'value1',
             'nova_config': 'value3',
             'ceph_config': 'value2'},
            serialized[0]['configuration']
        )

    def test_cluster_attributes_in_serialized(self):
        objects.Cluster.prepare_for_deployment(self.cluster_db)
        serialized = self.serializer.serialize(self.cluster_db, [self.node])
        for node_info in serialized:
            self.assertEqual(
                objects.Cluster.to_dict(self.cluster_db),
                node_info['cluster']
            )
            self.assertEqual(
                objects.Release.to_dict(self.cluster_db.release),
                node_info['release']
            )

        self.assertEqual(
            ['compute'],
            serialized[0]['roles']
        )
        self.assertEqual(
            [consts.TASK_ROLES.master],
            serialized[1]['roles']
        )

    @mock.patch.object(
        adapters.PluginAdapterBase, 'repo_files',
        mock.MagicMock(return_value=True)
    )
    def test_plugins_in_serialized(self):
        releases = [
            {'repository_path': 'repositories/ubuntu',
             'version': 'mitaka-9.0', 'os': 'ubuntu',
             'mode': ['ha', 'multinode'],
             'deployment_scripts_path': 'deployment_scripts/'}
        ]
        plugin1 = self.env.create_plugin(
            cluster=self.cluster_db,
            name='plugin_1',
            attributes_metadata={'attributes': {'name': 'plugin_1'}},
            package_version='4.0.0',
            releases=releases
        )
        plugin2 = self.env.create_plugin(
            cluster=self.cluster_db,
            name='plugin_2',
            attributes_metadata={'attributes': {'name': 'plugin_2'}},
            package_version='4.0.0',
            releases=releases
        )
        self.env.create_plugin(
            cluster=self.cluster_db,
            enabled=False,
            name='plugin_3',
            attributes_metadata={'attributes': {'name': 'plugin_3'}},
            package_version='4.0.0',
            releases=releases
        )

        self.env.create_node(
            cluster_id=self.cluster_db.id,
            roles=['compute']
        )
        plugins_data = [
            {
                'name': p.name,
                'scripts': [{
                    'remote_url': p.master_scripts_path(self.cluster_db),
                    'local_path': p.slaves_scripts_path
                }],
                'repositories': [{
                    'type': 'deb',
                    'name': p.full_name,
                    'uri': p.repo_url(self.cluster_db),
                    'suite': '/',
                    'section': '',
                    'priority': 1100
                }]
            }
            for p in six.moves.map(adapters.wrap_plugin, [plugin1, plugin2])
        ]
        objects.Cluster.prepare_for_deployment(self.cluster_db)
        serialized = self.serializer.serialize(
            self.cluster_db, self.cluster_db.nodes)
        for node in serialized:
            self.assertIn('plugins', node)
            self.datadiff(plugins_data, node['plugins'], compare_sorted=True)

    def test_serialize_with_customized(self):
        objects.Cluster.prepare_for_deployment(self.cluster_db)
        serialized = self.serializer.serialize(self.cluster_db, [self.node])

        objects.Cluster.replace_deployment_info(self.cluster_db, serialized)
        objects.Cluster.prepare_for_deployment(self.cluster_db)
        cust_serialized = self.serializer.serialize(
            self.cluster_db, [self.node])

        for item in serialized:
            if item['uid'] != consts.MASTER_NODE_UID:
                self.assertIn(item, cust_serialized)
            else:
                item['cluster']['is_customized'] = True
                self.assertIn(item, cust_serialized)


class TestDeploymentHASerializer90(
    TestSerializer90Mixin,
    test_orchestrator_serializer_80.TestDeploymentHASerializer80
):
    def test_glance_properties(self):
        self.check_no_murano_data()

    def test_ceph_keys(self):
        storage_attrs = self.serializer.get_common_attrs(
            self.env.clusters[0]
        )['storage']
        expected_keys = (
            'fsid', 'mon_key', 'admin_key', 'bootstrap_osd_key', 'radosgw_key'
        )
        for ceph_key in expected_keys:
            self.assertIn(ceph_key, storage_attrs)

    def test_serialize_with_customized(self):
        cluster = self.env.clusters[0]
        serializer = self.create_serializer(cluster)

        objects.Cluster.prepare_for_deployment(cluster)
        serialized = serializer.serialize(cluster, cluster.nodes)

        objects.Cluster.replace_deployment_info(cluster, serialized)
        objects.Cluster.prepare_for_deployment(cluster)
        cust_serialized = serializer.serialize(
            cluster, cluster.nodes)

        for item in serialized:
            self.assertIn(item, cust_serialized)


class TestDeploymentTasksSerialization90(
    TestSerializer90Mixin,
    test_orchestrator_serializer_80.TestDeploymentTasksSerialization80
):
    pass


class TestMultiNodeGroupsSerialization90(
    TestSerializer90Mixin,
    test_orchestrator_serializer_80.TestMultiNodeGroupsSerialization80
):
    pass


class TestNetworkTemplateSerializer90CompatibleWith80(
    TestSerializer90Mixin,
    test_orchestrator_serializer_80.
        TestNetworkTemplateSerializer80CompatibleWith70
):
    general_serializer = NeutronNetworkDeploymentSerializer90
    template_serializer = NeutronNetworkTemplateSerializer90

    def check_vendor_specific_is_not_set(self, use_net_template=False):
        node = self.env.create_node(
            cluster_id=self.cluster.id,
            roles=['controller'], primary_roles=['controller']
        )
        objects.Cluster.set_network_template(
            self.cluster,
            self.net_template if use_net_template else None)
        objects.Cluster.prepare_for_deployment(self.cluster)
        serializer = deployment_serializers.get_serializer_for_cluster(
            self.cluster)
        net_serializer = serializer.get_net_provider_serializer(self.cluster)
        nm = objects.Cluster.get_network_manager(self.cluster)
        networks = nm.get_node_networks(node)
        endpoints = net_serializer.generate_network_scheme(
            node, networks)['endpoints']

        for name in endpoints:
            # Just 'provider_gateway' can be in 'vendor_specific'
            if endpoints[name].get('vendor_specific'):
                self.assertItemsEqual(['provider_gateway'],
                                      endpoints[name]['vendor_specific'])

    # This test is replaced as we have different attributes set in 9.0
    def test_multiple_node_roles_network_metadata_attrs(self):
        for node_data in self.serialized_for_astute:
            self.assertItemsEqual(
                node_data['network_metadata'], ['nodes', 'vips'])
            nodes = node_data['network_metadata']['nodes']
            for node_name, node_attrs in nodes.items():
                self.assertTrue(
                    {'uid', 'fqdn', 'name', 'user_node_name', 'swift_zone',
                     'node_roles', 'network_roles',
                     'nova_cpu_pinning_enabled'}.issubset(node_attrs)
                )
                node = objects.Node.get_by_uid(node_attrs['uid'])
                self.assertEqual(objects.Node.get_slave_name(node), node_name)
                self.assertEqual(node_attrs['uid'], node.uid)
                self.assertEqual(node_attrs['fqdn'],
                                 objects.Node.get_node_fqdn(node))
                self.assertEqual(node_attrs['name'], node_name)
                self.assertEqual(node_attrs['user_node_name'], node.name)
                self.assertEqual(node_attrs['swift_zone'], node.uid)
                self.assertEqual(node_attrs['nova_cpu_pinning_enabled'], False)


class TestNetworkTemplateSerializer90(
    TestSerializer90Mixin,
    test_orchestrator_serializer_80.TestNetworkTemplateSerializer80
):
    legacy_serializer = NeutronNetworkDeploymentSerializer90
    template_serializer = NeutronNetworkTemplateSerializer90

    def check_selective_gateway(self, use_net_template=False):
        node = self.env.create_node(
            cluster_id=self.cluster.id,
            roles=['controller'], primary_roles=['controller']
        )
        objects.Cluster.set_network_template(
            self.cluster,
            self.net_template if use_net_template else None)
        objects.Cluster.prepare_for_deployment(self.cluster)

        serializer = deployment_serializers.get_serializer_for_cluster(
            self.cluster)
        net_serializer = serializer.get_net_provider_serializer(self.cluster)
        nm = objects.Cluster.get_network_manager(self.cluster)
        networks_list = nm.get_node_networks(node)
        networks = {net['name']: net for net in networks_list}
        endpoints = net_serializer.generate_network_scheme(
            node, networks_list)['endpoints']

        na = self.net_template[
            'adv_net_template']['default']['network_assignments']
        ep_net_map = {na[net_name]['ep']: net_name for net_name in na}

        for name in endpoints:
            if name not in ep_net_map:
                self.assertNotIn('vendor_specific', endpoints[name])
                continue
            if networks[ep_net_map[name]].get('gateway') is None:
                self.assertNotIn('vendor_specific', endpoints[name])
            else:
                self.assertIn('vendor_specific', endpoints[name])
                self.assertEqual(
                    endpoints[name]['vendor_specific']['provider_gateway'],
                    networks[ep_net_map[name]]['gateway'])

    def test_selective_gateway_in_deployment_serializer(self):
        self.check_selective_gateway()

    def test_selective_gateway_in_template_serializer(self):
        self.check_selective_gateway(use_net_template=True)


class TestSerializeInterfaceDriversData90(
    TestSerializer90Mixin,
    test_orchestrator_serializer_80.TestSerializeInterfaceDriversData80
):
    pass


class TestSriovSerialization90(
    TestSerializer90Mixin,
    test_orchestrator_serializer_80.BaseDeploymentSerializer
):
    def setUp(self, *args):
        super(TestSriovSerialization90, self).setUp()
        cluster = self.env.create(
            release_kwargs={'version': self.env_version},
            cluster_kwargs={
                'mode': consts.CLUSTER_MODES.ha_compact,
                'net_provider': consts.CLUSTER_NET_PROVIDERS.neutron,
                'net_segment_type': consts.NEUTRON_SEGMENT_TYPES.vlan,
                'status': consts.CLUSTER_STATUSES.new},
        )
        self.cluster_db = objects.Cluster.get_by_uid(cluster['id'])
        self.env.create_nodes_w_interfaces_count(
            nodes_count=1, if_count=3, cluster_id=self.env.clusters[0].id,
            pending_roles=['compute'], pending_addition=True)

    def serialize(self):
        objects.Cluster.prepare_for_deployment(self.env.clusters[0])
        serializer = self.create_serializer(self.env.clusters[0])
        return serializer.serialize(self.env.clusters[0], self.env.nodes)

    def test_nic_sriov_info_is_serialized(self):
        for nic in self.env.nodes[0].nic_interfaces:
            if not nic.assigned_networks_list:
                nic_sriov = nic
                nic.interface_properties['sriov'] = {
                    'enabled': True,
                    'sriov_numvfs': 8,
                    'sriov_totalvfs': 8,
                    'available': True,
                    'pci_id': '1234:5678',
                    'physnet': 'new_physnet'
                }
                objects.NIC.update(
                    nic, {'interface_properties': nic.interface_properties})
                break
        else:
            self.fail('NIC without assigned networks was not found')

        node0 = self.serialize()[0]
        self.assertEqual(
            node0['quantum_settings']['supported_pci_vendor_devs'],
            ['1234:5678']
        )
        for trans in node0['network_scheme']['transformations']:
            if trans.get('name') == nic_sriov.name:
                self.assertEqual(
                    trans['vendor_specific'],
                    {
                        'sriov_numvfs': 8,
                        'physnet': 'new_physnet'
                    }
                )
                self.assertEqual(trans['provider'], 'sriov')
                break
        else:
            self.fail('NIC with SR-IOV enabled was not found')
