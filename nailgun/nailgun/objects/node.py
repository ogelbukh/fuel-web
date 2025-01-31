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

"""
Node-related objects and collections
"""

import collections
import copy
from datetime import datetime
import itertools
import math
import operator
import traceback

from netaddr import IPAddress
from netaddr import IPNetwork
from oslo_serialization import jsonutils
import six
from sqlalchemy.orm import joinedload
from sqlalchemy.orm import subqueryload_all

from nailgun import consts
from nailgun.db import db
from nailgun.db.sqlalchemy import models
from nailgun import errors
from nailgun.extensions import fire_callback_on_node_collection_delete
from nailgun.extensions import fire_callback_on_node_create
from nailgun.extensions import fire_callback_on_node_delete
from nailgun.extensions import fire_callback_on_node_reset
from nailgun.extensions import fire_callback_on_node_update
from nailgun.extensions.network_manager.template import NetworkTemplate
from nailgun.extensions.network_manager import utils as network_utils
from nailgun.logger import logger
from nailgun.objects import Bond
from nailgun.objects import Cluster
from nailgun.objects import IPAddr
from nailgun.objects import NailgunCollection
from nailgun.objects import NailgunObject
from nailgun.objects import NetworkGroup
from nailgun.objects import NetworkGroupCollection
from nailgun.objects import NIC
from nailgun.objects import Notification
from nailgun.objects import Release
from nailgun.objects.serializers.node import NodeSerializer
from nailgun.policy import cpu_distribution
from nailgun.policy import hugepages_distribution
from nailgun.settings import settings
from nailgun import utils


