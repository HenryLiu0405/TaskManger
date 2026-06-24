#!/usr/bin/env python3
"""ZMQ topic bridge: ROS2 subscriber → ZMQ PUB,  ZMQ SUB → ROS2 publisher.
   One direction = one sub process + one pub process, connected by localhost ZMQ.
   ZMQ must be initialized BEFORE rclpy to avoid DDS interference.
   Usage:
     zmq_bridge.py sub <rmw> <domain> <topic> <type> <port> [transient_local]
     zmq_bridge.py pub <rmw> <domain> <topic> <type> <port> [transient_local]
   The optional 'transient_local' flag enables TRANSIENT_LOCAL durability
   (needed for latched topics like /map).
"""
import sys, os, signal, zmq

CDDS_URI = '<CycloneDDS><Domain><General><NetworkInterfaceAddress>enP8p1s0</NetworkInterfaceAddress></General></Domain></CycloneDDS>'

def import_type(s: str):
    pkg, sub, name = s.split('/')
    mod = __import__(f'{pkg}.{sub}', fromlist=[name])
    return getattr(mod, name)

def setup_env(rmw: str, domain: str):
    os.environ['RMW_IMPLEMENTATION'] = rmw
    os.environ['ROS_DOMAIN_ID'] = domain
    os.environ.pop('CYCLONEDDS_URI', None)
    if 'cyclone' in rmw.lower():
        os.environ['CYCLONEDDS_URI'] = CDDS_URI

# ── subscriber side: ROS → ZMQ ─────────────────────────────────────
def run_sub(rmw, domain, topic, msg_type_str, port, transient=False):
    setup_env(rmw, domain)

    # ZMQ BEFORE rclpy (critical – verified working order)
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUB)
    sock.bind(f'tcp://127.0.0.1:{port}')

    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, DurabilityPolicy
    from rclpy.serialization import serialize_message

    rclpy.init()
    node = Node(f'zmqsub_{topic.lstrip("/").replace("/","_")}')
    Msg = import_type(msg_type_str)

    last_data = None   # cache last message for ZMQ late-joiner latching

    def cb(msg):
        nonlocal last_data
        data = serialize_message(msg)
        sock.send(data)
        last_data = data

    qos = QoSProfile(depth=10,
        durability=DurabilityPolicy.TRANSIENT_LOCAL if transient else DurabilityPolicy.VOLATILE)
    node.create_subscription(Msg, topic, cb, qos)
    node.get_logger().info(f'ROS sub [{rmw}:{domain}] {topic} ({"TL" if transient else "VOL"}) → ZMQ :{port}')

    # Manual spin: re-send last message every 1s so late-joining ZMQ
    # subscribers (pub workers) receive latched topics like /map.
    while rclpy.ok():
        rclpy.spin_once(node, timeout_sec=1.0)
        if transient and last_data is not None:
            sock.send(last_data)


# ── publisher side: ZMQ → ROS ──────────────────────────────────────
def run_pub(rmw, domain, topic, msg_type_str, port, transient=False):
    setup_env(rmw, domain)

    # ZMQ BEFORE rclpy (critical)
    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.connect(f'tcp://127.0.0.1:{port}')
    sock.setsockopt(zmq.SUBSCRIBE, b'')

    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, DurabilityPolicy
    from rclpy.serialization import deserialize_message

    rclpy.init()
    node = Node(f'zmqpub_{topic.lstrip("/").replace("/","_")}')
    Msg = import_type(msg_type_str)
    qos = QoSProfile(depth=10,
        durability=DurabilityPolicy.TRANSIENT_LOCAL if transient else DurabilityPolicy.VOLATILE)
    pub = node.create_publisher(Msg, topic, qos)

    node.get_logger().info(f'ZMQ :{port} → ROS pub [{rmw}:{domain}] {topic} ({"TL" if transient else "VOL"})')

    while rclpy.ok():
        try:
            data = sock.recv(zmq.NOBLOCK)
            msg = deserialize_message(data, Msg)
            pub.publish(msg)
        except zmq.Again:
            rclpy.spin_once(node, timeout_sec=0.05)


if __name__ == '__main__':
    mode = sys.argv[1]
    rmw  = sys.argv[2]
    dom  = sys.argv[3]
    topic = sys.argv[4]
    mtype = sys.argv[5]
    port  = int(sys.argv[6])
    transient = len(sys.argv) > 7 and sys.argv[7] == 'transient_local'

    {'sub': run_sub, 'pub': run_pub}[mode](rmw, dom, topic, mtype, port, transient)
