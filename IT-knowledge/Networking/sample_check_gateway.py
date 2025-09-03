import socket

def check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False

if __name__ == "__main__":
    print("Gateway reachable:", check_port("10.0.0.1", 443))