class Node(NailgunObject):
    """Node object"""

    #: SQLAlchemy model for Node
    model = models.Node

    #: Serializer for Node
    serializer = NodeSerializer

    @classmethod
    def delete(cls, instance):
        fire_callback_on_node_delete(instance)
        super(Node, cls).delete(instance)

    @classmethod
    def get_network_ips_count(cls, node_id, network_id):
        """Get number of IPs from specified network assigned to node.

        :param node_id: Node ID
        :param network_id: NetworkGroup ID
        :returns: count of IP addresses
        """
        return db().query(models.IPAddr).filter_by(
            node=node_id, network=network_id).count()

    @classmethod
    def get_networks_ips(cls, instance):
        """Get IPs assigned to node grouped by network group.

        :param node: Node instance
        :returns: query
        """
        return db().query(
            models.NetworkGroup, models.IPAddr.ip_addr
        ).filter(
            models.NetworkGroup.group_id == instance.group_id,
            models.IPAddr.network == models.NetworkGroup.id,
            models.IPAddr.node == instance.id
        )

    @classmethod
    def get_networks_ips_dict(cls, instance):
        """Get IPs assigned to node grouped by network group.

        :param node: Node instance
        :returns: dict mapping network group name to IP addresses
        """
        return {ng.name: ip for ng, ip in cls.get_networks_ips(instance)}

    @classmethod
    def set_networks_ips(cls, instance, ips_by_network_name):
        """Set IPs by provided network-to-IP mapping.

        :param instance: Node instance
        :param ips_by_network_name: dict
        :returns: None
        """
        ngs = db().query(
            models.NetworkGroup.name, models.IPAddr
        ).filter(
            models.NetworkGroup.group_id == instance.group_id,
            models.IPAddr.network == models.NetworkGroup.id,
            models.IPAddr.node == instance.id,
            models.NetworkGroup.name.in_(ips_by_network_name)
        )
        for ng_name, ip_addr in ngs:
            ip_addr.ip_addr = ips_by_network_name[ng_name]
        db().flush()

    @classmethod
    def set_netgroups_ids(cls, instance, netgroups_id_mapping):
        """Set IP network group assignment based on provided mapping.

        :param instance: Node instance
        :param netgroups_id_mapping: dict of network IDs to network IDs
        :returns: None
        """
        ip_addrs = db().query(models.IPAddr).filter(
            models.IPAddr.node == instance.id)
        for ip_addr in ip_addrs:
            ip_addr.network = netgroups_id_mapping[ip_addr.network]

    @classmethod
    def set_nic_assignment_netgroups_ids(cls, instance, netgroups_id_mapping):
        """Set network group mapping for NIC assignments.

        :param instance: Node instance
        :param netgroups_id_mapping: dict of network IDs to network IDs
        :returns: None
        """
        nic_assignments = db.query(models.NetworkNICAssignment).\
            join(models.NodeNICInterface).\
            filter(models.NodeNICInterface.node_id == instance.id)
        for nic_assignment in nic_assignments:
            nic_assignment.network_id = \
                netgroups_id_mapping[nic_assignment.network_id]

    @classmethod
    def clear_bonds(cls, instance):
        instance.bond_interfaces = []

    @classmethod
    def set_bond_assignment_netgroups_ids(cls, instance, netgroups_id_mapping):
        """Set network group mapping for bond assignments.

        :param instance: Node instance
        :param netgroups_id_mapping: dict
        :returns: None
        """
        bond_assignments = db.query(models.NetworkBondAssignment).\
            join(models.NodeBondInterface).\
            filter(models.NodeBondInterface.node_id == instance.id)
        for bond_assignment in bond_assignments:
            bond_assignment.network_id = \
                netgroups_id_mapping[bond_assignment.network_id]

    @classmethod
    def get_interface_by_net_name(cls, instance_id, netname):
        """Get interface with specified network assigned to it.

        This method first checks for a NodeNICInterface with the specified
        network assigned. If that fails it will look for a NodeBondInterface
        with that network assigned.

        :param instance_id: Node ID
        :param netname: NetworkGroup name
        :returns: either NodeNICInterface or NodeBondInterface
        """
        iface = db().query(models.NodeNICInterface).join(
            (models.NetworkGroup,
             models.NodeNICInterface.assigned_networks_list)
        ).filter(
            models.NetworkGroup.name == netname
        ).filter(
            models.NodeNICInterface.node_id == instance_id
        ).first()
        if iface:
            return iface

        return db().query(models.NodeBondInterface).join(
            (models.NetworkGroup,
             models.NodeBondInterface.assigned_networks_list)
        ).filter(
            models.NetworkGroup.name == netname
        ).filter(
            models.NodeBondInterface.node_id == instance_id
        ).first()

    @classmethod
    def get_interface_by_mac_or_name(cls, instance, mac=None, name=None):
        # try to get interface by mac address
        interface = next((
            n for n in instance.nic_interfaces
            if network_utils.is_same_mac(n.mac, mac)), None)

        # try to get interface instance by interface name. this protects
        # us from loosing nodes when some NICs was replaced with a new one
        interface = interface or next((
            n for n in instance.nic_interfaces if n.name == name), None)
        return interface

    @classmethod
    def get_admin_ip(cls, node, admin_net_id=None):
        """Get admin IP for specified node.

        When admin_net_id is None the admin network group for the node's
        nodegroup will be used.

        If all_ips is True it will return all IPs from the admin network
        assigned to that node. Otherwise it just returns a single IP.

        :param node: return admin IP of this node
        :type node: nailgun.db.sqlalchemy.models.Node
        :param admin_net_id: Admin NetworkGroup ID
        :type admin_net_id: int
        :param all_ips:
        :type all_ips: boolean
        :returns: IPAddr instance
        """
        if not admin_net_id:
            admin_net = NetworkGroup.get_admin_network_group(node)
            admin_net_id = admin_net.id

        admin_ip = next((ip for ip in node.ip_addrs
                         if ip.network == admin_net_id), None)

        return getattr(admin_ip, 'ip_addr', None)

    @classmethod
    def get_all_node_ips(cls):
        """Get all Admin IPs assigned to nodes.

        :returns: List of single element tuples with IP address
        """
        return db().query(models.Node.ip)

    @classmethod
    def get_by_mac_or_uid(cls, mac=None, node_uid=None):
        """Get Node instance by MAC or ID.

        :param mac: MAC address as string
        :param node_uid: Node ID
        :returns: Node instance
        """
        node = None
        if not mac and not node_uid:
            return node

        q = db().query(cls.model)
        if mac:
            node = q.filter_by(mac=mac.lower()).first()
        else:
            node = q.get(node_uid)
        return node

    @classmethod
    def get_by_hostname(cls, hostname, cluster_id):
        """Get Node instance by hostname.

        :param hostname: hostname as string
        :param cluster_id: Node will be searched \
            only within the cluster with this ID.
        :returns: Node instance
        """

        if not hostname:
            return None

        q = db().query(cls.model).filter_by(
            hostname=hostname, cluster_id=cluster_id)
        return q.first()

    @classmethod
    def get_by_meta(cls, meta):
        """Search for instance using mac, node id or interfaces.

        :param meta: dict with nodes metadata
        :returns: Node instance
        """
        node = cls.get_by_mac_or_uid(
            mac=meta.get('mac'), node_uid=meta.get('id'))

        if not node:
            can_search_by_ifaces = all([
                meta.get('meta'), meta['meta'].get('interfaces')])

            if can_search_by_ifaces:
                node = cls.search_by_interfaces(meta['meta']['interfaces'])

        return node

    @classmethod
    def search_by_interfaces(cls, interfaces):
        """Search for instance using MACs on interfaces.

        :param interfaces: dict of Node interfaces
        :returns: Node instance
        """
        return db().query(cls.model).join(
            models.NodeNICInterface,
            cls.model.nic_interfaces
        ).filter(
            models.NodeNICInterface.mac.in_(
                [n["mac"].lower() for n in interfaces]
            )
        ).first()

    @classmethod
    def should_have_public_with_ip(cls, instance, roles_metadata=None):
        """Returns True if node should have IP belonging to Public network.

        :param instance: Node DB instance
        :returns: True when node has Public network
        """
        if Cluster.should_assign_public_to_all_nodes(instance.cluster):
            return True

        roles = itertools.chain(instance.roles, instance.pending_roles)
        if not roles_metadata:
            roles_metadata = Cluster.get_roles(instance.cluster)

        for role in roles:
            if roles_metadata.get(role, {}).get('public_ip_required'):
                return True

        return False

    @classmethod
    def should_have_public(cls, instance, roles_metadata=None):
        """Determine whether this node should be connected to Public network,

        no matter with or without an IP address assigned from that network

        For example Neutron DVR does require Public network access on compute
        nodes, but does not require IP address assigned to external bridge.

        :param instance: Node DB instance
        :returns: True when node has Public network
        """
        roles_metadata = roles_metadata or Cluster.get_roles(instance.cluster)
        if cls.should_have_public_with_ip(instance, roles_metadata):
            return True

        dvr_enabled = Cluster.neutron_dvr_enabled(instance.cluster)
        if dvr_enabled:
            roles = itertools.chain(instance.roles, instance.pending_roles)

            for role in roles:
                if roles_metadata.get(role, {}).get('public_for_dvr_required'):
                    return True

        return False

    @classmethod
    def assign_public_network(cls, node):
        public_net = next(NetworkGroupCollection.filter_by(
            node.nodegroup.networks,
            name=consts.NETWORKS.public), None)
        cls.assign_network_to_interface(node, public_net)

    @staticmethod
    def get_interfaces_without_bonds_slaves(node):
        ifaces = set(node.interfaces)
        for bond in node.bond_interfaces:
            ifaces ^= set(bond.slaves)
        return sorted(ifaces, key=operator.attrgetter('name'))

    @classmethod
    def assign_network_to_interface(cls, node, network):
        """Assign network to interface by default for single node

        Assign given network to first available interface.
        Checks interface type, if network is already assigned
        and already assigned networks.
        """
        if network is None:
            return
        untagged = NetworkGroup.is_untagged(network)
        dedicated = network.meta.get('dedicated_nic')
        ifaces = cls.get_interfaces_without_bonds_slaves(node)
        for iface in ifaces:
            if dedicated and iface.assigned_networks_list:
                continue
            for net in iface.assigned_networks_list:
                if net.meta.get('dedicated_nic'):
                    break
                if net == network:
                    return
                if untagged and NetworkGroup.is_untagged(net):
                    break
            else:
                assigned_nets = iface.assigned_networks_list + [network]
                NIC.assign_networks(iface, assigned_nets)
                break
        else:
            logger.warning(
                "Cannot assign network %r appropriately for "
                "node %r. Set unassigned network to the "
                "interface %r",
                network.name, node.name, ifaces[0].name
            )
            assigned_nets = ifaces[0].assigned_networks_list + [network]
            NIC.assign_networks(ifaces[0], assigned_nets)

    @classmethod
    def unassign_public_network(cls, node):
        public_net = next(NetworkGroupCollection.filter_by(
            node.nodegroup.networks,
            name=consts.NETWORKS.public), None)
        ifaces = cls.get_interfaces_without_bonds_slaves(node)
        for iface in ifaces:
            network_list = iface.assigned_networks_list
            if public_net in network_list:
                network_list.remove(public_net)
                NIC.assign_networks(iface, network_list)

    @classmethod
    def create(cls, data):
        """Create Node instance with specified parameters in DB.

        This includes:

        * generating its name by MAC (if name is not specified in data)
        * adding node to Cluster (if cluster_id is not None in data) \
        (see :func:`add_into_cluster`) with specified roles \
        (see :func:`update_roles` and :func:`update_pending_roles`)
        * creating interfaces for Node in DB (see :func:`update_interfaces`)
        * creating default Node attributes (see :func:`create_attributes`)
        * creating Notification about newly discovered Node \
        (see :func:`create_discover_notification`)

        :param data: dictionary of key-value pairs as object fields
        :returns: Node instance
        """
        if "name" not in data:
            data["name"] = "Untitled ({0})".format(
                data['mac'][-5:].lower()
            )
        data["timestamp"] = datetime.now()
        data.pop("id", None)

        # TODO(enchantner): fix this temporary hack in clients
        if "cluster_id" not in data and "cluster" in data:
            cluster_id = data.pop("cluster", None)
            data["cluster_id"] = cluster_id

        roles = data.pop("roles", None)
        pending_roles = data.pop("pending_roles", None)
        primary_roles = data.pop("primary_roles", None)

        new_node_meta = data.pop("meta", {})
        new_node_cluster_id = data.pop("cluster_id", None)
        new_node = super(Node, cls).create(data)
        new_node.create_meta(new_node_meta)

        if 'hostname' not in data:
            new_node.hostname = \
                cls.get_unique_hostname(new_node, new_node_cluster_id)
        db().flush()

        # Add interfaces for node from 'meta'.
        if new_node.meta and new_node.meta.get('interfaces'):
            cls.update_interfaces(new_node)
            cls.update_interfaces_offloading_modes(new_node)

        # role cannot be assigned if cluster_id is not set
        if new_node_cluster_id:
            new_node.cluster_id = new_node_cluster_id
        # updating roles
        if roles is not None:
            cls.update_roles(new_node, roles)
        if pending_roles is not None:
            cls.update_pending_roles(new_node, pending_roles)
        if primary_roles is not None:
            cls.update_primary_roles(new_node, primary_roles)

        # adding node into cluster
        if new_node_cluster_id:
            cls.add_into_cluster(new_node, new_node_cluster_id)

        # creating attributes
        cls.create_discover_notification(new_node)

        if new_node.ip:
            cls.check_ip_belongs_to_any_admin_network(new_node)

        fire_callback_on_node_create(new_node)

        return new_node

    @classmethod
    def set_error_status_and_file_notification(cls, instance, etype, emessage):
        instance.status = consts.NODE_STATUSES.error
        instance.error_type = etype
        instance.error_msg = emessage
        db().flush()
        Notification.create({
            "topic": consts.NOTIFICATION_TOPICS.error,
            "message": instance.error_msg,
            "node_id": instance.id
        })

    @classmethod
    def check_ip_belongs_to_any_admin_network(cls, instance, new_ip=None):
        """Checks that node's IP belongs to any of Admin networks IP ranges.

        Node can be inside or out of a cluster. Set node to error and file a
        notification if node's IP does not belong to any of Admin networks.

        :param instance: node instance
        :param new_ip:   new IP for a node (got from Nailgun agent)
        :return:         True if IP belongs to any of Admin networks
        """
        ip = new_ip or instance.ip
        admin_ranges_db = db().query(
            models.IPAddrRange.first,
            models.IPAddrRange.last
        ).join(
            models.NetworkGroup
        ).filter(
            models.NetworkGroup.name == consts.NETWORKS.fuelweb_admin
        )
        nm = Cluster.get_network_manager(instance.cluster)
        match = nm.check_ips_belong_to_ranges([ip], admin_ranges_db)
        if not match:
            cls.set_error_status_and_file_notification(
                instance,
                consts.NODE_ERRORS.discover,
                "Node '{0}' has IP '{1}' that does not match any Admin "
                "network".format(instance.hostname, ip)
            )
        return match

    @classmethod
    def check_ip_belongs_to_own_admin_network(cls, instance, new_ip=None):
        """Checks that node's IP belongs to node's Admin network IP ranges.

        Node should be inside a cluster. Set node to error and file a
        notification if node's IP does not belong to its Admin network.

        :param instance: node instance
        :param new_ip:   new IP for a node (got from Nailgun agent)
        :return:         True if IP belongs to node's Admin network
        """
        ip = new_ip or instance.ip
        nm = Cluster.get_network_manager(instance.cluster)
        admin_ng = NetworkGroup.get_admin_network_group(instance)
        match = nm.is_same_network(ip, admin_ng.cidr)
        if not match:
            cls.set_error_status_and_file_notification(
                instance,
                consts.NODE_ERRORS.discover,
                "Node '{0}' has IP '{1}' that does not match its own Admin "
                "network '{2}'".format(instance.hostname, ip, admin_ng.cidr)
            )
        return match

    @classmethod
    def assign_group(cls, instance):
        if instance.group_id is None and instance.ip:
            admin_ngs = db().query(models.NetworkGroup).filter_by(
                name="fuelweb_admin")
            ip = IPAddress(instance.ip)

            for ng in admin_ngs:
                if ip in IPNetwork(ng.cidr):
                    instance.group_id = ng.group_id
                    break

        if not instance.group_id:
            instance.group_id = Cluster.get_default_group(instance.cluster).id

        db().add(instance)
        db().flush()

    @classmethod
    def is_interfaces_configuration_locked(cls, instance):
        """Returns true if update of network configuration is not allowed.

        It is not allowed during provision/deployment, after
        successful deployment and during node removal.
        """
        return instance.status not in (
            consts.NODE_STATUSES.discover,
            consts.NODE_STATUSES.error,
        )

    @classmethod
    def hardware_info_locked(cls, instance):
        """Returns true if update of hardware information is not allowed.

        It is not allowed during provision/deployment, after
        successful provision/deployment and during node removal.
        """
        return instance.status not in (
            consts.NODE_STATUSES.discover,
            consts.NODE_STATUSES.error,
        )

    @classmethod
    def update_interfaces(cls, instance):
        """Update interfaces for Node instance using Cluster

        network manager (see :func:`get_network_manager`)

        :param instance: Node instance
        :returns: None
        """
        try:
            network_manager = Cluster.get_network_manager(instance.cluster)
            network_manager.update_interfaces_info(instance)

            db().refresh(instance)
        except errors.InvalidInterfacesInfo as exc:
            logger.warning(
                "Failed to update interfaces for node '%s' - invalid info "
                "in meta: %s", instance.human_readable_name, exc.message
            )
            logger.warning(traceback.format_exc())

    @classmethod
    def create_discover_notification(cls, instance):
        """Create notification about discovering new Node.

        :param instance: Node instance
        :returns: None
        """
        try:
            # we use multiplier of 1024 because there are no problems here
            # with unfair size calculation
            ram = str(round(float(
                instance.meta['memory']['total']) / 1073741824, 1)) + " GB RAM"
        except Exception:
            logger.warning(traceback.format_exc())
            ram = "unknown RAM"

        try:
            # we use multiplier of 1000 because disk vendors specify HDD size
            # in terms of decimal capacity. Sources:
            # http://knowledge.seagate.com/articles/en_US/FAQ/172191en
            # http://physics.nist.gov/cuu/Units/binary.html
            hd_size = round(
                float(
                    sum(
                        [d["size"] for d in instance.meta["disks"]]
                    ) / 1000000000
                ),
                1
            )
            # if HDD > 100 GB we show it's size in TB
            if hd_size > 100:
                hd_size = str(hd_size / 1000) + " TB HDD"
            else:
                hd_size = str(hd_size) + " GB HDD"
        except Exception:
            logger.warning(traceback.format_exc())
            hd_size = "unknown HDD"

        cores = str(instance.meta.get('cpu', {}).get('total', "unknown"))

        Notification.create({
            "topic": "discover",
            "message": u"New node is discovered: "
                       u"{0} CPUs / {1} / {2} ".format(cores, ram, hd_size),
            "node_id": instance.id
        })

    @classmethod
    def update(cls, instance, data):
        """Update Node instance with specified parameters in DB.

        This includes:

        * adding node to Cluster (if cluster_id is not None in data) \
        (see :func:`add_into_cluster`)
        * updating roles for Node if it belongs to Cluster \
        (see :func:`update_roles` and :func:`update_pending_roles`)
        * removing node from Cluster (if cluster_id is None in data) \
        (see :func:`remove_from_cluster`)
        * updating interfaces for Node in DB (see :func:`update_interfaces`)
        * creating default Node attributes (see :func:`create_attributes`)

        :param data: dictionary of key-value pairs as object fields
        :returns: Node instance
        """
        data.pop("id", None)
        data.pop("network_data", None)

        roles = data.pop("roles", None)
        pending_roles = data.pop("pending_roles", None)
        new_meta = data.pop("meta", None)

        disks_changed = None
        if new_meta and "disks" in new_meta and "disks" in instance.meta:
            key = operator.itemgetter("name")

            new_disks = sorted(new_meta["disks"], key=key)
            old_disks = sorted(instance.meta["disks"], key=key)

            disks_changed = (new_disks != old_disks)

        # TODO(enchantner): fix this temporary hack in clients
        if "cluster_id" not in data and "cluster" in data:
            cluster_id = data.pop("cluster", None)
            data["cluster_id"] = cluster_id

        if new_meta:
            instance.update_meta(new_meta)
            # The call to update_interfaces will execute a select query for
            # the current instance. This appears to overwrite the object in the
            # current session and we lose the meta changes.
            db().flush()
            if cls.is_interfaces_configuration_locked(instance):
                logger.debug("Interfaces are locked for update on node %s",
                             instance.human_readable_name)
            else:
                instance.ip = data.pop("ip", None) or instance.ip
                instance.mac = data.pop("mac", None) or instance.mac
                db().flush()
                cls.update_interfaces(instance)
                cls.update_interfaces_offloading_modes(
                    instance,
                    bool(data.pop('is_agent', None)))

        cluster_changed = False
        add_to_cluster = False
        if "cluster_id" in data:
            new_cluster_id = data.pop("cluster_id")
            if instance.cluster_id:
                if new_cluster_id is None:
                    # removing node from cluster
                    cluster_changed = True
                    cls.remove_from_cluster(instance)
                elif new_cluster_id != instance.cluster_id:
                    # changing node cluster to another
                    # (is currently not allowed)
                    raise errors.CannotUpdate(
                        u"Changing cluster on the fly is not allowed"
                    )
            else:
                if new_cluster_id is not None:
                    # assigning node to cluster
                    cluster_changed = True
                    add_to_cluster = True
                    instance.cluster_id = new_cluster_id

        if "group_id" in data:
            new_group_id = data.pop("group_id")
            if instance.group_id != new_group_id:
                nm = Cluster.get_network_manager(instance.cluster)
                nm.clear_assigned_networks(instance)
                nm.clear_bond_configuration(instance)
            instance.group_id = new_group_id
            add_to_cluster = True

        # calculating flags
        roles_changed = (
            roles is not None and set(roles) != set(instance.roles)
        )
        pending_roles_changed = (
            pending_roles is not None and
            set(pending_roles) != set(instance.pending_roles)
        )

        super(Node, cls).update(instance, data)

        # we need to update relationships, if fk's are changed
        db.refresh(instance)

        if roles_changed:
            cls.update_roles(instance, roles)
        if pending_roles_changed:
            cls.update_pending_roles(instance, pending_roles)

        if add_to_cluster:
            cls.add_into_cluster(instance, instance.cluster_id)

        if any((
            roles_changed,
            pending_roles_changed,
            cluster_changed,
            disks_changed,
        )) and instance.status not in (
            consts.NODE_STATUSES.provisioning,
            consts.NODE_STATUSES.deploying
        ):
            # TODO(eli): we somehow should move this
            # condition into extension, in order to do
            # that probably we will have to create separate
            # table to keep disks which were used to create
            # volumes mapping.
            # Should be solved as a part of blueprint
            # https://blueprints.launchpad.net/fuel/+spec
            #                                 /volume-manager-refactoring
            fire_callback_on_node_update(instance)

        return instance

    @classmethod
    def reset_to_discover(cls, instance):
        """Flush database objects which is not consistent with actual node

        configuration in the event of resetting node to discover state

        :param instance: Node database object
        :returns: None
        """
        node_data = {
            "status": consts.NODE_STATUSES.discover,
            "pending_addition": True,
            "pending_deletion": False,
        }
        cls.update(instance, node_data)
        cls.move_roles_to_pending_roles(instance)
        # when node reseted to discover:
        # - cobbler system is deleted
        # - mac to ip mapping from dnsmasq.conf is deleted
        # imho we need to revert node to original state, as it was
        # added to cluster (without any additonal state in database)
        IPAddr.delete_by_node(instance.id)
        fire_callback_on_node_reset(instance)
        db().flush()

    @classmethod
    def update_cluster_assignment(cls, instance, cluster,
                                  roles, pending_roles):
        """Update assignment of the node to the other cluster.

        This method primarily used by the cluster_upgrade extension for
        reassigning of a node. Be careful to use it outside of this extension
        because node still plugged to networks of a previous cluster.

        :param instance: An instance of :class:`Node`.
        :param cluster: An instance of :class:`Cluster`.
        :param roles: A list of roles to be assigned to the node.
        :param pending_roles: A list of pending roles to be assigned to
                              the node.
        """
        instance.cluster_id = cluster.id
        instance.group_id = None
        instance.kernel_params = None
        cls.update_roles(instance, roles)
        cls.update_pending_roles(instance, pending_roles)
        cls.remove_replaced_params(instance)
        cls.assign_group(instance)
        cls.set_network_template(instance)
        cls.set_default_attributes(instance)
        db().flush()

    @classmethod
    def update_by_agent(cls, instance, data):
        """Update Node instance with some specific cases for agent.

        * don't update provisioning or error state back to discover
        * don't update volume information if disks arrays is empty

        :param data: dictionary of key-value pairs as object fields
        :returns: Node instance
        """
        # don't update provisioning and error back to discover
        data_status = data.get('status')
        if instance.status in ('provisioning', 'error'):
            if data.get('status', 'discover') == 'discover':
                logger.debug(
                    u"Node {0} has provisioning or error status - "
                    u"status not updated by agent".format(
                        instance.human_readable_name
                    )
                )

                data.pop('status', None)

        meta = data.get('meta', {})
        # don't update volume information, if agent has sent an empty array
        if len(meta.get('disks', [])) == 0 and instance.meta.get('disks'):

            logger.warning(
                u'Node {0} has received an empty disks array - '
                u'volume information will not be updated'.format(
                    instance.human_readable_name
                )
            )
            meta['disks'] = instance.meta['disks']

        # don't update volume information, if it is locked by node status
        if 'disks' in meta and cls.hardware_info_locked(instance):
            logger.debug("Volume information is locked for update on node %s",
                         instance.human_readable_name)
            meta['disks'] = instance.meta['disks']

        if not cls.is_interfaces_configuration_locked(instance) \
                and data.get('ip'):
            if instance.cluster_id:
                update_status = cls.check_ip_belongs_to_own_admin_network(
                    instance, data['ip'])
            else:
                update_status = cls.check_ip_belongs_to_any_admin_network(
                    instance, data['ip'])
            if update_status:
                if instance.status == consts.NODE_STATUSES.error and \
                        instance.error_type == consts.NODE_ERRORS.discover:
                    # accept the status from agent if the node had wrong IP
                    # previously
                    if data_status:
                        instance.status = data_status
                    else:
                        instance.status = consts.NODE_STATUSES.discover
            else:
                data.pop('status', None)
        return cls.update(instance, data)

    @classmethod
    def update_interfaces_offloading_modes(cls, instance, keep_states=False):
        """Update information about offloading modes for node interfaces.

        :param instance: Node object
        :param keep_states: If True, information about available modes will be
               updated, but states configured by user will not be overwritten.
        """
        for interface in instance.meta["interfaces"]:
            new_offloading_modes = interface.get('offloading_modes')
            if new_offloading_modes:
                NIC.update_offloading_modes(
                    cls.get_interface_by_mac_or_name(
                        instance,
                        interface['mac'],
                        interface['name']
                    ),
                    new_offloading_modes,
                    keep_states
                )

    @classmethod
    def update_roles(cls, instance, new_roles):
        """Update roles for Node instance.

        Logs an error if node doesn't belong to Cluster

        :param instance: Node instance
        :param new_roles: list of new role names
        :returns: None
        """
        if not instance.cluster_id:
            logger.warning(
                u"Attempting to assign roles to node "
                u"'{0}' which isn't added to cluster".format(
                    instance.full_name))
            return

        logger.debug(
            u"Updating roles for node {0}: {1}".format(
                instance.full_name,
                new_roles))

        instance.roles = new_roles
        db().flush()

    @classmethod
    def update_pending_roles(cls, instance, new_pending_roles):
        """Update pending_roles for Node instance.

        Logs an error if node doesn't belong to Cluster

        :param instance: Node instance
        :param new_pending_roles: list of new pending role names
        :returns: None
        """
        if not instance.cluster_id:
            logger.warning(
                u"Attempting to assign pending roles to node "
                u"'{0}' which isn't added to cluster".format(
                    instance.full_name))
            return

        logger.debug(
            u"Updating pending roles for node {0}: {1}".format(
                instance.full_name,
                new_pending_roles))

        if new_pending_roles == []:
            # TODO(enchantner): research why the hell we need this
            Cluster.clear_pending_changes(
                instance.cluster,
                node_id=instance.id
            )

        instance.pending_roles = new_pending_roles
        db().flush()

    @classmethod
    def update_primary_roles(cls, instance, new_primary_roles):
        """Update primary_roles for Node instance.

        Logs an error if node doesn't belong to Cluster

        :param instance: Node instance
        :param new_primary_roles: list of new pending role names
        :returns: None
        """
        if not instance.cluster_id:
            logger.warning(
                u"Attempting to assign pending roles to node "
                u"'{0}' which isn't added to cluster".format(
                    instance.full_name))
            return

        assigned_roles = set(instance.roles + instance.pending_roles)
        for role in new_primary_roles:
            if role not in assigned_roles:
                logger.warning(
                    u"Could not mark node {0} as primary for {1} role, "
                    u"because there's no assigned {1} role.".format(
                        instance.full_name, role)
                )
                return

        logger.debug(
            u"Updating primary roles for node {0}: {1}".format(
                instance.full_name,
                new_primary_roles))

        instance.primary_roles = new_primary_roles
        db().flush()

    @classmethod
    def add_into_cluster(cls, instance, cluster_id):
        """Adds Node to Cluster by its ID.

        Also assigns networks by default for Node.

        :param instance: Node instance
        :param cluster_id: Cluster ID
        :returns: None
        """
        instance.cluster_id = cluster_id

        cls.assign_group(instance)
        network_manager = Cluster.get_network_manager(instance.cluster)
        network_manager.assign_networks_by_default(instance)
        cls.refresh_dpdk_properties(instance)
        cls.add_pending_change(instance, consts.CLUSTER_CHANGES.interfaces)
        cls.set_network_template(instance)
        cls.set_default_attributes(instance)

    @classmethod
    def set_network_template(cls, instance):
        template = instance.cluster.network_config.configuration_template
        cls.apply_network_template(instance, template)
        db().flush()

    @classmethod
    def add_pending_change(cls, instance, change):
        """Add pending change into Cluster.

        :param instance: Node instance
        :param change: string value of cluster change
        :returns: None
        """
        if instance.cluster:
            Cluster.add_pending_changes(
                instance.cluster, change, node_id=instance.id
            )

    @classmethod
    def get_admin_physical_iface(cls, instance):
        """Returns node's physical iface.

        In case if we have bonded admin iface, first
        of the bonded ifaces will be returned

        :param instance: Node instance
        :returns: interface instance
        """
        admin_iface = Cluster.get_network_manager(instance.cluster) \
            .get_admin_interface(instance)

        if admin_iface.type != consts.NETWORK_INTERFACE_TYPES.bond:
            return admin_iface

        for slave in admin_iface.slaves:
            if slave.pxe or slave.mac == instance.mac:
                return slave

        return admin_iface.slaves[-1]

    @classmethod
    def remove_from_cluster(cls, instance):
        """Remove Node from Cluster.

        Also drops networks assignment for Node and clears both
        roles and pending roles

        :param instance: Node instance
        :returns: None
        """
        if instance.cluster:
            Cluster.clear_pending_changes(
                instance.cluster,
                node_id=instance.id
            )
            netmanager = Cluster.get_network_manager(
                instance.cluster
            )
            netmanager.clear_assigned_networks(instance)
            netmanager.clear_bond_configuration(instance)
        cls.update_roles(instance, [])
        cls.update_pending_roles(instance, [])
        cls.remove_replaced_params(instance)
        instance.cluster_id = None
        instance.group_id = None
        instance.kernel_params = None
        instance.primary_roles = []
        instance.hostname = cls.default_slave_name(instance)
        instance.attributes = {}

        from nailgun.objects import OpenstackConfig
        OpenstackConfig.disable_by_nodes([instance])

        db().flush()
        db().refresh(instance)

    @classmethod
    def move_roles_to_pending_roles(cls, instance):
        """Move roles to pending_roles"""
        instance.pending_roles = instance.pending_roles + instance.roles
        instance.roles = []
        instance.primary_roles = []
        db().flush()

    @classmethod
    def get_slave_name(cls, instance):
        if not instance.hostname:
            return cls.default_slave_name(instance)
        return instance.hostname

    @classmethod
    def default_slave_name(cls, instance):
        return cls.permanent_id(instance)

    @classmethod
    def permanent_id(cls, instance):
        return u"node-{node_id}".format(node_id=instance.id)

    @classmethod
    def generate_fqdn_by_hostname(cls, hostname):
        return u"{instance_name}.{dns_domain}" \
            .format(instance_name=hostname,
                    dns_domain=settings.DNS_DOMAIN)

    @classmethod
    def get_node_fqdn(cls, instance):
        return cls.generate_fqdn_by_hostname(instance.hostname)

    @classmethod
    def get_kernel_params(cls, instance):
        """Get kernel params.

        Assemble kernel_params if they weren't replaced by custom params.
        """

        if instance.kernel_params:
            return instance.kernel_params

        kernel_params = Cluster.get_default_kernel_params(instance.cluster)

        # Add intel_iommu=on amd_iommu=on if SR-IOV is enabled on node
        for nic in instance.nic_interfaces:
            if NIC.is_sriov_enabled(nic):
                if 'intel_iommu=' not in kernel_params:
                    kernel_params += ' intel_iommu=on'
                if 'amd_iommu=' not in kernel_params:
                    kernel_params += ' amd_iommu=on'
                break

        if 'hugepages' not in kernel_params:
            kernel_params += NodeAttributes.hugepages_kernel_opts(instance)

        isolated_cpus = NodeAttributes.distribute_node_cpus(
            instance)['isolated_cpus']
        if isolated_cpus and 'isolcpus' not in kernel_params:
            kernel_params += " isolcpus={0}".format(
                ",".join(six.moves.map(str, isolated_cpus)))

        return kernel_params

    @classmethod
    def remove_replaced_params(cls, instance):
        instance.replaced_deployment_info = []
        instance.replaced_provisioning_info = {}
        instance.network_template = None

    @classmethod
    def all_roles(cls, instance):
        roles = set(instance.roles + instance.pending_roles)
        roles -= set(instance.primary_roles)

        primary_roles = set([
            'primary-{0}'.format(role) for role in instance.primary_roles])

        return sorted(roles | primary_roles)

    @classmethod
    def apply_network_template(cls, instance, template):
        if template is None:
            instance.network_template = None
            return

        template_body = template['adv_net_template']
        # Get the correct nic_mapping for this node so we can
        # dynamically replace any interface references in any
        # template for this node.
        from nailgun.objects import NodeGroup
        node_group = NodeGroup.get_by_uid(instance.group_id).name
        if node_group not in template_body:
            node_group = 'default'

        node_name = cls.get_slave_name(instance)
        nic_mapping = template_body[node_group]['nic_mapping']
        if node_name not in nic_mapping:
            node_name = 'default'

        nic_mapping = nic_mapping[node_name]

        # Replace interface references and re-parse JSON
        template_object = NetworkTemplate(jsonutils.dumps(template_body))
        node_template = template_object.safe_substitute(nic_mapping)
        parsed_template = jsonutils.loads(node_template)

        output = parsed_template[node_group]
        output['templates'] = output.pop('network_scheme')
        output['roles'] = {}
        output['endpoints'] = {}
        for v in output['templates'].values():
            for endpoint in v['endpoints']:
                output['endpoints'][endpoint] = {}
            for role, ep in v['roles'].items():
                output['roles'][role] = ep

        instance.network_template = output
        db().flush()

        if instance.cluster:
            nm = Cluster.get_network_manager(instance.cluster)
            nm.assign_networks_by_template(instance)

    @classmethod
    def get_unique_hostname(cls, node, cluster_id):
        """Generate default hostname.

        Hostname is 'node-{id}' if it's not used or 'node-{uuid} otherwise.
        It's needed for case when user have manually renamed any another node
        to 'node-{id}'.
        """
        hostname = cls.get_slave_name(node)
        if cls.get_by_hostname(hostname, cluster_id):
            hostname = 'node-{0}'.format(node.uuid)
        return hostname

    @classmethod
    def get_nic_by_name(cls, instance, iface_name):
        nic = db().query(models.NodeNICInterface).filter_by(
            name=iface_name
        ).filter_by(
            node_id=instance.id
        ).first()

        return nic

    @classmethod
    def reset_vms_created_state(cls, node):
        if consts.VIRTUAL_NODE_TYPES.virt not in node.all_roles:
            return

        for vm in node.vms_conf:
            vm['created'] = False
        node.vms_conf.changed()

    @classmethod
    def set_default_attributes(cls, instance):
        if not instance.cluster_id:
            logger.warning(
                u"Attempting to update attributes of node "
                u"'{0}' which isn't added to any cluster".format(
                    instance.full_name))
            return

        instance.attributes = copy.deepcopy(
            instance.cluster.release.node_attributes)
        NodeAttributes.set_default_hugepages(instance)

    @classmethod
    def dpdk_enabled(cls, instance):
        for iface in instance.nic_interfaces:
            if NIC.dpdk_enabled(iface):
                return True
        for iface in instance.bond_interfaces:
            if Bond.dpdk_enabled(iface):
                return True
        return False

    @classmethod
    def sriov_enabled(cls, instance):
        for iface in instance.nic_interfaces:
            if NIC.is_sriov_enabled(iface):
                return True
        return False

    @classmethod
    def get_attributes(cls, instance):
        return copy.deepcopy(instance.attributes)

    @classmethod
    def update_attributes(cls, instance, attrs):
        instance.attributes = utils.dict_merge(instance.attributes, attrs)

    @classmethod
    def refresh_dpdk_properties(cls, instance):
        if not instance.cluster:
            dpdk_drivers = {}
        else:
            dpdk_drivers = Release.get_supported_dpdk_drivers(
                instance.cluster.release)
        for interface in instance.nic_interfaces:
            NIC.refresh_interface_dpdk_properties(interface, dpdk_drivers)
        for interface in instance.bond_interfaces:
            Bond.refresh_interface_dpdk_properties(interface, dpdk_drivers)

    @classmethod
    def is_provisioned(cls, instance):
        """Checks that node has been provisioned already.

        :param instance: the Node object
        :returns: True if provisioned otherwise False
        """
        already_provisioned_statuses = (
            consts.NODE_STATUSES.deploying,
            consts.NODE_STATUSES.ready,
            consts.NODE_STATUSES.provisioned,
            consts.NODE_STATUSES.stopped
        )
        return (
            instance.status in already_provisioned_statuses or
            (instance.status == consts.NODE_STATUSES.error and
             instance.error_type == consts.NODE_ERRORS.deploy)
        )


