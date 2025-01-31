# -*- coding: utf-8 -*-

#    Copyright 2016 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the 'License'); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an 'AS IS' BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import mock

from nailgun import consts
from nailgun.task.legacy_tasks_adapter import adapt_legacy_tasks

from nailgun.test.base import BaseUnitTest


class TestLegacyTasksAdapter(BaseUnitTest):
    @classmethod
    def setUpClass(cls):
        super(TestLegacyTasksAdapter, cls).setUpClass()
        cls.role_resolver = mock.MagicMock()
        cls.role_resolver.get_all_roles.side_effect = lambda x: set(x)

    def test_returns_same_task_if_no_legacy(self):
        tasks = [
            {'id': 'test1', 'version': '2.0.0', 'roles': ['group1'],
             'type': consts.ORCHESTRATOR_TASK_TYPES.puppet,
             'required_for': []},
            {'id': 'group1', 'type': consts.ORCHESTRATOR_TASK_TYPES.group},
            {'id': 'stage1', 'type': consts.ORCHESTRATOR_TASK_TYPES.stage}
        ]

        new_tasks = list(adapt_legacy_tasks(tasks, None, self.role_resolver))
        self.datadiff(tasks, new_tasks, ignore_keys='required_for')
        self.assertEqual([], tasks[0]['required_for'])
        self.assertEqual(
            ['group1_end'], new_tasks[0]['required_for']
        )

    def test_legacy_deployment_task_adaptation(self):
        tasks = [
            {'id': 'task1', 'version': '2.0.0', 'roles': 'group1',
             'type': consts.ORCHESTRATOR_TASK_TYPES.puppet},
            {'id': 'task2', 'roles': ['group2'],
             'type': consts.ORCHESTRATOR_TASK_TYPES.puppet},
            {'id': 'group1', 'roles': ['group1'],
             'type': consts.ORCHESTRATOR_TASK_TYPES.group,
             'requires': ['stage1'], 'required_for': ['group2']},
            {'id': 'group3', 'roles': ['group3'], 'requires': ['group1'],
             'type': consts.ORCHESTRATOR_TASK_TYPES.group, },
            {'id': 'group2', 'type': consts.ORCHESTRATOR_TASK_TYPES.group,
             'roles': ['group2'], 'required_for': ['stage2']},
            {'id': 'stage1', 'type': consts.ORCHESTRATOR_TASK_TYPES.stage},
            {'id': 'stage2', 'type': consts.ORCHESTRATOR_TASK_TYPES.stage}
        ]
        self.role_resolver.get_all_roles.side_effect = lambda x: set(x)
        new_tasks = list(adapt_legacy_tasks(tasks, [], self.role_resolver))

        self.assertEqual(
            {
                'id': 'group1_start',
                'type': consts.ORCHESTRATOR_TASK_TYPES.skipped,
                'version': consts.TASK_CROSS_DEPENDENCY,
                'roles': ['group1'],
                'cross_depends': [],
                'cross_depended_by': [{'name': 'group1_end', 'role': 'self'}]
            },
            next(x for x in new_tasks if x['id'] == 'group1_start')
        )
        self.assertEqual(
            {
                'id': 'group1_end',
                'type': consts.ORCHESTRATOR_TASK_TYPES.skipped,
                'version': consts.TASK_CROSS_DEPENDENCY,
                'roles': ['group1']
            },
            next(x for x in new_tasks if x['id'] == 'group1_end')
        )

        self.assertEqual(
            {
                'id': 'group2_start',
                'type': consts.ORCHESTRATOR_TASK_TYPES.skipped,
                'version': consts.TASK_CROSS_DEPENDENCY,
                'roles': ['group2'],
                'cross_depends': [{'name': 'group1_end', 'role': ['group1']}],
                'cross_depended_by': [{'name': 'group2_end', 'role': 'self'}]
            },
            next(x for x in new_tasks if x['id'] == 'group2_start')
        )
        self.assertEqual(
            {
                'id': 'group2_end',
                'type': consts.ORCHESTRATOR_TASK_TYPES.skipped,
                'version': consts.TASK_CROSS_DEPENDENCY,
                'roles': ['group2']
            },
            next(x for x in new_tasks if x['id'] == 'group2_end')
        )

        self.assertEqual(
            {
                'id': 'group3_start',
                'type': consts.ORCHESTRATOR_TASK_TYPES.skipped,
                'version': consts.TASK_CROSS_DEPENDENCY,
                'roles': ['group3'],
                'cross_depends': [{'name': 'group1_end', 'role': ['group1']}],
                'cross_depended_by': [{'name': 'group3_end', 'role': 'self'}]
            },
            next(x for x in new_tasks if x['id'] == 'group3_start')
        )
        self.assertEqual(
            {
                'id': 'group3_end',
                'type': consts.ORCHESTRATOR_TASK_TYPES.skipped,
                'version': consts.TASK_CROSS_DEPENDENCY,
                'roles': ['group3']
            },
            next(x for x in new_tasks if x['id'] == 'group3_end')
        )
        self.assertEqual(
            {
                'id': 'task2',
                'type': consts.ORCHESTRATOR_TASK_TYPES.puppet,
                'version': '2.0.0',
                'roles': ['group2'],
                'required_for': ['group2_end'],
                'cross_depends': [{'name': 'group2_start', 'role': 'self'}],
            },
            next(x for x in new_tasks if x['id'] == 'task2')
        )

    def test_legacy_plugin_tasks_adaptation(self):
        tasks = [
            {'id': 'task1', 'version': '2.0.0', 'roles': 'group1',
             'type': consts.ORCHESTRATOR_TASK_TYPES.puppet},
            {'id': 'group1', 'roles': ['group1'],
             'type': consts.ORCHESTRATOR_TASK_TYPES.group,
             'requires': ['stage1'], 'required_for': ['stage2']},
            {'id': 'stage1_start',
             'type': consts.ORCHESTRATOR_TASK_TYPES.stage},
            {'id': 'stage1_end', 'requires': 'stage1_start',
             'type': consts.ORCHESTRATOR_TASK_TYPES.stage},
            {'id': 'stage2_start', 'requires': ['stage1_end'],
             'type': consts.ORCHESTRATOR_TASK_TYPES.stage},
            {'id': 'stage2_end', 'requires': ['stage2_start'],
             'type': consts.ORCHESTRATOR_TASK_TYPES.stage},
            {'id': 'stage3_start', 'requires': ['stage2_end'],
             'type': consts.ORCHESTRATOR_TASK_TYPES.stage},
            {'id': 'stage3_end', 'requires': ['stage3_start'],
             'type': consts.ORCHESTRATOR_TASK_TYPES.stage}
        ]

        legacy_plugin_tasks = [
            {
                'roles': '*',
                'stage': 'stage1',
                'type': consts.ORCHESTRATOR_TASK_TYPES.puppet,
                'parameters': {'number': 0}
            },
            {
                'roles': '*',
                'stage': 'stage1/100',
                'type': consts.ORCHESTRATOR_TASK_TYPES.puppet,
                'parameters': {'number': 2}
            },
            {
                'roles': '*',
                'stage': 'stage1/10',
                'type': consts.ORCHESTRATOR_TASK_TYPES.puppet,
                'parameters': {'number': 1}
            },
            {
                'roles': '*',
                'stage': 'stage3/100',
                'type': consts.ORCHESTRATOR_TASK_TYPES.puppet,
                'parameters': {'number': 1}
            },
            {
                'roles': 'group1',
                'stage': 'stage3',
                'type': consts.ORCHESTRATOR_TASK_TYPES.puppet,
                'parameters': {'number': 0}
            }
        ]
        new_tasks = list(adapt_legacy_tasks(
            tasks, legacy_plugin_tasks, self.role_resolver
        ))
        stage1_tasks = new_tasks[-5:-2]
        depends = [{'role': None, 'name': 'stage1_end'}]
        depended_by = [{'role': None, 'name': 'stage2_start'}]
        for idx, task in enumerate(stage1_tasks):
            self.assertEqual(
                {
                    'id': 'stage1_{0}'.format(idx),
                    'type': legacy_plugin_tasks[idx]['type'],
                    'roles': legacy_plugin_tasks[idx]['roles'],
                    'version': consts.TASK_CROSS_DEPENDENCY,
                    'cross_depends': depends,
                    'cross_depended_by': depended_by,
                    'condition': True,
                    'parameters': {'number': idx}
                },
                task
            )
            depends = [{'role': task['roles'], 'name': task['id']}]

        stage3_tasks = new_tasks[-2:]
        depends = [{'role': None, 'name': 'stage3_end'}]
        depended_by = []
        for idx, task in enumerate(stage3_tasks):
            self.assertEqual(
                {
                    'id': 'stage3_{0}'.format(idx),
                    'type': legacy_plugin_tasks[3 + idx]['type'],
                    'roles': legacy_plugin_tasks[3 + idx]['roles'],
                    'version': consts.TASK_CROSS_DEPENDENCY,
                    'cross_depends': depends,
                    'cross_depended_by': depended_by,
                    'condition': True,
                    'parameters': {'number': idx}
                },
                task
            )
            depends = [{'role': task['roles'], 'name': task['id']}]
