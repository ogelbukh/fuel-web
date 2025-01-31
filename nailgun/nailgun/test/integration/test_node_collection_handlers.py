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

import copy

from oslo_serialization import jsonutils

from nailgun.db.sqlalchemy.models import Node
from nailgun.db.sqlalchemy.models import Notification
from nailgun.test.base import BaseIntegrationTest
from nailgun.utils import reverse


class TestHandlers(BaseIntegrationTest):
    def test_node_list_empty(self):
        resp = self.app.get(
            reverse('NodeCollectionHandler'),
            headers=self.default_headers
        )
        self.assertEqual(200, resp.status_code)
        self.assertEqual([], resp.json_body)

    def test_notification_node_id(self):
        node = self.env.create_node(
            api=True,
            meta=self.env.default_metadata()
        )
        notif = self.db.query(Notification).first()
        self.assertEqual(node['id'], notif.node_id)
        resp = self.app.get(
            reverse('NotificationCollectionHandler'),
            headers=self.default_headers
        )
        notif_api = resp.json_body[0]
        self.assertEqual(node['id'], notif_api['node_id'])

    def test_node_get_with_cluster(self):
        self.env.create(
            cluster_kwargs={"api": True},
            nodes_kwargs=[
                {"cluster_id": None},
                {},
            ]
        )
        cluster = self.env.clusters[0]

        resp = self.app.get(
            reverse('NodeCollectionHandler'),
            params={'cluster_id': cluster.id},
            headers=self.default_headers
        )
        self.assertEqual(200, resp.status_code)
        self.assertEqual(1, len(resp.json_body))
        self.assertEqual(
            self.env.nodes[1].id,
            resp.json_body[0]['id']
        )

    def test_node_get_with_cluster_None(self):
        self.env.create(
            cluster_kwargs={"api": True},
            nodes_kwargs=[
                {"cluster_id": None},
                {},
            ]
        )

        resp = self.app.get(
            reverse('NodeCollectionHandler'),
            params={'cluster_id': ''},
            headers=self.default_headers
        )
        self.assertEqual(200, resp.status_code)
        self.assertEqual(1, len(resp.json_body))
        self.assertEqual(self.env.nodes[0].id, resp.json_body[0]['id'])

    def test_node_get_without_cluster_specification(self):
        self.env.create(
            cluster_kwargs={"api": True},
            nodes_kwargs=[
                {"cluster_id": None},
                {},
            ]
        )

        resp = self.app.get(
            reverse('NodeCollectionHandler'),
            headers=self.default_headers
        )
        self.assertEqual(200, resp.status_code)
        self.assertEqual(2, len(resp.json_body))

    def test_node_get_with_cluster_and_assigned_ip_addrs(self):
        self.env.create(
            cluster_kwargs={},
            nodes_kwargs=[
                {"pending_addition": True, "api": True},
                {"pending_addition": True, "api": True}
            ]
        )

        self.env.network_manager.assign_ips(
            self.env.clusters[-1],
            self.env.nodes,
            "management"
        )

        resp = self.app.get(
            reverse('NodeCollectionHandler'),
            headers=self.default_headers
        )

        self.assertEqual(200, resp.status_code)
        self.assertEqual(2, len(resp.json_body))

    def test_node_creation(self):
        resp = self.app.post(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps({'mac': self.env.generate_random_mac(),
                             'meta': self.env.default_metadata(),
                             'status': 'discover'}),
            headers=self.default_headers)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual('discover', resp.json_body['status'])

    def test_node_update(self):
        node = self.env.create_node(api=False)
        resp = self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([{'mac': node.mac, 'manufacturer': 'new'}]),
            headers=self.default_headers)
        self.assertEqual(resp.status_code, 200)
        resp = self.app.get(
            reverse('NodeCollectionHandler'),
            headers=self.default_headers
        )
        node = self.db.query(Node).get(node.id)
        self.assertEqual('new', node.manufacturer)

    def test_node_update_empty_mac_or_id(self):
        node = self.env.create_node(api=False)

        resp = self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([{'manufacturer': 'man0'}]),
            headers=self.default_headers,
            expect_errors=True)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json_body["message"],
            "Neither MAC nor ID is specified"
        )

        resp = self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([{'id': node.id,
                              'mac': None,
                              'manufacturer': 'man4'}]),
            headers=self.default_headers,
            expect_errors=True)
        self.assertEqual(resp.status_code, 400)
        self.assertIn(
            "schema['properties']['mac']",
            resp.json_body["message"]
        )
        self.assertIn(
            "None is not of type 'string'",
            resp.json_body["message"]
        )

        resp = self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([{'mac': node.mac,
                              'manufacturer': 'man5'}]),
            headers=self.default_headers
        )
        self.assertEqual(resp.status_code, 200)

        resp = self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([{'id': node.id,
                              'manufacturer': 'man6'}]),
            headers=self.default_headers
        )
        self.assertEqual(resp.status_code, 200)

        resp = self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([{'mac': node.mac,
                              'manufacturer': 'man7'}]),
            headers=self.default_headers)
        self.assertEqual(resp.status_code, 200)

        resp = self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([{'id': node.id,
                              'mac': node.mac,
                              'manufacturer': 'man8'}]),
            headers=self.default_headers)
        self.assertEqual(resp.status_code, 200)

    def node_update_with_invalid_id(self):
        node = self.env.create_node(api=False)

        resp = self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([{'id': 'new_id',
                              'mac': node.mac}]),
            headers=self.default_headers,
            expect_errors=True)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json_body["message"],
            "Invalid ID specified"
        )

    def test_node_update_agent_discover(self):
        self.env.create_node(
            api=False,
            status='provisioning',
            meta=self.env.default_metadata()
        )
        node_db = self.env.nodes[0]
        resp = self.app.put(
            reverse('NodeAgentHandler'),
            jsonutils.dumps(
                {'mac': node_db.mac,
                 'status': 'discover', 'manufacturer': 'new'}
            ),
            headers=self.default_headers
        )
        self.assertEqual(resp.status_code, 200)
        resp = self.app.get(
            reverse('NodeCollectionHandler'),
            headers=self.default_headers
        )
        node_db = self.db.query(Node).get(node_db.id)
        self.assertEqual('new', node_db.manufacturer)
        self.assertEqual('provisioning', node_db.status)

    def test_node_timestamp_updated_only_by_agent(self):
        node = self.env.create_node(api=False)
        timestamp = node.timestamp
        resp = self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([
                {'mac': node.mac, 'status': 'discover',
                 'manufacturer': 'old'}
            ]),
            headers=self.default_headers)
        self.assertEqual(resp.status_code, 200)
        node = self.db.query(Node).get(node.id)
        self.assertEqual(node.timestamp, timestamp)

        resp = self.app.put(
            reverse('NodeAgentHandler'),
            jsonutils.dumps(
                {'mac': node.mac, 'status': 'discover',
                 'manufacturer': 'new'}
            ),
            headers=self.default_headers)
        self.assertEqual(resp.status_code, 200)
        node = self.db.query(Node).get(node.id)
        self.assertNotEqual(node.timestamp, timestamp)
        self.assertEqual('new', node.manufacturer)

    def test_agent_caching(self):
        node = self.env.create_node(api=False)
        resp = self.app.put(
            reverse('NodeAgentHandler'),
            jsonutils.dumps({
                'mac': node.mac,
                'manufacturer': 'new',
                'agent_checksum': 'test'
            }),
            headers=self.default_headers)
        response = resp.json_body
        self.assertEqual(resp.status_code, 200)
        self.assertFalse('cached' in response and response['cached'])
        resp = self.app.put(
            reverse('NodeAgentHandler'),
            jsonutils.dumps({
                'mac': node.mac,
                'manufacturer': 'new',
                'agent_checksum': 'test'
            }),
            headers=self.default_headers)
        response = resp.json_body
        self.assertEqual(resp.status_code, 200)
        self.assertTrue('cached' in response and response['cached'])

    def test_agent_updates_node_by_interfaces(self):
        node = self.env.create_node(api=False)
        interface = node.meta['interfaces'][0]

        resp = self.app.put(
            reverse('NodeAgentHandler'),
            jsonutils.dumps({
                'mac': '00:00:00:00:00:00',
                'meta': {
                    'interfaces': [interface]},
            }),
            headers=self.default_headers)

        self.assertEqual(resp.status_code, 200)

    def test_node_create_ip_not_in_admin_range(self):
        node = self.env.create_node(api=False)

        # Set IP outside of admin network range on eth1
        meta = copy.deepcopy(node.meta)
        meta['interfaces'][1]['ip'] = '10.21.0.3'

        self.app.put(
            reverse('NodeAgentHandler'),
            jsonutils.dumps({
                'mac': node.mac,
                'meta': meta,
            }),
            headers=self.default_headers)

        self.env.network_manager.update_interfaces_info(node)

        # node.mac == eth0 mac so eth0 should now be admin interface
        admin_iface = self.env.network_manager.get_admin_interface(node)

        self.assertEqual(admin_iface.name, 'eth0')

    def test_node_create_ext_mac(self):
        node1 = self.env.create_node(
            api=False
        )
        node2_json = {
            "mac": self.env.generate_random_mac(),
            "meta": self.env.default_metadata(),
            "status": "discover"
        }
        node2_json["meta"]["interfaces"][0]["mac"] = node1.mac
        resp = self.app.post(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps(node2_json),
            headers=self.default_headers,
            expect_errors=True)
        self.assertEqual(resp.status_code, 409)

    def test_node_create_without_mac(self):
        node = self.env.create_node(
            api=True,
            exclude=["mac"],
            expect_http=400,
            expected_error="No mac address specified"
        )
        self.assertEqual(node, None)

    def test_node_create_with_invalid_disk_model(self):
        meta = self.env.default_metadata()
        meta['disks'][0]['model'] = None

        node = self.env.create_node(
            api=True,
            expect_http=201,
            meta=meta
        )
        self.assertIsNotNone(node)

    def test_node_create_mac_validation(self):
        # entry format: (mac_address, http_response_code)
        maccaddresses = (
            # invalid macaddresses
            ('60a44c3528ff', 400),
            ('60:a4:4c:35:28', 400),
            ('60:a4:4c:35:28:fg', 400),
            ('76:DC:7C:CA:G4:75', 400),
            ('76-DC-7C-CA-G4-75', 400),

            # valid macaddresses
            ('60:a4:4c:35:28:ff', 201),
            ('48-2C-6A-1E-59-3D', 201),
        )

        for mac, http_code in maccaddresses:
            response = self.app.post(
                reverse('NodeCollectionHandler'),
                jsonutils.dumps({
                    'mac': mac,
                    'status': 'discover',
                }),
                headers=self.default_headers,
                expect_errors=(http_code != 201)
            )
            self.assertEqual(response.status_code, http_code)

    def test_node_update_ext_mac(self):
        meta = self.env.default_metadata()
        node1 = self.env.create_node(
            api=False,
            mac=meta["interfaces"][0]["mac"],
            meta={}
        )
        node1_json = {
            "mac": self.env.generate_random_mac(),
            "meta": meta
        }
        # We want to be sure that new mac is not equal to old one
        self.assertNotEqual(node1.mac, node1_json["mac"])

        # Here we are trying to update node
        resp = self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([node1_json]),
            headers=self.default_headers,
            expect_errors=True
        )
        self.assertEqual(resp.status_code, 200)

        # Here we are checking if node mac is successfully updated
        self.assertEqual(node1_json["mac"], resp.json_body[0]["mac"])
        self.assertEqual(meta, resp.json_body[0]["meta"])

    def test_duplicated_node_create_fails(self):
        node = self.env.create_node(api=False)
        resp = self.app.post(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps({'mac': node.mac, 'status': 'discover'}),
            headers=self.default_headers,
            expect_errors=True)
        self.assertEqual(409, resp.status_code)

    def test_node_creation_fail(self):
        resp = self.app.post(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps({'mac': self.env.generate_random_mac(),
                             'meta': self.env.default_metadata(),
                             'status': 'error'}),
            headers=self.default_headers,
            expect_errors=True)
        self.assertEqual(resp.status_code, 403)

    def test_reset_cluster_name_when_unassign_node(self):
        node_name = 'new_node_name'
        self.env.create(
            nodes_kwargs=[
                {'pending_roles': ['controller'],
                 'pending_addition': True,
                 'name': node_name}])

        node = self.env.nodes[0]

        resp = self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([{'id': node.id,
                              'cluster_id': None,
                              'pending_roles': []}]),
            headers=self.default_headers)
        self.assertEqual(200, resp.status_code)
        self.assertEqual(1, len(resp.json_body))
        self.assertEqual(node.id, resp.json_body[0]['id'])
        self.assertEqual(node.name, node_name)
        self.assertEqual(node.cluster, None)
        self.assertEqual(node.pending_roles, [])

    def test_discovered_node_unified_name(self):
        node_mac = self.env.generate_random_mac()

        def node_name_test(mac):
            self.env.create_node(
                api=True,
                **{'mac': mac}
            )

            node = self.app.get(reverse('NodeCollectionHandler')).json_body[0]
            self.assertEqual(node['name'],
                             'Untitled ({0})'.format(node_mac[-5:]))

        node_name_test(node_mac.upper())

        node_id = self.app.get(
            reverse('NodeCollectionHandler')
        ).json_body[0]['id']

        self.app.delete(
            reverse('NodeHandler', {'obj_id': node_id})
        )

        node_name_test(node_mac.lower())

    def check_pending_roles(self, to_check, msg):
        node = self.env.create_node(api=False)

        data = {'id': node.id,
                'cluster_id': 1}
        data.update(to_check)

        resp = self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([data]),
            headers=self.default_headers,
            expect_errors=True)

        self.assertEqual(400, resp.status_code)
        self.assertIn(msg, resp.json_body["message"])

    def test_pending_role_non_existing(self):
        cluster = self.env.create()
        self.check_pending_roles({'pending_roles': ['qwe'],
                                  'cluster_id': cluster.id},
                                 'are not valid for node')

    def test_pending_role_duplicates(self):
        self.check_pending_roles({'pending_roles': ['cinder', 'cinder']},
                                 'contains duplicates')

    def test_pending_role_not_list(self):
        self.check_pending_roles({'pending_roles': 'cinder'},
                                 "Failed validating 'type'")

    def test_pending_role_not_strings(self):
        self.check_pending_roles({'pending_roles': ['cinder', 1]},
                                 "Failed validating 'type'")

    def test_role_non_existing(self):
        cluster = self.env.create()
        self.check_pending_roles({'roles': ['qwe'],
                                  'cluster_id': cluster.id},
                                 'are not valid for node')

    def test_role_duplicates(self):
        self.check_pending_roles({'roles': ['cinder', 'cinder']},
                                 'contains duplicates')

    def test_roles_not_list(self):
        self.check_pending_roles({'roles': 'cinder'},
                                 'Failed validating')

    def test_roles_not_strings(self):
        self.check_pending_roles({'roles': ['cinder', 1]},
                                 'Failed validating')

    def check_update_role_no_cluster_id(self, data_to_check):
        self.env.create()

        node = self.env.create_node(api=False)

        data = {'id': node.id}
        data.update(data_to_check)

        resp = self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([data]),
            headers=self.default_headers,
            expect_errors=True)

        self.assertEqual(400, resp.status_code)
        self.assertIn("doesn't belong to any cluster",
                      resp.json_body["message"])

    def test_update_role_no_cluster_id(self):
        self.check_update_role_no_cluster_id({'pending_roles': ['compute']})

    def test_update_pending_role_no_cluster_id(self):
        self.check_update_role_no_cluster_id({'roles': ['compute']})