class NodeCollection(NailgunCollection):
    """Node collection"""

    #: Single Node object class
    single = Node

    @classmethod
    def eager_nodes_handlers(cls, iterable):
        """Eager load objects instances that is used in nodes handler.

        :param iterable: iterable (SQLAlchemy query)
        :returns: iterable (SQLAlchemy query)
        """
        options = (
            joinedload('cluster'),
            subqueryload_all('nic_interfaces.assigned_networks_list'),
            subqueryload_all('bond_interfaces.assigned_networks_list'),
            subqueryload_all('ip_addrs.network_data')
        )
        return cls.eager_base(iterable, options)

    @classmethod
    def lock_nodes(cls, instances):
        """Locking nodes instances, fetched before, but required to be locked

        :param instances: list of nodes
        :return: list of locked nodes
        """
        instances_ids = [instance.id for instance in instances]
        q = cls.filter_by_list(None, 'id', instances_ids, order_by='id')
        return cls.lock_for_update(q).all()

    @classmethod
    def get_by_group_id(cls, group_id):
        return cls.filter_by(None, group_id=group_id)

    @classmethod
    def get_by_ids(cls, ids, cluster_id=None):
        query = db.query(models.Node).filter(models.Node.id.in_(ids))

        if cluster_id is not None:
            query = query.filter(models.Node.cluster_id == cluster_id)

        return query.all()

    @classmethod
    def reset_network_template(cls, instances):
        for instance in instances:
            instance.network_template = None

    @classmethod
    def reset_attributes(cls, instances):
        for instance in instances:
            instance.attributes = {}

    @classmethod
    def delete_by_ids(cls, ids):
        fire_callback_on_node_collection_delete(ids)
        db.query(cls.single.model).filter(
            cls.single.model.id.in_(ids)).delete(synchronize_session='fetch')

    @classmethod
    def discovery_node_ids(self):
        """Ids of nodes which belong to the cluster and have 'discovery' status

        :returns: list of node ids
        """
        q_discovery = db().query(
            models.Node.id).filter_by(status=consts.NODE_STATUSES.discover)

        return [_id for (_id,) in q_discovery]


