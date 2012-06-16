# Copyright (C) 2012  Aldo Cortesi
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import sys, os, string, socket, time
import shutil, tempfile, threading
import optparse, SocketServer
import utils, flow, certutils, version, wsgi, netlib, protocol
from OpenSSL import SSL


class ProxyError(Exception):
    def __init__(self, code, msg):
        self.code, self.msg = code, msg

    def __str__(self):
        return "ProxyError(%s, %s)"%(self.code, self.msg)


class ProxyConfig:
    def __init__(self, certfile = None, cacert = None, clientcerts = None, cert_wait_time=0, upstream_cert=False, body_size_limit = None, reverse_proxy=None, transparent_proxy=None):
        assert not (reverse_proxy and transparent_proxy)
        self.certfile = certfile
        self.cacert = cacert
        self.clientcerts = clientcerts
        self.certdir = None
        self.cert_wait_time = cert_wait_time
        self.upstream_cert = upstream_cert
        self.body_size_limit = body_size_limit
        self.reverse_proxy = reverse_proxy
        self.transparent_proxy = transparent_proxy


class RequestReplayThread(threading.Thread):
    def __init__(self, config, flow, masterq):
        self.config, self.flow, self.masterq = config, flow, masterq
        threading.Thread.__init__(self)

    def run(self):
        try:
            r = self.flow.request
            server = ServerConnection(self.config, r.scheme, r.host, r.port)
            server.send(r)
            response = server.read_response(r)
            response._send(self.masterq)
        except (ProxyError, ProtocolError), v:
            err = flow.Error(self.flow.request, v.msg)
            err._send(self.masterq)
        except netlib.NetLibError, v:
            raise ProxyError(502, v)


class ServerConnection(netlib.TCPClient):
    def __init__(self, config, scheme, host, port):
        clientcert = None
        if config.clientcerts:
            path = os.path.join(config.clientcerts, self.host) + ".pem"
            if os.path.exists(clientcert):
                clientcert = path
        netlib.TCPClient.__init__(
            self,
            True if scheme == "https" else False,
            host,
            port,
            clientcert
        )
        self.config, self.scheme = config, scheme
        self.requestcount = 0

    def send(self, request):
        self.requestcount += 1
        try:
            d = request._assemble()
            if not d:
                raise ProxyError(502, "Incomplete request could not not be readied for transmission.")
            self.wfile.write(d)
            self.wfile.flush()
        except socket.error, err:
            raise ProxyError(502, 'Error sending data to "%s": %s' % (request.host, err))

    def read_response(self, request):
        line = self.rfile.readline()
        if line == "\r\n" or line == "\n": # Possible leftover from previous message
            line = self.rfile.readline()
        if not line:
            raise ProxyError(502, "Blank server response.")
        parts = line.strip().split(" ", 2)
        if len(parts) == 2: # handle missing message gracefully
            parts.append("")
        if not len(parts) == 3:
            raise ProxyError(502, "Invalid server response: %s."%line)
        proto, code, msg = parts
        httpversion = protocol.parse_http_protocol(proto)
        if httpversion is None:
            raise ProxyError(502, "Invalid HTTP version: %s."%httpversion)
        try:
            code = int(code)
        except ValueError:
            raise ProxyError(502, "Invalid server response: %s."%line)
        headers = protocol.read_headers(self.rfile)
        if code >= 100 and code <= 199:
            return self.read_response()
        if request.method == "HEAD" or code == 204 or code == 304:
            content = ""
        else:
            content = protocol.read_http_body(self.rfile, headers, True, self.config.body_size_limit)
        return flow.Response(request, httpversion, code, msg, headers, content, self.cert)

    def terminate(self):
        try:
            if not self.wfile.closed:
                self.wfile.flush()
            self.connection.close()
        except IOError:
            pass


