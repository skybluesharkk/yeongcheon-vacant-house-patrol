# -*- coding: utf-8 -*-
"""
도로 그래프 정의 모듈

영천 빈집 순찰 시뮬레이션을 위한 도로 노드/엣지/빈집 매핑 정보를 담는다.

좌표계 약속 (실측 확인된 변환):
    - 노드/빈집 GameObject 들은 0.1배 스케일된 월드맵의 자식이라, Unity 인스펙터에 보이는
      Transform.position 은 실제 월드 좌표의 10배다.  → ODOM_SCALE = 0.1 로 보정.
    - Unity(left-handed, Y-up) → ROS(right-handed, Z-up) 변환은 ROS-TCP-Endpoint 가
      자동으로 해주는데, 실측 결과 다음 매핑이다:
          odom.x =  unity.Z
          odom.y = -unity.X
          yaw_rad = -unity_rotationY_deg * pi/180
      (검증: 차량 Unity Transform (X=-7.66, Z=9.07, rotY=174°) → TF (x=9.07, y=7.66, yaw≈-174°) ✓)
    - 따라서 _RAW_NODES / _RAW_HOUSES 에는 Unity 인스펙터 값을 그대로 적으면 되고,
      모듈 로드 시 NODES / HOUSES 가 odom 좌표로 자동 변환된다.
"""

import math

# 노드가 0.1배 스케일 월드맵 자식이라 inspector 값이 실제의 10배.
# 월드 스케일을 1.0으로 바꾸면 이 값만 1.0으로 수정.
ODOM_SCALE = 0.1


def unity_to_odom(unity_x, unity_z):
    """Unity 인스펙터의 (X, Z) → ROS odom (x, y) 변환."""
    return (unity_z * ODOM_SCALE, -unity_x * ODOM_SCALE)


# ---------------------------------------------------------------------------
# 도로 교차점/분기점 노드 (Unity 인스펙터 X, Z 값)
# ---------------------------------------------------------------------------
_RAW_NODES = {
    "N0":  (-85.0,  89.0),   # 차량 스폰 위치 근처
    "N1":  (-82.0,  83.0),
    "N2":  (-24.0,  88.0),
    "N3":  ( 9.0,  80.0), # 빈집. 진행방향에서 좌측으로 45도 정도

    "N4":  ( 72.0,  66.0),
    "N5":  ( 46.0,  -4.0),
    "N6":  ( 73.2, -21.3), # 빈집 진행방향에서 좌측으로 30도

    "N7":  ( 59.0, -73.0),
    "N8":  ( 63.1,-90.5), # 빈집. 진행방향에서 좌측으로 90도
    "N9":  ( -5.0, -21.0),
    "N10": (-63.0, -48.0),

    "N11": (-80.0, -66.0),
    "N12": (-96.0, -84.0),
    "N13": (-92.0, -12.0),
}

# planner / patrol_node 가 실제로 쓰는 좌표 (ROS odom 프레임)
NODES = {nid: unity_to_odom(x, z) for nid, (x, z) in _RAW_NODES.items()}

# ---------------------------------------------------------------------------
# 도로 인접 정보 (양방향)
# ---------------------------------------------------------------------------
# 한 방향만 적어두면 build_bidirectional_edges()가 반대 방향도 채워준다.
_RAW_EDGES = {
    "N0":  ["N1"],
    "N1":  ["N2"],
    "N2":  ["N3"],
    "N3":  ["N4"],
    "N4":  ["N5"],
    "N5":  ["N6", "N9"],
    "N6":  ["N7"],
    "N7":  ["N8"],
    "N8":  ["N9"],
    "N9":  ["N10"],
    "N10": ["N11"],
    "N11": ["N12","N13"],
    
}


def build_bidirectional_edges(raw):
    """단방향으로 정의된 엣지를 양방향 dict로 변환."""
    edges = {nid: set() for nid in NODES.keys()}
    for src, neighbors in raw.items():
        for dst in neighbors:
            edges[src].add(dst)
            edges[dst].add(src)
    # set -> list 로 정렬해서 디버깅 시 보기 좋게
    return {nid: sorted(list(ns)) for nid, ns in edges.items()}


EDGES = build_bidirectional_edges(_RAW_EDGES)


# ---------------------------------------------------------------------------
# 유틸: 임의 좌표 -> 가장 가까운 노드 id
# ---------------------------------------------------------------------------
def nearest_node(x, y, nodes=NODES):
    """주어진 (x, y)에서 가장 가까운 노드 id를 반환."""
    best_id = None
    best_d2 = float("inf")
    for nid, (nx, ny) in nodes.items():
        d2 = (nx - x) ** 2 + (ny - y) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best_id = nid
    return best_id


# ---------------------------------------------------------------------------
# 빈집 정의 (Unity 인스펙터 값 그대로)
# ---------------------------------------------------------------------------
# 각 빈집의 "마커 GameObject" 한 개씩을 빈집 앞 도로에 만들어 두고,
#   pos  : 마커의 Transform.position 의 (X, Z)  - Unity 인스펙터 값 그대로 (Y는 높이라 안 씀)
#   yaw  : 마커의 Transform.rotation Y (deg)    - 마커의 +Z 화살표가 빈집을 향하도록 설정
# 를 적는다. 모듈 로드 시 HOUSES 가 ROS odom (m, rad) 으로 자동 변환됨.
#
# 부분 탐지는 코드 수정 없이 /house_request 토픽에 ID 부분집합을 보내거나,
# patrol_node.py 의 DEFAULT_HOUSE_IDS 를 수정하면 된다.
_RAW_HOUSES = {
    "H1": {"pos": ( 13.2,   79.2),  "yaw":   67.841},
    "H2": {"pos": ( 85.4, -37.3),  "yaw":   48.510},
    "H3": {"pos": ( 67.1, -90.4),  "yaw":  142.832},
    "H4": {"pos": (-65.2, -63.6),  "yaw": -172.736},
    "H5": {"pos": (-94.2, -82.2),  "yaw":   -9.900},
    "H6": {"pos": (-85.9, -14.6),  "yaw":  -37.389},
}


# planner / patrol_node 가 실제로 쓰는 빈집 좌표 (ROS odom 프레임, yaw 는 rad)
HOUSES = {
    hid: {
        "pos": unity_to_odom(d["pos"][0], d["pos"][1]),
        "yaw": math.radians(-d["yaw"]),
    }
    for hid, d in _RAW_HOUSES.items()
}


# 라우팅을 위해 각 빈집을 가장 가까운 도로 노드와 자동 매핑.
# planner 는 이 매핑으로 A* 경로를 짠 뒤, 마지막에 HOUSES[h]["pos"] 로 접근한다.
HOUSE_NODES = {hid: nearest_node(d["pos"][0], d["pos"][1])
               for hid, d in HOUSES.items()}


def get_graph_json():
    """웹소켓 및 백엔드 전송용 JSON 호환 dict 생성"""
    return {
        "type": "graph",
        "nodes": {nid: [pos[0], pos[1]] for nid, pos in NODES.items()},
        "edges": EDGES,
        "houses": {
            hid: {
                "pos": [data["pos"][0], data["pos"][1]],
                "yaw": data["yaw"]
            }
            for hid, data in HOUSES.items()
        }
    }
