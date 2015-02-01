# -*- coding: utf-8 -*-
import random
import time
from rlp import int_to_big_endian
from devp2p import kademlia

random.seed(42)


class WireMock(kademlia.WireInterface):

    def __init__(self):
        self.messages = []

    def send_ping(self, node):
        ping_id = hex(random.randint(0, 2**256))[-32:]
        self.messages.append((node, 'ping', ping_id))
        return ping_id

    def send_pong(self, node, ping_id):
        self.messages.append((node, 'pong', ping_id))

    def send_find_node(self, node, nodeid):
        self.messages.append((node, 'find_node', nodeid))

    def send_neighbours(self, node, neighbours):
        self.messages.append((node, 'neighbours', neighbours))

    def poll(self, node):
        for i, x in enumerate(self.messages):
            if x[0] == node:
                del self.messages[i]
                return x[1:]


def random_pubkey():
    pk = int_to_big_endian(random.getrandbits(kademlia.k_id_size))
    return '\x00' * (kademlia.k_id_size / 8 - len(pk)) + pk


def random_node():
    return kademlia.Node(random_pubkey())


def routing_table(num_nodes=1000):
    node = random_node()
    routing = kademlia.RoutingTable(node)
    for i in range(num_nodes):
        routing.add_node(random_node())
        assert len(routing.buckets) <= i + 2
    assert len(routing.buckets) <= 512
    assert i == num_nodes - 1
    return routing


def protocol():
    this_node = random_node()
    wire = WireMock()
    return kademlia.KademliaProtocol(this_node, wire)


def test_setup():
    """
    nodes connect to any peer and do a lookup for them selfs
    """

    proto = protocol()
    wire = proto.wire
    other = routing_table()

    # lookup self
    proto.bootstrap(nodes=[other.this_node])
    msg = wire.poll(other.this_node)
    assert msg == ('find_node', proto.routing.this_node.pubkey)
    assert wire.poll(other.this_node) is None
    assert wire.messages == []

    # respond with neighbours
    closest = other.neighbours(kademlia.Node(msg[1]))
    assert len(closest) == kademlia.k_bucket_size
    proto.recv_neighbours(closest)

    # expect another lookup
    msg = wire.poll(closest[0])
    assert msg == ('find_node', proto.routing.this_node.pubkey)

    # and pings for all nodes
    for node in closest:
        msg = wire.poll(node)
        assert msg[0] == 'ping'

    # nothing else
    assert wire.messages == []


def test_find_node_timeout():
    proto = protocol()
    wire = proto.wire
    other = routing_table()

    # lookup self
    proto.bootstrap(nodes=[other.this_node])
    msg = wire.poll(other.this_node)
    assert msg == ('find_node', proto.routing.this_node.pubkey)
    assert wire.poll(other.this_node) is None
    assert wire.messages == []

    # do timeout
    time.sleep(kademlia.k_request_timeout)

    # respond with neighbours
    closest = other.neighbours(kademlia.Node(msg[1]))
    assert len(closest) == kademlia.k_bucket_size
    proto.recv_neighbours(closest)

    # expect pings, but no other lookup
    msg = wire.poll(closest[0])
    assert msg[0] == 'ping'
    assert wire.poll(closest[0]) is None


def test_eviction():
    proto = protocol()
    proto.routing = routing_table(1000)
    wire = proto.wire

    # trigger node ping
    node = proto.routing.neighbours(random_node())[0]
    proto.ping(node)
    msg = wire.poll(node)
    assert msg[0] == 'ping'
    assert wire.messages == []
    proto.recv_pong(node, msg[1])

    # expect no message and that node is still there
    assert wire.messages == []
    assert node in proto.routing

    # expect node to be on the tail
    assert proto.routing.bucket_by_node(node).tail == node


def test_eviction_timeout():
    proto = protocol()
    proto.routing = routing_table(1000)
    wire = proto.wire

    # trigger node ping
    node = proto.routing.neighbours(random_node())[0]
    proto.ping(node)
    msg = wire.poll(node)
    assert msg[0] == 'ping'
    assert wire.messages == []

    time.sleep(kademlia.k_eviction_check_interval)
    proto.recv_pong(node, msg[1])
    # expect no message and that is not there anymore
    assert wire.messages == []
    assert node not in proto.routing

    # expect node not to be in the replacement_cache
    assert node not in proto.routing.bucket_by_node(node).replacement_cache


