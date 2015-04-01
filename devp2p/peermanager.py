import random
import gevent
import socket
from gevent.server import StreamServer
from gevent.socket import create_connection
from service import WiredService
from protocol import BaseProtocol
from p2p_protocol import P2PProtocol
from peer import Peer
import crypto
import utils

import slogging
log = slogging.get_logger('peermgr')


class PeerManager(WiredService):

    """
    todo:
        connects new peers if there are too few
        selects peers based on a DHT
        keeps track of peer reputation
        saves/loads peers (rather discovery buckets) to disc

    connection strategy
        for service which requires peers
            while num peers > min_num_peers:
                    gen random id
                    resolve closest node address
                    [ideally know their services]
                    connect closest node
    """
    name = 'peermanager'
    wire_protocol = P2PProtocol
    default_config = dict(p2p=dict(privkey=crypto.mk_privkey(''),
                                   bootstrap_nodes=[]))

    def __init__(self, app):
        log.info('PeerManager init')
        self.peers = []
        WiredService.__init__(self, app)

        # setup nodeid based on privkey
        if 'nodeid' not in self.config['p2p']:
            self.config['p2p']['nodeid'] = crypto.privtopub(self.config['p2p']['privkey'])

    def on_hello_received(self, p2p_proto, data):
        log.debug('hello_received', peer=p2p_proto.peer)
        # register more protocols

    @property
    def wired_services(self):
        return [s for s in self.app.services.values() if isinstance(s, WiredService)]

    def broadcast(self, method, num_peers=None, *args, **kargs):
        assert issubclass(method.im_class, BaseProtocol)
        assert num_peers is None or num_peers > 0
        proto_name = method.im_class.name
        peers_with_proto = [p for p in self.peers if proto_name in p.protocols]
        num_peers = num_peers or len(peers_with_proto)
        for p in random.sample(peers_with_proto, min(num_peers, len(peers_with_proto))):
            method(p.protocols[proto_name], *args, **kargs)

    def _start_peer(self, connection, address, remote_pubkey=None):
        log.debug('new connect', connection=connection)
        # create peer
        peer = Peer(self, connection, remote_pubkey=remote_pubkey)
        log.debug('created new peer', peer=peer)
        self.peers.append(peer)

        # loop
        peer.start()
        log.debug('peer started', peer=peer)

    def connect(self, address, remote_pubkey):
        log.debug('connecting', address=address)
        """
        gevent.socket.create_connection(address, timeout=Timeout, source_address=None)
        Connect to address (a 2-tuple (host, port)) and return the socket object.
        Passing the optional timeout parameter will set the timeout
        getdefaulttimeout() is default
        """
        connection = create_connection(address)
        self._start_peer(connection, address, remote_pubkey)

    def _bootstrap(self):
        for uri in self.config['p2p']['bootstrap_nodes']:
            ip, port, pubkey = utils.host_port_pubkey_from_uri(uri)
            log.info('connecting bootstrap server', uri=uri)
            try:
                self.connect((ip, port), pubkey)
            except socket.error:
                log.warn('connecting bootstrap server failed')

    def start(self):
        log.info('starting peermanager')
        # start a listening server
        ip = self.config['p2p']['listen_host']
        port = self.config['p2p']['listen_port']
        log.info('starting listener', host=ip, port=port)
        self.server = StreamServer((ip, port), handle=self._start_peer)
        self.server.start()
        self._bootstrap()
        super(PeerManager, self).start()

    def _run(self):
        evt = gevent.event.Event()
        evt.wait()

    def stop(self):
        log.info('stopping peermanager')
        self.server.stop()
        for peer in self.peers:
            peer.stop()
