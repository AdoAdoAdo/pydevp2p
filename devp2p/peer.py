import gevent
from collections import OrderedDict
from protocol import BaseProtocol, P2PProtocol
from service import WiredService
import multiplexer
from muxsession import MultiplexedSession
import slogging

log = slogging.get_logger('peer')


class Peer(gevent.Greenlet):

    mux = None

    def __init__(self, peermanager, connection, remote_pubkey=None):
        super(Peer, self).__init__()
        self.peermanager = peermanager
        self.connection = connection
        self.config = peermanager.config
        self.protocols = OrderedDict()

        log.debug('peer init', peer=self)

        # create and register p2p protocol
        p2p_proto = P2PProtocol(self)
        self.register_protocol(p2p_proto)
        hello_packet = p2p_proto.create_hello()

        # create multiplexed encrypted session
        privkey = self.config['p2p']['privkey']
        self.mux = MultiplexedSession(privkey, hello_packet,
                                      token_by_pubkey=dict(), remote_pubkey=remote_pubkey)

        # register p2p protocol
        assert issubclass(self.peermanager.wire_protocol, P2PProtocol)
        self.connect_service(self.peermanager)

    def __repr__(self):
        return '<Peer(%r) thread=%r>' % (self.connection.getpeername(), id(gevent.getcurrent()))

    @property
    def ip_port(self):
        return self.connection.getpeername()

    def connect_service(self, service):
        assert isinstance(service, WiredService)
        protocol_class = service.wire_protocol
        assert issubclass(protocol_class, BaseProtocol)
        # create protcol instance which connects peer with serivce
        protocol = protocol_class(self, service)
        # register protocol
        assert protocol_class not in self.protocols
        log.debug('registering protocol', protocol=protocol.name, peer=self)
        self.protocols[protocol_class] = protocol
        self.mux.add_protocol(protocol.protocol_id)

    def has_protocol(self, protocol):
        assert issubclass(protocol, BaseProtocol)
        return protocol in self.protocols

    def receive_hello(self, version, client_version, capabilities, listen_port, nodeid):
        # register in common protocols
        for service in self.peermanager.wired_services:
            if (service.wire_protocol.name, service.wire_protocol.version) in capabilities:
                if service != self.peermanger:  # p2p protcol already registered
                    self.connect_service(service)

    @property
    def capabilities(self):
        return [(s.wire_protocol.name, s.wire_protocol.version)
                for s in self.peermanager.wired_services]

    # sending p2p messages

    def send_packet(self, packet):
        # rewrite cmd id / future FIXME  to packet.protocol_id
        for i, protocol in enumerate(self.protocols.values()):
            if packet.protocol_id > i:
                packet.cmd_id += protocol.max_cmd_id
        packet.protocol_id = 0
        # done rewrite
        self.mux.add_packet(packet)

    # receiving p2p messages

    def _handle_packet(self, packet):
        assert isinstance(packet, multiplexer.Packet)
        log.debug('handling packet', cmd_id=packet.cmd_id, peer=self)
        # packet.protocol_id not yet used. old adaptive cmd_ids instead
        # future FIXME  to packet.protocol_id

        # get protocol and protocol.cmd_id from packet.cmd_id
        max_id = 0
        found = False
        for protocol in self.protocols.values():
            if packet.cmd_id < max_id + protocol.max_cmd_id + 1:
                found = True
                packet.cmd_id -= max_id  # rewrite cmd_id
                break
            max_id += protocol.max_cmd_id
        if not found:
            raise Exception('no protocol for id %s' % packet.cmd_id)
        # done get protocol
        protocol.receive_packet(packet)

    def send(self, data):
        if data:
            log.debug('send', size=len(data))
            self.connection.sendall(data)  # check if gevent chunkes and switchs contexts
            log.debug('send sent', size=len(data))

    def _run(self):
        """
        Loop through queues

        fixme: option to wait for any finished event
             gevent.wait(objects=None, timeout=None, count=None)
             and wrap connection.wait_ready in an event

        mux.evt
        and spawn a wait_read which triggers an event

        """
        default_timeout = 0.01
        while True:
            # handle decoded packets
            while not self.mux.packet_queue.empty():
                self._handle_packet(self.mux.get_packet())

            # read egress data from the multiplexer queue
            emsg = self.mux.get_message()
            if emsg:
                self.send(emsg)
                timeout = 0
            else:
                timeout = default_timeout
            try:
                #log.debug('polling data', peer=self, timeout=timeout)
                gevent.socket.wait_read(self.connection.fileno(), timeout=timeout)
                imsg = self.connection.recv(4096)
            except gevent.socket.timeout:
                continue
            if imsg:
                self.mux.add_message(imsg)
            else:
                log.debug('loop_socket.not_data', peer=self)
                self.stop()
                break

    def stop(self):
        log.debug('stopped', thread=gevent.getcurrent())
        for p in self.protocols.values():
            p.stop()
        self.peermanager.peers.remove(self)
        self.kill()
