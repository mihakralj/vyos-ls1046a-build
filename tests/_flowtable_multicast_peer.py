"""Persistent multicast listeners controlled over the ordinary traffic peer."""


class MulticastListeners:
    def __init__(self):
        self.listeners = {}

    def rpc(self, action, group, iface=None, port=None):
        import asyncio
        import socket
        import struct

        loop = asyncio.get_running_loop()
        if action == 'join':
            assert group not in self.listeners
            family = socket.AF_INET6 if ':' in group else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, iface.encode() + b'\0')
                sock.bind((group, port))
                index = socket.if_nametoindex(iface)
                if family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP,
                                    socket.inet_pton(family, group) + struct.pack('@I', index))
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_RECVHOPLIMIT, 1)
                else:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                    socket.inet_aton(group) + b'\0' * 4 + struct.pack('@i', index))
                    sock.setsockopt(socket.IPPROTO_IP, 12, 1)  # IP_RECVTTL
                sock.setblocking(False)
                state = {'sock': sock, 'windows': {}, 'errors': []}
                self.listeners[group] = state
                def receive():
                    try:
                        while True:
                            data, ancillary, _, address = sock.recvmsg(2048, 256)
                            assert data[:8] == b'ASKMCv1:', (data[:32], address)
                            window, sequence = struct.unpack('!II', data[8:16])
                            assert data[16:] == b'x' * 496, (len(data), address)
                            item = state['windows'].setdefault(str(window), {'seen': set(), 'duplicates': 0, 'hops': set(), 'sources': set()})
                            item['duplicates'] += sequence in item['seen']
                            item['seen'].add(sequence)
                            item['sources'].add(address[0])
                            for level, kind, value in ancillary:
                                if (level, kind) in [(socket.IPPROTO_IP, socket.IP_TTL),
                                                    (socket.IPPROTO_IPV6, socket.IPV6_HOPLIMIT)]:
                                    item['hops'].add(struct.unpack('@i', value)[0])
                    except BlockingIOError:
                        pass
                    except BaseException as error:
                        state['errors'].append(repr(error))
                        loop.remove_reader(sock.fileno())
                loop.add_reader(sock.fileno(), receive)
            except BaseException:
                sock.close()
                raise
        state = self.listeners[group]
        result = {'windows': {key: {'received': len(value['seen']), 'duplicates': value['duplicates'],
                                   'hops': sorted(value['hops']), 'sources': sorted(value['sources'])}
                              for key, value in state['windows'].items()}, 'errors': list(state['errors'])}
        if action == 'leave':
            loop.remove_reader(state['sock'].fileno())
            state['sock'].close()
            del self.listeners[group]
        else:
            assert action in ('join', 'status'), action
        return result

    def close(self):
        for group in list(self.listeners):
            self.rpc('leave', group)
