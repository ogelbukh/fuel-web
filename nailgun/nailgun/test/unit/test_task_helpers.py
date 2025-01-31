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

import mock
import web

from nailgun import consts
from nailgun.db.sqlalchemy.models import Cluster
from nailgun.db.sqlalchemy.models import Task
from nailgun import objects
from nailgun.orchestrator.deployment_serializers \
    import DeploymentHASerializer
from nailgun.task.helpers import TaskHelper
from nailgun.test.base import BaseTestCase


class TestTaskHelpers(BaseTestCase):

    def create_env(self, nodes):
        cluster = self.env.create(
            nodes_kwargs=nodes)

        cluster_db = self.db.query(Cluster).get(cluster['id'])
        objects.Cluster.prepare_for_deployment(cluster_db)
        self.db.flush()
        return cluster_db

    @property
    def serializer(self):
        return DeploymentHASerializer

    def filter_by_role(self, nodes, role):
        return filter(lambda node: role in node.all_roles, nodes)

    def test_redeploy_all_controller_if_single_controller_failed(self):
        cluster = self.create_env([
            {'roles': ['controller'], 'status': 'error'},
            {'roles': ['controller']},
            {'roles': ['controller', 'cinder']},
            {'roles': ['compute', 'cinder']},
            {'roles': ['compute']},
            {'roles': ['cinder']}])

        nodes = TaskHelper.nodes_to_deploy(cluster)
        self.assertEqual(len(nodes), 3)

        controllers = self.filter_by_role(nodes, 'controller')
        self.assertEqual(len(controllers), 3)

    def test_redeploy_only_compute_cinder(self):
        cluster = self.create_env([
            {'roles': ['controller']},
            {'roles': ['controller']},
            {'roles': ['controller', 'cinder']},
            {'roles': ['compute', 'cinder']},
            {'roles': ['compute'], 'status': 'error'},
            {'roles': ['cinder'], 'status': 'error'}])

        nodes = TaskHelper.nodes_to_deploy(cluster)
        self.assertEqual(len(nodes), 2)

        cinders = self.filter_by_role(nodes, 'cinder')
        self.assertEqual(len(cinders), 1)

        computes = self.filter_by_role(nodes, 'compute')
        self.assertEqual(len(computes), 1)

    def test_redeploy_all_controller_and_compute_cinder(self):
        cluster = self.create_env([
            {'roles': ['controller'], 'status': 'error'},
            {'roles': ['controller']},
            {'roles': ['controller', 'cinder']},
            {'roles': ['compute', 'cinder']},
            {'roles': ['compute'], 'status': 'error'},
            {'roles': ['cinder'], 'status': 'error'}])

        nodes = TaskHelper.nodes_to_deploy(cluster)
        self.assertEqual(len(nodes), 5)

        controllers = self.filter_by_role(nodes, 'controller')
        self.assertEqual(len(controllers), 3)

        cinders = self.filter_by_role(nodes, 'cinder')
        self.assertEqual(len(cinders), 2)

        computes = self.filter_by_role(nodes, 'compute')
        self.assertEqual(len(computes), 1)

    def test_redeploy_with_critial_roles(self):
        cluster = self.create_env([
            {'roles': ['controller'], 'status': 'error'},
            {'roles': ['controller'], 'status': 'provisioned'},
            {'roles': ['controller'], 'status': 'provisioned'},
            {'roles': ['compute', 'cinder'], 'status': 'provisioned'},
            {'roles': ['compute'], 'status': 'provisioned'},
            {'roles': ['cinder'], 'status': 'provisioned'}])

        nodes = TaskHelper.nodes_to_deploy(cluster)
        self.assertEqual(len(nodes), 6)

        controllers = self.filter_by_role(nodes, 'controller')
        self.assertEqual(len(controllers), 3)

        cinders = self.filter_by_role(nodes, 'cinder')
        self.assertEqual(len(cinders), 2)

        computes = self.filter_by_role(nodes, 'compute')
        self.assertEqual(len(computes), 2)

    def test_redeploy_with_stopped_nodes(self):
        cluster = self.create_env([
            {'roles': ['controller'], 'status': 'error'},
            {'roles': ['controller'], 'status': 'stopped'},
            {'roles': ['controller'], 'status': 'stopped'},
            {'roles': ['compute', 'cinder'], 'status': 'stopped'},
            {'roles': ['compute'], 'status': 'error',
             'error_type': 'stop_deployment'},
            {'roles': ['cinder'], 'status': 'error',
             'error_type': 'deploy'}])

        nodes = TaskHelper.nodes_to_deploy(cluster)
        self.assertEqual(len(nodes), 6)

        controllers = self.filter_by_role(nodes, 'controller')
        self.assertEqual(len(controllers), 3)

        cinders = self.filter_by_role(nodes, 'cinder')
        self.assertEqual(len(cinders), 2)

        computes = self.filter_by_role(nodes, 'compute')
        self.assertEqual(len(computes), 2)

    # TODO(aroma): move it to utils testing code
    def test_recalculate_deployment_task_progress(self):
        cluster = self.create_env([
            {'roles': ['controller'],
             'status': 'provisioned',
             'progress': 100},
            {'roles': ['compute'],
             'status': 'deploying',
             'progress': 100},
            {'roles': ['compute'],
             'status': 'ready',
             'progress': 0},
            {'roles': ['compute'],
             'status': 'discover',
             'progress': 0}])

        task = Task(name='deploy', cluster_id=cluster.id)
        self.db.add(task)
        self.db.commit()

        progress = TaskHelper.recalculate_deployment_task_progress(task)
        self.assertEqual(progress, 25)

    # TODO(aroma): move it to utils testing code
    def test_recalculate_provisioning_task_progress(self):
        cluster = self.create_env([
            {'roles': ['controller'],
             'status': 'provisioned',
             'progress': 100},
            {'roles': ['compute'],
             'status': 'provisioning',
             'progress': 0}])

        task = Task(name='provision', cluster_id=cluster.id)
        self.db.add(task)
        self.db.commit()

        progress = TaskHelper.recalculate_provisioning_task_progress(task)
        self.assertEqual(progress, 50)

    def test_get_task_cache(self):
        expected = {"key": "value"}
        task = Task()
        task.cache = expected

        self.db.add(task)
        self.db.flush()

        actual = TaskHelper.get_task_cache(task)
        self.assertDictEqual(expected, actual)

        # NOTE: We need to expire 'cache' attribute because otherwise
        #       the 'task.cache' won't throw 'ObjectDeletedError' and
        #       will be unable to test 'get_task_cache'.
        self.db.expire(task, ['cache'])

        task_from_db = objects.Task.get_by_uuid(task.uuid)
        self.db.delete(task_from_db)
        self.db.flush()

        expected = {}
        actual = TaskHelper.get_task_cache(task)
        self.assertDictEqual(expected, actual)

    def test_prepare_action_log_kwargs_with_web_ctx(self):
        self.env.create(
            nodes_kwargs=[
                {'roles': ['compute'], 'provisioning': True},
            ]
        )
        cluster = self.env.clusters[0]
        task = Task(name='provision', cluster_id=cluster.id)
        self.db.add(task)
        self.db.flush()

        actor_id = 'xx'
        with mock.patch.dict(web.ctx,
                             {'env': {'fuel.action.actor_id': actor_id}}):
            kwargs = TaskHelper.prepare_action_log_kwargs(task)
            self.assertIn('actor_id', kwargs)
            self.assertEqual(actor_id, kwargs['actor_id'])

        with mock.patch.dict(web.ctx, {'env': {}}):
            kwargs = TaskHelper.prepare_action_log_kwargs(task)
            self.assertIn('actor_id', kwargs)
            self.assertIsNone(kwargs['actor_id'])

    def test_prepare_action_log_kwargs_without_web_ctx(self):
        self.env.create(
            nodes_kwargs=[
                {'roles': ['compute'], 'pending_addition': True},
                {'roles': ['controller'], 'pending_addition': True},
            ]
        )
        cluster = self.env.clusters[0]
        deployment_task = Task(name='deployment', cluster_id=cluster.id)
        self.db.add(deployment_task)
        self.db.flush()

        # Checking with task without parent
        kwargs = TaskHelper.prepare_action_log_kwargs(deployment_task)
        self.assertIn('actor_id', kwargs)
        self.assertIsNone(kwargs['actor_id'])

        # Checking with empty actor_id in ActionLog
        al_kwargs = TaskHelper.prepare_action_log_kwargs(deployment_task)
        al = objects.ActionLog.create(al_kwargs)

        check_task = Task(name='check_before_deployment',
                          cluster_id=cluster.id,
                          parent_id=deployment_task.id)
        self.db.add(check_task)
        self.db.flush()

        kwargs = TaskHelper.prepare_action_log_kwargs(check_task)
        self.assertIn('actor_id', kwargs)
        self.assertIsNone(kwargs['actor_id'])

        # Checking with actor_id is not None in ActionLog
        actor_id = 'xx'
        al.actor_id = actor_id
        self.db.flush()

        kwargs = TaskHelper.prepare_action_log_kwargs(check_task)
        self.assertIn('actor_id', kwargs)
        self.assertEqual(actor_id, kwargs['actor_id'])

    def test_nodes_to_deploy_if_lcm(self):
        cluster = self.env.create(
            nodes_kwargs=[
                {'status': consts.NODE_STATUSES.ready},
                {'status': consts.NODE_STATUSES.discover},
                {'status': consts.NODE_STATUSES.provisioning},
                {'status': consts.NODE_STATUSES.provisioned},
                {'status': consts.NODE_STATUSES.deploying},
                {'status': consts.NODE_STATUSES.error,
                 'error_type': consts.NODE_ERRORS.deploy},
                {'status': consts.NODE_STATUSES.error,
                 'error_type': consts.NODE_ERRORS.provision},
                {'status': consts.NODE_STATUSES.stopped},
                {'status': consts.NODE_STATUSES.removing},
                {'status': consts.NODE_STATUSES.ready,
                 'pending_deletion': True},
            ],
            release_kwargs={
                'version': 'mitaka-9.0',
                'operating_system': consts.RELEASE_OS.ubuntu
            }
        )
        nodes_to_deploy = TaskHelper.nodes_to_deploy(cluster)
        self.assertEqual(5, len(nodes_to_deploy))

        expected_status = [
            consts.NODE_STATUSES.provisioned,
            consts.NODE_STATUSES.stopped,
            consts.NODE_STATUSES.ready,
            consts.NODE_STATUSES.error,
            consts.NODE_STATUSES.deploying
        ]
        for node in nodes_to_deploy:
            self.assertIn(node.status, expected_status)
            self.assertIn(node.error_type, [None, consts.NODE_ERRORS.deploy])
            self.assertFalse(node.pending_deletion)