class ProxyHandler(netlib.BaseHandler):
    def __init__(self, config, connection, client_address, server, q):
        self.mqueue = q
        self.config = config
        self.server_conn = None
        self.proxy_connect_state = None
        netlib.BaseHandler.__init__(self, connection, client_address, server)

    def handle(self):
        cc = flow.ClientConnect(self.client_address)
        cc._send(self.mqueue)
        while self.handle_request(cc) and not cc.close:
            pass
        cc.close = True
        cd = flow.ClientDisconnect(cc)
        cd._send(self.mqueue)

    def server_connect(self, scheme, host, port):
        sc = self.server_conn
        if sc and (scheme, host, port) != (sc.scheme, sc.host, sc.port):
            sc.terminate()
            self.server_conn = None
        if not self.server_conn:
            try:
                self.server_conn = ServerConnection(self.config, scheme, host, port)
            except netlib.NetLibError, v:
                raise ProxyError(502, v)

    def handle_request(self, cc):
        try:
            request, err = None, None
            try:
                request = self.read_request(cc)
            except IOError, v:
                raise IOError, "Reading request: %s"%v
            if request is None:
                return
            cc.requestcount += 1

            app = self.server.apps.get(request)
            if app:
                app.serve(request, self.wfile)
            else:
                request = request._send(self.mqueue)
                if request is None:
                    return

                if isinstance(request, flow.Response):
                    response = request
                    request = False
                    response = response._send(self.mqueue)
                else:
                    if self.config.reverse_proxy:
                        scheme, host, port = self.config.reverse_proxy
                    else:
                        scheme, host, port = request.scheme, request.host, request.port
                    self.server_connect(scheme, host, port)
                    self.server_conn.send(request)
                    try:
                        response = self.server_conn.read_response(request)
                    except IOError, v:
                        raise IOError, "Reading response: %s"%v
                    response = response._send(self.mqueue)
                    if response is None:
                        self.server_conn.terminate()
                if response is None:
                    return
                self.send_response(response)
                if protocol.request_connection_close(request.httpversion, request.headers):
                    return
                # We could keep the client connection when the server
                # connection needs to go away.  However, we want to mimic
                # behaviour as closely as possible to the client, so we
                # disconnect.
                if protocol.response_connection_close(response.httpversion, response.headers):
                    return
        except IOError, v:
            cc.connection_error = v
        except (ProxyError, ProtocolError), e:
            cc.connection_error = "%s: %s"%(e.code, e.msg)
            if request:
                err = flow.Error(request, e.msg)
                err._send(self.mqueue)
                self.send_error(e.code, e.msg)
        else:
            return True

    def find_cert(self, host, port):
        if self.config.certfile:
            return self.config.certfile
        else:
            sans = []
            if self.config.upstream_cert:
                cert = certutils.get_remote_cert(host, port)
                sans = cert.altnames
                host = cert.cn
            ret = certutils.dummy_cert(self.config.certdir, self.config.cacert, host, sans)
            time.sleep(self.config.cert_wait_time)
            if not ret:
                raise ProxyError(502, "mitmproxy: Unable to generate dummy cert.")
            return ret

    def read_request(self, client_conn):
        line = self.rfile.readline()
        if line == "\r\n" or line == "\n": # Possible leftover from previous message
            line = self.rfile.readline()
        if line == "":
            return None

        if self.config.transparent_proxy:
            host, port = self.config.transparent_proxy["resolver"].original_addr(self.connection)
            if port in self.config.transparent_proxy["sslports"]:
                scheme = "https"
                certfile = self.find_cert(host, port)
                self.convert_to_ssl(certfile, self.config.certfile or self.config.cacert)
            else:
                scheme = "http"
            method, path, httpversion = protocol.parse_init_http(line)
            headers = protocol.read_headers(self.rfile)
            content = protocol.read_http_body_request(
                        self.rfile, self.wfile, headers, httpversion, self.config.body_size_limit
                    )
            return flow.Request(client_conn, httpversion, host, port, "http", method, path, headers, content)
        elif self.config.reverse_proxy:
            scheme, host, port = self.config.reverse_proxy
            method, path, httpversion = protocol.parse_init_http(line)
            headers = protocol.read_headers(self.rfile)
            content = protocol.read_http_body_request(
                        self.rfile, self.wfile, headers, httpversion, self.config.body_size_limit
                    )
            return flow.Request(client_conn, httpversion, host, port, "http", method, path, headers, content)
        else:
            if line.startswith("CONNECT"):
                host, port, httpversion = protocol.parse_init_connect(line)
                # FIXME: Discard additional headers sent to the proxy. Should I expose
                # these to users?
                while 1:
                    d = self.rfile.readline()
                    if d == '\r\n' or d == '\n':
                        break
                self.wfile.write(
                            'HTTP/1.1 200 Connection established\r\n' +
                            ('Proxy-agent: %s\r\n'%version.NAMEVERSION) +
                            '\r\n'
                            )
                self.wfile.flush()
                certfile = self.find_cert(host, port)
                self.convert_to_ssl(certfile, self.config.certfile or self.config.cacert)
                self.proxy_connect_state = (host, port, httpversion)
                line = self.rfile.readline(line)
            if self.proxy_connect_state:
                host, port, httpversion = self.proxy_connect_state
                method, path, httpversion = protocol.parse_init_http(line)
                headers = protocol.read_headers(self.rfile)
                content = protocol.read_http_body_request(
                    self.rfile, self.wfile, headers, httpversion, self.config.body_size_limit
                )
                return flow.Request(client_conn, httpversion, host, port, "https", method, path, headers, content)
            else:
                method, scheme, host, port, path, httpversion = protocol.parse_init_proxy(line)
                headers = protocol.read_headers(self.rfile)
                content = protocol.read_http_body_request(
                    self.rfile, self.wfile, headers, httpversion, self.config.body_size_limit
                )
                return flow.Request(client_conn, httpversion, host, port, scheme, method, path, headers, content)

    def send_response(self, response):
        d = response._assemble()
        if not d:
            raise ProxyError(502, "Incomplete response could not not be readied for transmission.")
        self.wfile.write(d)
        self.wfile.flush()

    def send_error(self, code, body):
        try:
            import BaseHTTPServer
            response = BaseHTTPServer.BaseHTTPRequestHandler.responses[code][0]
            self.wfile.write("HTTP/1.1 %s %s\r\n" % (code, response))
            self.wfile.write("Server: %s\r\n"%version.NAMEVERSION)
            self.wfile.write("Connection: close\r\n")
            self.wfile.write("Content-type: text/html\r\n")
            self.wfile.write("\r\n")
            self.wfile.write('<html><head>\n<title>%d %s</title>\n</head>\n'
                    '<body>\n%s\n</body>\n</html>' % (code, response, body))
            self.wfile.flush()
        except:
            pass


