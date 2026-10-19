import socket
import threading
import sys

def client_pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            data = data.replace(b":18777", b":48777")
            dst.sendall(data)
    except Exception:
        pass
    finally:
        try: src.close()
        except Exception: pass
        try: dst.close()
        except Exception: pass

def server_pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        try: src.close()
        except Exception: pass
        try: dst.close()
        except Exception: pass

def handle(client):
    try:
        server = socket.create_connection(("127.0.0.1", 48777), timeout=5)
    except Exception:
        try: client.close()
        except Exception: pass
        return
    threading.Thread(target=client_pipe, args=(client, server), daemon=True).start()
    threading.Thread(target=server_pipe, args=(server, client), daemon=True).start()

def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", 18777))
        s.listen(128)
    except Exception as e:
        print(f"[Warn] Port 18777 bind failed: {e}")
        return
    print("[fwd] Forwarding 127.0.0.1:18777 -> 127.0.0.1:48777")
    while True:
        try:
            c, _ = s.accept()
            handle(c)
        except Exception:
            break

if __name__ == "__main__":
    main()
