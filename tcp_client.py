"""
TCP Client helper — used by Flask routes to communicate with the TCP server.
"""

import socket
import json
from config import Config


def tcp_request(action, data=None, timeout=10):
    """
    Send a JSON request to the TCP server and return the parsed response.

    Args:
        action: The action string (e.g., 'balance', 'transfer')
        data: Additional data dict to include in the request
        timeout: Socket timeout in seconds

    Returns:
        dict: Parsed JSON response from the TCP server
    """
    if data is None:
        data = {}

    request_data = {'action': action}
    request_data.update(data)

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        host = Config.TCP_HOST
        if host == '0.0.0.0':
            host = '127.0.0.1'
        sock.connect((host, Config.TCP_PORT))

        # Send request
        message = json.dumps(request_data) + '\n'
        sock.sendall(message.encode('utf-8'))

        # Receive response
        response_data = b''
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response_data += chunk
            if b'\n' in response_data:
                break

        sock.close()

        if response_data:
            return json.loads(response_data.decode('utf-8').strip())
        else:
            return {'status': 'error', 'message': 'No response from TCP server'}

    except socket.timeout:
        return {'status': 'error', 'message': 'TCP server connection timed out'}
    except ConnectionRefusedError:
        return {'status': 'error', 'message': 'TCP server is not running'}
    except Exception as e:
        return {'status': 'error', 'message': f'TCP connection error: {str(e)}'}
