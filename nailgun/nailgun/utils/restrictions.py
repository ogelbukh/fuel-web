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

"""
Classes for checking data restrictions and limits
"""

from functools import partial
import re
import six

from nailgun import errors
from nailgun.expression import Expression
from nailgun.utils import camel_to_snake_case
from nailgun.utils import compact
from nailgun.utils import flatten


class LimitsMixin(object):
    """Mixin with limits processing functionality"""

    def check_node_limits(self, models, nodes, role,
                          limits, limit_reached=True,
                          limit_types=['min', 'max', 'recommended']):
        """Check nodes limits for current role

        :param models: objects which represent models in restrictions
        :type models: dict
        :param nodes: list of nodes to check limits count for role
        :type nodes: list
        :param role: node role name
        :type role: string
        :param limits: object with min|max|recommended values and overrides
        :type limits: dict
        :param limit_reached: flag to check possibility adding/removing nodes
        :type limit_reached: bool
        :param limit_types: List of possible limit types (min|max|recommended)
        :type limit_types: list
        :returns: dict -- object with bool 'valid' flag and related information
        """
        self.checked_limit_types = {}
        self.models = models
        self.overrides = limits.get('overrides', [])
        self.limit_reached = limit_reached
        self.limit_types = limit_types
        self.limit_values = {
            'max': self._evaluate_expression(
                limits.get('max'), self.models),
            'min': self._evaluate_expression(
                limits.get('min'), self.models),
            'recommended': self._evaluate_expression(
                limits.get('recommended'), self.models)
        }
        self.count = len(filter(
            lambda node: not(node.pending_deletion) and (role in node.roles),
            nodes))

        self.messages = compact(flatten(
            map(self._check_override, self.overrides)))
        self.messages += compact(flatten(
            map(self._check_limit_type, self.limit_types)))
        self.messages = compact(flatten(
            map(self._get_message, limit_types)))
        self.messages = '. '.join(self.messages)

        return {
            'count': self.count,
            'limits': self.limit_values,
            'messages': self.messages,
            'valid': not self.messages
        }

    def _check_limit(self, obj, limit_type):
        """Check limit value with nodes count

        :param obj: limits or overrides item data
        :type obj: dict
        :param limit_type: one of (min|max|recommended) values
        :type limit_type: string
        :returns: dict -- message data in format:
                    {
                        'type': 'min|max|recommended'
                        'value': '1',
                        'message': 'Message for limit'
                    }
        """
        if not obj.get(limit_type):
            return

        if limit_type == 'min':
            compare = lambda a, b: a < b if self.limit_reached else a <= b
        elif limit_type == 'max':
            compare = lambda a, b: a > b if self.limit_reached else a >= b
        else:
            compare = lambda a, b: a < b

        limit_value = int(
            self._evaluate_expression(obj.get(limit_type), self.models))
        self.limit_values[limit_type] = limit_value
        self.checked_limit_types[limit_type] = True
        # TODO(apopovych): write proper default message
        if compare(self.count, limit_value):
            return {
                'type': limit_type,
                'value': limit_value,
                'message': obj.get('message', 'Default message')
            }

    def _check_override(self, override):
        """Check overridden restriction for limit"""
        expression = override.get('condition')
        result = self._evaluate_expression(expression, self.models)
        if result:
            return map(partial(self._check_limit, override), self.limit_types)

    def _check_limit_type(self, limit_type):
        """Check limit types for role

        :param limit_type: one of (min|max|recommended) values
        :type limit_type: string
        """
        if self.checked_limit_types.get(limit_type):
            return

        return self._check_limit(self.limit_values, limit_type)

    def _get_message(self, limit_type):
        """Get proper message if we have more than one

        :param limit_type: one of (min|max|recommended) values
        :type limit_type: string
        :returns: string -- first relevant message
        """
        message = sorted(filter(
            lambda message: message.get('type') == limit_type,
            self.messages), key=lambda message: message.get('value'))

        if limit_type != 'max':
            message = message[::-1]

        if message:
            return message[0].get('message')

    def _evaluate_expression(self, expression, models):
        """Evaluate expression if it exists"""
        if expression:
            return Expression(str(expression), models).evaluate()


