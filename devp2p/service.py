#!/usr/bin/env python
from gevent import Greenlet
import utils


class BaseService(Greenlet):

    """
    service instances are added to the application under
    app.services.<service_name>

    app should be passed to the service in order to query other services

    services may be a greenlet or spawn greenlets.
    both must implement a .stop()
    if a services spawns additional greenlets, it's responsible to stop them.
    """

    name = ''
    default_config = {name: dict()}

    def __init__(self, app):
        Greenlet.__init__(self)
        self.app = app
        self.config = utils.update_with_defaults(app.config, self.default_config)

    def start(self):
        Greenlet.start(self)

    def stop(self):
        Greenlet.kill(self)

    @classmethod
    def register_with_app(klass, app):
        """
        services know best how to initiate themselfs.
        create a service instance, propably based on
        app.config and app.services
        """
        s = klass(app)
        app.register_service(s)
        return s

    def _run(self):
        "implement this for the greenlet event loop"
        pass


class WiredService(BaseService):

    """
    A Service which has an associated WireProtocol

    peermanager checks all services registered with app.services
        if isinstance(service, WiredService):
            add WiredService.wire_protocol to announced capabilities
            if a peer with the same protocol is connected
                a WiredService.wire_protocol instance is created
                with instances of Peer and WiredService
                WiredService.wire_protocol(Peer(), WiredService() )
    """
    wire_protocol = None

    def on_peer_handshake(self, proto):
        assert isinstance(proto, self.wire_protocol)

    def on_peer_disconnect(self, proto):
        assert isinstance(proto, self.wire_protocol)