def test_eviction_node_active():
    """
    active nodes (replying in time) should not be evicted
    """
    proto = protocol()
    proto.routing = routing_table(10000)  # set high, so add won't split
    wire = proto.wire
    # get a full bucket
    full_buckets = [b for b in proto.routing.buckets if b.is_full and not b.should_split]
    assert full_buckets
    bucket = full_buckets[0]
    assert not bucket.should_split
    assert len(bucket) == kademlia.k_bucket_size
    bucket_nodes = bucket.nodes[:]
    eviction_candidate = bucket.head

    # create node to insert
    node = kademlia.Node.from_id(bucket.start + 1)  # should not split
    assert bucket.in_range(node)
    assert bucket == proto.routing.bucket_by_node(node)

    # insert node
    proto.update(node)

    # expect bucket was not split
    assert len(bucket) == kademlia.k_bucket_size

    # expect bucket to be unchanged
    assert bucket_nodes == bucket.nodes
    assert eviction_candidate == bucket.head

    # expect node not to be in bucket yet
    assert node not in bucket
    assert node not in proto.routing

    # expect a ping to bucket.head
    msg = wire.poll(eviction_candidate)
    assert msg[0] == 'ping'
    assert msg[1] in proto._expected_pongs
    assert wire.messages == []
    # reply in time
    print 'sending pong'
    proto.recv_pong(eviction_candidate, msg[1])
    # expect no other messages
    assert wire.messages == []

    # expect node was not added
    assert node not in proto.routing
    # eviction_candidate is around and was promoted to bucket.tail
    assert eviction_candidate in proto.routing
    assert eviction_candidate == bucket.tail
    # expect node to be in the replacement_cache
    assert node in bucket.replacement_cache


def test_eviction_node_inactive():
    """
    active nodes (replying in time) should not be evicted
    """
    proto = protocol()
    proto.routing = routing_table(10000)  # set high, so add won't split
    wire = proto.wire
    # get a full bucket
    full_buckets = [b for b in proto.routing.buckets if b.is_full and not b.should_split]
    assert full_buckets
    bucket = full_buckets[0]
    assert not bucket.should_split
    assert len(bucket) == kademlia.k_bucket_size
    bucket_nodes = bucket.nodes[:]
    eviction_candidate = bucket.head

    # create node to insert
    node = kademlia.Node.from_id(bucket.start + 1)  # should not split
    assert bucket.in_range(node)
    assert bucket == proto.routing.bucket_by_node(node)

    # insert node
    proto.update(node)

    # expect bucket was not split
    assert len(bucket) == kademlia.k_bucket_size

    # expect bucket to be unchanged
    assert bucket_nodes == bucket.nodes
    assert eviction_candidate == bucket.head

    # expect node not to be in bucket yet
    assert node not in bucket
    assert node not in proto.routing

    # expect a ping to bucket.head
    msg = wire.poll(eviction_candidate)
    assert msg[0] == 'ping'
    assert msg[1] in proto._expected_pongs
    assert wire.messages == []
    # reply late
    time.sleep(kademlia.k_eviction_check_interval)
    proto.recv_pong(eviction_candidate, msg[1])

    # expect no other messages
    assert wire.messages == []

    # expect node was not added
    assert node in proto.routing
    # eviction_candidate is around and was promoted to bucket.tail
    assert eviction_candidate not in proto.routing
    assert node == bucket.tail
    # expect node to be in the replacement_cache
    assert eviction_candidate not in bucket.replacement_cache


def test_eviction_node_split():
    """
    active nodes (replying in time) should not be evicted
    """
    proto = protocol()
    proto.routing = routing_table(1000)  # set lpw, so we'll split
    wire = proto.wire
    # get a full bucket
    full_buckets = [b for b in proto.routing.buckets if b.is_full and b.should_split]
    assert full_buckets
    bucket = full_buckets[0]
    assert bucket.should_split
    assert len(bucket) == kademlia.k_bucket_size
    bucket_nodes = bucket.nodes[:]
    eviction_candidate = bucket.head

    # create node to insert
    node = kademlia.Node.from_id(bucket.start + 1)  # should split
    assert bucket.in_range(node)
    assert bucket == proto.routing.bucket_by_node(node)

    # insert node
    proto.update(node)

    # expect bucket to be unchanged
    assert bucket_nodes == bucket.nodes
    assert eviction_candidate == bucket.head

    # expect node not to be in bucket yet
    assert node not in bucket
    assert node in proto.routing

    # expect no ping to bucket.head
    assert not wire.poll(eviction_candidate)
    assert wire.messages == []

    # expect node was not added
    assert node in proto.routing

    # eviction_candidate is around and was unchanged
    assert eviction_candidate == bucket.head