class ProxyServerError(Exception): pass


class ProxyServer(netlib.TCPServer):
    allow_reuse_address = True
    bound = True
    def __init__(self, config, port, address=''):
        """
            Raises ProxyServerError if there's a startup problem.
        """
        self.config, self.port, self.address = config, port, address
        try:
            netlib.TCPServer.__init__(self, (address, port))
        except socket.error, v:
            raise ProxyServerError('Error starting proxy server: ' + v.strerror)
        self.masterq = None
        self.certdir = tempfile.mkdtemp(prefix="mitmproxy")
        config.certdir = self.certdir
        self.apps = wsgi.AppRegistry()

    def start_slave(self, klass, masterq):
        slave = klass(masterq, self)
        slave.start()

    def set_mqueue(self, q):
        self.masterq = q

    def handle_connection(self, request, client_address):
        ProxyHandler(self.config, request, client_address, self, self.masterq)

    def handle_shutdown(self):
        try:
            shutil.rmtree(self.certdir)
        except OSError:
            pass


class DummyServer:
    bound = False
    def __init__(self, config):
        self.config = config

    def start_slave(self, klass, masterq):
        pass

    def shutdown(self):
        pass


# Command-line utils
def certificate_option_group(parser):
    group = optparse.OptionGroup(parser, "SSL")
    group.add_option(
        "--cert", action="store",
        type = "str", dest="cert", default=None,
        help = "User-created SSL certificate file."
    )
    group.add_option(
        "--client-certs", action="store",
        type = "str", dest = "clientcerts", default=None,
        help = "Client certificate directory."
    )
    parser.add_option_group(group)


def process_proxy_options(parser, options):
    if options.cert:
        options.cert = os.path.expanduser(options.cert)
        if not os.path.exists(options.cert):
            parser.error("Manually created certificate does not exist: %s"%options.cert)

    cacert = os.path.join(options.confdir, "mitmproxy-ca.pem")
    cacert = os.path.expanduser(cacert)
    if not os.path.exists(cacert):
        certutils.dummy_ca(cacert)
    if getattr(options, "cache", None) is not None:
        options.cache = os.path.expanduser(options.cache)
    body_size_limit = utils.parse_size(options.body_size_limit)

    if options.reverse_proxy:
        rp = utils.parse_proxy_spec(options.reverse_proxy)
        if not rp:
            parser.error("Invalid reverse proxy specification: %s"%options.reverse_proxy)
    else:
        rp = None

    if options.clientcerts:
        options.clientcerts = os.path.expanduser(options.clientcerts)
        if not os.path.exists(options.clientcerts) or not os.path.isdir(options.clientcerts):
            parser.error("Client certificate directory does not exist or is not a directory: %s"%options.clientcerts)

    return ProxyConfig(
        certfile = options.cert,
        cacert = cacert,
        clientcerts = options.clientcerts,
        cert_wait_time = options.cert_wait_time,
        body_size_limit = body_size_limit,
        upstream_cert = options.upstream_cert,
        reverse_proxy = rp
    )
