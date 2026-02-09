import threading
from time import sleep
from unittest.mock import Mock, patch

from kubernetes.stream.ws_client import RESIZE_CHANNEL

from k8s_sandbox._pod.op import _send_keepalive


def test_send_keepalive_sends_frames_and_stops():
    ws_client = Mock()
    ws_client.is_open.return_value = True
    stop = threading.Event()

    with patch("k8s_sandbox._pod.op._KEEPALIVE_INTERVAL_SECONDS", 0.05):
        t = threading.Thread(target=_send_keepalive, args=(ws_client, stop))
        t.start()
        sleep(0.2)
        stop.set()
        t.join(timeout=1)

    assert ws_client.write_channel.call_count >= 2
    for call in ws_client.write_channel.call_args_list:
        assert call[0][0] == RESIZE_CHANNEL


def test_send_keepalive_exits_when_socket_closes():
    ws_client = Mock()
    ws_client.is_open.return_value = False
    stop = threading.Event()

    with patch("k8s_sandbox._pod.op._KEEPALIVE_INTERVAL_SECONDS", 0.01):
        t = threading.Thread(target=_send_keepalive, args=(ws_client, stop))
        t.start()
        t.join(timeout=1)

    assert t.is_alive() is False
    ws_client.write_channel.assert_not_called()