class NodeAttributes(object):

    @classmethod
    def _safe_get_hugepages(cls, node, attributes=None):
        if not attributes:
            attributes = Node.get_attributes(node)
        hugepages = attributes.get('hugepages', {})

        return hugepages

    @classmethod
    def _safe_get_cpu_pinning(cls, node, attributes=None):
        if not attributes:
            attributes = Node.get_attributes(node)
        cpu_pinning = attributes.get('cpu_pinning', {})

        return cpu_pinning

    @classmethod
    def set_default_hugepages(cls, node):
        supported_hugepages = node.meta['numa_topology']['supported_hugepages']
        hugepages = cls._safe_get_hugepages(node)
        if not hugepages:
            return

        for attrs in six.itervalues(hugepages):
            if attrs.get('type') == 'custom_hugepages':
                attrs['value'] = dict.fromkeys(supported_hugepages, 0)

        Node.update_attributes(node, {'hugepages': hugepages})

    @classmethod
    def distribute_node_cpus(cls, node):
        return cpu_distribution.distribute_node_cpus(
            node.meta['numa_topology']['numa_nodes'],
            list(six.itervalues(cls.node_cpu_pinning_info(node)['components']))
        )

    @classmethod
    def node_cpu_pinning_info(cls, node, attributes=None):
        cpu_pinning_attrs = cls._safe_get_cpu_pinning(
            node, attributes=attributes)

        total_required_cpus = 0
        components = {}

        for name, attrs in six.iteritems(cpu_pinning_attrs):
            # skip meta
            if 'value' in attrs:
                required_cpus = attrs['value']
                total_required_cpus += required_cpus
                components[name] = {'name': name,
                                    'required_cpus': required_cpus}
        return {'total_required_cpus': total_required_cpus,
                'components': components}

    @classmethod
    def is_nova_cpu_pinning_enabled(cls, node):
        cpu_pinning = cls._safe_get_cpu_pinning(node)
        return 'nova' in cpu_pinning and bool(cpu_pinning['nova']['value'])

    @staticmethod
    def pages_per_numa_node(size):
        """Convert memory size to 2 MiB pages count.

        :param size: memory size in MiBs
        :returns: amount of 2MiB pages
        """
        # for python3 compatibility we have to use int() and float()
        return int(math.ceil(float(size) / 2))

    @classmethod
    def total_hugepages(cls, node):
        """Return total hugepages for the node

        Iterate over hugepages attributes and sum them
        according their type: custom_hugepages - contains
        items (size: count), number - this is the number of
        memory in MB which must be allocated as hugepages
        on each NUMA node (default hugepages size 2M will
        be used and count will be calculated according to
        number of specified memory).

        :return: Dictionary with (size: count) items
        """

        hugepages = collections.defaultdict(int)
        numa_count = len(node.meta['numa_topology']['numa_nodes'])

        hugepages_attributes = cls._safe_get_hugepages(node)
        for name, attrs in six.iteritems(hugepages_attributes):
            if attrs.get('type') == 'custom_hugepages':
                value = attrs['value']
                for size, count in six.iteritems(value):
                    hugepages[size] += int(count)
            elif attrs.get('type') == 'number':
                # type number means that value is the number of memory in MB
                # per NUMA node which should be converted to pages count
                count_per_numa_node = cls.pages_per_numa_node(attrs['value'])
                hugepages[consts.DEFAULT_HUGEPAGE_SIZE] += (
                    count_per_numa_node * numa_count)

        return hugepages

    @classmethod
    def hugepages_kernel_opts(cls, node):
        hugepages = cls.total_hugepages(node)

        kernel_opts = ""
        for size, human_size in consts.HUGE_PAGES_SIZE_MAP:
            hugepage_size = hugepages.get(size, 0)
            if hugepage_size:
                # extend kernel params with lines for huge pages
                # hugepagesz is the size (2M, 1G, etc.)
                # hugepages is the number of pages for specific size
                kernel_opts += " hugepagesz={0} hugepages={1}".format(
                    human_size, hugepage_size)

        return kernel_opts

    @classmethod
    def is_nova_hugepages_enabled(cls, node):
        hugepages = cls._safe_get_hugepages(node)
        return ('nova' in hugepages and
                any(six.itervalues(hugepages['nova']['value'])))

    @classmethod
    def is_dpdk_hugepages_enabled(cls, node):
        return int(Node.get_attributes(
            node)['hugepages']['dpdk']['value']) != 0

    @classmethod
    def dpdk_hugepages_attrs(cls, node):
        """Return hugepages configuration for DPDK

        DPDK hugepages configured as the number of memory in MB
        per NUMA node. This configuration passed to deployment
        as comma-separeted values. (e.g. 'ovs_socket_mem': N,N,N,N
        where N is the specified number of memory and there are
        4 NUMA nodes)
        """
        hugepages = cls._safe_get_hugepages(node)

        if 'dpdk' not in hugepages or not hugepages['dpdk']['value']:
            return {}

        dpdk_memory = hugepages['dpdk']['value']
        numa_nodes_len = len(node.meta['numa_topology']['numa_nodes'])

        return {
            'ovs_socket_mem': ",".join(
                itertools.repeat(str(dpdk_memory), numa_nodes_len))}

    @classmethod
    def distribute_hugepages(cls, node, attributes=None):
        hugepages = cls._safe_get_hugepages(
            node, attributes=attributes)
        topology = node.meta['numa_topology']

        # split components to 2 groups:
        # components that should have pages on all numa nodes (such as dpdk)
        # and components that may have pages on any numa node
        components = {'all': [], 'any': []}

        for attrs in hugepages.values():
            if attrs.get('type') == 'number':
                # type number means size of memory in MiB to allocate with
                # 2MiB pages, so we need to calculate pages count
                pages_count = cls.pages_per_numa_node(attrs['value'])
                components['all'].append(
                    {consts.DEFAULT_HUGEPAGE_SIZE: pages_count})

            elif attrs.get('type') == 'custom_hugepages':
                components['any'].append(attrs['value'])

        return hugepages_distribution.distribute_hugepages(
            topology, components)