class RestrictionBase(object):
    """Mixin with restriction processing functionality"""

    @classmethod
    def check_restrictions(cls, models, restrictions, action=None,
                           strict=True):
        """Check if attribute satisfied restrictions

        :param models: objects which represent models in restrictions
        :type models: dict
        :param restrictions: list of restrictions to check
        :type restrictions: list
        :param action: filtering restrictions by action key
        :type action: string
        :param strict: disallow undefined variables in condition
        :type strict: bool
        :returns: dict -- object with 'result' as list of satisfied
                  restrictions and 'message' as dict
        """
        satisfied = []

        if restrictions:
            expened_restrictions = map(
                cls._expand_restriction, restrictions)
            # Filter by action
            if action:
                filterd_by_action_restrictions = filter(
                    lambda item: item.get('action') == action,
                    expened_restrictions)
            else:
                filterd_by_action_restrictions = expened_restrictions[:]
            # Filter which restriction satisfied condition
            satisfied = filter(
                lambda item: Expression(
                    item.get('condition'), models, strict=strict).evaluate(),
                filterd_by_action_restrictions)

        return {
            'result': satisfied,
            'message': '. '.join(item.get('message') for item in
                                 satisfied if item.get('message'))
        }

    @staticmethod
    def _expand_restriction(restriction):
        """Normalize restrictions into one canonical format

        :param restriction: restriction object
        :type restriction: string|dict
        :returns: dict -- restriction object in canonical format:
                    {
                        'action': 'enable|disable|hide|none'
                        'condition': 'value1 == value2',
                        'message': 'value1 shouldn't equal value2'
                    }
        """
        result = {
            'action': 'disable'
        }

        if isinstance(restriction, six.string_types):
            result['condition'] = restriction
        elif isinstance(restriction, dict):
            if 'condition' in restriction:
                result.update(restriction)
            else:
                result['condition'] = list(restriction)[0]
                result['message'] = list(restriction.values())[0]
        else:
            raise errors.InvalidData('Invalid restriction format')

        return result


class AttributesRestriction(RestrictionBase):

    @staticmethod
    def _group_attribute(data):
        return 'metadata' in data

    @classmethod
    def _get_label(cls, data):
        if cls._group_attribute(data):
            return data['metadata'].get('label')
        return data.get('label')

    @classmethod
    def _get_value(cls, data):
        if cls._group_attribute(data):
            return data['metadata'].get('enabled', True)
        return data.get('value')

    @classmethod
    def _get_restrictions(cls, data):
        if cls._group_attribute(data):
            return data['metadata'].get('restrictions', [])
        return data.get('restrictions', [])

    @classmethod
    def check_data(cls, models, data):
        """Check cluster attributes data

        :param models: objects which represent models in restrictions
        :type models: dict
        :param data: cluster attributes object
        :type data: list|dict
        :returns: list of errors
        """
        def find_errors(data=data):
            """Generator which traverses through cluster attributes tree

            Checks regex for each attribute. Check restrictions for each
            enabled attribute with type 'checkbox'. All hidden attributes
            are skipped as well as disabled group attrbites. Similar
            logic is used on UI
            """
            if isinstance(data, list):
                for item in data:
                    for err in find_errors(item):
                        yield err
                return

            if not isinstance(data, dict):
                return

            group_attribute = cls._group_attribute(data)
            value = cls._get_value(data)
            label = cls._get_label(data)
            restrictions = cls._get_restrictions(data)

            # hidden attributes should be skipped
            if cls.check_restrictions(
                    models, restrictions, action='hide')['result']:
                return

            if group_attribute and not value:
                # disabled group attribute should be skipped as well
                # as all sub-attributes
                return

            attr_type = data.get('type')
            if attr_type in ['text_list', 'textarea_list']:
                err = cls.check_fields_length(data)
                if err is not None:
                    yield err

            regex_error = cls.validate_regex(data)
            if regex_error is not None:
                yield regex_error

            # restrictions with 'disable' action should be checked only for
            # enabled attributes which type is 'checkbox' or group attributes
            if (attr_type == 'checkbox' or group_attribute) and value:
                restr = cls.check_restrictions(
                    models, restrictions, action='disable')

                for item in restr['result']:
                    error = (
                        "restriction with action='{}' and condition="
                        "'{}' failed due to attribute value='{}'"
                        .format(item['action'], item['condition'], value)
                    )
                    if item.get('message'):
                        error = item['message']

                    yield ("Validation failed for attribute '{}':"
                           " {}".format(label, error))

            for key, value in six.iteritems(data):
                if key == 'metadata':
                    continue
                for err in find_errors(value):
                    yield err

        return list(find_errors())

    @staticmethod
    def validate_regex(data):
        attr_regex = data.get('regex', {})
        if attr_regex:
            attr_value = data.get('value')
            pattern = re.compile(attr_regex.get('source'))
            error = attr_regex.get('error')

            def test_regex(value, pattern=pattern, error=error):
                if not pattern.search(value):
                    return error

            if isinstance(attr_value, six.string_types):
                return test_regex(attr_value)
            elif isinstance(attr_value, list):
                errors = map(test_regex, attr_value)
                if compact(errors):
                    return errors
            else:
                return ('Value {0} is of invalid type, cannot check '
                        'regexp'.format(attr_value))

    @staticmethod
    def check_fields_length(data):
        min_items_num = data.get('min')
        max_items_num = data.get('max')
        attr_value = data.get('value')

        if min_items_num is not None and len(attr_value) < min_items_num:
            return ('Value {0} should have at least {1} '
                    'items'.format(attr_value, min_items_num))
        if max_items_num is not None and len(attr_value) > max_items_num:
            return ('Value {0} should not have more than {1} '
                    'items'.format(attr_value, max_items_num))


class VmwareAttributesRestriction(RestrictionBase):

    @classmethod
    def check_data(cls, models, metadata, data):
        """Check cluster vmware attributes data

        :param models: objects which represent models in restrictions
        :type models: dict
        :param metadata: vmware attributes metadata object
        :type metadata: list|dict
        :param data: vmware attributes data(value) object
        :type data: list|dict
        :retruns: func -- generator which produces errors
        """
        root_key = camel_to_snake_case(cls.__name__)

        def find_errors(metadata=metadata, path_key=root_key):
            """Generator for vmware attributes errors

            for each attribute in 'metadata' gets relevant values from vmware
            'value' and checks them with restrictions and regexs
            """
            if isinstance(metadata, dict):
                restr = cls.check_restrictions(
                    models, metadata.get('restrictions', []))
                if restr.get('result'):
                    # TODO(apopovych): handle restriction message?
                    return
                else:
                    for mkey, mvalue in six.iteritems(metadata):
                        if mkey == 'name':
                            value_path = path_key.replace(
                                root_key, '').replace('.fields', '')
                            values = cls._get_values(value_path, data)
                            attr_regex = metadata.get('regex', {})
                            if attr_regex:
                                pattern = re.compile(attr_regex.get('source'))
                                for value in values():
                                    if not pattern.match(value):
                                        yield attr_regex.get('error')
                        for err in find_errors(
                                mvalue, '.'.join([path_key, mkey])):
                            yield err
            elif isinstance(metadata, list):
                for i, item in enumerate(metadata):
                    current_key = item.get('name') or str(i)
                    for err in find_errors(
                            item, '.'.join([path_key, current_key])):
                        yield err

        return list(find_errors())

    @classmethod
    def _get_values(cls, path, data):
        """Generator for all values from data selected by given path

        :param path: path to all releted values
        :type path: string
        :param data: vmware attributes value
        :type data: list|dict
        """
        keys = path.split('.')
        key = keys[-1]

        def find(data=data):
            if isinstance(data, dict):
                for k, v in six.iteritems(data):
                    if k == key:
                        yield v
                    elif k in keys:
                        for result in find(v):
                            yield result
            elif isinstance(data, list):
                for d in data:
                    for result in find(d):
                        yield result

        return find
